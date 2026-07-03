"""RSSI multilateration + IMU dead-reckoning fusion.

Produces a *relative* 2D position per node — there is no absolute
coordinate frame here (see docs/protocol.md's caveat on RSSI accuracy).
Positions are anchored so the team lead sits at (0, 0) and are otherwise
free-floating relative to it; expect several meters of error and visible
jumps when the RSSI solve re-anchors. This is "where is everyone relative
to the team lead," not survey-grade GPS.

Two update paths feed a per-node fused position:
  1. IMU predict — every TELEMETRY report carries a displacement since the
     last report (dx/dy/dtheta). We rotate that into a running per-node
     world-frame heading and add it to the fused position immediately.
     Cheap, frequent, but drifts (see dead_reckoning.h — ZUPT bounds it
     somewhat, this doesn't eliminate it).
  2. RSSI correct — periodically (recompute_multilateration), we build a
     distance graph from every pairwise RSSI observation across the whole
     team, solve a least-squares layout, and pull each node's fused
     position toward that solve. Noisy and infrequent, but doesn't drift.

The two are combined with a simple complementary filter (weighted
average), not a full Kalman filter — deliberately, for a prototype: it's
far easier to reason about and debug when something looks wrong on the
map, at the cost of not being statistically optimal. Revisit if/when you
have real hardware data to tune noise covariances against.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.optimize import least_squares

from protocol import ParStatus, TelemetryPayload

log = logging.getLogger("fusion")

# Log-distance path-loss model: rssi = tx_1m - 10*n*log10(d)
# => d = 10 ** ((tx_1m - rssi) / (10*n))
#
# A SINGLE global exponent is indefensible — a wrong n is a systematic BIAS,
# not zero-mean noise: the same measured loss solves to ~100 m at n=2.0 vs
# ~4.8 m at n=5.85 (a >20x spread), dragging the whole map (research-log.md
# item 1.1). So n/sigma are per-environment PRESETS selected at incident start.
# sigma (log-normal shadowing std, dB) is what sizes the map's honest error
# bars — fractional range error = (ln10/10)*(sigma/n); at wildland
# sigma=8.7/n=2.7 that's ~+-74% of range, which is why 0.2 says render
# proximity/topology, not survey coordinates.
@dataclass(frozen=True)
class EnvPreset:
    name: str
    path_loss_n: float
    shadowing_sigma_db: float
    tx_power_1m_dbm: float
    confidence: str   # 'measured' (a cited campaign) or 'indicative' (plausible magnitude, needs bench cal)
    note: str = ""

    def fractional_range_sigma(self) -> float:
        """1-sigma range error as a fraction of distance (distance-independent
        under log-normal shadowing). Feeds the map's confidence sizing (0.2)."""
        return (math.log(10) / 10.0) * (self.shadowing_sigma_db / self.path_loss_n)


# NOTE on provenance (research-log.md Pass 5): STRUCTURAL/INDUSTRIAL n/sigma are
# from a real measured campaign (Pereira et al. 2018 IEEE I2MTC, doc 8409563 —
# office + hydro-plant 2.4 GHz mesh RSSI). WILDLAND is `indicative`: the
# magnitudes are plausible per the near-ground forest-propagation literature,
# but the originally-cited "ITU-R P.833" attribution was WRONG (P.833 has no
# path-loss exponent; its 8.7 dB is excess-vegetation-loss scatter, not
# log-distance shadowing). Bench-calibrate every preset on the actual boards +
# mounting before trusting distances (walk to 1/2/4/8 m per environment).
ENV_PRESETS: dict[str, EnvPreset] = {
    "WILDLAND":   EnvPreset("WILDLAND",   2.7, 8.7, -40.0, "indicative",
                            "near-ground forest; magnitudes plausible, NOT from ITU-R P.833; bench-calibrate"),
    "STRUCTURAL": EnvPreset("STRUCTURAL", 4.5, 8.1, -40.0, "measured",
                            "office 2.4 GHz mesh (Pereira et al. 2018 I2MTC)"),
    "INDUSTRIAL": EnvPreset("INDUSTRIAL", 5.85, 4.0, -40.0, "measured",
                            "hydro/industrial plant (Pereira et al. 2018 I2MTC, n 5.2-6.5 / sigma 3.6-4.3)"),
}
DEFAULT_ENV = "WILDLAND"   # primary use case for this project (wildfire/SAR)

RSSI_OBSERVATION_MAX_AGE_S = 15.0
FUSION_CORRECTION_WEIGHT = 0.35  # how strongly each RSSI solve pulls fused position toward it


