# -*- coding: utf-8 -*-
import json
import math
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon, QColor

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsField,
    QgsDistanceArea,
    QgsSymbol,
    QgsSingleSymbolRenderer,
    QgsRendererCategory,
    QgsCategorizedSymbolRenderer
)
from qgis.PyQt.QtCore import QVariant

from .ics_mesh_dockwidget import IcsMeshDockWidget
from .map_tool import SetAnchorMapTool

class IcsMeshPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dockwidget = None
        self.websocket = None
        
        self.layer = None
        self.anchor_lon = None
        self.anchor_lat = None
        
        self.distance_area = QgsDistanceArea()
        self.distance_area.setEllipsoid('WGS84')

    def initGui(self):
        # Create action that will start plugin configuration
        self.action = QAction("ICS Mesh Tracker", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        
        # Add toolbar button and menu item
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("ICS Mesh Tracker", self.action)
        
        # Initialize DockWidget
        self.dockwidget = IcsMeshDockWidget()
        self.dockwidget.connectClicked.connect(self.connect_ws)
        self.dockwidget.disconnectClicked.connect(self.disconnect_ws)
        self.dockwidget.setAnchorClicked.connect(self.activate_map_tool)
        
        # PyQt6 (QGIS 4) needs the SCOPED enum — unscoped Qt.RightDockWidgetArea
        # raises AttributeError and aborts plugin load.
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dockwidget)
        self.dockwidget.hide()

    def unload(self):
        # Remove the plugin menu item and icon
        self.iface.removePluginMenu("ICS Mesh Tracker", self.action)
        self.iface.removeToolBarIcon(self.action)
        
        # Disconnect and cleanup
        self.disconnect_ws()
        if self.dockwidget:
            self.iface.removeDockWidget(self.dockwidget)
            self.dockwidget.deleteLater()

    def run(self):
        self.dockwidget.show()

    def activate_map_tool(self):
        self.map_tool = SetAnchorMapTool(self.iface.mapCanvas())
        self.map_tool.anchorSet.connect(self.on_anchor_set)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def on_anchor_set(self, lon, lat, crs_auth_id):
        # Manual origin override (used when there's no host GPS): push the
        # clicked point to the server as the incident origin = the GATEWAY's
        # position. The server then georeferences every node relative to it and
        # returns lat/lon on /api/nodes — so we do NOT store a client-side
        # anchor or do any client-side trig (that was the old north-bug).
        try:
            import urllib.request
            base = self.api_url.rsplit("/api/nodes", 1)[0] if getattr(self, "api_url", None) else "http://127.0.0.1:8000"
            body = json.dumps({"lat": lat, "lon": lon}).encode()
            req = urllib.request.Request(base + "/api/origin", data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=2.0)
            self.dockwidget.set_anchor_status(f"Origin set: {lat:.5f}, {lon:.5f}")
        except Exception as e:
            self.dockwidget.set_anchor_status(f"Origin set FAILED: {e}")
        self.iface.mapCanvas().unsetMapTool(self.map_tool)

    def ensure_layer(self):
        if self.layer and self.layer.isValid():
            return
            
        # Create a memory layer for points
        self.layer = QgsVectorLayer("Point?crs=EPSG:4326", "ICS Mesh Nodes", "memory")
        pr = self.layer.dataProvider()
        
        # Add attributes
        pr.addAttributes([
            QgsField("node_id", QVariant.Int),
            QgsField("par_status", QVariant.String),
            QgsField("battery", QVariant.Int),
            QgsField("grade", QVariant.String),
            QgsField("pos_conf", QVariant.Double),
            QgsField("is_anchor", QVariant.Bool),
            QgsField("age_s", QVariant.Double),
            QgsField("cls", QVariant.String),
        ])
        self.layer.updateFields()

        # Honesty styling: categorize on PAR status x located-ness. An unlocated
        # (topology/stale) responder renders HOLLOW because its bearing is
        # unknown — never a solid confident dot; the gateway/anchor is a diamond.
        from qgis.core import QgsMarkerSymbol
        par_colors = {"MAYDAY": ("#ff3333", 6.0), "EMERGENCY": ("#ffbb33", 5.2),
                      "OK": ("#33dd33", 4.0), "OUT_OF_CONTACT": ("#888888", 3.6)}
        cats = []
        for par, (hexc, size) in par_colors.items():
            solid = QgsMarkerSymbol.createSimple({
                "name": "circle", "color": hexc, "outline_color": "black",
                "outline_width": "0.3", "size": str(size)})
            hollow = QgsMarkerSymbol.createSimple({
                "name": "circle", "color": "0,0,0,0", "outline_color": hexc,
                "outline_width": "0.8", "outline_style": "dash", "size": str(size)})
            anchor = QgsMarkerSymbol.createSimple({
                "name": "diamond", "color": hexc, "outline_color": "black",
                "outline_width": "0.3", "size": str(size + 1.5)})
            cats.append(QgsRendererCategory(f"{par}|loc", solid, f"{par} (located)"))
            cats.append(QgsRendererCategory(f"{par}|unloc", hollow, f"{par} (not located)"))
            cats.append(QgsRendererCategory(f"{par}|anc", anchor, f"{par} (gateway)"))
        other = QgsMarkerSymbol.createSimple({
            "name": "circle", "color": "0,0,0,0", "outline_color": "#888888",
            "outline_width": "0.8", "outline_style": "dash", "size": "3.6"})
        cats.append(QgsRendererCategory("", other, "other"))
        self.layer.setRenderer(QgsCategorizedSymbolRenderer("cls", cats))
        QgsProject.instance().addMapLayer(self.layer)

    def connect_ws(self, url):
        # We repurposed the WS connect button to start HTTP REST polling instead
        # to bypass Qt6 WebSocket incompatibility on macOS.
        if hasattr(self, 'poll_timer') and self.poll_timer.isActive():
            self.poll_timer.stop()
            
        import urllib.parse
        parsed = urllib.parse.urlparse(url)
        # Convert ws:// to http:// and /ws to /api/nodes
        self.api_url = f"http://{parsed.netloc}/api/nodes"
        
        from qgis.PyQt.QtCore import QTimer
        self.poll_timer = QTimer()
        self.poll_timer.timeout.connect(self.fetch_data)
        self.poll_timer.start(1000) # Poll every 1 second
        
        self.dockwidget.set_connected_state(True)
        self.ensure_layer()

    def disconnect_ws(self):
        if hasattr(self, 'poll_timer') and self.poll_timer.isActive():
            self.poll_timer.stop()
        self.dockwidget.set_connected_state(False)

    def fetch_data(self):
        import urllib.request
        try:
            req = urllib.request.Request(self.api_url)
            with urllib.request.urlopen(req, timeout=1.0) as response:
                data = json.loads(response.read().decode())
            nodes = data.get("nodes", [])
            self.dockwidget.update_nodes(nodes)
            if self.layer and self.layer.isValid():
                self.update_layer(nodes)
            self._show_banner(data.get("env", {}), data.get("origin", {}), len(nodes))
        except Exception:
            self.dockwidget.status_label.setText("Status: Polling Error")
            self.dockwidget.status_label.setStyleSheet("color: red")

    def update_layer(self, nodes):
        # Plot the SERVER-computed lat/lon directly. The server georeferences
        # every node through the gateway/host-GPS origin WITH the frame rotation
        # applied (geo.local_to_wgs84), so there is no client-side trig and no
        # north-assumption. Nodes without lat/lon (origin not set yet) are skipped.
        pr = self.layer.dataProvider()
        pr.truncate()
        feats = []
        for n in nodes:
            lon, lat = n.get("lon"), n.get("lat")
            if lon is None or lat is None:
                continue
            grade = n.get("grade", "topology")
            if n.get("is_anchor"):
                loc = "anc"
            elif grade == "coordinate":
                loc = "loc"
            else:
                loc = "unloc"
            feat = QgsFeature(self.layer.fields())
            feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
            feat.setAttributes([
                int(n.get("node_id", 0)),
                n.get("par_status", "OK"),
                int(n.get("battery_pct", 0) or 0),
                grade,
                float(n.get("pos_confidence", 0.0) or 0.0),
                bool(n.get("is_anchor", False)),
                float(n.get("age_s", 0.0) or 0.0),
                f'{n.get("par_status", "OK")}|{loc}',
            ])
            feats.append(feat)
        pr.addFeatures(feats)
        self.layer.triggerRepaint()

    def _show_banner(self, env, origin, n_nodes):
        # Same honesty logic as the server /qgis/status + web map banner.
        msgs = []
        if env.get("flip_unstable"):
            msgs.append("TOPOLOGY MODE — reflection-unstable; proximity only")
        elif env.get("graph_rigid") is False:
            msgs.append("TOPOLOGY MODE — mesh under-constrained")
        elif (env.get("n_coordinate_grade") or 0) == 0 and n_nodes > 1:
            msgs.append("PROXIMITY ONLY — nobody constrained enough to locate")
        if not origin.get("origin_set"):
            msgs.append("NO ORIGIN — waiting on gateway/host GPS")
        elif not origin.get("rotation_surveyed"):
            msgs.append("ROTATION UNSURVEYED — orientation vs north arbitrary")
        txt = "Status: Connected — %d nodes" % n_nodes
        if msgs:
            txt += "  |  " + "  |  ".join(msgs)
        self.dockwidget.status_label.setText(txt)
        self.dockwidget.status_label.setStyleSheet("color: %s" % ("#b8860b" if msgs else "green"))

