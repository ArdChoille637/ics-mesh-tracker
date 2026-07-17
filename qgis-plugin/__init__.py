# -*- coding: utf-8 -*-
def classFactory(iface):
    from .ics_mesh_plugin import IcsMeshPlugin
    return IcsMeshPlugin(iface)
