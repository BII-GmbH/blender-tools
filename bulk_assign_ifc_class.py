import bpy
import bmesh

def get_evaluated_world_mesh(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    
    # Apply world transform to mesh vertices
    mesh.transform(obj.matrix_world)
    
    return mesh

def calculate_volume(mesh):
    bm = bmesh.new()
    bm.from_mesh(mesh)
    volume = bm.calc_volume(signed=True)
    bm.free()
    return volume

def calculate_height(mesh):
    # Example calculation - adjust as needed
    z_coords = [v.co.z for v in mesh.vertices]
    height = max(z_coords) - min(z_coords)
    return height

def set_ifc_class_for_bulk(self, context, material):
    try:
        import ifcopenshell
        import bonsai
        from bonsai.bim.ifc import IfcStore
        import bonsai.tool as tool
        from bonsai.bim.module.pset.data import Data as PsetData
        # Get the active IFC file
        ifc_file = IfcStore.get_file()
        if not ifc_file:
            print("No IFC file found. Ensure you're working in a Bonsai project if you want to generate an IFC file.")
            print("Be sure to activate 'Custom Properties' when exporting FBX.")
            return    

        # Create a new Pset template file and the Pset template for Bulk
        pset_template = ifcopenshell.api.run("pset_template.add_pset_template", ifc_file, name="dProB_Bulk")
        ifcopenshell.api.run("pset_template.add_prop_template", ifc_file, pset_template=pset_template, name="BulkMaterial", description="Limited to what dProB is able to interpret!")
        ifcopenshell.api.run("pset_template.add_prop_template", ifc_file, pset_template=pset_template, name="BulkVolume", primary_measure_type="IfcVolumeMeasure")
        ifcopenshell.api.run("pset_template.add_prop_template", ifc_file, pset_template=pset_template, name="BulkHeight", primary_measure_type="IfcLengthMeasure")
        bonsai.bim.handler.refresh_ui_data()
        bonsai.bim.schema.reload(tool.Ifc.get().schema)
        
        # Loop through all selected objects
        for obj in bpy.context.selected_objects:
            if obj.type == 'MESH':

                # Set the active object
                bpy.context.view_layer.objects.active = obj
                # Assign the IFC class
                bpy.ops.bim.assign_class(ifc_class="IfcBuilding")
                # get the ifc object
                ifc_obj = IfcStore.get_file().by_id(obj.BIMObjectProperties.ifc_definition_id)
                # check if the object is valid
                if not ifc_obj:
                    print(f"Object {obj.name} has no IFC object.")
                    continue
                pset = ifcopenshell.api.run("pset.add_pset", ifc_file, product=ifc_obj, name="dProB_Bulk")

                mesh = get_evaluated_world_mesh(obj)
                volume = calculate_volume(mesh)
                height = calculate_height(mesh)

                # Set the properties of the Pset
                new_values = {
                    "BulkMaterial": material,            
                    "BulkVolume": volume, 
                    "BulkHeight": height
                }
                ifcopenshell.api.run("pset.edit_pset", ifc_file, pset=pset, properties=new_values)

                print(f"Assigned IfcBuilding and custom Pset to {obj.name}.")
    except:
        self.report({'INFO'}, "Bonsai is not installed. Good for you.")

def set_fbx_class_for_bulk(self, context, material):
    fbxs = 0
    for obj in bpy.context.selected_objects:
        if obj.type == 'MESH':
            mesh = get_evaluated_world_mesh(obj)
            volume = calculate_volume(mesh)
            height = calculate_height(mesh)
            obj["BulkMaterial"] = material
            obj["BulkVolume"] = volume
            obj["BulkHeight"] = height
            print(f"Assigned Custom Properties to {obj.name}.")
            fbxs += 1
    self.report({'INFO'}, f"Assigned Bulk Properties to {fbxs} objects. Be sure to activate 'Custom Properties' when exporting FBX.")

class SetIfcClassForBulkOperator(bpy.types.Operator):
    """Set IFC Class / FBX Custom Properties of selected Objects for custom dProB-Bulk"""
    bl_idname = "object.set_ifc_class_for_bulk_operator"
    bl_label = "Set IFC / FBX Class for Bulk"

    def execute(self, context):
        material = context.scene.bulk_material        
        set_ifc_class_for_bulk(self, context, material)
        set_fbx_class_for_bulk(self, context, material)
        return {'FINISHED'}


def register():
    bpy.utils.register_class(SetIfcClassForBulkOperator)

def unregister():
    bpy.utils.unregister_class(SetIfcClassForBulkOperator)