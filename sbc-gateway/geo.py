"""Georeferencing + GeoJSON for the QGIS command map.

The fusion frame is RELATIVE: meters, anchor at the origin, and — because the
nodes are magnetometer-free (research item 1.4) — an ARBITRARY rotation with
respect to true north. To put responders on a real basemap, the operator sets an
*incident origin* at incident start: the anchor node's real-world lat/lon plus
the bearing of the local +Y axis (degrees clockwise from true north). Until the
rotation is surveyed (walk a known line, or eyeball a road/feature), treat the
cloud's orientation as arbitrary — every feature carries `origin_set` and
`rotation_surveyed` flags so the map can say so honestly.

Pure stdlib on purpose: the GeoJSON builders take a fusion snapshot (list of
dicts) and return dicts, so they unit-test with plain python3 and stay
importable from QGIS's bundled interpreter.
"""
from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import asdict, dataclass

# Meters per degree of latitude (WGS84 mean). At strike-team scales (tens to a
# few hundred meters) the spherical-earth error is far below the RSSI noise
# floor, so the simple equirectangular offset is the honest tool here.
_M_PER_DEG_LAT = 111_320.0

ORIGIN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "origin.json")


@dataclass
class IncidentOrigin:
    lat: float = 0.0
    lon: float = 0.0
    rotation_deg: float = 0.0     # bearing of the local +Y axis, CW from true north
    origin_set: bool = False      # operator has placed the anchor on the map
    rotation_surveyed: bool = False  # operator has actually surveyed the rotation


_origin = IncidentOrigin()
_origin_lock = threading.Lock()


def _validate(lat: float, lon: float, rotation_deg: float) -> None:
    """Shared range/finiteness gate for BOTH set_origin and load_origin — a
    parsable-but-bad origin.json (NaN, lat=100) must not teleport the team or
    leak NaN into the GeoJSON any more than a bad POST may."""
    if not (math.isfinite(lat) and -90.0 <= lat <= 90.0):
        raise ValueError(f"lat out of range: {lat!r}")
    if not (math.isfinite(lon) and -180.0 <= lon <= 180.0):
        raise ValueError(f"lon out of range: {lon!r}")
    if not math.isfinite(rotation_deg):
        raise ValueError(f"rotation_deg not finite: {rotation_deg!r}")


def load_origin(path: str = ORIGIN_PATH) -> IncidentOrigin:
    """Load the persisted origin (survives server restarts). A missing, corrupt,
    or out-of-range file just means 'not set yet'."""
    global _origin
    try:
        with open(path) as f:
            d = json.load(f)
        lat, lon = float(d["lat"]), float(d["lon"])
        rot = float(d.get("rotation_deg", 0.0))
        _validate(lat, lon, rot)
        with _origin_lock:
            _origin = IncidentOrigin(
                lat=lat, lon=lon, rotation_deg=rot,
                origin_set=bool(d.get("origin_set", True)),
                rotation_surveyed=bool(d.get("rotation_surveyed", False)),
            )
    except Exception:
        pass
    return get_origin()


def get_origin() -> IncidentOrigin:
    with _origin_lock:
        return IncidentOrigin(**asdict(_origin))


def set_origin(lat: float, lon: float, rotation_deg: float = 0.0,
               rotation_surveyed: bool = False, path: str = ORIGIN_PATH) -> IncidentOrigin:
    """Set + persist the incident origin. Values must be finite and in range —
    a bad origin silently teleports the whole team, so reject loudly.

    The lock is held across the write+rename too: concurrent POSTs would
    otherwise interleave on the shared .tmp file (torn origin.json) or persist
    an older origin than the one memory serves."""
    _validate(lat, lon, rotation_deg)
    global _origin
    with _origin_lock:
        _origin = IncidentOrigin(lat=lat, lon=lon, rotation_deg=rotation_deg % 360.0,
                                 origin_set=True, rotation_surveyed=rotation_surveyed)
        snap = asdict(_origin)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f, indent=2)
        os.replace(tmp, path)
    return get_origin()


