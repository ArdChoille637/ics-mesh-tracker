"""RSSI multilateration + IMU dead-reckoning fusion.

Produces a *relative* 2D position per node — there is no absolute
coordinate frame here (see docs/protocol.md's caveat on RSSI accuracy).
Positions are anchored so the team lead sits at (0, 0) and are otherwise
free-floating relative to it; expect several meters of error and visible
jumps when the RSSI solve re-anchors. This is "where is everyone relative
to the team lead," not survey-grade GPS.

Two update paths feed a per-node fused position:
  1. IMU predict — every TELEMETRY report carries step-PDR motion (steps +
     stride + heading). We advance the fused position along the reported
     heading immediately. Cheap, frequent, but heading drifts.
  2. RSSI correct — periodically (recompute_multilateration), we build a
     distance graph from every pairwise RSSI observation, solve a weighted
     least-squares layout in dB space, flip-guard + align it to the previous
     frame, and complementary-blend it into the fused positions. Noisy and
     infrequent, but doesn't drift.

The 0.2 redesign (research-log.md Passes 8/10/11/12) makes the RSSI solve
HONEST about what range-only body-worn RSSI can and can't know:
  - residuals are whitened dB-space log-ratios (shadowing is log-normal), so
    far edges no longer dominate near ones;
  - each node gets a confidence ELLIPSE whose tangential (bearing) axis blows
    up when the geometry can't fix bearing — range-only tells you distance,
    not direction;
  - each node is graded coordinate- vs topology-grade; under-constrained,
    under-sampled, flipped, or motion-smeared nodes FAIL SAFE to topology
    (a proximity ring around the anchor) rather than a false pinpoint. The
    overriding rule: never show a possibly-downed responder as more precisely
    located than the data supports.
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
# sigma=8/n=3 that's ~+-61% of range, which is why 0.2 says render
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


# NOTE on provenance (research-log.md Passes 5-8): STRUCTURAL/INDUSTRIAL n/sigma
# are from a real measured campaign (Pereira et al. 2018 IEEE I2MTC, doc 8409563
# — office + hydro-plant 2.4 GHz mesh RSSI). WILDLAND is `indicative`:
#   - The original "ITU-R P.833, n=2.7, sigma=8.7 dB" was WRONG (P.833 has no
#     path-loss exponent; its 8.7 dB is excess-vegetation-loss scatter).
#   - Near-ground 2.4 GHz is TWO-SLOPE: n~2 short-range, rising to n~3.5-4 past
#     the first-Fresnel-zone breakpoint (~50-110 m for ~1.3 m antennas) — so a
#     single LOW n (~2.0) UNDER-predicts loss; the single-slope planning value is
#     n~3.0.
#   - PROVENANCE (primary sources read 2026-07-04): n and sigma are grounded in
#     MEASURED vegetation campaigns, no longer assumed:
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
# CAVEAT (item 1.7b): at ~0.5 Hz per-link RSSI a window holds only ~1-4 samples,
# so the estimator is sample-rate-limited (couples to the Tier-5 beacon-rate
# decision). It degrades gracefully and is forward-compatible with a higher rate;
# under-sampled edges are down-weighted and their nodes grade topology (below).
RSSI_PERCENTILE = 75.0
RSSI_WINDOW_MAX_S = 8.0            # per-edge sample window cap (still/slow responders)
RSSI_WINDOW_MIN_S = 2.0           # window floor (brisk movement)
STALE_DISPLACEMENT_FLOOR_M = 3.0  # keep stale-edge displacement under ~this (speed-adaptive freshness)

# --- 0.2 solve + confidence redesign (research-log.md Pass 12) ---
# Residuals are WHITENED dB-space log-ratios: r = (10n/sigma_dB)*log10(d_model/d_meas).
# Shadowing is log-normal (Gaussian in dB) so this is homoscedastic — no hand-coded
# distance weight, and far edges stop dominating near ones. The anchor gauge is
# hard-eliminated (only non-anchor coords are free variables). The confidence
# ellipse is a CLOSED-FORM polar model (radial from the preset sigma + edge count;
# tangential INJECTED from bearing dilution) — deliberately NOT read from the solver
# Jacobian, which reports ~zero tangential variance for range-only geometry (a
# confident dot placed exactly where bearing is least known — the backwards answer).
D_FLOOR_M = 0.5              # floor on d_model/d_meas: guards log10(0) + the zero-radius Jacobian singularity (two coincident nodes)
SOFT_L1_F_SCALE = 1.5       # robust-loss knee in sigma units (whitened residuals) — down-weights >~1.5-sigma NLOS/multipath edges
SOLVE_MAX_NFEV = 300
ONEWAY_SIGMA_MULT = 1.4     # inflate one-way edges — no bidirectional per-unit-offset cancellation (item 4.3)
K_SAMP = 3.0               # sample-starvation sigma inflation: sqrt(1 + K_SAMP/n_samp); n=2 -> x1.58, n=20 -> x1.07
MIN_R_M = 1.0              # floor on distance-from-anchor when sizing the confidence ellipse
# Tangential (bearing) uncertainty follows range-only GDOP: sigma_t ~ sigma_r / sin(sep),
# where sep is the widest angular separation of a node's incident edges (0 => bearing is a
# pure gauge => huge tangential smear; ~90 deg => bearing as well-constrained as range).
# This is more physical than a mean-resultant "one-sidedness" proxy, which over-penalizes
# corner nodes whose neighbours still span a wide arc (research-log Pass 12 tuning).
SIN_SEP_FLOOR = 0.15      # clamp on sin(sep): a lone/collinear edge set -> ~6.7x radial tangential smear
COLLINEAR_SV_M = 1.0      # flip guard: trust a REFLECTION decision only if the tracked cloud's smaller singular value exceeds this (it's genuinely 2D, not near-collinear)
# topology-vs-coordinate gating (ALL bench-tunable; honest default is topology)
RIGIDITY_MIN_EDGES = 2     # need >=2 non-collinear incident edges to fix a 2D position (1 edge = a circle = bearing gauge)
BEARING_SPREAD_DEG = 20.0  # two incident edges within this bearing are "collinear" (don't add independent rigidity)
RIGIDITY_MAJOR_M = 25.0    # ellipse major semi-axis above this => position unconstrained => topology
N_SAMP_MIN = 5            # best incident edge must have >=5 samples (robust_rssi's median path threshold; item 1.7b)
YAW_COV_MIN = 0.3         # min heading circular-spread (~45 deg span) for a moving-but-not-still node to be coordinate-grade
MOVING_THRESH_MPS = 0.3   # above this the node is "moving" -> topology (RSSI motion-smeared; PDR carries position then)
RESID_REJECT_SIGMA = 4.0  # a node with a >4-sigma whitened edge residual is inconsistent -> topology
STALE_AGE_S = 15.0
AMBIG_RATIO = 2.5         # ellipse a/b above this sets bearing_ambiguous (client draws an arc/ring, not a dot)
FLIP_UNSTABLE_COUNT = 3   # reflection repairs in the last 10 solves -> whole map forced topology (chronically ambiguous)
AREA_EPS_M2 = 0.5         # reference triangle below this area => chirality ambiguous => skip flip alignment this frame


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
class EdgeObs:
    """One unordered node-pair distance observation with its whitening sigma and
    provenance. Internal to the solve — never serialized."""
    a: int
    b: int
    dist_m: float
    sigma_db_eff: float
    n_samp: int
    one_way: bool


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
    speed_mps: float = 0.0     # from PDR steps/report; drives speed-adaptive RSSI freshness + the moving/topology gate
    stationary: bool = False   # node's stillness flag; still windows are the best orientation-averaging windows
    last_telemetry_s: float = field(default_factory=time.time)
    is_anchor: bool = False     # team lead == anchor at (0,0)
    # --- 0.2 solve outputs, cached by recompute_multilateration, read by snapshot ---
    grade: str = "topology"     # "anchor" | "coordinate" | "topology" | "stale"
    ell_a: float = 0.0          # confidence ellipse: major semi-axis (1-sigma, m)
    ell_b: float = 0.0          # minor semi-axis (1-sigma, m)
    ell_theta_deg: float = 0.0  # major-axis orientation, 0=+x, degrees
    bearing_ambiguous: bool = False
    ring_m: Optional[float] = None  # topology/stale: proximity-band radius around anchor; None for coordinate
    range_m: float = 0.0        # distance from the anchor (the always-trustworthy scalar)
    range_sigma_m: float = 0.0  # 1-sigma radial (range) uncertainty
    n_edges: int = 0
    n_indep: int = 0            # non-collinear incident edges (rigidity count)
    best_n_samp: int = 0
    yaw_cov: float = 0.0
    pos_confidence: float = 0.0  # 0..1 scalar rollup for opacity/coloring

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


def _ang_gap(a: float, b: float) -> float:
    """Absolute smallest angular difference between two bearings (radians)."""
    d = abs(a - b) % (2 * math.pi)
    return min(d, 2 * math.pi - d)


class NetworkFusion:
    def __init__(self, environment: str = DEFAULT_ENV):
        self.nodes: dict[int, NodeFusionState] = {}
        # DIRECTIONAL RSSI windows: (observer, neighbor) -> deque of (rssi_dbm, ts).
        # Kept per direction (not collapsed to a sorted pair) so bidirectional
        # edges can be averaged — RSSI(A->B) != RSSI(B->A) by a few dB from TX
        # power / antenna-pattern / per-unit variation (item 1.7).
        self._rssi_samples: dict[tuple[int, int], deque] = {}
        # per-node heading window (heading_rad, ts) for the yaw-coverage gate (1.7b).
        self._heading_samples: dict[int, deque] = {}
        self._anchor_node_id: Optional[int] = None
        self._preset = ENV_PRESETS[environment]
        self._flip_history: deque = deque(maxlen=10)  # rolling reflection-repair flags
        self._flip_unstable = False
        self._graph_rigid = True
        log.info("environment preset: %s (n=%.2f sigma=%.1f dB, %s)",
                 self._preset.name, self._preset.path_loss_n, self._preset.shadowing_sigma_db,
                 self._preset.confidence)

    def set_environment(self, name: str):
        """Select the path-loss preset at incident start (WILDLAND / STRUCTURAL
        / INDUSTRIAL). An ambiguous scene (a structure inside a wildland fire) is
        exactly when the topology view — not a mis-preset coordinate map — is the
        honest fallback."""
        self._preset = ENV_PRESETS[name]
        log.info("environment preset -> %s", name)

    @property
    def preset(self) -> EnvPreset:
        return self._preset

    def set_anchor(self, node_id: int):
        """Call once you know which node is TEAM_LEAD — usually the first
        TELEMETRY_BATCH sender you see, since only leads send batches. Resets the
        flip-guard state: chirality/rotation are tracked relative to a frame the
        old anchor defined."""
        if self._anchor_node_id == node_id:
            return
        self._anchor_node_id = node_id
        for n in self.nodes.values():
            n.is_anchor = (n.node_id == node_id)
        a = self._ensure_node(node_id)
        a.is_anchor = True
        # A promoted anchor DEFINES the origin — zero its stale non-anchor coords,
        # or snapshot() would draw the team lead (and thus the whole frame) offset
        # from (0,0) after a lead handoff (review finding).
        a.x_m = 0.0
        a.y_m = 0.0
        self._flip_history.clear()
        self._flip_unstable = False
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

        hdq = self._heading_samples.get(node_id)
        if hdq is None:
            hdq = self._heading_samples[node_id] = deque(maxlen=64)
        hdq.append((state.heading_rad, now))

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

    def _node_yaw_cov(self, node_id: int, now: float) -> float:
        """Heading circular-spread over the node's fresh window: 1 - |mean unit
        heading vector|. 0 = never turned (one orientation), ~1 = swept a wide
        arc. This is the real orientation-percentile validity gate (1.7b): a link
        with samples all at one heading is NOT a valid orientation percentile."""
        dq = self._heading_samples.get(node_id)
        if not dq:
            return 0.0
        obs = self.nodes.get(node_id)
        max_age = self._edge_max_age_s(obs.speed_mps if obs else 0.0)
        fresh = [h for (h, ts) in dq if now - ts <= max_age]
        if len(fresh) < 2:
            return 0.0
        cx = sum(math.cos(h) for h in fresh) / len(fresh)
        cy = sum(math.sin(h) for h in fresh) / len(fresh)
        return 1.0 - math.hypot(cx, cy)

    def _fresh_edges(self) -> list[EdgeObs]:
        """One EdgeObs per unordered node pair: the robust high-percentile of
        each direction's speed-fresh RSSI window, bidirectionally averaged, with
        a per-edge whitening sigma inflated for one-way and sample-starved links."""
        now = time.time()
        pair_dir: dict[tuple[int, int], list[tuple[float, int]]] = {}
        for (obs, nbr), dq in list(self._rssi_samples.items()):
            observer = self.nodes.get(obs)
            max_age = self._edge_max_age_s(observer.speed_mps if observer else 0.0)
            while dq and now - dq[0][1] > RSSI_WINDOW_MAX_S:
                dq.popleft()
            fresh = [r for (r, ts) in dq if now - ts <= max_age]
            if not fresh:
                continue
            key = (obs, nbr) if obs < nbr else (nbr, obs)
            pair_dir.setdefault(key, []).append((robust_rssi(fresh), len(fresh)))
        base_sigma = self._preset.shadowing_sigma_db
        edges = []
        for (a, b), dirs in pair_dir.items():
            avg_rssi = sum(r for r, _ in dirs) / len(dirs)
            n_samp_eff = min(n for _, n in dirs)      # weakest direction governs
            one_way = len(dirs) == 1
            sigma_eff = (base_sigma
                         * (ONEWAY_SIGMA_MULT if one_way else 1.0)
                         * math.sqrt(1.0 + K_SAMP / max(n_samp_eff, 1)))
            edges.append(EdgeObs(a, b, rssi_to_distance_m(avg_rssi, self._preset),
                                 sigma_eff, n_samp_eff, one_way))
        return edges

    def recompute_multilateration(self):
        """Weighted dB-space least-squares layout, flip-guarded and blended into
        the fused positions, with per-node confidence ellipse + coordinate/
        topology grade. No-op if no fresh edges or no anchor yet."""
        now = time.time()
        edges = self._fresh_edges()
        anchor = self._anchor_node_id
        if anchor is None:
            return
        if not edges:
            self._age_out({anchor}, now)   # a quiet mesh must still age responders out — not freeze grades
            return
        node_ids = sorted({n for e in edges for n in (e.a, e.b)} | {anchor})
        if len(node_ids) < 2:
            return
        free = [nid for nid in node_ids if nid != anchor]
        if not free:
            return
        fidx = {nid: i for i, nid in enumerate(free)}
        n_pl = self._preset.path_loss_n

        # x0 from current fused positions; nudge brand-new (origin) nodes off the
        # zero-radius singularity, deterministically by node_id (not random).
        x0 = np.zeros(2 * len(free))
        for nid, i in fidx.items():
            s = self.nodes.get(nid)
            if s and (s.x_m != 0.0 or s.y_m != 0.0):
                x0[2 * i], x0[2 * i + 1] = s.x_m, s.y_m
            else:
                ang = (nid % 360) * math.pi / 180.0
                r0 = D_FLOOR_M + 0.1
                x0[2 * i], x0[2 * i + 1] = r0 * math.cos(ang), r0 * math.sin(ang)

        def positions(v):
            pts = {anchor: (0.0, 0.0)}
            for nid, i in fidx.items():
                pts[nid] = (v[2 * i], v[2 * i + 1])
            return pts

        def residuals(v):
            pts = positions(v)
            out = []
            for e in edges:
                ax, ay = pts[e.a]
                bx, by = pts[e.b]
                d_model = math.hypot(ax - bx, ay - by)
                out.append((10.0 * n_pl / e.sigma_db_eff)
                           * math.log10(max(d_model, D_FLOOR_M) / max(e.dist_m, D_FLOOR_M)))
            return np.asarray(out)

        try:
            res = least_squares(residuals, x0, method="trf", loss="soft_l1",
                                f_scale=SOFT_L1_F_SCALE, max_nfev=SOLVE_MAX_NFEV)
        except Exception:
            log.exception("multilateration solve failed")
            return

        raw = {anchor: np.zeros(2)}
        for nid, i in fidx.items():
            raw[nid] = np.array([res.x[2 * i], res.x[2 * i + 1]], dtype=float)

        # flip-guard: rotate/reflect the raw solve onto the previous fused frame
        repaired = self._flip_align(raw, free)
        self._flip_history.append(bool(repaired))
        self._flip_unstable = sum(self._flip_history) >= FLIP_UNSTABLE_COUNT

        # complementary blend of the aligned solve into the fused positions
        w = FUSION_CORRECTION_WEIGHT
        for nid in free:
            s = self._ensure_node(nid)
            s.x_m = float((1 - w) * s.x_m + w * raw[nid][0])
            s.y_m = float((1 - w) * s.y_m + w * raw[nid][1])

        # global rigidity proxy: a 2D range-only graph with a pinned anchor needs
        # >= 2*|free| - 1 edges (Laman-ish necessary condition) or it has flex
        # modes beyond the single rotation gauge -> everyone topology this frame.
        self._graph_rigid = len(edges) >= (2 * len(free) - 1)

        self._grade_nodes(edges, res.fun, node_ids, anchor)
        self._age_out(set(node_ids), now)

    def _flip_align(self, raw: dict, free: list) -> bool:
        """Orthogonal-Procrustes align the raw non-anchor solve onto the current
        fused layout (anchor fixed at origin, so only rotation+reflection are
        fitted). Both a layout and its mirror satisfy range constraints equally,
        so the solver's arbitrary reflection/rotation choice flips the map frame
        to frame; applying the best orthogonal map (INCLUDING a reflection when
        that fits better) snaps the new solve onto the tracked frame BEFORE the
        blend, so the fixed weight never drags a node through the anchor.
        Returns True when a reflection was applied (chirality was flipped)."""
        prev, new = [], []
        for nid in free:
            s = self.nodes.get(nid)
            if s and (s.x_m != 0.0 or s.y_m != 0.0):
                prev.append([s.x_m, s.y_m])
                new.append(raw[nid])
        if not prev:
            return False  # first solve / nothing tracked — accept raw as-is
        P = np.asarray(prev, dtype=float)
        Nw = np.asarray(new, dtype=float)
        M = P.T @ Nw                      # 2x2 cross-covariance
        try:
            U, _, Vt = np.linalg.svd(M)
        except np.linalg.LinAlgError:
            return False
        R = U @ Vt                        # best orthogonal map: R @ new ~= prev (may be improper)
        # ALWAYS remove the arbitrary solver rotation (range-only + pinned anchor
        # is rotation-invariant, so the raw solve is at a random angle — blending
        # it un-rotated spins nodes and SHRINKS range-from-anchor, a false
        # "closer/safer" reading). Only the REFLECTION decision is gated: trust an
        # un-mirror only when the tracked cloud is genuinely 2D (its smaller
        # singular value is well above collinear); otherwise apply the best PROPER
        # rotation and leave chirality alone (review finding).
        repaired = False
        if np.linalg.det(R) < 0:
            prev_sv = (np.linalg.svd(P, compute_uv=False) if len(prev) >= 2
                       else np.array([0.0]))
            chirality_ok = len(prev_sv) >= 2 and prev_sv[-1] >= COLLINEAR_SV_M
            if chirality_ok:
                repaired = True                       # improper R un-mirrors the flipped solve
            else:
                R = U @ np.diag([1.0, -1.0]) @ Vt      # proper rotation only; don't force a handedness
        for nid in free:
            raw[nid] = R @ raw[nid]
        return repaired

    def _bearing_geometry(self, node: NodeFusionState, neighbor_ids: list) -> tuple[int, float]:
        """(n_indep, sin_sep) for the rigidity gate + confidence ellipse.
        n_indep: incident edges clustered by LINE DIRECTION (mod pi, so two
        anti-parallel edges — a node collinear between two neighbors — count as
        ONE independent direction, not two; that geometry leaves the cross-line
        position a pure gauge). sin_sep: the BEST pairwise bearing separation,
        max over pairs of sin(gap) — this is what actually pins bearing (a node
        with edges at 0/90/180 deg is well-constrained by the 0-90 pair even
        though the widest pair 0-180 is degenerate), and it sizes the tangential
        ellipse axis (~0 => bearing unknown => huge smear; ~1 => bearing as tight
        as range)."""
        bearings = []
        for nb in neighbor_ids:
            ns = self.nodes.get(nb)
            if ns is None:
                continue
            dx, dy = ns.x_m - node.x_m, ns.y_m - node.y_m
            if math.hypot(dx, dy) < 1e-6:
                continue
            bearings.append(math.atan2(dy, dx))
        if not bearings:
            return 0, 0.0
        # rigidity: cluster by line direction (mod pi) so anti-parallel edges merge
        thr = math.radians(BEARING_SPREAD_DEG)
        line_clusters: list[float] = []
        for b in sorted(x % math.pi for x in bearings):
            if all(min(abs(b - c), math.pi - abs(b - c)) > thr for c in line_clusters):
                line_clusters.append(b)
        # tangential dilution: best (not widest) pairwise angular separation
        sin_sep = 0.0
        for i in range(len(bearings)):
            for j in range(i + 1, len(bearings)):
                sin_sep = max(sin_sep, math.sin(_ang_gap(bearings[i], bearings[j])))
        return len(line_clusters), sin_sep

    def _grade_nodes(self, edges, resid_vec, node_ids, anchor):
        now = time.time()
        frac = self._preset.fractional_range_sigma()
        incident: dict[int, list[int]] = {}
        best_ns: dict[int, int] = {}
        max_resid: dict[int, float] = {}
        for k, e in enumerate(edges):
            incident.setdefault(e.a, []).append(e.b)
            incident.setdefault(e.b, []).append(e.a)
            best_ns[e.a] = max(best_ns.get(e.a, 0), e.n_samp)
            best_ns[e.b] = max(best_ns.get(e.b, 0), e.n_samp)
            r = abs(float(resid_vec[k])) if k < len(resid_vec) else 0.0
            max_resid[e.a] = max(max_resid.get(e.a, 0.0), r)
            max_resid[e.b] = max(max_resid.get(e.b, 0.0), r)

        for nid in node_ids:
            s = self._ensure_node(nid)
            if nid == anchor:
                s.grade = "anchor"
                s.ell_a = s.ell_b = s.ell_theta_deg = 0.0
                s.bearing_ambiguous = False
                s.ring_m = None
                s.range_m = s.range_sigma_m = 0.0
                s.n_edges = len(incident.get(nid, []))
                s.n_indep = 0
                s.best_n_samp = int(best_ns.get(nid, 0))
                s.yaw_cov = 0.0
                s.pos_confidence = 1.0
                continue

            nbrs = incident.get(nid, [])
            n_inc = len(nbrs)
            R = math.hypot(s.x_m, s.y_m)
            Rf = max(R, MIN_R_M)
            n_indep, sin_sep = self._bearing_geometry(s, nbrs)

            # closed-form polar covariance: radial tightens with edge count;
            # tangential (bearing) follows range-only GDOP — huge when the
            # incident edges give no angular separation (bearing is a gauge),
            # shrinking to ~radial when two edges are ~90 deg apart.
            sigma_r = frac * Rf / math.sqrt(max(1, n_inc))
            sigma_t = frac * Rf / max(sin_sep, SIN_SEP_FLOOR)
            phi = math.atan2(s.y_m, s.x_m)
            if sigma_t >= sigma_r:
                a, b, th = sigma_t, sigma_r, phi + math.pi / 2
            else:
                a, b, th = sigma_r, sigma_t, phi
            s.ell_a = float(a)
            s.ell_b = float(b)
            s.ell_theta_deg = float(math.degrees(th) % 180.0)
            s.bearing_ambiguous = bool((a / max(b, 1e-6)) >= AMBIG_RATIO)
            s.range_m = float(R)
            s.range_sigma_m = float(sigma_r)
            s.n_edges = int(n_inc)
            s.n_indep = int(n_indep)
            s.best_n_samp = int(best_ns.get(nid, 0))
            s.yaw_cov = float(self._node_yaw_cov(nid, now))

            age = now - s.last_telemetry_s
            grade = "coordinate"
            if age > STALE_AGE_S:
                grade = "stale"
            else:
                # Gate A — rigidity
                if n_indep < RIGIDITY_MIN_EDGES or a > RIGIDITY_MAJOR_M:
                    grade = "topology"
                # Gate B — sample sufficiency
                if s.best_n_samp < N_SAMP_MIN:
                    grade = "topology"
                # Gate C — yaw coverage (coverage, not count, is the real gate)
                if s.speed_mps > MOVING_THRESH_MPS:
                    grade = "topology"          # motion-smeared RSSI; PDR carries it
                elif (not s.stationary) and s.yaw_cov < YAW_COV_MIN:
                    grade = "topology"          # slow but never turned -> no orientation diversity
                # Fit sanity — grossly inconsistent edges
                if max_resid.get(nid, 0.0) > RESID_REJECT_SIGMA:
                    grade = "topology"
                # global fail-safes
                if self._flip_unstable or not self._graph_rigid:
                    grade = "topology"

            s.grade = grade
            s.ring_m = None if grade == "coordinate" else float(R)
            yaw_ok = 1.0 if s.stationary else min(1.0, s.yaw_cov / YAW_COV_MIN)
            conf = (min(1.0, n_indep / RIGIDITY_MIN_EDGES)
                    * min(1.0, s.best_n_samp / N_SAMP_MIN)
                    * yaw_ok
                    * min(1.0, sin_sep)          # same GDOP quantity as the ellipse — opacity agrees with size
                    * (1.0 if grade == "coordinate" else 0.3))
            s.pos_confidence = float(round(max(0.0, min(1.0, conf)), 2))

    def _age_out(self, graded_ids: set, now: float):
        """Safety sweep over ALL known nodes each cycle. The honesty gates in
        _grade_nodes only run for nodes in this frame's fresh edges, so a
        responder that goes off-air (no telemetry AND unobserved by any neighbor)
        would otherwise keep its last 'coordinate' grade + tight ellipse forever
        — a confident pinpoint for a possibly-downed responder (the exact failure
        the redesign forbids). Force such nodes to a safe grade every cycle."""
        for nid, s in self.nodes.items():
            if s.is_anchor or nid in graded_ids:
                continue
            age = now - s.last_telemetry_s
            if age > STALE_AGE_S:
                s.grade = "stale"
                s.ring_m = float(s.range_m) if s.range_m > 0 else None
                s.bearing_ambiguous = True
                # inflate the ellipse to a big smear so any consumer reading it
                # sees uncertainty, not the last live (tight) value
                s.ell_a = max(s.ell_a, float(s.range_m))
                s.ell_b = max(s.ell_b, float(s.range_m))
                s.pos_confidence = 0.0
            elif s.grade == "coordinate":
                # recent telemetry but no fresh RSSI edges this frame -> position
                # is not currently observable -> cannot be coordinate-grade
                s.grade = "topology"
                s.ring_m = float(s.range_m) if s.range_m > 0 else None
                s.pos_confidence = min(s.pos_confidence, 0.3)

    def environment_summary(self) -> dict:
        """Active preset + network-level honesty flags for the map (0.2)."""
        p = self._preset
        n_coord = sum(1 for s in self.nodes.values() if s.grade == "coordinate")
        n_topo = sum(1 for s in self.nodes.values() if s.grade in ("topology", "stale"))
        return {
            "environment": p.name,
            "path_loss_n": p.path_loss_n,
            "shadowing_sigma_db": p.shadowing_sigma_db,
            "fractional_range_sigma": round(p.fractional_range_sigma(), 3),
            "confidence": p.confidence,
            "note": p.note,
            "flip_unstable": bool(self._flip_unstable),   # whole map forced topology (chronic reflection ambiguity)
            "graph_rigid": bool(self._graph_rigid),        # false => under-constrained graph, all-topology this frame
            "n_coordinate_grade": int(n_coord),
            "n_topology_grade": int(n_topo),
        }

    def snapshot(self) -> list[dict]:
        now = time.time()
        out = []
        for s in self.nodes.values():
            out.append({
                "node_id": s.node_id,
                "x_m": round(s.x_m, 2),
                "y_m": round(s.y_m, 2),
                "par_status": s.par_status.name,
                "battery_pct": s.battery_pct,
                "imu_present": s.imu_present,
                "heading_deg": round(math.degrees(s.heading_rad), 0),
                "heading_conf": s.heading_conf,
                "is_anchor": s.is_anchor,
                "age_s": round(now - s.last_telemetry_s, 1),
                # --- 0.2 honesty fields ---
                "grade": s.grade,                          # anchor | coordinate | topology | stale
                "pos_confidence": s.pos_confidence,        # 0..1 for opacity/coloring
                "ellipse": {                               # 1-sigma position-uncertainty ellipse, same frame as x/y
                    "a_m": round(s.ell_a, 2),
                    "b_m": round(s.ell_b, 2),
                    "theta_deg": round(s.ell_theta_deg, 1),
                    "bearing_ambiguous": s.bearing_ambiguous,
                },
                "ring_m": (round(s.ring_m, 2) if s.ring_m is not None else None),
                "range_from_anchor_m": round(s.range_m, 2),
                "range_sigma_m": round(s.range_sigma_m, 2),
                "n_edges": s.n_edges,
                "n_indep_edges": s.n_indep,
                "best_n_samp": s.best_n_samp,
                "yaw_cov": round(s.yaw_cov, 2),
            })
        return out
