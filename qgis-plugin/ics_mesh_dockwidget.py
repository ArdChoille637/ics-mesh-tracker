# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import (QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
                             QHeaderView, QMessageBox)
from qgis.PyQt.QtCore import Qt, pyqtSignal

class IcsMeshDockWidget(QDockWidget):
    # Signals for the main plugin to connect to
    connectClicked = pyqtSignal(str)
    disconnectClicked = pyqtSignal()
    setAnchorClicked = pyqtSignal()

    def __init__(self, parent=None):
        super(IcsMeshDockWidget, self).__init__("ICS Mesh Tracker", parent)
        
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        
        # Main widget and layout
        self.widget = QWidget()
        self.layout = QVBoxLayout()
        self.widget.setLayout(self.layout)
        self.setWidget(self.widget)
        
        # Connection settings
        conn_layout = QHBoxLayout()
        conn_layout.addWidget(QLabel("Gateway WS URL:"))
        
        self.url_input = QLineEdit("ws://127.0.0.1:8000/ws")
        conn_layout.addWidget(self.url_input)
        self.layout.addLayout(conn_layout)
        
        btn_layout = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        btn_layout.addWidget(self.connect_btn)
        
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self._on_disconnect_clicked)
        self.disconnect_btn.setEnabled(False)
        btn_layout.addWidget(self.disconnect_btn)
        self.layout.addLayout(btn_layout)
        
        # Status Label
        self.status_label = QLabel("Status: Disconnected")
        self.layout.addWidget(self.status_label)
        
        # Anchor settings
        anchor_layout = QHBoxLayout()
        self.anchor_btn = QPushButton("Set Anchor on Map")
        self.anchor_btn.clicked.connect(self.setAnchorClicked.emit)
        anchor_layout.addWidget(self.anchor_btn)
        
        self.anchor_status = QLabel("Anchor: Not set")
        anchor_layout.addWidget(self.anchor_status)
        self.layout.addLayout(anchor_layout)
        
        # Node list
        self.layout.addWidget(QLabel("Active Nodes:"))
        self.node_tree = QTreeWidget()
        self.node_tree.setHeaderLabels(["Node ID", "Role", "PAR", "Battery"])
        self.node_tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.layout.addWidget(self.node_tree)

    def _on_connect_clicked(self):
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Error", "URL cannot be empty")
            return
        self.connectClicked.emit(url)

    def _on_disconnect_clicked(self):
        self.disconnectClicked.emit()

    def set_connected_state(self, connected: bool):
        self.connect_btn.setEnabled(not connected)
        self.url_input.setEnabled(not connected)
        self.disconnect_btn.setEnabled(connected)
        if connected:
            self.status_label.setText("Status: Connected")
            self.status_label.setStyleSheet("color: green")
        else:
            self.status_label.setText("Status: Disconnected")
            self.status_label.setStyleSheet("color: red")

    def update_nodes(self, nodes_data: list):
        self.node_tree.clear()
        for node in nodes_data:
            node_id = str(node.get("node_id", ""))
            role = "Anchor" if node.get("is_anchor") else node.get("grade", "topology")
            par = node.get("par_status", "UNKNOWN")
            batt = f"{node.get('battery_pct', 0)}%"
            
            item = QTreeWidgetItem([node_id, role, par, batt])
            # Color coding for PAR
            if par == "OK":
                item.setForeground(2, Qt.darkGreen)
            elif par == "EMERGENCY":
                item.setForeground(2, Qt.darkYellow)
            elif par == "MAYDAY":
                item.setForeground(2, Qt.red)
            else:
                item.setForeground(2, Qt.gray)
                
            self.node_tree.addTopLevelItem(item)

    def set_anchor_status(self, text: str):
        self.anchor_status.setText(text)
