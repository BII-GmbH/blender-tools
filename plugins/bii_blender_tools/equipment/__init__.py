"""Equipment rigs: joints, preview and export as dProB scriptable equipment (.dequip).

See README.md in this folder. rig_api is the interface for scripts and AI agents.
"""

from . import ik_mode, rig_props, rig_preview, rig_ui

_modules = (rig_props, rig_preview, ik_mode, rig_ui)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()
