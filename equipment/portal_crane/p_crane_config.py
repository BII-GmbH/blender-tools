import bpy
import json
from pathlib import Path

from typing import Any


def json_parser() -> dict[str, Any]:
    blend_dir = Path(bpy.data.filepath).parent
    with (blend_dir/"config.json").open(encoding="utf-8") as f:
        parameters = json.load(f)
    return parameters


def set_geo_node_val(geo_node: Any, parameters: dict[str, Any]) -> None:
    for key, val in parameters.items():
        print(f"Setting value: {key} with value: {val}.")
        # Find geo socket
        socket = next(
            item for item in geo_node.node_group.interface.items_tree
            if item.item_type == 'SOCKET'
            and item.in_out == 'INPUT'
            and item.name == key
        )

        if hasattr(geo_node, "properties"):
            # 5.x.
            getattr(geo_node.properties.inputs, socket.identifier).value = val
        else:
            # 4.x.
            geo_node[socket.identifier] = val


def export_fbx(name: str) -> None:
    path = bpy.path.abspath(f"//{name}.fbx")
    bpy.ops.export_scene.fbx(
        filepath=path,
        use_selection=False,
        use_mesh_modifiers=True,  # Include the Geometry Nodes result
        bake_anim=False,  # Export static geometry
    )

def post_portal_crane_setup(parameters: dict[str, Any]):
    print("Setup trolley and anchor...")
    portal_crane = bpy.data.objects["Portal_Crane"]
    trolley = bpy.data.objects["Trolley"]
    anchor = bpy.data.objects["Anchor"]

    trolley.parent = portal_crane
    # move by config specification
    z = parameters["Dimensions"]["Height"] + parameters["Offset"]["Middle Beam Height"] - parameters["Optional"]["Base Beam Thickness"]
    trolley.location.z += z

    anchor.parent = trolley



def main() -> None:
    portal_crane = bpy.data.objects["Portal_Crane"]
    mod = portal_crane.modifiers["GeometryNodes"]
    pars = json_parser()
    print(f"Active object {portal_crane.name} with geo node {mod.node_group.name}")

    # Respect panels inside json
    for panel_name, panel_values in pars.items():
        set_geo_node_val(mod, panel_values)

    # recalc geo
    portal_crane.update_tag(refresh={'DATA'})
    bpy.context.view_layer.update()

    post_portal_crane_setup(pars)

    export_fbx("portal_crane")


if __name__ == '__main__':
    main()
