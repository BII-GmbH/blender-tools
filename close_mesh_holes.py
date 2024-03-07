import bpy

def close_mesh_holes(self, context):
    # Loop through all selected objects
    for obj in bpy.context.selected_objects:
        # Check if the object is a mesh
        if obj.type == 'MESH':
            # Switch to Object Mode (required to perform some operations)
            bpy.ops.object.mode_set(mode='OBJECT')
            # Make the current object the active object
            bpy.context.view_layer.objects.active = obj
            # Switch to Edit Mode
            bpy.ops.object.mode_set(mode='EDIT')
            # Select all vertices
            bpy.ops.mesh.select_all(action='SELECT')
            # Fill holes
            # Here, sides=0 means it will fill holes of any size. Adjust as needed.
            bpy.ops.mesh.fill_holes(sides=0)
            # Switch back to Object Mode
            bpy.ops.object.mode_set(mode='OBJECT')

    # Deselect all to clean up selection after operation
            
class CloseMeshHolesOperator(bpy.types.Operator):
    """Print Hello World"""
    bl_idname = "object.close_mesh_holes_operator"
    bl_label = "Close Selected Mesh Holes"

    def execute(self, context):
        close_mesh_holes(self, context)
        return {'FINISHED'}

def register():
    bpy.utils.register_class(CloseMeshHolesOperator)

def unregister():
    bpy.utils.unregister_class(CloseMeshHolesOperator)