def local_to_wgs84(x_m: float, y_m: float, origin: IncidentOrigin) -> tuple[float, float]:
    """Local fusion meters -> (lon, lat). rotation_deg is the true-north bearing
    of the local +Y axis; rotation 0 means +Y=north, +X=east.

    Known limitation: an origin within ~1 km of the antimeridian can yield
    longitudes just past +/-180 (continuous-longitude convention). QGIS/OGR
    render that correctly; strict RFC 7946 validators would not. Deliberately
    NOT normalized — wrapping per-vertex would tear rings that straddle the
    line into world-spanning polygons, which is far worse."""
    th = math.radians(origin.rotation_deg)
    east = x_m * math.cos(th) + y_m * math.sin(th)
    north = -x_m * math.sin(th) + y_m * math.cos(th)
    lat = origin.lat + north / _M_PER_DEG_LAT
    coslat = math.cos(math.radians(origin.lat))
    # Guard the polar singularity — nobody is running a strike team ON the pole,
    # but a typo'd origin shouldn't divide by zero.
    lon = origin.lon + east / (_M_PER_DEG_LAT * max(coslat, 1e-6))
    return (lon, lat)


def _feature(geometry: dict, props: dict) -> dict:
    return {"type": "Feature", "geometry": geometry, "properties": props}


def _collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
            "features": features}


def _honesty_props(origin: IncidentOrigin) -> dict:
    return {"origin_set": origin.origin_set, "rotation_surveyed": origin.rotation_surveyed}


def _ring_vertices_local(cx: float, cy: float, radius_m: float, n: int = 48) -> list[tuple[float, float]]:
    pts = [(cx + radius_m * math.cos(2 * math.pi * i / n),
            cy + radius_m * math.sin(2 * math.pi * i / n)) for i in range(n)]
    pts.append(pts[0])  # close the ring
    return pts


def _ellipse_vertices_local(cx: float, cy: float, a_m: float, b_m: float,
                            theta_deg: float, n: int = 48) -> list[tuple[float, float]]:
    """1-sigma ellipse in the LOCAL frame; theta_deg = major-axis angle from the
    local +X axis (fusion's convention). Vertices are then geo-transformed by the
    caller, which folds in the incident rotation automatically."""
    th = math.radians(theta_deg)
    ct, st = math.cos(th), math.sin(th)
    pts = []
    for i in range(n):
        u = 2 * math.pi * i / n
        ex, ey = a_m * math.cos(u), b_m * math.sin(u)
        pts.append((cx + ex * ct - ey * st, cy + ex * st + ey * ct))
    pts.append(pts[0])
    return pts


def _polygon_geometry(local_vertices: list[tuple[float, float]], origin: IncidentOrigin) -> dict:
    return {"type": "Polygon",
            "coordinates": [[list(local_to_wgs84(x, y, origin)) for x, y in local_vertices]]}


def _anchor_xy(snapshot: list[dict]) -> tuple[float, float]:
    for n in snapshot:
        if n.get("is_anchor"):
            return (float(n["x_m"]), float(n["y_m"]))
    return (0.0, 0.0)


def nodes_geojson(snapshot: list[dict], origin: IncidentOrigin | None = None) -> dict:
    """One Point per node, all honesty fields flattened for QGIS styling."""
    origin = origin or get_origin()
    feats = []
    for n in snapshot:
        lon, lat = local_to_wgs84(float(n["x_m"]), float(n["y_m"]), origin)
        ell = n.get("ellipse") or {}
        props = {
            "node_id": n["node_id"],
            "grade": n.get("grade", "topology"),
            "par_status": n.get("par_status", "OK"),
            "is_anchor": bool(n.get("is_anchor")),
            "age_s": n.get("age_s"),
            "battery_pct": n.get("battery_pct"),
            "imu_present": n.get("imu_present"),
            "heading_deg_local": n.get("heading_deg"),
            "heading_conf": n.get("heading_conf"),
            "pos_confidence": n.get("pos_confidence"),
            "range_from_anchor_m": n.get("range_from_anchor_m"),
            "range_sigma_m": n.get("range_sigma_m"),
            "ell_a_m": ell.get("a_m"),
            "ell_b_m": ell.get("b_m"),
            "bearing_ambiguous": ell.get("bearing_ambiguous"),
            # The label carries the honesty cues the marker alone can't:
            # grade suffix for unlocated nodes, ROT?/NO-ORIGIN when the geo
            # placement itself is unsurveyed/unset.
            "label": f'{n["node_id"]} {n.get("par_status", "OK")}'
                     + ("" if n.get("grade") == "coordinate" or n.get("is_anchor") else f' [{n.get("grade", "?").upper()}]')
                     + ("" if origin.origin_set else " NO-ORIGIN")
                     + ("" if origin.rotation_surveyed else " ROT?"),
            **_honesty_props(origin),
        }
        feats.append(_feature({"type": "Point", "coordinates": [lon, lat]}, props))
    return _collection(feats)


