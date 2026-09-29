"""Kilometrierung entlang von Bahnstrecken aus OpenStreetMap / OpenRailwayMap.

Erzeugt ausschließlich die Kilometrierungslinie (Gleisachse) und die
Kilometerwerte als Beschriftung - keine Schienengeometrie.
"""

# Für Blender-Versionen vor 4.2 (klassische Add-ons). Ab 4.2 gilt das
# blender_manifest.toml der Erweiterung.
bl_info = {
    "name": "Umgebung",
    "author": "Stefan Brauner",
    "version": (1, 25, 0),
    "blender": (3, 3, 0),
    "location": "3D-Ansicht > Seitenleiste (N) > Umgebung",
    "description": "Umgebung zu Bahnstrecken aufbauen: Kilometrierungslinie, Gleisachsen, "
                   "Gelände, Kartentextur und Gebäude (Daten: OpenStreetMap / amtliche DGM)",
    "category": "Import-Export",
}

import importlib
import sys

from . import (buildings, builder, corridor, dgm, maptexture, operators, overpass,
               projection, props, railgeom, selector, tracknetwork, ui)

_modules = (projection, overpass, railgeom, tracknetwork, dgm, corridor, maptexture, selector,
            buildings, builder, props, operators, ui)


def _reload():
    """Erlaubt „Reload Scripts“ während der Entwicklung."""
    for module in _modules:
        if module.__name__ in sys.modules:
            importlib.reload(module)


if "bpy" in locals():                                             # pragma: no cover
    _reload()

import bpy  # noqa: E402


def register():
    props.register()
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    props.unregister()


if __name__ == "__main__":                                        # pragma: no cover
    register()
