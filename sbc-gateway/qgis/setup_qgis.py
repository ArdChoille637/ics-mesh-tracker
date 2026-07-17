"""ICS Mesh Tracker — QGIS command-map builder.

Builds a QGIS project that renders the live responder picture straight from the
SBC server's GeoJSON endpoints (no custom map app): node points styled by PAR
status + grade, 1-sigma confidence ellipses, and anchor-centred proximity rings
for topology/stale nodes — the same honesty semantics as static/map.html. All
three layers auto-refresh every 2 s. Saves  qgis/ics-mesh.qgz  for reuse.

Run it either way (both use QGIS's own Python — no env setup needed), with the
SBC server already running (python server.py --serial-port ...):
  • Launch:  /Applications/QGIS-final-4_0_3.app/Contents/MacOS/QGIS-final-4_0_3 \
               --nologo --code ~/Claude/ics-mesh-tracker/sbc-gateway/qgis/setup_qgis.py
  • Or in QGIS:  Plugins ▸ Python Console ▸ Show Editor ▸ open this file ▸ Run (▶)

Set the incident origin first (anchor's real lat/lon + frame rotation), or the
team renders at origin_set=false:
  curl -X POST http://127.0.0.1:8000/api/origin \
       -H 'Content-Type: application/json' \
       -d '{"lat": 39.077, "lon": -84.1769, "rotation_deg": 0}'

HONESTY NOTE (same as the web map): positions are RELATIVE RSSI+PDR estimates.
A dashed ring means "this responder is ~R meters from the team lead, bearing
unknown" — never trust a topology/stale node as a located dot. Until
rotation_surveyed=true the whole cloud's orientation vs true north is arbitrary.
"""
import json
import os
import urllib.request

from qgis.core import (
    Qgis, QgsProject, QgsVectorLayer, QgsRasterLayer, QgsCoordinateReferenceSystem,
    QgsRectangle, QgsReferencedRectangle,
    QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsSingleSymbolRenderer,
    QgsMarkerSymbol, QgsFillSymbol,
    QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsTextFormat,
)
from qgis.PyQt.QtGui import QColor

SERVER = os.environ.get("ICS_SBC_URL", "http://127.0.0.1:8000")
# NOTE: no __file__ here — QGIS's --code path exec()s the script without
# defining it (same reason the awareness-engine builder hardcodes its path).
OUT_DIR = os.path.expanduser("~/Claude/ics-mesh-tracker/sbc-gateway/qgis")
OUT = os.path.join(OUT_DIR, "ics-mesh.qgz")
RESULT_FILE = "/tmp/qgis_ics_setup_result.txt"
REFRESH_MS = 2000
WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

PAR_COLORS = [  # value, colour, marker size — MAYDAY loudest
    ("MAYDAY", "#c0392b", 6.0),
    ("EMERGENCY", "#e67e22", 5.2),
    ("OK", "#27ae60", 4.0),
]


def _get_origin():
    try:
        with urllib.request.urlopen(f"{SERVER}/api/origin", timeout=4) as r:
            return json.load(r)
    except Exception:
        return None


def _autorefresh(layer, ms=REFRESH_MS):
    # MUST be ReloadData: the OGR GeoJSON driver parses the document once at
    # layer-open, and the (deprecated) setAutoRefreshEnabled path is RedrawOnly
    # — it repaints CACHED features forever, silently freezing the "live" map
    # at its first snapshot. ReloadData re-fetches the URL each tick (verified
    # against this QGIS install).
    layer.setAutoRefreshInterval(ms)
    layer.setAutoRefreshMode(Qgis.AutoRefreshMode.ReloadData)


def _label(layer, expression, size=9.0, color="#111111"):
    s = QgsPalLayerSettings()
    s.fieldName = expression
    s.isExpression = True
    fmt = QgsTextFormat()
    fmt.setSize(size)
    fmt.setColor(QColor(color))
    s.setFormat(fmt)
    layer.setLabelsEnabled(True)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(s))


# Classification: PAR status x located-ness. A topology/stale node must NEVER
# render as a solid confident dot — its bearing is unknown (only the anchor-
# centred ring is honest), so unlocated nodes get a HOLLOW marker, mirroring
# static/map.html. The anchor gets a diamond.
NODE_CLASS_EXPR = (
    "concat(\"par_status\", '|', "
    "if(\"is_anchor\", 'anc', if(\"grade\" IN ('coordinate','anchor'), 'loc', 'unloc')))"
)


def style_nodes(layer):
    cats = []
    for val, color, size in PAR_COLORS:
        solid = QgsMarkerSymbol.createSimple({
            "name": "circle", "color": color,
            "outline_color": "black", "outline_width": "0.3", "size": str(size)})
        hollow = QgsMarkerSymbol.createSimple({
            "name": "circle", "color": "0,0,0,0",
            "outline_color": color, "outline_width": "0.8",
            "outline_style": "dash", "size": str(size)})
        anchor = QgsMarkerSymbol.createSimple({
            "name": "diamond", "color": color,
            "outline_color": "black", "outline_width": "0.3", "size": str(size + 1.0)})
        cats.append(QgsRendererCategory(f"{val}|loc", solid, f"{val} (located)"))
        cats.append(QgsRendererCategory(f"{val}|unloc", hollow, f"{val} (NOT located)"))
        cats.append(QgsRendererCategory(f"{val}|anc", anchor, f"{val} (anchor)"))
    other = QgsMarkerSymbol.createSimple({
        "name": "circle", "color": "0,0,0,0",
        "outline_color": "#7f8c8d", "outline_width": "0.8",
        "outline_style": "dash", "size": "3.6"})
    cats.append(QgsRendererCategory(None, other, "other"))
    layer.setRenderer(QgsCategorizedSymbolRenderer(NODE_CLASS_EXPR, cats))
    _label(layer, '"label"')
    layer.triggerRepaint()


