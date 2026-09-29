from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import bpy
import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.group
import ifcopenshell.api.material
import ifcopenshell.api.project
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.style
import ifcopenshell.api.unit


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

IFC_SCHEMA = "IFC4X3"

# Temporary geometry-only fallback.
# Later we can replace this per object with IfcRail, IfcGeographicElement, etc.
DEFAULT_IFC_CLASS = "IfcBuildingElementProxy"


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def get_output_path() -> Path:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--output",
        help="Output IFC path. Defaults to the .blend filename with .ifc.",
    )

    script_args = []

    if "--" in sys.argv:
        script_args = sys.argv[sys.argv.index("--") + 1:]

    args = parser.parse_args(script_args)

    if args.output:
        return Path(args.output).expanduser().resolve()

    if not bpy.data.filepath:
        raise RuntimeError(
            "Blend file is unsaved. "
            "Specify an output file using --output."
        )

    return Path(bpy.data.filepath).with_suffix(".ifc")


# ---------------------------------------------------------------------------
# IFC project
# ---------------------------------------------------------------------------

def create_ifc_project(scene):
    project_name = (
        Path(bpy.data.filepath).stem
        if bpy.data.filepath
        else scene.name
    )

    model = ifcopenshell.api.project.create_file(
        version=IFC_SCHEMA
    )

    project = ifcopenshell.api.root.create_entity(
        model,
        ifc_class="IfcProject",
        name=project_name,
    )

    # -------------------------------------------------------
    # Units
    #
    # No prefix -> metre / square metre / cubic metre.
    # -------------------------------------------------------

    length_unit = ifcopenshell.api.unit.add_si_unit(
        model,
        unit_type="LENGTHUNIT",
    )

    area_unit = ifcopenshell.api.unit.add_si_unit(
        model,
        unit_type="AREAUNIT",
    )

    volume_unit = ifcopenshell.api.unit.add_si_unit(
        model,
        unit_type="VOLUMEUNIT",
    )

    ifcopenshell.api.unit.assign_unit(
        model,
        units=[
            length_unit,
            area_unit,
            volume_unit,
        ],
    )

    # -------------------------------------------------------
    # Geometry contexts
    # -------------------------------------------------------

    model_context = ifcopenshell.api.context.add_context(
        model,
        context_type="Model",
    )

    body_context = ifcopenshell.api.context.add_context(
        model,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=model_context,
    )

    # -------------------------------------------------------
    # Spatial structure
    # -------------------------------------------------------

    site = ifcopenshell.api.root.create_entity(
        model,
        ifc_class="IfcSite",
        name=f"{project_name} Site",
    )

    ifcopenshell.api.aggregate.assign_object(
        model,
        products=[site],
        relating_object=project,
    )

    # Identity site placement.
    ifcopenshell.api.geometry.edit_object_placement(
        model,
        product=site,
    )

    return model, site, body_context


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def clamp01(value):
    return max(0.0, min(1.0, float(value)))


def get_blender_material_rgba(material):
    """
    V1 material conversion.

    Prefer an unlinked Principled BSDF Base Color.
    Fall back to Blender's diffuse_color.

    Textures and arbitrary shader graphs are intentionally ignored.
    """

    rgba = tuple(float(v) for v in material.diffuse_color)

    if material.use_nodes and material.node_tree:

        principled = next(
            (
                node
                for node in material.node_tree.nodes
                if node.type == "BSDF_PRINCIPLED"
            ),
            None,
        )

        if principled is not None:

            base_color = principled.inputs.get("Base Color")

            if (
                base_color is not None
                and not base_color.is_linked
            ):
                color = base_color.default_value

                rgba = (
                    float(color[0]),
                    float(color[1]),
                    float(color[2]),
                    rgba[3],
                )

            alpha = principled.inputs.get("Alpha")

            if (
                alpha is not None
                and not alpha.is_linked
            ):
                rgba = (
                    rgba[0],
                    rgba[1],
                    rgba[2],
                    float(alpha.default_value),
                )

    return tuple(clamp01(v) for v in rgba)


def get_or_create_ifc_material(
    model,
    body_context,
    blender_material,
    material_cache,
):
    key = blender_material.as_pointer()

    if key in material_cache:
        return material_cache[key]

    # -------------------------------------------------------
    # Actual IFC material
    # -------------------------------------------------------

    ifc_material = ifcopenshell.api.material.add_material(
        model,
        name=blender_material.name,
    )

    # -------------------------------------------------------
    # Visual IFC style
    # -------------------------------------------------------

    r, g, b, a = get_blender_material_rgba(
        blender_material
    )

    surface_style = ifcopenshell.api.style.add_style(
        model,
        name=blender_material.name,
    )

    ifcopenshell.api.style.add_surface_style(
        model,
        style=surface_style,
        ifc_class="IfcSurfaceStyleShading",
        attributes={
            "SurfaceColour": {
                "Name": None,
                "Red": r,
                "Green": g,
                "Blue": b,
            },
            "Transparency": 1.0 - a,
        },
    )

    # Associate visual style with the material.
    ifcopenshell.api.style.assign_material_style(
        model,
        material=ifc_material,
        style=surface_style,
        context=body_context,
    )

    entry = {
        "material": ifc_material,
        "style": surface_style,
        "name": blender_material.name,
    }

    material_cache[key] = entry

    return entry


