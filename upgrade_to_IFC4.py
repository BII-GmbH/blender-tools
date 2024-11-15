import bpy
from blenderbim.bim.ifc import IfcStore
import ifcpatch
import ifcopenshell

def upgrade_IFC4(self, context):
    # Upgrade the IFC file to IFC4
    # Get the active IFC file
    ifc_file = IfcStore.get_file()
    if not ifc_file:
        print("No IFC file found. Ensure you're working in a BlenderBIM project.")
        return
    ifc_file.upgrade("IFC4")
            
class UpgradeIFC4Operator(bpy.types.Operator):
    bl_idname = "object.upgrade_ifc_operator"
    bl_label = "Upgrade to IFC4"

    def execute(self, context):
        upgrade_IFC4(self, context)
        return {'FINISHED'}

def register():
    bpy.utils.register_class(UpgradeIFC4Operator)

def unregister():
    bpy.utils.unregister_class(UpgradeIFC4Operator)