def rssi_to_distance_m(rssi_dbm: float, preset: EnvPreset) -> float:
    return 10 ** ((preset.tx_power_1m_dbm - rssi_dbm) / (10 * preset.path_loss_n))


@dataclass
class NodeFusionState:
    node_id: int
    x_m: float = 0.0
    y_m: float = 0.0
    heading_rad: float = 0.0
    heading_conf: int = 0     # 0-255 from the node's NMNI re-zero recency; sizes map heading uncertainty
    par_status: ParStatus = ParStatus.OK
    battery_pct: int = 100
    imu_present: bool = False
    last_telemetry_s: float = field(default_factory=time.time)
    is_anchor: bool = False  # team lead == anchor at (0,0)

    def predict(self, step_count: int, stride_mm: int, heading_mrad: int, heading_conf: int):
        """Step-detection PDR prediction: advance the fused position by
        step_count strides along the node's reported heading. Replaces the
        old displacement-vector integration (which relied on the diverging
        on-node double integration — see docs/research-log.md 0.1). Heading
        is the node's own absolute value in its arbitrary frame, so we set
        (not accumulate) it each report."""
        self.heading_rad = heading_mrad / 1000.0
        self.heading_conf = heading_conf
        if self.is_anchor:
            return  # anchor defines the origin of this coordinate frame
        distance_m = step_count * (stride_mm / 1000.0)
        self.x_m += distance_m * math.cos(self.heading_rad)
        self.y_m += distance_m * math.sin(self.heading_rad)
        self.last_telemetry_s = time.time()


