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
import statistics
import time
from collections import deque
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


# NOTE on provenance (research-log.md Passes 5-6): STRUCTURAL/INDUSTRIAL n/sigma
# are from a real measured campaign (Pereira et al. 2018 IEEE I2MTC, doc 8409563
# — office + hydro-plant 2.4 GHz mesh RSSI). WILDLAND is `indicative`:
#   - The original "ITU-R P.833, n=2.7, sigma=8.7 dB" was WRONG (P.833 has no
#     path-loss exponent; its 8.7 dB is excess-vegetation-loss scatter).
#   - Near-ground 2.4 GHz is TWO-SLOPE: n~2 short-range, rising to n~3.5-4 past
#     the first-Fresnel-zone breakpoint (~50-110 m for ~1.3 m antennas) — so a
#     single LOW n (~2.0) UNDER-predicts loss; the single-slope planning value is
#     n~3.0.
#   - PROVENANCE (primary sources read 2026-07-04): n and sigma are now grounded
#     in MEASURED vegetation campaigns, no longer assumed:
#       * Schneider 2026 (Future Internet, 3.75 GHz vineyard LNS, RX @1.5 m =
#         responder height) — alpha/sigma by foliage density: 2.27/7.21 (bare),
#         3.28/8.21 (growing), 4.23/8.97 (dense canopy). THIS is the sigma source.
#       * Olasupo 2016 (IEEE TAP, 2.4 GHz natural grass): grass exponents ~2.9-4.
#       * Klaina 2018 (Sensors, 2.4 GHz near-ground): obstructed slopes bracket 3.
#       * Boonlom 2026 (Sensors, 923 MHz LoRa): forest n=3.22 (LOS 2.31).
#       * Barrios-Ulloa 2022 (Sensors, review): vegetated models carry high error.
#     Convergent picture: vegetation n ~2.3 (light) -> ~3.3 (moderate) -> ~4.2
#     (dense canopy), sigma ~7-9 dB rising with foliage density. Preset uses the
#     MODERATE point n=3.0 / sigma=8.0.
#     Still `indicative`: the sigma source is 3.75 GHz vineyard (not 2.4 GHz
#     forest), so bench-calibrate on the actual boards; a density-parameterized
#     or two-slope model is the eventual upgrade.
# Bench-calibrate every preset on the actual boards + mounting before trusting
# distances (walk to 1/2/4/8 m per environment); a two-slope wildland model is
# the eventual upgrade.
ENV_PRESETS: dict[str, EnvPreset] = {
    "WILDLAND":   EnvPreset("WILDLAND",   3.0, 8.0, -40.0, "indicative",
                            "near-ground vegetation, MODERATE-density single-slope; density-dependent per "
                            "measured LNS (light n~2.3/sig~7.2, moderate n~3.3/sig~8.2, dense-canopy "
                            "n~4.2/sig~9.0 — Schneider 2026 vineyard); two-slope past the Fresnel "
                            "breakpoint; bench-calibrate"),
    "STRUCTURAL": EnvPreset("STRUCTURAL", 4.5, 8.1, -40.0, "measured",
                            "office 2.4 GHz mesh (Pereira et al. 2018 I2MTC)"),
    "INDUSTRIAL": EnvPreset("INDUSTRIAL", 5.85, 4.0, -40.0, "measured",
                            "hydro/industrial plant (Pereira et al. 2018 I2MTC, n 5.2-6.5 / sigma 3.6-4.3)"),
}
DEFAULT_ENV = "WILDLAND"   # primary use case for this project (wildfire/SAR)

FUSION_CORRECTION_WEIGHT = 0.35  # how strongly each RSSI solve pulls fused position toward it

# --- RSSI hygiene (item 1.7; see docs/research-log.md Pass 10) ---
# The old design used the single most-recent RSSI per edge, which aliases the
# 10-20 dB body-shadow swing to a random distance. Instead, keep a short
# per-direction window and estimate the near-LoS RSSI with a HIGH PERCENTILE:
#  - mean/median sit in the middle of the one-sided shadow swing -> distance
#    over-estimate (pessimistic);
#  - raw MAX chases the *symmetric* fast-fading peaks -> distance UNDER-estimate,
#    i.e. a downed responder looks closer/safer (the dangerous direction);
#  - a ~75th-90th percentile recovers the least-shadowed side without chasing
#    fading. 75 is Science's simulated near-unbiased point; Code's reproduction
#    put it nearer 90 — model-dependent, so it's BENCH-tunable.
# CAVEAT: at the current ~2 s beacon cadence a window holds only ~1-4 samples,
# so this estimator is sample-rate-limited until beaconing is faster (couples
# to the Tier-5 beacon-rate/power decision). It degrades gracefully to the
# available samples and is forward-compatible with a higher beacon rate.
RSSI_PERCENTILE = 75.0
RSSI_WINDOW_MAX_S = 8.0            # per-edge sample window cap (still/slow responders)
RSSI_WINDOW_MIN_S = 2.0           # window floor (brisk movement)
STALE_DISPLACEMENT_FLOOR_M = 3.0  # keep stale-edge displacement under ~this (speed-adaptive freshness)


def rssi_to_distance_m(rssi_dbm: float, preset: EnvPreset) -> float:
    return 10 ** ((preset.tx_power_1m_dbm - rssi_dbm) / (10 * preset.path_loss_n))


