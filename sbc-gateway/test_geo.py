"""Tests for geo.py — georeferencing + the QGIS GeoJSON layers.

Run: python3 test_geo.py   (stdlib only, like test_protocol.py)

The cases guard the honesty invariants: topology/stale nodes must come out as
anchor-centred rings (never located dots/ellipses), unset origins must be
flagged, and the local->WGS84 transform must be metrically right (a wrong
transform silently teleports responders).
"""
from __future__ import annotations

import json
import math
import os
import tempfile

import geo
from geo import IncidentOrigin


def _snap(**over):
    """One fusion-snapshot node dict with sane defaults."""
    d = {
        "node_id": 3098, "x_m": 0.0, "y_m": 0.0, "par_status": "OK",
        "battery_pct": 100, "imu_present": True, "heading_deg": 0.0,
        "heading_conf": 255, "is_anchor": False, "age_s": 1.0,
        "grade": "coordinate", "pos_confidence": 0.9,
        "ellipse": {"a_m": 2.0, "b_m": 1.0, "theta_deg": 0.0, "bearing_ambiguous": False},
        "ring_m": None, "range_from_anchor_m": 5.0, "range_sigma_m": 2.0,
        "n_edges": 3, "n_indep_edges": 2, "best_n_samp": 10, "yaw_cov": 1.0,
    }
    d.update(over)
    return d


def test_transform_metric():
    """100 m north/east must be ~0.000898° lat / lon-scaled at the origin."""
    o = IncidentOrigin(lat=39.0, lon=-84.0, rotation_deg=0.0, origin_set=True)
    lon, lat = geo.local_to_wgs84(0.0, 100.0, o)         # +Y, rot 0 -> due north
    assert abs(lat - (39.0 + 100.0 / 111320.0)) < 1e-9, lat
    assert abs(lon - (-84.0)) < 1e-9, lon
    lon, lat = geo.local_to_wgs84(100.0, 0.0, o)         # +X -> due east
    assert abs(lat - 39.0) < 1e-9
    expect_dlon = 100.0 / (111320.0 * math.cos(math.radians(39.0)))
    assert abs((lon - (-84.0)) - expect_dlon) < 1e-9


def test_transform_rotation():
    """rotation_deg=90 points local +Y due east (bearing of +Y, CW from north)."""
    o = IncidentOrigin(lat=0.0, lon=0.0, rotation_deg=90.0, origin_set=True)
    lon, lat = geo.local_to_wgs84(0.0, 100.0, o)
    assert lon > 0 and abs(lat) < 1e-9, (lon, lat)       # east, no northing
    lon, lat = geo.local_to_wgs84(100.0, 0.0, o)
    assert lat < 0 and abs(lon) < 1e-9, (lon, lat)       # +X swings to south


def test_nodes_layer_props_and_flags():
    o = IncidentOrigin(lat=39.0, lon=-84.0, rotation_deg=0.0,
                       origin_set=True, rotation_surveyed=False)
    fc = geo.nodes_geojson([_snap(), _snap(node_id=27420, grade="topology", par_status="MAYDAY",
                                          x_m=3.0, y_m=4.0, ring_m=5.0)], o)
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 2
    f0, f1 = fc["features"]
    assert f0["geometry"]["type"] == "Point"
    lon, lat = f0["geometry"]["coordinates"]             # GeoJSON = [lon, lat]
    assert abs(lon - (-84.0)) < 1e-9 and abs(lat - 39.0) < 1e-9
    assert f0["properties"]["origin_set"] is True
    assert f0["properties"]["rotation_surveyed"] is False
    assert "TOPOLOGY" in f1["properties"]["label"]       # honesty suffix
    assert "TOPOLOGY" not in f0["properties"]["label"]   # coordinate nodes stay clean
    json.dumps(fc)                                        # serializable end-to-end


def test_ellipses_only_for_coordinate_grade():
    o = IncidentOrigin(lat=39.0, lon=-84.0, origin_set=True)
    snap = [
        _snap(node_id=1, grade="coordinate"),
        _snap(node_id=2, grade="topology", ring_m=8.0),
        _snap(node_id=3, grade="stale", ring_m=6.0),
        _snap(node_id=4, grade="coordinate", is_anchor=True),   # anchor: no ellipse
        _snap(node_id=5, grade="coordinate", ellipse={"a_m": 0.0, "b_m": 0.0, "theta_deg": 0}),
    ]
    fc = geo.ellipses_geojson(snap, o)
    ids = [f["properties"]["node_id"] for f in fc["features"]]
    assert ids == [1], ids                                # ONLY the located, non-anchor node
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1] and len(ring) >= 20        # closed polygon


def test_rings_center_on_anchor():
    o = IncidentOrigin(lat=39.0, lon=-84.0, origin_set=True)
    snap = [
        _snap(node_id=10, is_anchor=True, grade="anchor", x_m=0.0, y_m=0.0),
        _snap(node_id=11, grade="topology", x_m=99.0, y_m=99.0, ring_m=10.0),
    ]
    fc = geo.rings_geojson(snap, o)
    assert len(fc["features"]) == 1
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    lats = [p[1] for p in ring]
    lons = [p[0] for p in ring]
    # ring must be centred on the ANCHOR (39,-84), not the node's stale x/y
    assert abs((max(lats) + min(lats)) / 2 - 39.0) < 1e-6
    assert abs((max(lons) + min(lons)) / 2 - (-84.0)) < 1e-6
    # 10 m radius -> ~0.0000898 deg of latitude half-height
    assert abs((max(lats) - min(lats)) / 2 - 10.0 / 111320.0) < 1e-7


