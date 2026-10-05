import math
from typing import Any, Generator
import time
from contextlib import contextmanager

import bpy
import sys
import argparse
from pathlib import Path


@contextmanager
def timer(label: str) -> Generator[None, Any, None]:
    start = time.perf_counter()

    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        minutes, seconds = divmod(elapsed, 60)

        print(f"[Timing] {label}: {int(minutes):02d}:{seconds:05.2f} min")

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
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for generated FBX and texture files.",
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


def add_geo_node_modifier(obj: Any) -> Any | None:
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


def apply_modifier(obj: Any, mod: str) -> None:
    print("Applying Geometry Node modifier...")
    try:
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=mod)
        print(f"Successfully applied modifier: {mod}")
    except Exception as e:
        print(f"Error applying modifier: {e}")


def smart_uv_unwrap(obj: Any) -> None:
    # Make the target the only selected object.
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # Select every face and unwrap.
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")

    bpy.ops.uv.smart_project(
        angle_limit=math.radians(66),
        margin_method="FRACTION",
        island_margin=0,
        correct_aspect=True,
        scale_to_bounds=True,
    )

    bpy.ops.object.mode_set(mode="OBJECT")


def get_material(obj:Any, name: str) -> Any:
    material = bpy.data.materials.get(name)

    if material is None:
        raise RuntimeError(
            f'Material "{name}" was not found.'
        )

    if material.name not in {
        slot.material.name
        for slot in obj.material_slots
        if slot.material
    }:
        raise RuntimeError(
            f'Active object does not use material "{name}".'
        )

    return material


def make_only_material(obj: Any, material: Any) -> None:
    obj.data.materials.clear()
    obj.data.materials.append(material)

    for polygon in obj.data.polygons:
        polygon.material_index = 0


def create_bake_target(material: Any, img_name: str, res: int) -> tuple[Any, Any]:
    nodes = material.node_tree.nodes

    # reusing image routine
    img = bpy.data.images.get(img_name)
    # img there but not right size
    if img and tuple(img.size) != (res, res):
        bpy.data.images.remove(img)
        img = None
    if img is not None:
        return img

    # create texture
    img = bpy.data.images.new(
        name = img_name,
        width= res,
        height = res,
        alpha = False,
        float_buffer = False
    )
    img.colorspace_settings.name = "sRGB"
    img.file_format = "PNG"
    blend_dir = Path(bpy.data.filepath).parent
    img.filepath_raw = str(f"{blend_dir}/{img_name}.png")

    # create img_texture node in material
    img_node = nodes.get(img_name)
    if img_node is None or img_node.type != "TEX_IMAGE":
        img_node = nodes.new("ShaderNodeTexImage")
        img_node.name = img_name
        img_node.label = img_name
        img_node.image = img

    # set the image node texture to active -> so we use it for the baking process
    for node in nodes:
        node.select = False
    img_node.select = True
    nodes.active = img_node

    return img, img_node


def bake_point_cloud_diffuse_colors(obj: Any, img: Any) -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"

    # GPU is optimal, CPU backup
    scene.cycles.device = "GPU"

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    bpy.ops.object.bake(
        type="DIFFUSE",
        pass_filter={"COLOR"},
        target="IMAGE_TEXTURES",
        margin=16,
        use_clear=True,
    )

    # FBX embedding is most dependable when the image has an actual file.
    img.save()


def use_baked_img(material: Any, image_node: Any) -> None:
    nodes = material.node_tree.nodes
    links = material.node_tree.links

    principled = next(
        (node for node in nodes if node.type == "BSDF_PRINCIPLED"),
        None,
    )

    if principled is None:
        raise RuntimeError(
            f'Material "{material.name}" has no Principled BSDF node.'
        )

    base_color = principled.inputs.get("Base Color")

    if base_color is None:
        raise RuntimeError("Principled BSDF has no Base Color input.")

    # This removes the existing Attribute → Base Color connection, if any.
    for link in list(base_color.links):
        links.remove(link)

    links.new(image_node.outputs["Color"], base_color)


def add_decimate_mod(obj: Any, ratio: float) -> Any | None:
    mod = obj.modifiers.new(
        name="Decimate_Auto",
        type="DECIMATE",
    )
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio

    result = bpy.ops.object.modifier_apply(modifier=mod.name)
    if "FINISHED" not in result:
        raise RuntimeError(f"Could not apply Decimate: {result}")

    print("Decimate mod applied successfully.")


def export_fbx(obj: Any, name: str, output_dir: str | None) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    blend_dir = Path(bpy.data.filepath).parent
    if output_dir:
        output_dir = str(output_dir)
        path = f"{blend_dir}/{output_dir}/{name}.fbx"
    else:
        path = f"{blend_dir}/{name}.fbx"
    bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=True,
        object_types={"MESH"},
        path_mode="COPY",
        embed_textures=True,
        bake_anim=False,
        add_leaf_bones=False,
    )


def save_blend_file(path: str) -> None:
    try:
        blend_dir = Path(bpy.data.filepath).parent
        bpy.ops.wm.save_as_mainfile(filepath=f"{blend_dir}/{path}")
        print(f"SUCCESS: File saved to -> {Path(bpy.data.filepath).parent}/{path}")
    except Exception as e:
        print(f"Error saving file: {e}")


def convert_las_to_fbx(las_file: str, output_dir: str):
    print(f"\n--- Starting Automation ---")
    print(f"Input LAS: {las_file}")

    with timer("Import & convert point cloud to mesh via geo node"):
        imported_las_obj = import_las_file(las_file)
        convert_point_cloud_to_mesh(imported_las_obj)

        mod = add_geo_node_modifier(imported_las_obj)
        apply_modifier(imported_las_obj, mod.name)

    name = Path(las_file).stem

    with timer("Embed color attribute into 4k texture"):
        smart_uv_unwrap(imported_las_obj)
        mat = get_material(imported_las_obj, "P Cloud")
        make_only_material(imported_las_obj, mat)
        img, img_node = create_bake_target(mat, name, 4096)
        bake_point_cloud_diffuse_colors(imported_las_obj, img)
        use_baked_img(mat, img_node)

    with timer("Apply Decimate to reduce vertex count -> file size"):
        add_decimate_mod(imported_las_obj, 0.25)

    export_fbx(imported_las_obj, name, output_dir)

    # Debug
    # save_blend_file(f"{name}.blend")


def main() -> None:
    args = parse_args()

    with timer("Complete pipeline"):
        convert_las_to_fbx(str(args.filepath), args.output_dir)


if __name__ == "__main__":
    main()