# ---------------------------------------------------------------------------
# Mesh conversion
# ---------------------------------------------------------------------------

def split_mesh_by_material(obj_eval, mesh):
    """
    Convert the Blender mesh to world coordinates.

    One IFC representation item is created per used material.

    Important:
    Every representation item receives the SAME complete vertex array.
    This keeps the vertices input rectangular for IfcOpenShell / NumPy.

    Faces are filtered by material and keep their original vertex indices.
    """

    world_matrix = obj_eval.matrix_world

    # -------------------------------------------------------
    # Apply complete Blender world transform.
    # -------------------------------------------------------

    world_vertices = [
        tuple(
            float(value)
            for value in (world_matrix @ vertex.co)
        )
        for vertex in mesh.vertices
    ]

    # -------------------------------------------------------
    # Collect faces by Blender material index.
    # -------------------------------------------------------

    faces_by_material = defaultdict(list)

    for polygon in mesh.polygons:

        face = list(polygon.vertices)

        # Mirrored / negative transforms reverse handedness.
        if world_matrix.is_negative:
            face.reverse()

        faces_by_material[
            polygon.material_index
        ].append(face)

    # -------------------------------------------------------
    # Build one representation item per used material.
    #
    # KEY DIFFERENCE:
    #
    # Do NOT make a reduced vertex array per material.
    # Every item receives world_vertices in full.
    #
    # Therefore:
    #
    # vertices.shape =
    #     (material_count, vertex_count, 3)
    #
    # rather than a ragged list.
    # -------------------------------------------------------

    mesh_items = []

    for material_index in sorted(faces_by_material):

        blender_material = None

        if material_index < len(mesh.materials):
            blender_material = mesh.materials[
                material_index
            ]

        mesh_items.append(
            {
                "vertices": world_vertices,
                "faces": faces_by_material[
                    material_index
                ],
                "blender_material": blender_material,
            }
        )

    return mesh_items


# ---------------------------------------------------------------------------
# Material assignment to IFC product
# ---------------------------------------------------------------------------

def assign_product_materials(
    model,
    product,
    representation,
    mesh_items,
    body_context,
    material_cache,
):
    used_materials = []

    # representation.Items corresponds to the mesh items we supplied.
    for representation_item, mesh_item in zip(
        representation.Items,
        mesh_items,
        strict=True,
    ):

        blender_material = mesh_item[
            "blender_material"
        ]

        if blender_material is None:
            continue

        entry = get_or_create_ifc_material(
            model,
            body_context,
            blender_material,
            material_cache,
        )

        used_materials.append(entry)

        # Explicitly style this geometry item.
        # This is important for multi-material objects.
        ifcopenshell.api.style.assign_item_style(
            model,
            item=representation_item,
            style=entry["style"],
        )

    # Remove duplicate materials.
    unique_materials = []
    seen = set()

    for entry in used_materials:

        material_id = entry["material"].id()

        if material_id in seen:
            continue

        seen.add(material_id)
        unique_materials.append(entry)

    # -------------------------------------------------------
    # Single material
    # -------------------------------------------------------

    if len(unique_materials) == 1:

        ifcopenshell.api.material.assign_material(
            model,
            products=[product],
            type="IfcMaterial",
            material=unique_materials[0]["material"],
        )

    # -------------------------------------------------------
    # Multiple materials
    # -------------------------------------------------------

    elif len(unique_materials) > 1:

        material_set = (
            ifcopenshell.api.material.add_material_set(
                model,
                name=f"{product.Name} Materials",
                set_type="IfcMaterialConstituentSet",
            )
        )

        for entry in unique_materials:

            ifcopenshell.api.material.add_constituent(
                model,
                constituent_set=material_set,
                material=entry["material"],
                name=entry["name"],
            )

        ifcopenshell.api.material.assign_material(
            model,
            products=[product],
            material=material_set,
        )


# ---------------------------------------------------------------------------
# Export one Blender object
# ---------------------------------------------------------------------------

