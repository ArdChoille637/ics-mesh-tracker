"""0.2 fusion-redesign tests: dB-space solve + confidence ellipses + topology
gating + flip guard + lifecycle safety.

    python test_fusion.py     (needs numpy+scipy, i.e. requirements.txt)

The honesty invariant these guard: the map must NEVER render a possibly-downed
responder as more precisely located than the data supports. Several cases here
are direct regressions for bugs an adversarial review caught (research-log.md
Pass 12) — especially the off-air node that used to linger as a confident dot.
"""
from __future__ import annotations

import json
import math
import time

from fusion import ENV_PRESETS, NetworkFusion, STALE_AGE_S, AMBIG_RATIO
from protocol import RssiSample, TelemetryPayload

WL = ENV_PRESETS["WILDLAND"]


def rssi_for(d: float) -> int:
    return round(WL.tx_power_1m_dbm - 10 * WL.path_loss_n * math.log10(max(d, 0.5)))


def telem(rssi_list, stationary=True, steps=0, stride=0):
    flags = 0x01 | (0x08 if stationary else 0)
    return TelemetryPayload(rssi=rssi_list, step_count=steps, stride_mm=stride,
                            heading_mrad=0, heading_conf=200, flags=flags)


def feed(f, node, neighbors_d, **kw):
    rl = [RssiSample(nb, rssi_for(d), 0) for nb, d in neighbors_d.items()]
    f.ingest_telemetry(node, telem(rl, **kw))


TRUTH = {1: (0, 0), 2: (6, 0), 3: (0, 6), 4: (5, 5)}


def _d(i, j):
    return math.hypot(TRUTH[i][0] - TRUTH[j][0], TRUTH[i][1] - TRUTH[j][1])


def build_rigid_k4() -> NetworkFusion:
    """A fully-connected, stationary, well-sampled 4-node graph — the case that
    SHOULD produce coordinate-grade positions."""
    f = NetworkFusion()
    f.set_anchor(1)
    for _ in range(8):
        for i in TRUTH:
            feed(f, i, {j: _d(i, j) for j in TRUTH if j != i})
        time.sleep(0.004)
    for _ in range(25):
        f.recompute_multilateration()
    return f


def test_rigid_graph_recovers_distances_and_grades_coordinate():
    f = build_rigid_k4()
    rd = lambda i, j: math.hypot(f.nodes[i].x_m - f.nodes[j].x_m, f.nodes[i].y_m - f.nodes[j].y_m)
    errs = [abs(rd(i, j) - _d(i, j)) for i in TRUTH for j in TRUTH if j > i]
    assert max(errs) < 1.5, f"inter-node distance error {max(errs):.2f} m too high"
    coord = sum(1 for i in TRUTH if f.nodes[i].grade == "coordinate")
    assert coord >= 3, {i: f.nodes[i].grade for i in TRUTH}


def test_single_edge_is_tangential_smear_topology_but_range_known():
    # anchor + one node, one edge: range is known, bearing is a pure gauge.
    f = NetworkFusion()
    f.set_anchor(1)
    for _ in range(6):
        feed(f, 1, {2: 5.0})
        feed(f, 2, {1: 5.0})
        time.sleep(0.004)
    for _ in range(12):
        f.recompute_multilateration()
    n2 = f.nodes[2]
    assert n2.grade == "topology", n2.grade
    assert n2.ell_a / max(n2.ell_b, 1e-6) >= AMBIG_RATIO and n2.bearing_ambiguous
    assert abs(n2.range_m - 5.0) < 1.5, n2.range_m       # range stays trustworthy
    assert n2.ring_m is not None


def test_moving_node_grades_topology():
    f = NetworkFusion()
    f.set_anchor(1)
    for _ in range(8):
        feed(f, 1, {2: 6, 3: 6, 4: 7}); feed(f, 3, {1: 6, 2: 6, 4: 5}); feed(f, 4, {1: 7, 2: 5, 3: 5})
        feed(f, 2, {1: 6, 3: 6, 4: 5}, stationary=False, steps=4, stride=800)  # brisk
        time.sleep(0.02)
    for _ in range(20):
        f.recompute_multilateration()
    assert f.nodes[2].grade == "topology"


