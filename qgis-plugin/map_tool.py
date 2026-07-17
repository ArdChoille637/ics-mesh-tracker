# -*- coding: utf-8 -*-
from qgis.gui import QgsMapToolEmitPoint
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject
from qgis.PyQt.QtCore import pyqtSignal

class SetAnchorMapTool(QgsMapToolEmitPoint):
    anchorSet = pyqtSignal(float, float, str)

    def __init__(self, canvas):
        super(SetAnchorMapTool, self).__init__(canvas)
        self.canvas = canvas

    def canvasReleaseEvent(self, e):
        # Get point in map coordinates
        point = self.toMapCoordinates(e.pos())
        
        # We want to store the anchor in EPSG:4326 (WGS84 Lat/Lon) regardless of map CRS
        # so it's stable and easy to project from.
        crsSrc = self.canvas.mapSettings().destinationCrs()
        crsDest = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(crsSrc, crsDest, QgsProject.instance())
        
        point_wgs84 = transform.transform(point)
        
        self.anchorSet.emit(point_wgs84.x(), point_wgs84.y(), "EPSG:4326")