class NetworkFusion:
    def __init__(self, environment: str = DEFAULT_ENV):
        self.nodes: dict[int, NodeFusionState] = {}
        # (a, b) -> (rssi_dbm, observed_at_s), a < b, most recent wins
        self._rssi_edges: dict[tuple[int, int], tuple[float, float]] = {}
        self._anchor_node_id: Optional[int] = None
        self._preset = ENV_PRESETS[environment]
        log.info("environment preset: %s (n=%.2f sigma=%.1f dB, %s)",
                 self._preset.name, self._preset.path_loss_n, self._preset.shadowing_sigma_db,
                 self._preset.confidence)

    def set_environment(self, name: str):
        """Select the path-loss preset at incident start (WILDLAND / STRUCTURAL
        / INDUSTRIAL). Command should set this per the scene; an ambiguous scene
        (a structure inside a wildland fire) is exactly when the 0.2 topology
        view — not a mis-preset coordinate map — is the honest fallback."""
        self._preset = ENV_PRESETS[name]
        log.info("environment preset -> %s", name)

    @property
    def preset(self) -> EnvPreset:
        return self._preset

    def set_anchor(self, node_id: int):
        """Call once you know which node is TEAM_LEAD — usually the first
        TELEMETRY_BATCH sender you see, since only leads send batches."""
        if self._anchor_node_id == node_id:
            return
        self._anchor_node_id = node_id
        for n in self.nodes.values():
            n.is_anchor = (n.node_id == node_id)
        self._ensure_node(node_id).is_anchor = True
        log.info("anchor set to node %d", node_id)

    def _ensure_node(self, node_id: int) -> NodeFusionState:
        if node_id not in self.nodes:
            self.nodes[node_id] = NodeFusionState(node_id, is_anchor=(node_id == self._anchor_node_id))
        return self.nodes[node_id]

    def ingest_telemetry(self, node_id: int, t: TelemetryPayload):
        state = self._ensure_node(node_id)
        state.predict(t.step_count, t.stride_mm, t.heading_mrad, t.heading_conf)
        state.par_status = t.par_status
        state.battery_pct = t.battery_pct
        state.imu_present = t.imu_present
        state.last_telemetry_s = time.time()

        now = time.time()
        for sample in t.rssi:
            a, b = sorted((node_id, sample.neighbor_node_id))
            self._rssi_edges[(a, b)] = (float(sample.rssi_dbm), now)

    def _fresh_edges(self) -> list[tuple[int, int, float]]:
        now = time.time()
        out = []
        for (a, b), (rssi, ts) in self._rssi_edges.items():
            if now - ts <= RSSI_OBSERVATION_MAX_AGE_S:
                out.append((a, b, rssi_to_distance_m(rssi, self._preset)))
        return out

    def recompute_multilateration(self):
        """Solve a relative 2D layout from all fresh RSSI edges and nudge
        each node's fused position toward it. No-op if there are no edges
        at all, or no anchor set yet. A single edge (the minimal 2-node
        case: anchor + one field node) is enough to place that node at the
        right *distance* from the anchor, even though bearing is
        undetermined in 2D from range alone — the solver just settles on
        whatever bearing the initial guess nudged it toward. More edges
        (3+ nodes, or repeated observations from different vantage points)
        are what actually pin down bearing."""
        edges = self._fresh_edges()
        if not edges or self._anchor_node_id is None:
            return

        node_ids = sorted({n for a, b, _ in edges for n in (a, b)} | {self._anchor_node_id})
        if len(node_ids) < 2:
            return
        idx = {nid: i for i, nid in enumerate(node_ids)}
        n = len(node_ids)

        # initial guess: current fused positions (keeps the solver near the
        # IMU-predicted layout instead of an arbitrary random start). A
        # brand-new non-anchor node defaults to (0, 0) same as the anchor —
        # the distance residual's Jacobian is singular exactly there (its
        # gradient w.r.t. position is undefined at zero radius), so LM gets
        # stuck refusing to move it at all. Nudge those off the origin
        # first, deterministically (by node_id, not random — Random is
        # unavailable in some call contexts and this only needs to break
        # the tie, not be unpredictable).
        x0 = np.zeros(2 * n)
        for nid, i in idx.items():
            state = self.nodes.get(nid)
            if state and (state.x_m != 0.0 or state.y_m != 0.0 or state.is_anchor):
                x0[2 * i] = state.x_m
                x0[2 * i + 1] = state.y_m
            elif nid != self._anchor_node_id:
                angle = (nid % 360) * math.pi / 180.0
                x0[2 * i] = 0.5 * math.cos(angle)
                x0[2 * i + 1] = 0.5 * math.sin(angle)

        anchor_i = idx[self._anchor_node_id]

        def residuals(v):
            pts = v.reshape(n, 2)
            pts[anchor_i] = (0.0, 0.0)  # anchor pinned at origin, not a free variable in spirit
            res = []
            for a, b, dist in edges:
                pa, pb = pts[idx[a]], pts[idx[b]]
                res.append(np.linalg.norm(pa - pb) - dist)
            # soft penalty pulling the anchor back to origin, since we can't
            # truly remove it from the optimization vector without a lot
            # more bookkeeping — keeps the solve simple for a prototype
            res.append((pts[anchor_i][0]) * 10)
            res.append((pts[anchor_i][1]) * 10)
            return np.array(res)

        try:
            # 'trf' (not 'lm'): with only 2-3 nodes, the residual count
            # (edges + 2 anchor-pin terms) can be less than the number of
            # free variables (2 per node), which 'lm' refuses to run on at
            # all. 'trf' has no such restriction.
            result = least_squares(residuals, x0, method="trf", max_nfev=200)
        except Exception:
            log.exception("multilateration solve failed")
            return

        pts = result.x.reshape(n, 2)
        for nid, i in idx.items():
            state = self._ensure_node(nid)
            if state.is_anchor:
                state.x_m, state.y_m = 0.0, 0.0
                continue
            w = FUSION_CORRECTION_WEIGHT
            # float(...): pts[i] entries are numpy.float64 from the
            # least_squares result: leaving them as numpy scalars here
            # means every field they touch downstream stays numpy-typed,
            # and json.dumps (used by FastAPI's ws.send_json in
            # server.py's broadcast loop) can't serialize float64 —
            # it'd raise mid-broadcast the first time the solver runs.
            state.x_m = float((1 - w) * state.x_m + w * pts[i][0])
            state.y_m = float((1 - w) * state.y_m + w * pts[i][1])

    def environment_summary(self) -> dict:
        """Active preset + the fractional range error it implies — the SBC map
        uses fractional_range_sigma × each node's distance-from-anchor to size
        confidence rings, and `confidence` to warn when the preset is
        indicative/uncalibrated (0.2 rendering)."""
        p = self._preset
        return {
            "environment": p.name,
            "path_loss_n": p.path_loss_n,
            "shadowing_sigma_db": p.shadowing_sigma_db,
            "fractional_range_sigma": round(p.fractional_range_sigma(), 3),
            "confidence": p.confidence,
            "note": p.note,
        }

    def snapshot(self) -> list[dict]:
        now = time.time()
        return [
            {
                "node_id": s.node_id,
                "x_m": round(s.x_m, 2),
                "y_m": round(s.y_m, 2),
                "par_status": s.par_status.name,
                "battery_pct": s.battery_pct,
                "imu_present": s.imu_present,
                "heading_deg": round(math.degrees(s.heading_rad), 0),
                "heading_conf": s.heading_conf,   # 0-255; low = heading drifted since last stillness re-zero
                "is_anchor": s.is_anchor,
                "age_s": round(now - s.last_telemetry_s, 1),
            }
            for s in self.nodes.values()
        ]
