import bpy

def get_material_options(self, context):
    return [
        ("Bulk", "Bulk", "Description for Option 0"),
        ("Gleisschotter (Verschmutzt)", "Gleisschotter (Verschmutzt)", "Description for Option 1"),
        ("Gleisschotter (Neu)", "Gleisschotter (Neu)", "Description for Option 2"),
        ("Asphalt", "Asphalt", "Description for Option 3"),
        ("Frostschutzschicht", "Frostschutzschicht", "Description for Option 4"),
        ("Gleisunterbau", "Gleisunterbau", "Description for Option 5"),
        ("Beton", "Beton", "Description for Option 6"),
        # Add more options as needed
    ]

def register():
    bpy.types.Scene.bulk_material = bpy.props.EnumProperty(
        name="Bulk Material",
        items=get_material_options,
        description="Select the material for the bulk",
    )

def unregister():
    del bpy.types.Scene.bulk_material

