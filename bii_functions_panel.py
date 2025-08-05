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

        layout.label(text="Clean and reduce Model")

        # layout.operator("object.upgrade_ifc_operator")

        layout.prop(context.window_manager, "decimate_ratio", text="Decimate Ratio")
        layout.operator("object.clean_reduce_ifc_operator")
        layout.label(text="Progress:")
        layout.label(text=context.scene.clean_progress)


def register():
    bpy.utils.register_class(BiiFunctionsPanel)
    bpy.types.WindowManager.decimate_ratio = bpy.props.FloatProperty(
        name="Decimate Ratio",
        default=1.0, min=0.1, max=1.0)
    bpy.types.Scene.clean_progress = bpy.props.StringProperty(default="Ready to clean and reduce Model")

def unregister():
    bpy.utils.unregister_class(BiiFunctionsPanel)
    del bpy.types.WindowManager.decimate_ratio
    del bpy.types.Scene.clean_progress