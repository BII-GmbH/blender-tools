from typing import Any

import bpy
import sys
import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """Parse only arguments appearing after Blender's '--' separator."""
    script_args = (
        sys.argv[sys.argv.index("--") + 1:]
        if "--" in sys.argv
        else []
    )

    parser = argparse.ArgumentParser(
        description="Convert a LAS point cloud to a Blender file."
    )
    parser.add_argument(
        "filepath",
        type=Path,
        help="Path to the LAS file, for example: example.las",
    )

    return parser.parse_args(script_args)


def import_las_file(filepath: str) -> Any | None:
    print("Importing LAS file...")
    try:
        bpy.ops.import_scene.point_cloud_las(filepath=filepath)
        imported_obj = bpy.context.active_object

        if not imported_obj:
            print("Error: Import succeeded but no active object was found.")
            return None

        print(f"Imported object: {imported_obj.name}")
        return imported_obj
    except Exception as e:
        print(f"Error during LAS import: {e}")
        return None


def convert_point_cloud_to_mesh(obj: Any) -> None:
    print(f"Converting Point Cloud '{obj.name}' to Mesh...")
    try:
        # IMPORTANT: The convert operator acts on SELECTED objects, not just the active one.
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        # Ensure we are in Object Mode to allow conversion
        if bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # Perform the conversion
        bpy.ops.object.convert(target="MESH")
        print("Conversion successful.")
    except Exception as e:
        print(f"Error during conversion: {e}")


def add_modifier(obj: Any) -> Any | None:
    geo_node_name = "Convert P Cloud to Mesh"
    geo_node_tree = bpy.data.node_groups.get(geo_node_name)

    if not geo_node_tree:
        print(f"Error: Could not find Geometry Node Tree '{geo_node_name}' in template.")

    print("Adding Geometry Node modifier...")
    try:
        mod = obj.modifiers.new(name="GeoNode_Auto", type='NODES')
        if geo_node_tree:
            mod.node_group = geo_node_tree
            print(f"Linked node tree: {geo_node_tree.name}")
        return mod
    except Exception as e:
        print(f"Error adding modifier: {e}")
        return None


def apply_modifier(obj: Any, mod: Any) -> None:
    if mod:
        print("Applying Geometry Node modifier...")
        try:
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.modifier_apply(modifier=mod.name)
            print(f"Successfully applied modifier: {mod.name}")
        except Exception as e:
            print(f"Error applying modifier: {e}")
    else:
        print("Warning: No modifier was created to apply.")


def save_blend_file(path: str) -> None:
    try:
        blend_dir = Path(bpy.data.filepath).parent
        bpy.ops.wm.save_as_mainfile(filepath=f"{blend_dir}/{path}")
        print(f"SUCCESS: File saved to -> {Path(bpy.data.filepath).parent}/{path}")
    except Exception as e:
        print(f"Error saving file: {e}")


def convert_las_to_blend(las_file):
    print(f"\n--- Starting Automation ---")
    print(f"Input LAS: {las_file}")

    imported_las_obj = import_las_file(las_file)

    convert_point_cloud_to_mesh(imported_las_obj)

    mod = add_modifier(imported_las_obj)

    apply_modifier(imported_las_obj, mod)

    name = las_file.split(".")[0]
    save_blend_file(f"{name}.blend")


def main() -> None:
    args = parse_args()
    try:
        convert_las_to_blend(str(args.filepath))
    except Exception as e:
        print(f"Error: No arguments found after '--' {e}")


if __name__ == "__main__":
    main()