def test_ellipse_rotates_with_frame():
    """A long-in-+X ellipse under rotation_deg=90 must become long in the
    north-south... no: +X (major axis) swings to SOUTH -> long in latitude."""
    snap = [_snap(node_id=1, grade="coordinate",
                  ellipse={"a_m": 10.0, "b_m": 1.0, "theta_deg": 0.0, "bearing_ambiguous": False})]
    fc0 = geo.ellipses_geojson(snap, IncidentOrigin(lat=0, lon=0, rotation_deg=0.0, origin_set=True))
    fc90 = geo.ellipses_geojson(snap, IncidentOrigin(lat=0, lon=0, rotation_deg=90.0, origin_set=True))
    def spans(fc):
        ring = fc["features"][0]["geometry"]["coordinates"][0]
        return (max(p[0] for p in ring) - min(p[0] for p in ring),
                max(p[1] for p in ring) - min(p[1] for p in ring))
    w0, h0 = spans(fc0)
    w90, h90 = spans(fc90)
    assert w0 > h0 * 5, (w0, h0)     # rot 0: major axis along +X = east-west
    assert h90 > w90 * 5, (w90, h90)  # rot 90: +X now points south = north-south


def test_origin_set_validation_and_persistence():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "origin.json")
        for bad in ((91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (float("nan"), 0.0)):
            try:
                geo.set_origin(bad[0], bad[1], path=path)
                assert False, f"accepted bad origin {bad}"
            except ValueError:
                pass
        got = geo.set_origin(39.077, -84.1769, rotation_deg=370.0, path=path)
        assert got.origin_set and abs(got.rotation_deg - 10.0) < 1e-9  # wraps mod 360
        loaded = geo.load_origin(path)
        assert loaded.origin_set and abs(loaded.lat - 39.077) < 1e-12
        assert loaded.rotation_surveyed is False


def test_unset_origin_flagged():
    fc = geo.nodes_geojson([_snap()], IncidentOrigin())   # defaults: origin_set=False
    assert fc["features"][0]["properties"]["origin_set"] is False
    assert "NO-ORIGIN" in fc["features"][0]["properties"]["label"]


def test_label_honesty_suffixes():
    surveyed = IncidentOrigin(lat=39, lon=-84, origin_set=True, rotation_surveyed=True)
    unsurveyed = IncidentOrigin(lat=39, lon=-84, origin_set=True, rotation_surveyed=False)
    lbl_ok = geo.nodes_geojson([_snap()], surveyed)["features"][0]["properties"]["label"]
    lbl_rot = geo.nodes_geojson([_snap()], unsurveyed)["features"][0]["properties"]["label"]
    assert "ROT?" not in lbl_ok and "NO-ORIGIN" not in lbl_ok
    assert "ROT?" in lbl_rot and "NO-ORIGIN" not in lbl_rot


def test_status_banner_levels():
    """QGIS banner must mirror map.html's renderBanner exactly, plus the
    geo-placement honesty that only exists on the QGIS side."""
    o = IncidentOrigin(lat=39, lon=-84, origin_set=True, rotation_surveyed=True)
    env_ok = {"flip_unstable": False, "graph_rigid": True, "n_coordinate_grade": 2}
    snap = [_snap(is_anchor=True, grade="anchor"), _snap(node_id=2)]

    fc = geo.status_geojson(snap, env_ok, o)
    p = fc["features"][0]["properties"]
    assert p["banner"] == "" and p["banner_level"] == ""    # healthy => silent

    p = geo.status_geojson(snap, {**env_ok, "flip_unstable": True}, o)["features"][0]["properties"]
    assert p["banner_level"] == "crit" and "reflection-unstable" in p["banner"]

    p = geo.status_geojson(snap, {**env_ok, "graph_rigid": False}, o)["features"][0]["properties"]
    assert p["banner_level"] == "warn" and "under-constrained" in p["banner"]

    p = geo.status_geojson(snap, {**env_ok, "n_coordinate_grade": 0}, o)["features"][0]["properties"]
    assert p["banner_level"] == "warn" and "PROXIMITY ONLY" in p["banner"]

    # geo-placement honesty: unsurveyed rotation / unset origin always warn
    unsurv = IncidentOrigin(lat=39, lon=-84, origin_set=True, rotation_surveyed=False)
    p = geo.status_geojson(snap, env_ok, unsurv)["features"][0]["properties"]
    assert p["banner_level"] == "warn" and "ROTATION UNSURVEYED" in p["banner"]
    p = geo.status_geojson(snap, env_ok, IncidentOrigin())["features"][0]["properties"]
    assert "ORIGIN NOT SET" in p["banner"]
    # a degraded net + unsurveyed rotation shows BOTH, crit wins
    p = geo.status_geojson(snap, {**env_ok, "flip_unstable": True}, unsurv)["features"][0]["properties"]
    assert p["banner_level"] == "crit" and "|" in p["banner"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} geo tests passed")