def _percentile(sorted_xs: list[float], pct: float) -> float:
    """Linear-interpolated percentile of an already-sorted list (numpy-free,
    so it stays cheap in the per-edge hot path)."""
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    rank = (pct / 100.0) * (len(sorted_xs) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (rank - lo)


def robust_rssi(samples: list[float]) -> float:
    """Near-LoS RSSI estimate for one directional edge window: reject impulsive
    multipath outliers with a light median pre-filter, then take a high
    percentile (item 1.7). Falls back cleanly for tiny windows."""
    if len(samples) >= 5:
        s = sorted(samples)
        # median-of-3 smoothing to blunt impulsive fast-fading spikes a raw
        # percentile would otherwise include
        sm = [statistics.median(s[max(0, i - 1):i + 2]) for i in range(len(s))]
        return _percentile(sorted(sm), RSSI_PERCENTILE)
    return _percentile(sorted(samples), RSSI_PERCENTILE)


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
    speed_mps: float = 0.0     # from PDR steps/report; drives speed-adaptive RSSI freshness (item 1.7)
    stationary: bool = False   # node's stillness flag; still windows are the best orientation-averaging windows
    last_telemetry_s: float = field(default_factory=time.time)
    is_anchor: bool = False  # team lead == anchor at (0,0)

    def predict(self, step_count: int, stride_mm: int, heading_mrad: int, heading_conf: int):
        """Step-detection PDR prediction: advance the fused position by
        step_count strides along the node's reported heading. Replaces the
        old displacement-vector integration (which relied on the diverging
        on-node double integration — see docs/research-log.md 0.1). Heading
        is the node's own absolute value in its arbitrary frame, so we set
        (not accumulate) it each report. (last_telemetry_s / speed are owned by
        ingest_telemetry, which needs the pre-update timestamp to compute dt.)"""
        self.heading_rad = heading_mrad / 1000.0
        self.heading_conf = heading_conf
        if self.is_anchor:
            return  # anchor defines the origin of this coordinate frame
        distance_m = step_count * (stride_mm / 1000.0)
        self.x_m += distance_m * math.cos(self.heading_rad)
        self.y_m += distance_m * math.sin(self.heading_rad)


class NetworkFusion:
    def __init__(self, environment: str = DEFAULT_ENV):
        self.nodes: dict[int, NodeFusionState] = {}
        # DIRECTIONAL windows: (observer, neighbor) -> deque of (rssi_dbm, ts).
        # Kept per direction (not collapsed to a sorted pair) so bidirectional
        # edges can be averaged — RSSI(A->B) != RSSI(B->A) by a few dB from TX
        # power / antenna-pattern / per-unit variation (item 1.7).
        self._rssi_samples: dict[tuple[int, int], deque] = {}
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
        now = time.time()
        dt = max(0.1, now - state.last_telemetry_s)   # report interval (~1 s)
        state.speed_mps = (t.step_count * (t.stride_mm / 1000.0)) / dt
        state.stationary = t.stationary
        state.predict(t.step_count, t.stride_mm, t.heading_mrad, t.heading_conf)
        state.par_status = t.par_status
        state.battery_pct = t.battery_pct
        state.imu_present = t.imu_present
        state.last_telemetry_s = now

        for sample in t.rssi:
            key = (node_id, sample.neighbor_node_id)   # directional: node_id observed neighbor
            dq = self._rssi_samples.get(key)
            if dq is None:
                dq = self._rssi_samples[key] = deque(maxlen=64)
            dq.append((float(sample.rssi_dbm), now))

    def _edge_max_age_s(self, observer_speed_mps: float) -> float:
        """Speed-adaptive freshness: a 15 s edge at 1.4 m/s is a ~21 m stale
        error. Bound stale-edge displacement under ~STALE_DISPLACEMENT_FLOOR_M —
        ~2 s when moving briskly, relaxing to ~8 s when slow/still (item 1.7)."""
        if observer_speed_mps <= 0.05:
            return RSSI_WINDOW_MAX_S
        return min(RSSI_WINDOW_MAX_S, max(RSSI_WINDOW_MIN_S,
                                          STALE_DISPLACEMENT_FLOOR_M / observer_speed_mps))

    def _fresh_edges(self) -> list[tuple[int, int, float]]:
        """One distance per unordered pair, from the robust high-percentile of
        each direction's speed-fresh RSSI window, bidirectionally averaged."""
        now = time.time()
        pair_dir_rssi: dict[tuple[int, int], list[float]] = {}
        for (obs, nbr), dq in self._rssi_samples.items():
            observer = self.nodes.get(obs)
            max_age = self._edge_max_age_s(observer.speed_mps if observer else 0.0)
            # prune anything older than the cap (bounded memory), then take the
            # speed-adaptive fresh window
            while dq and now - dq[0][1] > RSSI_WINDOW_MAX_S:
                dq.popleft()
            fresh = [r for (r, ts) in dq if now - ts <= max_age]
            if not fresh:
                continue
            key = (obs, nbr) if obs < nbr else (nbr, obs)
            pair_dir_rssi.setdefault(key, []).append(robust_rssi(fresh))
        out = []
        for (a, b), dir_rssis in pair_dir_rssi.items():
            # average the two directional estimates when both exist (recovers
            # the reciprocal path, halves the per-unit TX/RX offset error); a
            # one-way edge is used as-is (lower confidence — see item 4.3).
            avg_rssi = sum(dir_rssis) / len(dir_rssis)
            out.append((a, b, rssi_to_distance_m(avg_rssi, self._preset)))
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
