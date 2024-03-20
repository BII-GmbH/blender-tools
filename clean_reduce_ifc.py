import bpy

class CleanReduceIfcOperator(bpy.types.Operator):
    """Clean and reduce IFC"""
    bl_idname = "object.clean_reduce_ifc_operator"
    bl_label = "Clean and Reduce IFC"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        clean_reduce_ifc(self, context)
        self.report({'INFO'}, "IFC Clean and Reduce Completed")
        return {'FINISHED'}

def clean_and_link_mesh_data(context):
    # Check if there are selected objects
    if not context.selected_objects:
        print("No objects selected. Exiting function.")
        return  # Exit the function if no objects are selected

    # Check if there's an active object; if not, set the first selected object as active
    if not context.active_object or context.active_object not in context.selected_objects:
        context.view_layer.objects.active = context.selected_objects[0]

    # A dictionary to track original mesh data and their objects
    mesh_to_objects = {}
    for obj in bpy.context.selected_objects:
        if obj.type == 'MESH':
            if obj.data not in mesh_to_objects:
                mesh_to_objects[obj.data] = [obj]
            else:
                mesh_to_objects[obj.data].append(obj)

    total_objects = len(context.selected_objects)
    processed_objects = 0

    # Ensure we're in object mode to start
    bpy.ops.object.mode_set(mode='OBJECT')

    # Process each unique mesh data block
    for mesh_data, objects in mesh_to_objects.items():
        original_object = objects[0]  # Select the first object for modification

        # Ensure the object has a unique mesh data block
        original_object.data = original_object.data.copy()

        # Make the original object active
        bpy.context.view_layer.objects.active = original_object

        # Ensure all objects are deselected before selecting the active object
        bpy.ops.object.select_all(action='DESELECT')
        original_object.select_set(True)

        # Enter Edit mode and select all vertices
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')

        # Apply the necessary mesh operations
        bpy.ops.mesh.remove_doubles()
        bpy.ops.mesh.dissolve_limited(angle_limit=0.0872665)  # Example angle limit

        # Go back to Object mode
        bpy.ops.object.mode_set(mode='OBJECT')
        
        # Add and apply the Decimate modifier
        mod = original_object.modifiers.new(name="DecimateMod", type='DECIMATE')
        mod.ratio = context.window_manager.decimate_ratio
        bpy.ops.object.modifier_apply(modifier=mod.name)

        # Link the modified mesh data to the other objects
        for other_object in objects[1:]:  # Skip the first object since it's already modified
            other_object.data = original_object.data

        processed_objects += 1    
        
        # Update progress message every 10 objects
        if processed_objects % 15 == 0 or processed_objects == total_objects:
            progress_message = f"Processing {processed_objects} of {total_objects} objects..."
            context.scene.clean_progress = progress_message    
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    
    # Clear the status text when done
    context.scene.clean_progress = "Ready to clean and reduce IFC"
    bpy.context.workspace.status_text_set(None)

def clean_reduce_ifc(self, context):
    clean_and_link_mesh_data(context)

def register():
    bpy.utils.register_class(CleanReduceIfcOperator)

def unregister():
    bpy.utils.unregister_class(CleanReduceIfcOperator)

if __name__ == "__main__":
    register()