def style_status(layer):
    """Invisible marker; the BANNER text is the whole point (map.html parity:
    TOPOLOGY MODE / PROXIMITY ONLY / origin honesty, empty when healthy)."""
    sym = QgsMarkerSymbol.createSimple({"name": "circle", "color": "0,0,0,0",
                                        "outline_color": "0,0,0,0", "size": "0.1"})
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    _label(layer, '"banner"', size=13.0, color="#cc2222")
    layer.triggerRepaint()


# NB: every styler must layer.setRenderer(...) rather than touch
# layer.renderer() — a GeoJSON layer opened while the endpoint is EMPTY (fresh
# incident, mesh not up yet) has no geometry type and therefore NO default
# renderer; renderer() is None and .setSymbol() explodes.

def style_ellipses(layer):
    sym = QgsFillSymbol.createSimple({
        "color": "39,174,96,60",            # translucent green fill
        "outline_color": "#27ae60", "outline_width": "0.4"})
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    layer.triggerRepaint()


def style_rings(layer):
    sym = QgsFillSymbol.createSimple({
        "color": "0,0,0,0",                  # hollow
        "outline_color": "#ca8622", "outline_width": "0.7",
        "outline_style": "dash"})
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    _label(layer, "concat(\"node_id\", ' ~', \"ring_m\", 'm (', \"grade\", ')')", size=8.0, color="#8a5a12")
    layer.triggerRepaint()


def main():
    log = []
    proj = QgsProject.instance()
    proj.clear()
    proj.setCrs(WGS84)

    osm = QgsRasterLayer(
        "type=xyz&url=https://tile.openstreetmap.org/%7Bz%7D/%7Bx%7D/%7By%7D.png&zmax=19&zmin=0",
        "OpenStreetMap", "wms")
    if osm.isValid():
        proj.addMapLayer(osm)
        log.append("+ OpenStreetMap basemap")
    else:
        log.append("! FAILED: OpenStreetMap basemap")

    # bottom -> top: rings under ellipses under node dots under the banner.
    # |geometrytype= pins the layer type so an EMPTY endpoint (fresh incident,
    # mesh not up yet) still builds a typed, renderable layer that fills in as
    # ReloadData ticks bring features.
    specs = [
        (f"{SERVER}/qgis/rings.geojson", "Polygon", "Proximity rings (bearing unknown)", style_rings),
        (f"{SERVER}/qgis/ellipses.geojson", "Polygon", "1σ confidence ellipses", style_ellipses),
        (f"{SERVER}/qgis/nodes.geojson", "Point", "Responders (PAR status)", style_nodes),
        (f"{SERVER}/qgis/status.geojson", "Point", "STATUS (honesty banner)", style_status),
    ]
    n_ok = 0
    for url, gtype, name, styler in specs:
        lyr = None
        for uri in (f"{url}|geometrytype={gtype}", url, f"/vsicurl/{url}"):
            cand = QgsVectorLayer(uri, name, "ogr")
            if cand.isValid():
                lyr = cand
                break
        if lyr is None:
            log.append(f"! FAILED: {name} ({url}) — is the SBC server running?")
            continue
        styler(lyr)
        _autorefresh(lyr)
        proj.addMapLayer(lyr)
        n_ok += 1
        log.append(f"+ {name}: {lyr.featureCount()} feats, auto-refresh {REFRESH_MS} ms")

    origin = _get_origin()
    if origin and origin.get("origin_set"):
        lat, lon = origin["lat"], origin["lon"]
        aoi = QgsRectangle(lon - 0.004, lat - 0.003, lon + 0.004, lat + 0.003)  # ~800x650 m
        proj.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(aoi, WGS84))
        try:
            from qgis.utils import iface
            if iface:
                iface.mapCanvas().setExtent(aoi)
                iface.mapCanvas().refresh()
        except Exception:
            pass
        rot = "SURVEYED" if origin.get("rotation_surveyed") else "UNSURVEYED (cloud rotation arbitrary!)"
        log.append(f"+ incident origin ({lat:.5f}, {lon:.5f}) rot={origin.get('rotation_deg', 0)}° — {rot}")
    else:
        log.append("! incident origin NOT set — features render at origin_set=false; "
                   "POST /api/origin (see module docstring), then re-run")

    wrote = proj.write(OUT)
    log.append(f"== {n_ok}/{len(specs)} live layers; project {'SAVED' if wrote else 'SAVE FAILED'}: {OUT}")
    with open(RESULT_FILE, "w") as f:
        f.write("\n".join(log) + "\n")
    for line in log:
        print(line)


# Run under QGIS's --code path or the Python Console. Any exception in a GUI
# console lands in QGIS's message log, not stdout — so mirror it to RESULT_FILE
# too, otherwise a styling/API error looks identical to "still running".
try:
    main()
except Exception:
    import traceback
    tb = traceback.format_exc()
    try:
        with open(RESULT_FILE, "w") as _f:
            _f.write("SETUP FAILED:\n" + tb)
    except Exception:
        pass
    print(tb)
    raise