def ellipses_geojson(snapshot: list[dict], origin: IncidentOrigin | None = None) -> dict:
    """1-sigma uncertainty ellipse polygons — coordinate-grade nodes only (the
    anchor is the frame origin and topology/stale nodes get rings instead;
    drawing an ellipse for those would fabricate a bearing)."""
    origin = origin or get_origin()
    feats = []
    for n in snapshot:
        if n.get("grade") != "coordinate" or n.get("is_anchor"):
            continue
        ell = n.get("ellipse") or {}
        a, b = float(ell.get("a_m") or 0.0), float(ell.get("b_m") or 0.0)
        if a <= 0.0 or b <= 0.0:
            continue
        verts = _ellipse_vertices_local(float(n["x_m"]), float(n["y_m"]),
                                        a, b, float(ell.get("theta_deg") or 0.0))
        props = {"node_id": n["node_id"], "ell_a_m": round(a, 2), "ell_b_m": round(b, 2),
                 "bearing_ambiguous": ell.get("bearing_ambiguous"), **_honesty_props(origin)}
        feats.append(_feature(_polygon_geometry(verts, origin), props))
    return _collection(feats)


def rings_geojson(snapshot: list[dict], origin: IncidentOrigin | None = None) -> dict:
    """Proximity rings for topology/stale nodes: range from the ANCHOR is known,
    bearing is not, so the honest rendering is a ring around the anchor — never
    a located dot. Matches static/map.html's semantics exactly."""
    origin = origin or get_origin()
    ax, ay = _anchor_xy(snapshot)
    feats = []
    for n in snapshot:
        if n.get("grade") not in ("topology", "stale"):
            continue
        ring = n.get("ring_m")
        if ring is None or float(ring) <= 0.5:
            continue
        verts = _ring_vertices_local(ax, ay, float(ring))
        props = {"node_id": n["node_id"], "grade": n["grade"],
                 "par_status": n.get("par_status", "OK"),
                 "ring_m": round(float(ring), 2), "age_s": n.get("age_s"),
                 **_honesty_props(origin)}
        feats.append(_feature(_polygon_geometry(verts, origin), props))
    return _collection(feats)


def status_geojson(snapshot: list[dict], env: dict,
                   origin: IncidentOrigin | None = None) -> dict:
    """One Point at the anchor carrying the NETWORK-level honesty state — the
    QGIS equivalent of map.html's banner (renderBanner: same 3-way logic, same
    wording). banner_level: 'crit' | 'warn' | '' (healthy => empty banner)."""
    origin = origin or get_origin()
    if env.get("flip_unstable"):
        level, banner = "crit", "TOPOLOGY MODE — layout reflection-unstable; positions shown as proximity only"
    elif env.get("graph_rigid") is False:
        level, banner = "warn", "TOPOLOGY MODE — mesh under-constrained; not enough links to fix positions"
    elif (env.get("n_coordinate_grade") or 0) == 0 and len(snapshot) > 1:
        level, banner = "warn", "PROXIMITY ONLY — no responder is well enough constrained to locate yet"
    else:
        level, banner = "", ""
    if not origin.origin_set:
        level = level or "warn"
        banner = (banner + "  |  " if banner else "") + "INCIDENT ORIGIN NOT SET — positions not georeferenced"
    elif not origin.rotation_surveyed:
        level = level or "warn"
        banner = (banner + "  |  " if banner else "") + "ROTATION UNSURVEYED — cloud orientation vs north is arbitrary"
    ax, ay = _anchor_xy(snapshot)
    lon, lat = local_to_wgs84(ax, ay, origin)
    props = {"banner": banner, "banner_level": level, **env, **_honesty_props(origin)}
    return _collection([_feature({"type": "Point", "coordinates": [lon, lat]}, props)])
