import bpy
import bmesh
from mathutils import Matrix, Vector

from typing import Any

ATTRIBUTE_NAME = "Slice ID"
DELETE_SOURCE = False


def create_part(source: Any, mesh: Any, part_id: int, collection: Any) -> None:
    """Create an object containing only vertices with the requested ID."""
    part_mesh = mesh.copy()
    part_mesh.name = f"{source.name}_{part_id}"

    bm = bmesh.new()

    try:
        bm.from_mesh(part_mesh)
        id_layer = bm.verts.layers.int.get(ATTRIBUTE_NAME)

        vertices_to_remove = [
            vertex for vertex in bm.verts
            if vertex[id_layer] != part_id
        ]

        bmesh.ops.delete(bm, geom=vertices_to_remove, context='VERTS')
        bm.to_mesh(part_mesh)
    finally:
        bm.free()

    if not part_mesh.vertices:
        bpy.data.meshes.remove(part_mesh)
        return

    # Calculate the center AFTER removing vertices belonging to other IDs.
    center = sum(
        (vertex.co for vertex in part_mesh.vertices),
        Vector((0.0, 0.0, 0.0)),
    ) / len(part_mesh.vertices)

    # Center the mesh around its new origin.
    part_mesh.transform(Matrix.Translation(-center))
    part_mesh.update()

    part = bpy.data.objects.new(part_mesh.name, part_mesh)
    collection.objects.link(part)

    # Keep the geometry in its original world position.
    part.matrix_world = (
        source.matrix_world @ Matrix.Translation(center)
    )

    print(f"Created {part.name} for ID {part_id}")


def main() -> None:
    source = bpy.context.active_object

    if source is None or source.type not in {'MESH', 'CURVE'}:
        raise ValueError("Select a mesh or curve object first.")

    if source.mode != 'OBJECT':
        raise ValueError("Switch to Object Mode before running this script.")

    # Include Geometry Nodes and other modifiers in the resulting geometry.
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = source.evaluated_get(depsgraph)

    mesh = bpy.data.meshes.new_from_object(
        evaluated,
        preserve_all_data_layers=True,
        depsgraph=depsgraph,
    )

    try:
        attribute = mesh.attributes.get(ATTRIBUTE_NAME)

        if attribute is None:
            raise ValueError(f"Attribute '{ATTRIBUTE_NAME}' was not found.")

        if attribute.domain != 'POINT' or attribute.data_type != 'INT':
            raise ValueError(
                f"Attribute '{ATTRIBUTE_NAME}' must be Integer / Point. "
                f"Found {attribute.data_type} / {attribute.domain}."
            )

        part_ids = sorted({item.value for item in attribute.data})

        collection = (
            source.users_collection[0]
            if source.users_collection
            else bpy.context.collection
        )

        for part_id in part_ids:
            create_part(source, mesh, part_id, collection)

        print(f"Finished: created {len(part_ids)} objects.")

    finally:
        bpy.data.meshes.remove(mesh)

    # Delete the source only after successful completion.
    if DELETE_SOURCE:
        bpy.data.objects.remove(source, do_unlink=True)

if __name__ == "__main__":
    main()