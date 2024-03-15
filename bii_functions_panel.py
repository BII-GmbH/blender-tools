import bpy

class BiiFunctionsPanel(bpy.types.Panel):
    """Creates a Panel in the Object properties window"""
    bl_label = "BII IFC Tools"
    bl_idname = "OBJECT_PT_mesh_cleanup_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BII Tools'

    def draw(self, context):
        layout = self.layout

        # Add a short description of the panel
        row1 = layout.row(align = True)
        row1.alignment = 'EXPAND'
        row1.label(text="Default", icon='INFO')

        layout.operator("object.close_mesh_holes_operator")

        layout.prop(context.scene, "bulk_material", text="Bulk Material")

        layout.operator("object.set_ifc_class_for_bulk_operator")

        layout.operator("object.set_ifc_group_property_operator")


def register():
    bpy.utils.register_class(BiiFunctionsPanel)

def unregister():
    bpy.utils.unregister_class(BiiFunctionsPanel)