def export_mesh_object(
    model,
    obj,
    depsgraph,
    site,
    body_context,
    material_cache,
):

    # Use Blender's evaluated object.
    obj_eval = obj.evaluated_get(depsgraph)

    mesh = obj_eval.to_mesh()

    try:

        if (
            mesh is None
            or len(mesh.vertices) == 0
            or len(mesh.polygons) == 0
        ):
            return None

        mesh_items = split_mesh_by_material(
            obj_eval,
            mesh,
        )

        if not mesh_items:
            return None

        # ---------------------------------------------------
        # IFC product
        # ---------------------------------------------------

        product = ifcopenshell.api.root.create_entity(
            model,
            ifc_class=DEFAULT_IFC_CLASS,
            name=obj.name,
        )

        # ---------------------------------------------------
        # IFC geometry
        # ---------------------------------------------------

        representation = (
            ifcopenshell.api.geometry.add_mesh_representation(
                model,
                context=body_context,
                vertices=[
                    item["vertices"]
                    for item in mesh_items
                ],
                faces=[
                    item["faces"]
                    for item in mesh_items
                ],
            )
        )

        ifcopenshell.api.geometry.assign_representation(
            model,
            product=product,
            representation=representation,
        )

        # ---------------------------------------------------
        # Spatial containment
        # ---------------------------------------------------

        ifcopenshell.api.spatial.assign_container(
            model,
            products=[product],
            relating_structure=site,
        )

        # ---------------------------------------------------
        # Identity placement
        #
        # matrix_world was already baked into the mesh.
        # ---------------------------------------------------

        ifcopenshell.api.geometry.edit_object_placement(
            model,
            product=product,
        )

        # ---------------------------------------------------
        # Materials / appearance
        # ---------------------------------------------------

        assign_product_materials(
            model,
            product,
            representation,
            mesh_items,
            body_context,
            material_cache,
        )

        return product

    finally:

        if mesh is not None:
            obj_eval.to_mesh_clear()


# ---------------------------------------------------------------------------
# Blender collections -> IfcGroup
# ---------------------------------------------------------------------------

def walk_collections(root_collection):
    """
    Return each collection reachable from Scene Collection exactly once.
    """

    visited = set()
    stack = [root_collection]

    while stack:

        collection = stack.pop()

        key = collection.as_pointer()

        if key in visited:
            continue

        visited.add(key)

        yield collection

        stack.extend(
            reversed(list(collection.children))
        )


def create_collection_groups(
    model,
    root_collection,
):
    collections = list(
        walk_collections(root_collection)
    )

    groups = {}

    # -------------------------------------------------------
    # First create an IfcGroup per Blender collection.
    # -------------------------------------------------------

    for collection in collections:

        group = ifcopenshell.api.group.add_group(
            model,
            name=collection.name,
            description="Imported from Blender collection",
        )

        groups[
            collection.as_pointer()
        ] = group

    # -------------------------------------------------------
    # Reconstruct collection nesting.
    #
    # Parent IfcGroup
    #     -> child IfcGroup
    # -------------------------------------------------------

    for parent_collection in collections:

        parent_group = groups[
            parent_collection.as_pointer()
        ]

        for child_collection in parent_collection.children:

            child_group = groups[
                child_collection.as_pointer()
            ]

            ifcopenshell.api.group.assign_group(
                model,
                products=[child_group],
                group=parent_group,
            )

    return collections, groups


def assign_objects_to_collection_groups(
    model,
    collections,
    collection_groups,
    object_products,
):

    for collection in collections:

        products = []

        # collection.objects is intentionally used instead
        # of collection.all_objects.
        #
        # This preserves DIRECT collection membership.
        for obj in collection.objects:

            product = object_products.get(
                obj.as_pointer()
            )

            if product is not None:
                products.append(product)

        if not products:
            continue

        ifcopenshell.api.group.assign_group(
            model,
            products=products,
            group=collection_groups[
                collection.as_pointer()
            ],
        )


# ---------------------------------------------------------------------------
# Complete scene export
# ---------------------------------------------------------------------------

def export_scene(output_path: Path):

    scene = bpy.context.scene

    depsgraph = (
        bpy.context.evaluated_depsgraph_get()
    )

    model, site, body_context = (
        create_ifc_project(scene)
    )

    # -------------------------------------------------------
    # Collections
    # -------------------------------------------------------

    collections, collection_groups = (
        create_collection_groups(
            model,
            scene.collection,
        )
    )

    # -------------------------------------------------------
    # Objects
    # -------------------------------------------------------

    material_cache = {}
    object_products = {}

    skipped_objects = []

    for obj in scene.objects:

        # V1: mesh objects only.
        if obj.type != "MESH":
            continue

        product = export_mesh_object(
            model,
            obj,
            depsgraph,
            site,
            body_context,
            material_cache,
        )

        if product is None:
            skipped_objects.append(obj.name)
            continue

        object_products[
            obj.as_pointer()
        ] = product

    # -------------------------------------------------------
    # Collection membership
    # -------------------------------------------------------

    assign_objects_to_collection_groups(
        model,
        collections,
        collection_groups,
        object_products,
    )

    # -------------------------------------------------------
    # Write file
    # -------------------------------------------------------

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    model.write(str(output_path))

    # -------------------------------------------------------
    # Cheap sanity test:
    # can IfcOpenShell read its own output?
    # -------------------------------------------------------

    reopened = ifcopenshell.open(
        str(output_path)
    )

    print()
    print("=== Blender -> IFC V1 ===")
    print(f"Output:       {output_path}")
    print(f"Schema:       {reopened.schema}")
    print(f"Mesh objects: {len(object_products)}")
    print(f"Collections:  {len(collection_groups)}")
    print(f"Materials:    {len(material_cache)}")

    if skipped_objects:

        print("Skipped empty / face-less meshes:")

        for name in skipped_objects:
            print(f"  - {name}")

    print("Re-open check: OK")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():

    output_path = get_output_path()

    export_scene(output_path)


if __name__ == "__main__":
    main()