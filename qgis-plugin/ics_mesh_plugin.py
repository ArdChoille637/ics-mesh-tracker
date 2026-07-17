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
        
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dockwidget)
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
        self.anchor_lon = lon
        self.anchor_lat = lat
        self.dockwidget.set_anchor_status(f"Anchor: {lon:.5f}, {lat:.5f}")
        # Revert map tool to pan
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
            QgsField("age_s", QVariant.Double)
        ])
        self.layer.updateFields()
        
        # Set up basic symbology based on PAR status
        categories = []
        par_colors = {
            "OK": "51,221,51",         # Green
            "EMERGENCY": "255,187,51", # Orange
            "MAYDAY": "255,51,51",     # Red
            "OUT_OF_CONTACT": "136,136,136" # Gray
        }
        
        for par, color in par_colors.items():
            sym = QgsSymbol.defaultSymbol(self.layer.geometryType())
            sym.setColor(QColor(*[int(c) for c in color.split(',')]))
            categories.append(QgsRendererCategory(par, sym, par))
            
        renderer = QgsCategorizedSymbolRenderer("par_status", categories)
        self.layer.setRenderer(renderer)
        
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
        import urllib.error
        import json
        try:
            req = urllib.request.Request(self.api_url)
            with urllib.request.urlopen(req, timeout=1.0) as response:
                data = json.loads(response.read().decode())
                nodes = data.get("nodes", [])
                
                # Update UI
                self.dockwidget.update_nodes(nodes)
                
                # Update Map
                if self.layer and self.layer.isValid() and self.anchor_lon is not None and self.anchor_lat is not None:
                    self.update_layer(nodes)
                    
        except Exception as e:
            self.dockwidget.status_label.setText(f"Status: Polling Error")
            self.dockwidget.status_label.setStyleSheet("color: red")

    def update_layer(self, nodes):
        pr = self.layer.dataProvider()
        
        # Delete existing features
        feature_ids = [f.id() for f in self.layer.getFeatures()]
        if feature_ids:
            pr.deleteFeatures(feature_ids)
            
        features = []
        for n in nodes:
            # Calculate absolute position based on anchor + relative offsets
            # x_m is East, y_m is North
            dist = math.hypot(n.get("x_m", 0), n.get("y_m", 0))
            azimuth_rad = math.atan2(n.get("x_m", 0), n.get("y_m", 0))
            
            # If distance is almost 0, it's the anchor (or exactly coincident)
            if dist < 0.01:
                pt = QgsPointXY(self.anchor_lon, self.anchor_lat)
            else:
                pt = self.distance_area.computeDestination(QgsPointXY(self.anchor_lon, self.anchor_lat), dist, azimuth_rad)
                
            feat = QgsFeature(self.layer.fields())
            feat.setGeometry(QgsGeometry.fromPointXY(pt))
            feat.setAttributes([
                n.get("node_id", 0),
                n.get("par_status", "UNKNOWN"),
                n.get("battery_pct", 0),
                n.get("grade", "unknown"),
                n.get("pos_confidence", 0.0),
                n.get("is_anchor", False),
                n.get("age_s", 0.0)
            ])
            features.append(feat)
            
        pr.addFeatures(features)
        self.layer.triggerRepaint()

