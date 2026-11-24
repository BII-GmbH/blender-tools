"""dProB's built-in Crane equipment type, implemented on top of a generated IK capability.

When a rig has a crane hook (an attachment point with an IK capability, set on the rig root), the
export adds the Crane type's three capabilities:

- Move Arm: a wrapper that makes the global Destination relative to the equipment (Relative To
  its current pose) and invokes the hook's IK capability with it and Position Only (orientation
  weight 0.001 as in dProB's crane). The callers' rotations do not fit the hook (Lift passes
  identity, Deliver the hook's rotation from the cargo offset), and a rotation the hook cannot
  take pulls it off the position.
- Lift and Deliver: dProB's own implementations, copied from a sample crane
  (crane_templates.json), with their attachment point replaced by the hook. They attach and
  detach the cargo and move the arm by invoking the Crane's Move Arm, so they use the wrapper.

Lift and Deliver keep the cargo in two equipment state variables, "Current Cargo" and
"Current Cargo Hook Offset"; the export adds them (same ids as in dProB's samples) when missing.

The Crane type's implementations replace any the file had; other equipment types are kept.
"""

import json
import os
import uuid

from . import ik_capability

CRANE_TYPE = {"Identifier": {"Id": "Crane"}, "$case": "BuiltIn"}
MOVE_ARM = "00000000-0000-8000-8000-000000000001"
LIFT = "00000000-0000-8000-8000-000000000002"
DELIVER = "00000000-0000-8000-8000-000000000003"
# the Crane type's Move Arm signature (built in): inputs Self, Destination (GlobalPose), Duration;
# output Processes
MOVE_ARM_OUTPUT = "e11ed511-9104-51d8-be5c-fdf6acd9f01b"
PROCESSES_KEY = "BII.ScriptableEquipment.EquipmentTypes.Crane.Processes"
CRANE_ORIENTATION_WEIGHT = 0.001

_TEMPLATES = os.path.join(os.path.dirname(__file__), "crane_templates.json")


def _templates(hook_point_id, hook_name):
    with open(_TEMPLATES, encoding="utf-8") as f:
        text = f.read()
    text = text.replace("{{HOOK_POINT_ID}}", hook_point_id).replace("{{HOOK_POINT_NAME}}", json.dumps(hook_name)[1:-1])
    return json.loads(text)


def _port(name):
    return ik_capability.port(name)


