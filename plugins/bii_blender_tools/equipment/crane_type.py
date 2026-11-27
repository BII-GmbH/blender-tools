"""dProB's built-in Crane equipment type, implemented on top of a generated IK capability.

When a rig has a crane hook (an attachment point with an IK capability, set on the rig root), the
export adds the Crane type's three capabilities:

- Move Arm: a wrapper that makes the global Destination relative to the equipment (Relative To
  its current pose) and invokes the hook's IK capability with it (orientation weight 0.001 as in
  dProB's crane). The hook's frame has the equipment's axes at rest (rig_api.align_to_equipment),
  so the rotations Lift and Deliver pass, with dProB's up as up, are ones the hook can take: it
  turns about up (hook swivel or slewing) to the cargo's rotation.
- Lift and Deliver: dProB's own implementations from a sample crane (sample_graphs.py), with
  their attachment point replaced by the hook. They attach and
  detach the cargo and move the arm by invoking the Crane's Move Arm, so they use the wrapper.
  Lift without a hook offset puts the hook above the cargo turned like the cargo (the sample
  used identity), so Deliver later turns the cargo as asked and Move Arm keeps the hook's heading.

Lift and Deliver keep the cargo in two equipment state variables, "Current Cargo" and
"Current Cargo Hook Offset"; the export adds them (same ids as in dProB's samples).
"""

import uuid

from . import ik_capability, sample_graphs

CRANE_TYPE = {"Identifier": {"Id": "Crane"}, "$case": "BuiltIn"}
MOVE_ARM, LIFT, DELIVER = sample_graphs.MOVE_ARM, sample_graphs.LIFT, sample_graphs.DELIVER
# the Crane type's Move Arm signature (built in): inputs Self, Destination (GlobalPose), Duration;
# output Processes
MOVE_ARM_OUTPUT = sample_graphs.PROCESSES_OUTPUT
PROCESSES_KEY = "BII.ScriptableEquipment.EquipmentTypes.Crane.Processes"
CRANE_ORIENTATION_WEIGHT = 0.001

def _port(name):
    return ik_capability.port(name)


def move_arm_wrapper(equipment_id, capability):
    """Move Arm: the Destination relative to the equipment's current pose, then the IK
    capability with it, orientation weight 0.001 as dProB's own crane. A capability with Global
    Destination gets the Destination as it is (it makes it relative itself)."""
    sig = capability["Signature"]
    cap_id = sig["Id"]["Guid"]
    ns = uuid.uuid5(uuid.UUID(cap_id), "crane move arm")
    nid = lambda name: str(uuid.uuid5(ns, name))
    start, end = nid("start"), nid("end")
    call, rel, pose, dur = nid("invoke"), nid("relative to"), nid("current pose"), nid("duration")
    inputs = {(i["Name"].get("Text") or "Self"): i for i in sig["Inputs"]}
    out_id = sig["Outputs"][0]["Id"]["Guid"]
    self_in, dest_in, dur_in = _port("Self"), _port("Destination"), _port("Duration")
    is_global = inputs["Destination"]["Type"] == ik_capability.atomic("GlobalPose")
    link = lambda a, ap, b, bp: {"FromNode": {"Guid": a}, "OutputSlotId": {"Guid": ap},
                                 "ToNode": {"Guid": b}, "InputSlotId": {"Guid": bp}}
    data = [
        link(start, self_in, call, inputs["Self"]["Id"]["Guid"]),
        link(dur, ik_capability.port("Value"), call, inputs["Duration"]["Id"]["Guid"]),
        link(call, out_id, end, MOVE_ARM_OUTPUT),
    ]
    if is_global:
        data.append(link(start, dest_in, call, inputs["Destination"]["Id"]["Guid"]))
    else:
        data += [
            link(start, self_in, pose, _port("Subject")),
            link(pose, _port("Pose"), rel, _port("Reference")),
            link(start, dest_in, rel, _port("Global")),
            link(rel, _port("Local"), call, inputs["Destination"]["Id"]["Guid"]),
        ]
    # the IK settings as in Blender (the capability's defaults), with the orientation weight of
    # dProB's own crane (0.001): the position counts far more than the rotation. Not 0: the small
    # weight turns the hook to the cargo's heading and keeps it level within each IK pass, so the
    # pitch reversal loop converges
    fixed = [{"Node": {"Guid": call}, "InputSlotId": {"Guid": i["Id"]["Guid"]},
              "Value": {"Value": CRANE_ORIENTATION_WEIGHT, "$case": "Double"}
              if i["Name"].get("Text") == "Orientation Weight"
              else i["DefaultValue"]}
             for i in sig["Inputs"] if i.get("DefaultValue") is not None]
    meta = lambda n, x, y: {"Node": {"Guid": n}, "Description": None, "Xpos": 32.0 * x, "Ypos": 32.0 * y}
    nodes = [{"Collection": {"EquipmentId": equipment_id, "$case": "Custom"}, "CapabilityId": {"Guid": cap_id},
              "Id": {"Guid": call}, "$case": "CapabilityInvocation"},
             {"InputSlotId": {"Guid": dur_in}, "DisplayName": "Duration", "FromOuterContext": False,
              "IsOptional": False, "Id": {"Guid": dur}, "$case": "InputGetter"}]
    # positions in cells as the user arranged them in dProB; a getter's x is its right edge
    metadata = [meta(start, 0, 4), meta(call, 30, 4), meta(dur, 29, 8), meta(end, 43, 4)]
    if not is_global:
        nodes += [
            {"Node": {"Id": {"Guid": rel}, "TGlobal": ik_capability.atomic("GlobalPose"),
                      "TLocal": ik_capability.atomic("LocalPose"), "$case": "RelativeToNode"}, "$case": "AtomicNode"},
            {"Node": {"Id": {"Guid": pose}, "$case": "ReadCurrentPoseNode"}, "$case": "AtomicNode"},
        ]
        metadata += [meta(pose, 9, 8), meta(rel, 18, 6)]
    graph = {
        "Outputs": [{"Id": {"Guid": MOVE_ARM_OUTPUT}, "Name": {"LocalizationKey": PROCESSES_KEY, "$case": "Localized"},
                     "Type": ik_capability.LIST_PROCESS, "Description": None, "IsOptional": False}],
        "LocalVariables": [],
        "Nodes": nodes,
        "Frames": [],
        "FirstNodeId": {"Guid": start},
        "ExecutionConnections": [
            {"FromNode": {"Guid": start}, "SuccessionSlotId": {"Guid": ik_capability.SUCCESSOR}, "ToNode": {"Guid": call}},
            {"FromNode": {"Guid": call}, "SuccessionSlotId": {"Guid": ik_capability.SUCCESSOR}, "ToNode": {"Guid": end}},
        ],
        "DataConnections": data,
        "FixedInputs": fixed,
        "EditorMetadata": metadata,
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
    dict. capability: the hook's generated IK capability. Returns the names of the state variables."""
    crane = {"EquipmentType": CRANE_TYPE, "Implementations": [
        move_arm_wrapper(equipment_id, capability),
        sample_graphs.lift(ik_capability.attachment_point_id(hook_element_id), hook_name),
        sample_graphs.deliver()]}
    model["EquipmentTypeImplementations"] = [crane]
    model["StateVariables"] = sample_graphs.state_variables()
    return [v["Name"] for v in model["StateVariables"]]
