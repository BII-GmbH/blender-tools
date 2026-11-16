import re
import bpy

from typing import Any

COLLECTION_NAME = "BE_Flächen"
NODE_NAME = "BE Area"
STORED_NAMED_ATTRIBUTE = "BE"


def apply_geo_node_modifier(obj: Any, node: str) -> None:
    matches = [
        mod for mod in obj.modifiers
        if mod.type == "NODES"
           and mod.node_group is not None
           and mod.node_group.name == node
    ]
    if not matches:
        raise ValueError(f"Object {obj.name} does not use a modifier with the given node {node}!")
    if len(matches) > 1:
        raise ValueError(f"Object {obj.name} has multiple modifiers using the node {node}!")

    print(f"Applying Geometry Node modifier {node} on object {obj.name}")
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=matches[0].name)


def get_stored_named_attribute(obj: Any, name: str) -> Any:
    attribute = obj.data.attributes.get(name)
    if attribute is None or len(attribute.data) == 0:
        raise ValueError(f"Stored named attribute {name} has no value.")
    return attribute.data[0].value


def set_name(obj: Any, value: int) -> None:
    suffix = int(value)
    name = re.sub(r"\.\d{3,}$", "", obj.name)
    print(f"Apply suffix {suffix} to obj {obj.name}")
    obj.name = f"{name}_{suffix}"


def main() -> None:
    collection = bpy.data.collections.get(COLLECTION_NAME)
    if collection is None:
        raise ValueError(f"Collection {COLLECTION_NAME} not found.")

    for obj in collection.objects:
        apply_geo_node_modifier(obj, NODE_NAME)
        be_area_num = get_stored_named_attribute(obj, STORED_NAMED_ATTRIBUTE)
        set_name(obj, be_area_num)


if __name__ == "__main__":
    main()