def move_arm_wrapper(equipment_id, capability):
    """Move Arm: the Destination relative to the equipment's current pose, then the IK
    capability with Position Only.

    The callers' rotations do not fit the hook: Lift passes identity, Deliver the hook's rotation
    from the cargo offset, a gizmo whatever it is turned to. A rotation the hook cannot take
    pulls it off the position (Lift missed by meters), so the crane moves the hook to the
    position only; it keeps its rotation, turned with the slewing."""
    sig = capability["Signature"]
    cap_id = sig["Id"]["Guid"]
    ns = uuid.uuid5(uuid.UUID(cap_id), "crane move arm")
    nid = lambda name: str(uuid.uuid5(ns, name))
    start, end = nid("start"), nid("end")
    call, rel, pose = nid("invoke"), nid("relative to"), nid("current pose")
    inputs = {(i["Name"].get("Text") or "Self"): i for i in sig["Inputs"]}
    out_id = sig["Outputs"][0]["Id"]["Guid"]
    self_in, dest_in, dur_in = _port("Self"), _port("Destination"), _port("Duration")
    link = lambda a, ap, b, bp: {"FromNode": {"Guid": a}, "OutputSlotId": {"Guid": ap},
                                 "ToNode": {"Guid": b}, "InputSlotId": {"Guid": bp}}
    data = [
        link(start, self_in, call, inputs["Self"]["Id"]["Guid"]),
        link(start, self_in, pose, _port("Subject")),
        link(pose, _port("Pose"), rel, _port("Reference")),
        link(start, dest_in, rel, _port("Global")),
        link(rel, _port("Local"), call, inputs["Destination"]["Id"]["Guid"]),
        link(start, dur_in, call, inputs["Duration"]["Id"]["Guid"]),
        link(call, out_id, end, MOVE_ARM_OUTPUT),
    ]
    # the IK settings as in Blender (the capability's defaults), but nearly position only, as dProB's
    # own crane (orientation weight 0.001): Lift passes identity rotations, Deliver the hook's
    # rotation from the cargo offset, a gizmo whatever it is turned to. Not 0: the small weight
    # keeps the hook level within each IK pass, so the pitch reversal loop converges
    fixed = [{"Node": {"Guid": call}, "InputSlotId": {"Guid": i["Id"]["Guid"]},
              "Value": {"Value": CRANE_ORIENTATION_WEIGHT, "$case": "Double"}
              if i["Name"].get("Text") == "Orientation Weight"
              else {"Value": True, "$case": "Bool"} if i["Name"].get("Text") == "Position Only"
              else i["DefaultValue"]}
             for i in sig["Inputs"] if i.get("DefaultValue") is not None]
    meta = lambda n, x, y: {"Node": {"Guid": n}, "Description": None, "Xpos": float(x), "Ypos": float(y)}
    graph = {
        "Outputs": [{"Id": {"Guid": MOVE_ARM_OUTPUT}, "Name": {"LocalizationKey": PROCESSES_KEY, "$case": "Localized"},
                     "Type": ik_capability.LIST_PROCESS, "Description": None, "IsOptional": False}],
        "LocalVariables": [],
        "Nodes": [
            {"Collection": {"EquipmentId": equipment_id, "$case": "Custom"}, "CapabilityId": {"Guid": cap_id},
             "Id": {"Guid": call}, "$case": "CapabilityInvocation"},
            {"Node": {"Id": {"Guid": rel}, "TGlobal": ik_capability.atomic("GlobalPose"),
                      "TLocal": ik_capability.atomic("LocalPose"), "$case": "RelativeToNode"}, "$case": "AtomicNode"},
            {"Node": {"Id": {"Guid": pose}, "$case": "ReadCurrentPoseNode"}, "$case": "AtomicNode"},
        ],
        "Frames": [],
        "FirstNodeId": {"Guid": start},
        "ExecutionConnections": [
            {"FromNode": {"Guid": start}, "SuccessionSlotId": {"Guid": ik_capability.SUCCESSOR}, "ToNode": {"Guid": call}},
            {"FromNode": {"Guid": call}, "SuccessionSlotId": {"Guid": ik_capability.SUCCESSOR}, "ToNode": {"Guid": end}},
        ],
        "DataConnections": data,
        "FixedInputs": fixed,
        "EditorMetadata": [meta(start, 0, 128), meta(pose, 320, 0), meta(rel, 640, 192), meta(call, 992, 160),
                           meta(end, 1408, 128)],
        "InputPortTypeConstraints": [],
        "OutputPortTypeConstraints": [],
        "InvalidExecutionConnections": [],
        "InvalidDataConnections": [],
        "ViewportData": {"PanX": 0.0, "PanY": 0.0, "Zoom": 1.0},
        "OutputSections": [],
        "VariableSections": [],
    }
    return {"CapabilityId": {"Guid": MOVE_ARM},
            "Implementation": {"EndNodeIds": [{"Guid": end}], "GraphData": graph, "LocalFunctions": [],
                               "IndicatorData": [], "ShowInButtonWheel": True}}


def apply(model, equipment_id, hook_element_id, hook_name, capability):
    """Adds the Crane type (Move Arm wrapper, Lift, Deliver) and its state variables to a model.json
    dict. capability: the hook's generated IK capability. Returns the names of the added variables."""
    t = _templates(ik_capability.attachment_point_id(hook_element_id), hook_name)
    crane = {"EquipmentType": CRANE_TYPE, "Implementations": [
        move_arm_wrapper(equipment_id, capability),
        t["Lift"], t["Deliver"]]}
    others = [e for e in model.get("EquipmentTypeImplementations") or []
              if e.get("EquipmentType") != CRANE_TYPE]
    model["EquipmentTypeImplementations"] = others + [crane]
    have = {v["Id"]["Guid"] for v in model.get("StateVariables") or []}
    added = [v for v in t["StateVariables"] if v["Id"]["Guid"] not in have]
    model["StateVariables"] = list(model.get("StateVariables") or []) + added
    return [v["Name"] for v in added]
