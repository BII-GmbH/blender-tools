import bpy
import ifcopenshell
import bonsai
from bonsai.bim.ifc import IfcStore
import bonsai.tool as tool
from bonsai.bim.module.pset.data import Data as PsetData
import bmesh

# Global counter for group naming
group_counter = 0

def set_ifc_property(self, context, property_name):

    global group_counter  # Reference the global counter
    
    # Increment the group counter
    group_counter += 1
    property_name = f"Group#{group_counter}"  # Update property_name with incremented counter

     # Get the active IFC file
    ifc_file = IfcStore.get_file()
    if not ifc_file:
        print("No IFC file found. Ensure you're working in a Bonsai project.")
        return
    
    # Loop through all selected objects
    for obj in bpy.context.selected_objects:
        if obj.type == 'MESH':

            # Set the active object
            bpy.context.view_layer.objects.active = obj
            # get the ifc object
            ifc_obj = IfcStore.get_file().by_id(obj.BIMObjectProperties.ifc_definition_id)
            # check if the object is valid
            if not ifc_obj:
                print(f"Object {obj.name} has no IFC object.")
                continue
            pset = ifcopenshell.api.run("pset.add_pset", ifc_file, product=ifc_obj, name="Custom_dProB_Grouping")

            # Set the properties of the Pset
            new_values = {
                "Grouping": property_name,
            }
            ifcopenshell.api.run("pset.edit_pset", ifc_file, pset=pset, properties=new_values)
            

class SetIfcPropForGroup(bpy.types.Operator):
    """Set custom ifc property for grouping"""
    bl_idname = "object.set_ifc_group_property_operator"
    bl_label = "Set Group Property for selected"

    def execute(self, context):     
        set_ifc_property(self, context, None)
        return {'FINISHED'}


def register():
    bpy.utils.register_class(SetIfcPropForGroup)

def unregister():
    bpy.utils.unregister_class(SetIfcPropForGroup)