def test_offair_node_goes_stale_not_confident_coordinate():
    # THE critical regression: a node that was coordinate-grade then loses all
    # RF (no telemetry AND unobserved) must not linger as a confident dot.
    f = build_rigid_k4()
    assert f.nodes[4].grade == "coordinate"
    for k in list(f._rssi_samples):
        if 4 in k:
            del f._rssi_samples[k]
    f.nodes[4].last_telemetry_s -= (STALE_AGE_S + 5)
    for _ in range(3):
        for i in (1, 2, 3):
            feed(f, i, {j: _d(i, j) for j in (1, 2, 3) if j != i})
        f.recompute_multilateration()
    assert f.nodes[4].grade == "stale", f.nodes[4].grade
    assert f.nodes[4].pos_confidence == 0.0


def test_alive_but_unobserved_node_is_topology_not_coordinate():
    f = build_rigid_k4()
    for k in list(f._rssi_samples):
        if 4 in k:
            del f._rssi_samples[k]
    for _ in range(3):
        for i in (1, 2, 3):
            feed(f, i, {j: _d(i, j) for j in (1, 2, 3) if j != i})
        feed(f, 4, {})               # node 4 alive, no neighbors hear it
        f.recompute_multilateration()
    assert f.nodes[4].grade in ("topology", "stale"), f.nodes[4].grade


def test_reanchor_zeroes_new_anchor():
    f = build_rigid_k4()
    assert (f.nodes[4].x_m, f.nodes[4].y_m) != (0.0, 0.0)
    f.set_anchor(4)
    assert f.nodes[4].x_m == 0.0 and f.nodes[4].y_m == 0.0 and f.nodes[4].is_anchor


def test_collinear_node_is_topology_not_confident_along_line():
    # anchor(1)-node2-node3 all on the x-axis: node2's cross-line position is a
    # gauge, so it must NOT grade coordinate despite two incident edges.
    lin = {1: (0, 0), 2: (3, 0), 3: (6, 0)}
    ld = lambda i, j: abs(lin[i][0] - lin[j][0])
    f = NetworkFusion()
    f.set_anchor(1)
    for _ in range(8):
        for i in lin:
            feed(f, i, {j: ld(i, j) for j in lin if j != i})
        time.sleep(0.004)
    for _ in range(20):
        f.recompute_multilateration()
    assert f.nodes[2].grade == "topology", f.nodes[2].grade
    assert f.nodes[2].n_indep == 1                      # anti-parallel edges merged (mod-pi)


def test_flip_align_preserves_range_with_collinear_reference_pair():
    # regression: the old area guard used two arbitrary nodes; a collinear pair
    # made it skip alignment, blending an arbitrary rotation and shrinking range.
    import numpy as np
    f = NetworkFusion()
    f.set_anchor(1)
    for nid, (x, y) in {2: (10, 0), 3: (-5, 0), 4: (0, 8), 5: (3, 7)}.items():
        s = f._ensure_node(nid)
        s.x_m, s.y_m = float(x), float(y)
    rot = np.array([[0, -1], [1, 0]])                   # 90 deg gauge rotation
    raw = {1: np.zeros(2)}
    for nid in (2, 3, 4, 5):
        raw[nid] = rot @ np.array([f.nodes[nid].x_m, f.nodes[nid].y_m])
    f._flip_align(raw, [2, 3, 4, 5])
    for nid in (2, 3, 4, 5):
        prev_r = math.hypot(f.nodes[nid].x_m, f.nodes[nid].y_m)
        assert abs(math.hypot(*raw[nid]) - prev_r) < 0.05, nid   # range not shrunk


def test_snapshot_and_summary_json_serializable():
    f = build_rigid_k4()
    blob = json.dumps({"nodes": f.snapshot(), "env": f.environment_summary()})
    for key in ("grade", "ellipse", "pos_confidence", "flip_unstable", "n_coordinate_grade"):
        assert key in blob, key


if __name__ == "__main__":
    import sys

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
