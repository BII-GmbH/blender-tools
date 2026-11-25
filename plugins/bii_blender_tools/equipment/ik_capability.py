"""Generated dProB custom capabilities that move a part to a pose with inverse kinematics.

For every rig object marked as IK target the export writes one custom capability:

    inputs:  Self (the equipment), Destination (LocalPose of the part, relative to the
             equipment; with Global Destination a GlobalPose in the world, which the graph makes
             relative to the equipment's current pose as the Crane's Move Arm does), Duration,
             and the IK settings with the values from Blender as defaults:
             Iterations, Orientation Weight, Start Away from Limits, Passes (with couplings)
    output:  Processes (one Move Revolute/Prismatic/Ball Joint process per moved joint, all with
             the full duration, so they run together)

The graph follows dProB's built-in Crane "Move Arm": the Inverse Kinematics node computes the
joint values. When joints of the rig follow others or hold their value (Drive), the IK node cannot
know that, so the graph runs it in a Loop Range and sets those joints after every pass, the next
pass starting from the result. Without such joints one IK pass is enough.

The graph reads everything about the rig from the rig: joint limits with Get Revolute / Prismatic
Joint Constraints, the target's offset from its joint with the attachment point's Get Local Pose.
Only the couplings (factor, offset) are written as numbers; they exist only in Blender.

The first pass starts from the current joint values, each limited joint clamped to at least
"Start Away from Limits" of its range inside its limits (Lerp and Clamp macros). Joints resting
at a limit often stall the solver: a boom lying flat on its rest cannot get closer to the crane by
rising, to first order, so the solver never raises it.

Node positions come from layout(): execution nodes on straight rows (the loop's Done on its own
row below the Body), each one's data providers as a tree to its left and below, getters copied
next to each consumer instead of long wires, named frames around the sections.

Ids: node ids are UUIDv5 of the capability id and a name, so a re-export writes the same graph.
Built-in port ids are UUIDv5 (ISO OID namespace) of the port name, as dProB's PortIds.cs makes
them. Joint and attachment point ids are the element id with bit 0 / bit 1 flipped
(HierarchyElementId.AsRigJointId / AsAttachmentPointId).

simulate() runs such a graph with the ported IK node, for tests and for checking a file.
"""

import math
import uuid

import numpy as np

from . import dprob_math, ik_solver

ISO_OID = uuid.UUID("6ba7b812-9dad-11d1-80b4-00c04fd430c8")
GRID = 32                     # NodeEditorUiConstants.GridSize: editor positions are pixels

# built-in macros (StreamingAssets/BuiltInTemplateMacros)
LOOP_RANGE_MACRO = "00000000-0000-0000-0000-000000000004"
LOOP_FIRST = "a0eb0f6b-e649-50e9-9df9-14cc3af9e2d1"
LOOP_LAST = "cf487882-15c2-5b51-9fef-0a4f9e7cd91c"
LOOP_INCLUDE_LAST = "0c8bcf55-9cf9-5273-8de1-e03581b941e7"
LOOP_BODY = "53b0517f-b60b-5e88-8309-269bbbe3b687"
LOOP_DONE = "6ac3086e-e20c-5188-b3dc-ff35fc9d293b"
CLAMP_MACRO = "e803ffbf-7cfb-46e9-9909-d0e3ad38a4f3"
CLAMP_VALUE = "5bc35045-e0c1-5cde-9076-0bfcc5320ed1"
CLAMP_MIN = "dc71aa6b-8e00-5ef7-a0ef-6bb3f42e1079"
CLAMP_MAX = "a8b636ab-ed25-5607-b91e-9b2cfe773add"
LERP_MACRO = "00000000-0000-0000-0000-000000000003"
LERP_FROM = "d2aea092-6084-5cb8-a028-287fe515e0bd"
LERP_TO = "15cd53df-2be8-5fd3-b7ef-e72d5cabea77"
LERP_T = "e48a7f06-9b91-554e-b2d1-9b3b5c8107c1"
MACRO_RESULT = "95b7396c-8fca-55f4-8347-f0ec331ef218"
FOR_EACH_DICT_MACRO = "0a54f069-b33e-481a-9d03-cbb12d1fdf10"
EFFECTIVE_POSE_MACRO = "08c4429e-dad4-4e98-ae24-75c234ffbc3d"     # Get Current Effective Joint Pose
EFFECTIVE_POSE_JOINT = "fbb47952-bf57-485c-a325-f39d74d72214"
EFFECTIVE_POSE_RESULT = "16ac8965-3220-4834-9aca-1051452f0217"
SIGNED_ANGLE_MACRO = "6c4ce629-93d1-4e7d-bdbd-f7c919ff7fe0"       # Signed Angle on Plane
SIGNED_ANGLE_A = "8482b9be-7643-5b1c-9a02-8fc4a9cec74f"
SIGNED_ANGLE_B = "3b1e9d69-dfbf-5b1d-942a-cbafcca343b7"
SIGNED_ANGLE_AXIS = "64dc8b12-48b0-5e4f-a41d-c9b4f2e08352"
SIGNED_ANGLE_RESULT = "6bd1ba06-afbc-52d7-86f1-b62c08f2be6f"
FOR_EACH_DICT = "bf161b15-63dc-5ca9-918a-fc601c76b43c"
FOR_EACH_KEY = "43ec82d8-c84d-510f-8e59-5e8b7f57a5de"
FOR_EACH_VALUE = "3af21c62-deb4-4956-98f7-580eb3bb1a9e"
FOR_EACH_INDEX = "c81b083a-0a0b-43fd-9f63-629e942f8f18"

SELF_NAME_KEY = "BII.ScriptableTemplates.Capabilities.SelfInput"
SELF_DESCRIPTION_KEY = "BII.ScriptableTemplates.Capabilities.SelfInputDescription"
GENERATED_MARK = "[BII Tools IK]"

with open(__import__("os").path.join(__import__("os").path.dirname(__file__), "rotate_joint_function.json"),
          encoding="utf-8") as _f:
    ROTATE_JOINT = __import__("json").load(_f)       # LocalFunctionAssetModel from dProB's sample crane
ROTATE_JOINT_JOINT, ROTATE_JOINT_ANGLE, ROTATE_JOINT_DURATION = (i["Id"]["Guid"] for i in ROTATE_JOINT["Inputs"])
ROTATE_JOINT_PROCESS = ROTATE_JOINT["GraphData"]["Outputs"][0]["Id"]["Guid"]


def port(name):
    return str(uuid.uuid5(ISO_OID, name))


SUCCESSOR = port("Successor")


def flip_bit(element_id, index):
    """HierarchyElementId.flipBitAt: .NET Guid byte order (little-endian first fields)."""
    b = bytearray(uuid.UUID(element_id).bytes_le)
    b[index // 8] ^= 1 << (index % 8)
    return str(uuid.UUID(bytes_le=bytes(b)))


def joint_id(element_id):
    return flip_bit(element_id, 0)


def attachment_point_id(element_id):
    return flip_bit(element_id, 1)


def atomic(name):
    return {"Type": name, "$case": "Atomic"}


def dict_type(value):
    return {"KeyType": atomic("RigJoint"), "ValueType": atomic(value), "$case": "Dictionary"}


LIST_PROCESS = {"ElementType": atomic("Process"), "$case": "List"}
VALUE_TYPE = {"REVOLUTE": "Angle", "PRISMATIC": "Length", "BALL": "LocalRotation"}
DICT_VAR = {"REVOLUTE": "Revolute Values", "PRISMATIC": "Prismatic Values", "BALL": "Ball Joint Values"}
INITIAL = {"REVOLUTE": "Initial Revolute Values", "PRISMATIC": "Initial Prismatic Values",
           "BALL": "Initial Ball Joint Values"}
MOVE = {"REVOLUTE": ("MoveRevoluteJointNode", "Move Revolute Joint", "Target Angle"),
        "PRISMATIC": ("MovePrismaticJointNode", "Move Prismatic Joint", "Target Offset"),
        "BALL": ("MoveBallJointNode", "Move Ball Joint", "Target Rotation")}
READ_CURRENT = {"REVOLUTE": ("ReadCurrentRevoluteJointStateNode", "Current Revolute Joint State", "Angle"),
                "PRISMATIC": ("ReadCurrentPrismaticJointStateNode", "Current Prismatic Joint State", "Length")}
CONSTRAINTS = {"REVOLUTE": ("GetRevoluteJointConstraintsNode", "Get Revolute Joint Constraints",
                            ["Has Limits", "Min Angle", "Max Angle", "Axis"]),
               "PRISMATIC": ("GetPrismaticJointConstraintsNode", "Get Prismatic Joint Constraints",
                             ["Min Offset", "Max Offset", "Axis"])}
IK_INPUTS = ["End Effector", "Destination", "Root Joint", "Time", "Initial Prismatic Values",
             "Initial Revolute Values", "Initial Ball Joint Values", "Iterations", "Orientation Weight"]
IK_OUTPUTS = ["Revolute Values", "Prismatic Values", "Ball Joint Values"]


def unit_value(kind, si):
    case = "Angle" if kind == "REVOLUTE" else "Length"
    return {"Value": {"SiValue": float(si), "$case": case}, "$case": "ValueWithUnit"}


# ------------------------------------------------------------------ graph building

class Node:
    """One graph node with what the layout needs: title, port rows, execution ports, style."""

    def __init__(self, nid, json_, title, ins, outs, exec_in, exec_outs, style, section):
        self.id, self.json, self.title = nid, json_, title
        self.ins, self.outs = list(ins), list(outs)          # port ids in drawn row order
        self.exec_in, self.exec_outs = exec_in, list(exec_outs)
        self.style = style                                      # "node", "collapsed", "getter"
        self.section = section
        self.x = self.y = None

    @property
    def width(self):
        """Estimated size in grid cells (layout spacing absorbs the error)."""
        if self.style == "getter":
            return max(4, math.ceil(len(self.title) * 0.33) + 2)
        if self.style == "collapsed":
            return 4
        return max(7, math.ceil(len(self.title) * 0.36) + 3)

    @property
    def height(self):
        rows = max(len(self.ins) + (1 if self.exec_in else 0), len(self.outs) + len(self.exec_outs), 1)
        if self.style == "getter":
            return 1
        if self.style == "collapsed":
            return rows
        return rows + 2

    def in_row(self, port_id):
        """Row offset (cells from the node's top) of an input port."""
        i = self.ins.index(port_id) if port_id in self.ins else 0
        top = 0 if self.style != "node" else 1.5
        return top + i + (1 if self.exec_in else 0)


class Graph:
    """Builds ScriptGraphAssetDataModel JSON."""

    def __init__(self, cap_id):
        self.ns = uuid.UUID(cap_id)
        self.nodes = {}             # id -> Node, insertion ordered
        self.exec, self.data, self.fixed = [], [], []
        self.constraints = []       # InputUserConstraintModel: types of unwired generic ports
        self.section = None
        self._count = 0

    def id(self, name):
        return str(uuid.uuid5(self.ns, name))

    def add(self, name, body, title, ins=(), outs=(), exec_in=False, exec_outs=(), style="node",
            atomic_node=True):
        nid = self.id(name)
        if nid in self.nodes:
            raise ValueError(f"duplicate node {name}")
        json_ = ({"Node": {"Id": {"Guid": nid}, **body}, "$case": "AtomicNode"} if atomic_node
                 else {**body, "Id": {"Guid": nid}})
        self.nodes[nid] = Node(nid, json_, title, ins, outs, exec_in, exec_outs, style, self.section)
        return nid

    def unique(self, name):
        self._count += 1
        return f"{name} #{self._count}"

    def link(self, src, out_port, dst, in_port):
        self.data.append({"FromNode": {"Guid": src}, "OutputSlotId": {"Guid": out_port},
                          "ToNode": {"Guid": dst}, "InputSlotId": {"Guid": in_port}})

    def then(self, src, dst, slot=SUCCESSOR):
        self.exec.append({"FromNode": {"Guid": src}, "SuccessionSlotId": {"Guid": slot},
                          "ToNode": {"Guid": dst}})

    def set(self, node, in_port, value, type_=None):
        """A fixed input value. type_: the port's type when the node is generic or overloaded (Mul,
        Add): the editor takes such a port's type from its wire, so an unwired port needs a type
        constraint, else it shows "Set Type..." and drops the value."""
        self.fixed.append({"Node": {"Guid": node}, "InputSlotId": {"Guid": in_port}, "Value": value})
        if type_ is not None:
            self.constraints.append({"Node": {"Guid": node}, "InputSlotId": {"Guid": in_port}, "Type": type_})

    # ---- common nodes; getters are made per use ("local getters")
    def joint(self, eid, name):
        return self.add(self.unique(f"joint {eid}"), {"JointId": joint_id(eid), "CachedJointName": name,
                                                      "$case": "JointGetter"},
                        f"Joint {name}", outs=[port("Joint")], style="getter", atomic_node=False), port("Joint")

    def attachment_point(self, eid, name):
        return self.add(self.unique(f"point {eid}"), {"AttachmentPointId": attachment_point_id(eid),
                                                      "CachedPointName": name, "$case": "AttachmentPointGetter"},
                        name, outs=[port("AttachmentPoint")], style="getter",
                        atomic_node=False), port("AttachmentPoint")

    def input(self, slot, display):
        return self.add(self.unique(f"input {display}"), {"InputSlotId": {"Guid": slot}, "DisplayName": display,
                                                          "FromOuterContext": False, "IsOptional": False,
                                                          "$case": "InputGetter"},
                        display, outs=[port("Value")], style="getter", atomic_node=False), port("Value")

    def var(self, var_id, var_name):
        return self.add(self.unique(f"get {var_name}"), {"VarName": var_name, "VarId": {"Guid": var_id},
                                                         "FromOuterContext": False, "$case": "DeferredVarGetter"},
                        var_name, outs=[port("Value")], style="getter", atomic_node=False), port("Value")

    def var_set(self, var_id, var_name):
        return self.add(self.unique(f"set {var_name}"), {"VarName": var_name, "VarId": {"Guid": var_id},
                                                         "FromOuterContext": False, "$case": "VarSetter"},
                        f"Set {var_name}", ins=[port("ValueIn")], outs=[port("Value")], exec_in=True,
                        exec_outs=[SUCCESSOR], atomic_node=False)

    def binary(self, case, title, ta, tb, tres, out="Result"):
        return self.add(self.unique(case), {"TA": ta, "TB": tb, "TRes": tres, "$case": case}, title,
                        ins=[port("A"), port("B")], outs=[port(out)], style="collapsed")

    def macro(self, macro_id, title, ins, out, type_):
        return self.add(self.unique(title), {"MacroId": macro_id, "Name": title,
                                             "ResolvedInputTypes": [{"InputId": {"Guid": i}, "Type": type_} for i in ins],
                                             "ResolvedOutputTypes": [{"OutputId": {"Guid": out}, "Type": type_}],
                                             "$case": "GlobalMacro"},
                        title, ins=ins, outs=[out], style="collapsed", atomic_node=False)

    def get_entry(self, kind):
        vt = atomic(VALUE_TYPE[kind])
        return self.add(self.unique("get entry"), {"TKey": atomic("RigJoint"), "TValue": vt,
                                                   "$case": "GetDictEntryNode"},
                        "Get Value", ins=[port("Dictionary"), port("Key")], outs=[port("Value")], style="collapsed")

    def set_entry(self, kind):
        vt = atomic(VALUE_TYPE[kind])
        return self.add(self.unique("set entry"), {"TKey": atomic("RigJoint"), "TValue": vt,
                                                   "$case": "SetDictEntryNode"},
                        "Set Value", ins=[port("Dictionary"), port("Key"), port("Value")], outs=[port("Result")],
                        style="collapsed")

    def empty_dict(self, kind):
        return self.add(self.unique("empty dict"), {"TKey": atomic("RigJoint"), "TValue": atomic(VALUE_TYPE[kind]),
                                                    "$case": "EmptyDictionaryNode"},
                        "Empty Dictionary", outs=[port("Dictionary")], style="collapsed"), port("Dictionary")

    def read_current(self, eid, name, kind):
        case, title, out = READ_CURRENT[kind]
        n = self.add(self.unique(f"current {eid}"), {"$case": case}, title,
                     ins=[port("Joint"), port("Time")], outs=[port(out), port("Result")])
        self.link(*self.joint(eid, name), n, port("Joint"))
        return n, port(out)


def _input_port(port_id, name, type_, description=None, localized=False, default=None):
    text = ({"LocalizationKey": name, "$case": "Localized"} if localized else {"Text": name, "$case": "Plain"})
    desc = None
    if description:
        desc = ({"LocalizationKey": description, "$case": "Localized"} if localized
                else {"Text": description, "$case": "Plain"})
    return {"Id": {"Guid": port_id}, "Name": text, "Type": type_, "DefaultValue": default,
            "Description": desc, "IsOptional": False}


def build(spec):
    """Custom capability JSON (CustomCapabilityAssetModel) for an IK target (see build_graph)."""
    return build_graph(spec)[1]


def build_graph(spec):
    """(Graph with the laid out nodes, custom capability JSON (CustomCapabilityAssetModel)) for an IK
    target.

    spec: dict with
      id, name, description: the capability
      target: (element id, name) of the part the capability moves
      end: (element id, name) of the IK's end effector joint (the target, or the joint it sits on)
      attachment_point: True when the target is a fixed attachment point on the end joint
      root: (element id, name) of the IK root joint or None
      iterations, loops, orientation_weight, seed_margin: defaults of the capability's inputs
      global_destination: the Destination is a GlobalPose (made relative to Self's current pose)
      joints: {element id: (name, kind)} of every joint involved
      chain: [element id] of the IK chain (root first)
      seeds: [element id] of the chain joints that start away from their limits
      aim: None or {"joint": element id, "path": [element ids from the top to it]}: an unlimited
        joint (slewing) that starts turned so the target part points at the Destination
      end_path: [element id] of the joints from the top down to the end joint
      couplings: [(element id, "HOLD"|"FOLLOW", [(leader element id, factor)], offset SI)]
      moved: [element id] of the joints to move (chain without Hold, and Follow joints)
    """
    cap_id = spec["id"]
    g = Graph(cap_id)
    joints = spec["joints"]
    chain = set(spec["chain"])
    coupled = bool(spec["couplings"])
    seeded = bool(spec["seeds"])
    kinds_in_chain = {joints[e][1] for e in spec["chain"]}

    # ---- signature
    ins = {"Destination": g.id("input Destination"), "Duration": g.id("input Duration"),
           "Iterations": g.id("input Iterations"), "Orientation Weight": g.id("input Orientation Weight"),
           "Start Away from Limits": g.id("input Start Away from Limits"), "Passes": g.id("input Passes"),
           "Position Only": g.id("input Position Only")}
    inputs = [
        _input_port(port("Self"), SELF_NAME_KEY, atomic("Subject"), SELF_DESCRIPTION_KEY, localized=True),
        _input_port(ins["Destination"], "Destination", atomic("GlobalPose"),
                    f"Pose of {spec['target'][1]} in the world")
        if spec.get("global_destination") else
        _input_port(ins["Destination"], "Destination", atomic("LocalPose"),
                    f"Pose of {spec['target'][1]} relative to the equipment"),
        _input_port(ins["Duration"], "Duration", atomic("Duration"), "Duration of the movement"),
        _input_port(ins["Iterations"], "Iterations", atomic("Int"), "Steps of the IK solver per pass",
                    default={"Value": int(spec["iterations"]), "$case": "Int"}),
        _input_port(ins["Orientation Weight"], "Orientation Weight", atomic("Double"),
                    "How much the orientation counts against the position (orientation error in degrees)",
                    default={"Value": float(spec["orientation_weight"]), "$case": "Double"}),
    ]
    if seeded:
        inputs.append(_input_port(ins["Start Away from Limits"], "Start Away from Limits", atomic("Double"),
                                  "The IK starts with the limited joints at least this share of their range "
                                  "inside their limits", default={"Value": float(spec["seed_margin"]), "$case": "Double"}))
    inputs.append(_input_port(ins["Position Only"], "Position Only", atomic("Boolean"),
                              "Ignore the Destination's rotation: the part keeps its rotation, turned "
                              "with the slewing towards the Destination", default={"Value": False, "$case": "Bool"}))
    if coupled:
        inputs.append(_input_port(ins["Passes"], "Passes", atomic("Int"),
                                  "IK passes; the following and held joints are set after each",
                                  default={"Value": int(spec["loops"]), "$case": "Int"}))
    out_id = g.id("output Processes")
    outputs = [{"Id": {"Guid": out_id}, "Name": {"Text": "Processes", "$case": "Plain"}, "Type": LIST_PROCESS,
                "Description": None, "IsOptional": False}]

    start, end = g.id("start"), g.id("end")
    g.nodes[start] = Node(start, None, "Start", [], [i["Id"]["Guid"] for i in inputs], False, [SUCCESSOR],
                          "node", None)

    # ---- variables
    variables, var_ids = [], {}

    def variable(key, name, type_):
        vid = g.id(f"var {key}")
        var_ids[key] = (vid, name)
        variables.append({"Name": name, "Id": {"Guid": vid}, "Type": type_, "InitialValue": None, "Description": None})

    if coupled:
        for kind in ("REVOLUTE", "PRISMATIC", "BALL"):
            if kind != "BALL" or "BALL" in kinds_in_chain:
                variable(kind, DICT_VAR[kind], dict_type(VALUE_TYPE[kind]))
    variable("PROCESSES", "Processes", LIST_PROCESS)

    exec_chain = [start]          # main row, in order

    def then(node, slot=SUCCESSOR, src=None):
        g.then(src or exec_chain[-1], node, slot)
        exec_chain.append(node)

    # ---- start value of the aim joint (slewing): turned so the target part points at the target
    def frame_of(path, name):
        """Current pose of the last joint of path in the equipment (product of the joints'
        current effective poses, top first)."""
        pose = None
        for eid in path:
            ep = g.macro(EFFECTIVE_POSE_MACRO, "Get Current Effective Joint Pose", [EFFECTIVE_POSE_JOINT],
                         EFFECTIVE_POSE_RESULT, atomic("LocalPose"))
            g.nodes[ep].json["ResolvedInputTypes"] = []
            g.nodes[ep].json["ResolvedOutputTypes"] = []
            g.nodes[ep].style = "node"
            g.link(*g.joint(eid, joints[eid][0]), ep, EFFECTIVE_POSE_JOINT)
            if pose is None:
                pose = (ep, EFFECTIVE_POSE_RESULT)
            else:
                lpt = atomic("LocalPose")
                m = g.binary("MulNode", "Multiply", lpt, lpt, lpt)
                g.link(*pose, m, port("A"))
                g.link(ep, EFFECTIVE_POSE_RESULT, m, port("B"))
                pose = (m, port("Result"))
        return pose

    def unpack_local(src, name):
        n = g.add(g.unique(name), {"TPos": atomic("LocalPosition"), "TRot": atomic("LocalRotation"),
                                   "TDir": atomic("LocalDirection"), "TPose": atomic("LocalPose"),
                                   "$case": "UnpackPoseNode"}, "Unpack Pose", ins=[port("Pose")],
                  outs=[port("Position"), port("Rotation"), port("Forward")], style="collapsed")
        g.link(*src, n, port("Pose"))
        return n

    memo = {}

    def destination():
        """The Destination relative to the equipment: the input, or with Global Destination the
        input relative to the equipment's current pose (one Relative To node for all uses)."""
        if not spec.get("global_destination"):
            return g.input(ins["Destination"], "Destination")
        if "destination" not in memo:
            pose = g.add("current pose", {"$case": "ReadCurrentPoseNode"}, "Get Current Pose",
                         ins=[port("Subject")], outs=[port("Pose"), port("Position"), port("Rotation")])
            g.link(*g.input(port("Self"), "Self"), pose, port("Subject"))
            rel = g.add("relative to", {"TGlobal": atomic("GlobalPose"), "TLocal": atomic("LocalPose"),
                                        "$case": "RelativeToNode"}, "Relative To",
                        ins=[port("Global"), port("Reference")], outs=[port("Local")])
            g.link(*g.input(ins["Destination"], "Destination"), rel, port("Global"))
            g.link(pose, port("Pose"), rel, port("Reference"))
            memo["destination"] = (rel, port("Local"))
        return memo["destination"]

    def local_pose_of_target():
        lp = g.add(g.unique("local pose"), {"$case": "AttachmentPointGetLocalPoseNode"}, "Get Local Pose",
                   ins=[port("Attachment Point")], outs=[port("Pose"), port("Joint")])
        g.link(*g.attachment_point(*spec["target"]), lp, port("Attachment Point"))
        return lp, port("Pose")

    def current_part():
        """Unpacked current pose of the target part in the equipment: the joints' current
        effective poses from the top down to the end joint (times the attachment point's local
        pose)."""
        if "part" not in memo:
            end = frame_of(spec["end_path"], spec["end"][1])
            if spec["attachment_point"]:
                lpt = atomic("LocalPose")
                m = g.binary("MulNode", "Multiply", lpt, lpt, lpt)
                g.link(*end, m, port("A"))
                g.link(*local_pose_of_target(), m, port("B"))
                end = (m, port("Result"))
            memo["part"] = unpack_local(end, "unpack part")
        return memo["part"]

    def aim_parts():
        """(angle, axis) outputs: the signed angle about the aim joint's axis from the target
        part's current position to the Destination, and that axis in the equipment."""
        if "aim" not in memo:
            aim = spec["aim"]
            eid = aim["joint"]
            name = joints[eid][0]
            lpos, loff, ldir = atomic("LocalPosition"), atomic("LocalOffset"), atomic("LocalDirection")
            joint_frame = unpack_local(frame_of(aim["path"], name), "unpack aim joint")
            dest = unpack_local(destination(), "unpack aim destination")
            cons = g.add(g.unique(f"aim axis {eid}"), {"$case": CONSTRAINTS["REVOLUTE"][0]},
                         CONSTRAINTS["REVOLUTE"][1], ins=[port("Joint")],
                         outs=[port(o) for o in CONSTRAINTS["REVOLUTE"][2]])
            g.link(*g.joint(eid, name), cons, port("Joint"))
            axis = g.binary("MulNode", "Multiply", atomic("LocalRotation"), ldir, ldir)
            g.link(joint_frame, port("Rotation"), axis, port("A"))
            g.link(cons, port("Axis"), axis, port("B"))
            offsets = []
            for src in (current_part(), dest):
                sub = g.binary("SubtractNode", "Subtract", lpos, lpos, loff, out="Difference")
                g.link(src, port("Position"), sub, port("A"))
                g.link(joint_frame, port("Position"), sub, port("B"))
                offsets.append(sub)
            angle = g.add(g.unique("signed angle"), {
                "MacroId": SIGNED_ANGLE_MACRO, "Name": "Signed Angle on Plane",
                "ResolvedInputTypes": [{"InputId": {"Guid": SIGNED_ANGLE_A}, "Type": loff},
                                       {"InputId": {"Guid": SIGNED_ANGLE_B}, "Type": loff},
                                       {"InputId": {"Guid": SIGNED_ANGLE_AXIS}, "Type": ldir}],
                "ResolvedOutputTypes": [{"OutputId": {"Guid": SIGNED_ANGLE_RESULT}, "Type": atomic("Angle")}],
                "$case": "GlobalMacro"}, "Signed Angle on Plane",
                ins=[SIGNED_ANGLE_A, SIGNED_ANGLE_B, SIGNED_ANGLE_AXIS], outs=[SIGNED_ANGLE_RESULT],
                style="collapsed", atomic_node=False)
            g.link(offsets[0], port("Difference"), angle, SIGNED_ANGLE_A)
            g.link(offsets[1], port("Difference"), angle, SIGNED_ANGLE_B)
            g.link(axis, port("Result"), angle, SIGNED_ANGLE_AXIS)
            memo["aim"] = ((angle, SIGNED_ANGLE_RESULT), (axis, port("Result")))
        return memo["aim"]

    def aim_entry(d):
        """Adds the aim joint to the revolute start values d: its current angle plus the aim
        angle."""
        eid = spec["aim"]["joint"]
        name = joints[eid][0]
        angle, _ = aim_parts()
        vt = atomic("Angle")
        add = g.binary("AddNode", "Add", vt, vt, vt, out="Sum")
        g.link(*g.read_current(eid, name, "REVOLUTE"), add, port("A"))
        g.link(*angle, add, port("B"))
        s_ = g.set_entry("REVOLUTE")
        g.link(*(d or g.empty_dict("REVOLUTE")), s_, port("Dictionary"))
        g.link(*g.joint(eid, name), s_, port("Key"))
        g.link(add, port("Sum"), s_, port("Value"))
        return s_, port("Result")

    # ---- start values: limited joints away from their limits
    seed = {}
    if seeded or spec.get("aim"):
        g.section = "Start away from limits"
        for kind in ("REVOLUTE", "PRISMATIC"):
            d = None
            for eid in spec["seeds"]:
                name, k = joints[eid]
                if k != kind:
                    continue
                vt = atomic(VALUE_TYPE[kind])
                case, title, outs_ = CONSTRAINTS[kind]
                cons = g.add(g.unique(f"limits {eid}"), {"$case": case}, title, ins=[port("Joint")],
                             outs=[port(o) for o in outs_])
                g.link(*g.joint(eid, name), cons, port("Joint"))
                lo_port, hi_port = port(outs_[-3]), port(outs_[-2])
                lo = g.macro(LERP_MACRO, "Lerp", [LERP_FROM, LERP_TO, LERP_T], MACRO_RESULT, vt)
                g.link(cons, lo_port, lo, LERP_FROM)
                g.link(cons, hi_port, lo, LERP_TO)
                g.link(*g.input(ins["Start Away from Limits"], "Start Away from Limits"), lo, LERP_T)
                hi = g.macro(LERP_MACRO, "Lerp", [LERP_FROM, LERP_TO, LERP_T], MACRO_RESULT, vt)
                g.link(cons, hi_port, hi, LERP_FROM)
                g.link(cons, lo_port, hi, LERP_TO)
                g.link(*g.input(ins["Start Away from Limits"], "Start Away from Limits"), hi, LERP_T)
                clamp = g.macro(CLAMP_MACRO, "Clamp", [CLAMP_VALUE, CLAMP_MIN, CLAMP_MAX], MACRO_RESULT, vt)
                g.link(*g.read_current(eid, name, kind), clamp, CLAMP_VALUE)
                g.link(lo, MACRO_RESULT, clamp, CLAMP_MIN)
                g.link(hi, MACRO_RESULT, clamp, CLAMP_MAX)
                s = g.set_entry(kind)
                g.link(*(d or g.empty_dict(kind)), s, port("Dictionary"))
                g.link(*g.joint(eid, name), s, port("Key"))
                g.link(clamp, MACRO_RESULT, s, port("Value"))
                d = (s, port("Result"))
            if kind == "REVOLUTE" and spec.get("aim"):
                g.section = "Turn towards the target"
                d = aim_entry(d)
                g.section = "Start away from limits"
            if d is not None:
                seed[kind] = d
        g.section = None

    def destination_of_end_joint():
        """The end joint's target pose for the Destination of the target part.

        The Destination is the part's frame in the equipment, as dProB's attachment point poses
        are (Lift and Deliver pass such poses). With Position Only the target rotation is the
        part's current rotation, turned with the aim joint (slewing) towards the Destination, so
        a rotation the rig can reach; else the Destination's.

        The joint's position: the Destination's, minus the part's offset from its joint turned
        like the part will be (current rotation turned with the aim joint), not like the
        Destination: the IK may not reach the Destination's rotation, and an offset turned with it
        would move the part away from the position. For a hook hanging below its joint this is
        exact."""
        lpt, rot, lpos = atomic("LocalPose"), atomic("LocalRotation"), atomic("LocalPosition")

        def pack(position, rotation, name):
            n = g.add(g.unique(name), {"TPos": lpos, "TRot": rot, "TPose": lpt, "$case": "PackPoseNode"},
                      "Pack Pose", ins=[port("Position"), port("Rotation")], outs=[port("Pose")], style="collapsed")
            g.link(*position, n, port("Position"))
            g.link(*rotation, n, port("Rotation"))
            return n, port("Pose")

        def mul(a_, b_):
            n = g.binary("MulNode", "Multiply", lpt, lpt, lpt)
            g.link(*a_, n, port("A"))
            g.link(*b_, n, port("B"))
            return n, port("Result")

        def branch(if_true, if_false):
            n = g.add(g.unique("position only"), {"T": rot, "$case": "DeferredBranchNode"}, "Branch",
                      ins=[port("Pick A"), port("A"), port("B")], outs=[port("Result")], style="collapsed")
            g.link(*g.input(ins["Position Only"], "Position Only"), n, port("Pick A"))
            g.link(*if_true, n, port("A"))
            g.link(*if_false, n, port("B"))
            return n, port("Result")

        dest_pose = destination()
        dest = unpack_local(dest_pose, "unpack destination")
        turned = (current_part(), port("Rotation"))
        if spec.get("aim"):
            angle, axis = aim_parts()
            r = g.add(g.unique("aim rotation"), {"TRot": rot, "TDir": atomic("LocalDirection"),
                                                 "$case": "PackRotationAngleAxisNode"}, "Pack Rotation (Angle, Axis)",
                      ins=[port("Angle"), port("Axis")], outs=[port("Rotation")], style="collapsed")
            g.link(*angle, r, port("Angle"))
            g.link(*axis, r, port("Axis"))
            m = g.binary("MulNode", "Multiply", rot, rot, rot)
            g.link(r, port("Rotation"), m, port("A"))
            g.link(*turned, m, port("B"))
            turned = (m, port("Result"))
        if not spec["attachment_point"]:
            return pack((dest, port("Position")), branch(turned, (dest, port("Rotation"))), "pack joint target")
        inv = g.add(g.unique("invert"), {"T": lpt, "$case": "InvertNode"}, "Invert",
                    ins=[port("Input")], outs=[port("Result")], style="collapsed")
        g.link(*local_pose_of_target(), inv, port("Input"))
        where = unpack_local(mul(pack((dest, port("Position")), turned, "pack offset"), (inv, port("Result"))),
                             "unpack joint position")
        given = unpack_local(mul(dest_pose, (inv, port("Result"))), "unpack joint rotation")
        rotation = branch((where, port("Rotation")), (given, port("Rotation")))
        return pack((where, port("Position")), rotation, "pack joint target")

    def ik_node():
        n = g.add("ik", {"$case": "InverseKinematicsNode"}, "Inverse Kinematics",
                  ins=[port(p) for p in IK_INPUTS], outs=[port(p) for p in IK_OUTPUTS],
                  exec_in=True, exec_outs=[SUCCESSOR])
        g.link(*g.joint(*spec["end"]), n, port("End Effector"))
        g.link(*destination_of_end_joint(), n, port("Destination"))
        if spec["root"] is not None:
            g.link(*g.joint(*spec["root"]), n, port("Root Joint"))
        g.link(*g.input(ins["Iterations"], "Iterations"), n, port("Iterations"))
        g.link(*g.input(ins["Orientation Weight"], "Orientation Weight"), n, port("Orientation Weight"))
        return n

    if coupled:
        # ---- loop: IK, then the followers; the next pass starts from the result
        g.section = "Start away from limits" if seeded else None
        for kind in ("REVOLUTE", "PRISMATIC", "BALL"):
            if kind not in var_ids:
                continue
            vid, vname = var_ids[kind]
            s = g.var_set(vid, vname)
            g.link(*(seed.get(kind) or g.empty_dict(kind)), s, port("ValueIn"))
            then(s)
        g.section = None
        loop = g.add("loop", {"MacroId": LOOP_RANGE_MACRO, "Name": "Loop Range", "ResolvedInputTypes": [],
                              "ResolvedOutputTypes": [], "$case": "GlobalMacro"}, "Loop Range",
                     ins=[LOOP_FIRST, LOOP_LAST, LOOP_INCLUDE_LAST], outs=[port("Index")], exec_in=True,
                     exec_outs=[LOOP_BODY, LOOP_DONE], atomic_node=False)
        g.set(loop, LOOP_FIRST, {"Value": 0, "$case": "Int"})
        g.link(*g.input(ins["Passes"], "Passes"), loop, LOOP_LAST)
        g.set(loop, LOOP_INCLUDE_LAST, {"Value": False, "$case": "Bool"})
        then(loop)
        g.section = "IK pass"
        ik = ik_node()
        for kind in var_ids:
            if kind in INITIAL:
                g.link(*g.var(*var_ids[kind]), ik, port(INITIAL[kind]))
        g.then(loop, ik, LOOP_BODY)
        body = [ik]

        # followers, from the pass's result (chain joints) or the current state (others)
        g.section = "Set following and held joints"
        dicts = {k: (ik, port(DICT_VAR[k])) for k in ("REVOLUTE", "PRISMATIC")}
        made = {}

        def value_of(eid):
            name, kind = joints[eid]
            if eid in made:
                return made[eid]
            if eid in chain:
                get = g.get_entry(kind)
                g.link(*dicts[kind], get, port("Dictionary"))
                g.link(*g.joint(eid, name), get, port("Key"))
                return get, port("Value")
            return g.read_current(eid, name, kind)

        for eid, mode, terms, offset in spec["couplings"]:
            name, kind = joints[eid]
            vt = atomic(VALUE_TYPE[kind])
            if mode == "HOLD":
                v = g.read_current(eid, name, kind)
            else:
                acc = None
                for leader, factor in terms:
                    mul = g.binary("MulNode", "Multiply", vt, atomic("Double"), vt)
                    g.link(*value_of(leader), mul, port("A"))
                    g.set(mul, port("B"), {"Value": float(factor), "$case": "Double"}, atomic("Double"))
                    if acc is None:
                        acc = (mul, port("Result"))
                    else:
                        add = g.binary("AddNode", "Add", vt, vt, vt, out="Sum")
                        g.link(*acc, add, port("A"))
                        g.link(mul, port("Result"), add, port("B"))
                        acc = (add, port("Sum"))
                if acc is None or offset:
                    add = g.binary("AddNode", "Add", vt, vt, vt, out="Sum")
                    if acc is None:
                        g.set(add, port("A"), unit_value(kind, 0.0), vt)
                    else:
                        g.link(*acc, add, port("A"))
                    g.set(add, port("B"), unit_value(kind, offset), vt)
                    acc = (add, port("Sum"))
                v = acc
            s = g.set_entry(kind)
            g.link(*dicts[kind], s, port("Dictionary"))
            g.link(*g.joint(eid, name), s, port("Key"))
            g.link(*v, s, port("Value"))
            dicts[kind] = (s, port("Result"))
            made[eid] = v
        for kind in var_ids:
            if kind not in DICT_VAR:
                continue
            vid, vname = var_ids[kind]
            s = g.var_set(vid, vname)
            g.link(*(dicts[kind] if kind in dicts else (ik, port(DICT_VAR[kind]))), s, port("ValueIn"))
            g.then(body[-1], s)
            body.append(s)
        g.section = None
        result = {k: (lambda k=k: g.var(*var_ids[k])) for k in var_ids if k in DICT_VAR}
        exec_chain.append(None)            # the moves continue from the loop's Done
        move_from = (loop, LOOP_DONE)
    else:
        g.section = "IK"
        ik = ik_node()
        for kind, d in seed.items():
            g.link(*d, ik, port(INITIAL[kind]))
        then(ik)
        g.section = None
        result = {k: (lambda k=k: (ik, port(DICT_VAR[k]))) for k in ("REVOLUTE", "PRISMATIC", "BALL")}
        move_from = (ik, SUCCESSOR)
        body = None

    # ---- moves: For Each entry of the result dictionaries one process, collected in Processes
    g.section = "Move the joints"
    pvid, pname = var_ids["PROCESSES"]
    s = g.var_set(pvid, pname)
    empty = g.add(g.unique("empty list"), {"T": atomic("Process"), "$case": "EmptyListNode"}, "Empty List",
                  outs=[port("List")], style="collapsed")
    g.link(empty, port("List"), s, port("ValueIn"))
    g.then(move_from[0], s, move_from[1])
    prev, slot = s, SUCCESSOR
    moved = set(spec["moved"])
    functions = []
    for kind in ("REVOLUTE", "PRISMATIC", "BALL"):
        if not any(joints[e][1] == kind for e in moved) or kind not in result:
            continue
        vt = atomic(VALUE_TYPE[kind])
        d = result[kind]()
        # joints in the dictionary that must not move (Hold)
        listed = [e for e in spec["chain"] if joints[e][1] == kind]
        if coupled:
            listed += [e for e, *_ in spec["couplings"] if joints[e][1] == kind and e not in listed]
        for e in listed:
            if e in moved:
                continue
            rm = g.add(g.unique("remove held"), {"TKey": atomic("RigJoint"), "TValue": vt,
                                                  "$case": "RemoveDictEntryNode"}, "Remove Value",
                       ins=[port("Dictionary"), port("Key")], outs=[port("Result"), port("RemovedValue")],
                       style="collapsed")
            g.link(*d, rm, port("Dictionary"))
            g.link(*g.joint(e, joints[e][0]), rm, port("Key"))
            d = (rm, port("Result"))
        each = g.add(g.unique(f"for each {kind}"), {
            "MacroId": FOR_EACH_DICT_MACRO, "Name": "For Each Dictionary Entry",
            "ResolvedInputTypes": [{"InputId": {"Guid": FOR_EACH_DICT}, "Type": dict_type(VALUE_TYPE[kind])}],
            "ResolvedOutputTypes": [{"OutputId": {"Guid": FOR_EACH_VALUE}, "Type": vt},
                                    {"OutputId": {"Guid": FOR_EACH_KEY}, "Type": atomic("RigJoint")}],
            "$case": "GlobalMacro"}, "For Each Dictionary Entry", ins=[FOR_EACH_DICT],
            outs=[FOR_EACH_KEY, FOR_EACH_VALUE, FOR_EACH_INDEX], exec_in=True, exec_outs=[LOOP_BODY, LOOP_DONE],
            atomic_node=False)
        g.link(*d, each, FOR_EACH_DICT)
        g.then(prev, each, slot)
        if kind == "REVOLUTE":
            # the shortest way for unlimited joints (the Rotate Joint function of dProB's sample crane)
            move = g.add(g.unique("rotate joint"), {"FunctionId": {"Guid": ROTATE_JOINT["Id"]["Guid"]},
                                                    "$case": "LocalFunction"}, "Rotate Joint",
                         ins=[ROTATE_JOINT_JOINT, ROTATE_JOINT_ANGLE, ROTATE_JOINT_DURATION],
                         outs=[ROTATE_JOINT_PROCESS], exec_in=True, exec_outs=[SUCCESSOR], atomic_node=False)
            target_ports = (ROTATE_JOINT_JOINT, ROTATE_JOINT_ANGLE, ROTATE_JOINT_DURATION)
            process_port = ROTATE_JOINT_PROCESS
            functions.append(ROTATE_JOINT)
        else:
            case, title, target_port = MOVE[kind]
            move = g.add(g.unique(f"move {kind}"), {"$case": case}, title,
                         ins=[port("Joint"), port(target_port), port("Duration")], outs=[port("Process")],
                         exec_in=True, exec_outs=[SUCCESSOR])
            target_ports = (port("Joint"), port(target_port), port("Duration"))
            process_port = port("Process")
        g.link(each, FOR_EACH_KEY, move, target_ports[0])
        g.link(each, FOR_EACH_VALUE, move, target_ports[1])
        g.link(*g.input(ins["Duration"], "Duration"), move, target_ports[2])
        g.then(each, move, LOOP_BODY)
        app = g.add(g.unique("append"), {"T": atomic("Process"), "$case": "AppendNode"}, "Append",
                    ins=[port("List"), port("Item")], outs=[port("New List")], style="collapsed")
        g.link(*g.var(pvid, pname), app, port("List"))
        g.link(move, process_port, app, port("Item"))
        s = g.var_set(pvid, pname)
        g.link(app, port("New List"), s, port("ValueIn"))
        g.then(move, s)
        prev, slot = each, LOOP_DONE
    g.section = None
    g.nodes[end] = Node(end, None, "End", [out_id], [], True, [], "node", None)
    g.then(prev, end, slot)
    g.link(*g.var(pvid, pname), end, out_id)

    frames = layout(g, start)
    description = (spec.get("description") or f"Moves {spec['target'][1]} to a pose with inverse kinematics.")
    description = f"{description} {GENERATED_MARK}"
    meta = [{"Node": {"Guid": n.id}, "Description": None, "Xpos": float(n.x * GRID), "Ypos": float(n.y * GRID)}
            for n in g.nodes.values()]
    graph = {
        "Outputs": outputs,
        "LocalVariables": variables,
        "Nodes": [n.json for n in g.nodes.values() if n.json is not None],
        "Frames": frames,
        "FirstNodeId": {"Guid": start},
        "ExecutionConnections": g.exec,
        "DataConnections": g.data,
        "FixedInputs": g.fixed,
        "EditorMetadata": meta,
        "InputPortTypeConstraints": g.constraints,
        "OutputPortTypeConstraints": [],
        "InvalidExecutionConnections": [],
        "InvalidDataConnections": [],
        "ViewportData": {"PanX": 0.0, "PanY": 0.0, "Zoom": 0.6},
        "OutputSections": [],
        "VariableSections": [],
    }
    return g, {
        "Signature": {"Id": {"Guid": cap_id}, "Name": spec["name"], "Description": description,
                      "Icon": {"Id": "Working"}, "Inputs": inputs, "Outputs": outputs,
                      "InputSections": [], "OutputSections": []},
        "Implementation": {"EndNodeIds": [{"Guid": end}], "GraphData": graph, "LocalFunctions": functions[:1],
                           "IndicatorData": [], "ShowInButtonWheel": True},
    }


# ------------------------------------------------------------------ layout

GAP_X = 2          # cells between a node and its providers / between execution blocks
GAP_Y = 1          # cells between stacked providers
LANE_GAP = 4       # cells between execution rows
FRAME_PAD = 1


def layout(g, start):
    """Places every node (cells, Node.x / Node.y) and returns frames around the sections.

    Execution nodes sit on rows, left to right in execution order; a Loop Range's Body continues
    its row, its Done starts a new row below everything above. Each execution node's data
    providers form a tree to its left: a provider's own inputs go further left, stacked top-down
    from the row of the input they feed, deeper subtrees first, else in port order. A provider
    used by several nodes is placed with the first; getters are made per use, so they sit next to
    their consumer.
    """
    nodes = g.nodes
    succ = {}
    for c in g.exec:
        succ.setdefault(c["FromNode"]["Guid"], []).append((c["SuccessionSlotId"]["Guid"], c["ToNode"]["Guid"]))
    providers = {}
    for c in g.data:
        providers.setdefault(c["ToNode"]["Guid"], []).append((c["InputSlotId"]["Guid"], c["FromNode"]["Guid"]))
    is_exec = {nid for nid, n in nodes.items() if n.exec_in or n.exec_outs}
    depth = {}

    def chain_depth(nid):
        """Longest provider chain below a data node."""
        if nid not in depth:
            depth[nid] = 0
            depth[nid] = 1 + max([chain_depth(s) for _, s in providers.get(nid, ()) if s not in is_exec] + [-1])
        return depth[nid]

    # deeper subtrees first (else port order): a provider used by several nodes is placed with the
    # first that reaches it, and so lands left of the deepest one too
    for k, lst in providers.items():
        n = nodes[k]
        lst.sort(key=lambda p: (-chain_depth(p[1]) if p[1] not in is_exec else 0,
                                n.ins.index(p[0]) if p[0] in n.ins else 99))
    placed = set()

    def tree(nid, right, top, out):
        """Places nid with its right edge at `right`, top at `top` (relative); returns height."""
        n = nodes[nid]
        n.x, n.y = right - n.width, top
        placed.add(nid)
        out.append(nid)
        y = top
        bottom = top + n.height
        for in_port, src in providers.get(nid, ()):
            if src in placed or src in is_exec:
                continue
            y = max(y, top + n.in_row(in_port) - (0 if nodes[src].style != "node" else 1.5))
            h = tree(src, n.x - GAP_X, y, out)
            y += h + GAP_Y
            bottom = max(bottom, y - GAP_Y)
        return bottom - top

    def block(nid):
        """Providers of an execution node, placed relative to it at (0, 0); returns their ids."""
        out = []
        n = nodes[nid]
        y = 0
        for in_port, src in providers.get(nid, ()):
            if src in placed or src in is_exec:
                continue
            y = max(y, n.in_row(in_port) - (0 if nodes[src].style != "node" else 1.5))
            h = tree(src, -GAP_X, y, out)
            y += h + GAP_Y
        return out

    bottom = [0.0]

    def row(nid, x, y):
        """Places an execution row from nid at (x, y); returns the row's right end."""
        while nid is not None:
            n = nodes[nid]
            placed.add(nid)
            members = block(nid)
            left = min([nodes[m].x for m in members] + [0])
            nx = x - left
            n.x, n.y = nx, y
            for m in members:
                nodes[m].x += nx
                nodes[m].y += y
                bottom[0] = max(bottom[0], nodes[m].y + nodes[m].height)
            bottom[0] = max(bottom[0], y + n.height)
            x = nx + n.width + GAP_X * 2
            nexts = dict(succ.get(nid, ()))
            if LOOP_BODY in nexts:
                body_end = row(nexts[LOOP_BODY], x, y)
                done = nexts.get(LOOP_DONE)
                if done is not None:
                    row(done, n.x + n.width + GAP_X * 2, bottom[0] + LANE_GAP)
                return body_end
            nid = nexts.get(SUCCESSOR)
            if nid in placed:
                return x
        return x

    row(start, 0, 0)
    for n in nodes.values():            # anything unreachable (should not happen)
        if n.x is None:
            n.x, n.y = 0, bottom[0] + LANE_GAP

    frames = []
    sections = {}
    for n in nodes.values():
        if n.section:
            sections.setdefault(n.section, []).append(n)
    colors = [(0.35, 0.45, 0.6), (0.45, 0.55, 0.35), (0.6, 0.45, 0.3), (0.5, 0.35, 0.55), (0.35, 0.5, 0.5)]
    for i, (name, members) in enumerate(sections.items()):
        x0 = min(m.x for m in members) - FRAME_PAD
        y0 = min(m.y for m in members) - FRAME_PAD - 1
        x1 = max(m.x + m.width for m in members) + FRAME_PAD
        y1 = max(m.y + m.height for m in members) + FRAME_PAD
        r, gr, b = colors[i % len(colors)]
        frames.append({"Frame": {"Guid": g.id(f"frame {name}")}, "Name": name,
                       "Xpos": float(x0 * GRID), "Ypos": float(y0 * GRID),
                       "Width": float((x1 - x0) * GRID), "Height": float((y1 - y0) * GRID),
                       "ColorR": r, "ColorG": gr, "ColorB": b, "ColorA": 1.0})
    return frames


def overlaps(g):
    """Pairs of node titles whose estimated boxes overlap (cells)."""
    out = []
    ns = list(g.nodes.values())
    for i, a in enumerate(ns):
        for b in ns[i + 1:]:
            if (a.x < b.x + b.width and b.x < a.x + a.width and a.y < b.y + b.height and b.y < a.y + a.height):
                out.append((a.title, b.title))
    return out


# ------------------------------------------------------------------ simulation

class _Run:
    """Interprets one script graph (a capability or a local function)."""

    def __init__(self, sim, graph, input_values, functions):
        self.sim, self.g = sim, graph
        self.input_values = input_values
        self.functions = functions
        self.nodes = {}
        for n in graph["Nodes"]:
            body = n.get("Node", n)
            self.nodes[body["Id"]["Guid"]] = body
        self.data_in, self.fixed, self.nexts = {}, {}, {}
        for c in graph["DataConnections"]:
            self.data_in.setdefault(c["ToNode"]["Guid"], {})[c["InputSlotId"]["Guid"]] = (
                c["FromNode"]["Guid"], c["OutputSlotId"]["Guid"])
        for f in graph["FixedInputs"]:
            self.fixed.setdefault(f["Node"]["Guid"], {})[f["InputSlotId"]["Guid"]] = f["Value"]
        for c in graph["ExecutionConnections"]:
            self.nexts.setdefault(c["FromNode"]["Guid"], {})[c["SuccessionSlotId"]["Guid"]] = c["ToNode"]["Guid"]
        self.variables, self.outputs = {}, {}
        self.end_values = {}

    def inp(self, node_id, name_or_id, optional=False):
        pid = name_or_id if "-" in name_or_id and len(name_or_id) == 36 else port(name_or_id)
        src = self.data_in.get(node_id, {}).get(pid)
        if src is not None:
            return self.value(*src)
        f = self.fixed.get(node_id, {}).get(pid)
        if f is not None:
            return _fixed_value(f)
        if optional:
            return None
        raise KeyError(f"{self.nodes.get(node_id, {}).get('$case')} {node_id}: no input {name_or_id}")

    def value(self, node_id, out_port):
        if (node_id, out_port) in self.outputs:
            return self.outputs[(node_id, out_port)]
        sim, inp = self.sim, self.inp
        if node_id == self.g["FirstNodeId"]["Guid"]:       # the start node gives the inputs
            return self.input_values[out_port]
        n = self.nodes[node_id]
        case = n["$case"]
        if case == "InputGetter":
            return self.input_values[n["InputSlotId"]["Guid"]]
        if case == "Reroute":
            return inp(node_id, "Input")
        if case == "JointGetter":
            return sim.by_joint_id[n["JointId"]]
        if case == "AttachmentPointGetter":
            return sim.by_point_id[n["AttachmentPointId"]]
        if case == "DeferredVarGetter":
            return self.variables.get(n["VarId"]["Guid"])
        if case == "EmptyDictionaryNode":
            return {}
        if case == "EmptyListNode":
            return []
        if case == "AppendNode":
            return inp(node_id, "List") + [inp(node_id, "Item")]
        if case == "GetDictEntryNode":
            return inp(node_id, "Dictionary")[inp(node_id, "Key")]
        if case == "SetDictEntryNode":
            d = dict(inp(node_id, "Dictionary"))
            d[inp(node_id, "Key")] = inp(node_id, "Value")
            return d
        if case == "RemoveDictEntryNode":
            d = dict(inp(node_id, "Dictionary"))
            del d[inp(node_id, "Key")]
            return d
        if case == "MulNode":
            a, b = inp(node_id, "A"), inp(node_id, "B")
            return a @ b if isinstance(a, np.ndarray) and isinstance(b, np.ndarray) else a * b
        if case == "AddNode":
            return inp(node_id, "A") + inp(node_id, "B")
        if case == "SubtractNode":
            return inp(node_id, "A") - inp(node_id, "B")
        if case == "AbsNode":
            return abs(inp(node_id, "Input"))
        if case == "ModuloNode":
            return math.fmod(inp(node_id, "Dividend"), inp(node_id, "Divisor"))      # C# %
        if case == "CompareNode":
            a, b = inp(node_id, "A"), inp(node_id, "B")
            return {port("A < B"): a < b, port("A > B"): a > b, port("A == B"): a == b}[out_port]
        if case == "OrNode":
            return bool(inp(node_id, "A")) or bool(inp(node_id, "B"))
        if case == "DeferredBranchNode":
            return inp(node_id, "A") if inp(node_id, "Pick A") else inp(node_id, "B")
        if case == "InvertNode":
            return np.linalg.inv(inp(node_id, "Input"))
        if case == "GetJointStaticPose":
            return sim.joints[inp(node_id, "Joint")].initial_pose
        if case == "UnpackPoseNode":
            pose = inp(node_id, "Pose")
            return {port("Position"): pose[:3, 3], port("Rotation"): pose[:3, :3],
                    port("Forward"): pose[:3, 2]}[out_port]
        if case == "PackPoseNode":
            m = np.eye(4)
            m[:3, 3] = inp(node_id, "Position")
            m[:3, :3] = inp(node_id, "Rotation")
            return m
        if case == "AttachmentPointGetLocalPoseNode":
            point = inp(node_id, "Attachment Point")
            if out_port == port("Pose"):
                return sim.fixed_parts[point][0] if point in sim.fixed_parts else np.eye(4)
            return sim.fixed_parts[point][1] if point in sim.fixed_parts else point
        if case == "AttachmentPointGetGlobalPoseNode":
            return sim.subject_pose @ sim.point_pose(inp(node_id, "Attachment Point"))
        if case == "ReadCurrentPoseNode":
            return sim.subject_pose
        if case == "RelativeToNode":
            return np.linalg.inv(inp(node_id, "Reference")) @ inp(node_id, "Global")
        if case in ("GetRevoluteJointConstraintsNode", "GetPrismaticJointConstraintsNode"):
            joint = sim.joints[inp(node_id, "Joint")]
            lim = joint.limits
            names = {port("Min Angle"): 0, port("Max Angle"): 1, port("Min Offset"): 0, port("Max Offset"): 1}
            if out_port == port("Axis"):
                return joint.axis
            if out_port == port("Has Limits"):
                return lim is not None
            return None if lim is None else lim[names[out_port]]
        if case == "PackRotationAngleAxisNode":
            axis = np.asarray(inp(node_id, "Axis"), dtype=float)
            return ik_solver.axis_angle(axis / np.linalg.norm(axis), inp(node_id, "Angle"))
        if case == "GlobalMacro" and n["MacroId"] == EFFECTIVE_POSE_MACRO:
            j = inp(node_id, EFFECTIVE_POSE_JOINT)
            return sim.joints[j].initial_pose @ ik_solver.offset(sim.joints[j], sim.state[j])
        if case == "GlobalMacro" and n["MacroId"] == SIGNED_ANGLE_MACRO:
            return ik_solver.aim_angle(np.zeros(3), inp(node_id, SIGNED_ANGLE_AXIS),
                                       inp(node_id, SIGNED_ANGLE_A), inp(node_id, SIGNED_ANGLE_B))
        if case == "GlobalMacro" and n["MacroId"] == CLAMP_MACRO:
            return min(max(inp(node_id, CLAMP_VALUE), inp(node_id, CLAMP_MIN)), inp(node_id, CLAMP_MAX))
        if case == "GlobalMacro" and n["MacroId"] == LERP_MACRO:
            a, b = inp(node_id, LERP_FROM), inp(node_id, LERP_TO)
            return a + (b - a) * inp(node_id, LERP_T)
        if case in ("ReadCurrentRevoluteJointStateNode", "ReadCurrentPrismaticJointStateNode"):
            return sim.state[inp(node_id, "Joint")]
        raise ValueError(f"cannot evaluate {case}")

    def run(self, node_id=None):
        if node_id is None:
            node_id = self.nexts[self.g["FirstNodeId"]["Guid"]][SUCCESSOR]
        sim, inp = self.sim, self.inp
        while node_id is not None:
            n = self.nodes.get(node_id)
            if n is None:          # the end node
                for pid, src in self.data_in.get(node_id, {}).items():
                    self.end_values[pid] = self.value(*src)
                return
            case = n["$case"]
            slot = SUCCESSOR
            if case == "VarSetter":
                self.variables[n["VarId"]["Guid"]] = inp(node_id, "ValueIn")
            elif case == "InverseKinematicsNode":
                self._ik(node_id)
            elif case == "GlobalMacro" and n["MacroId"] == LOOP_RANGE_MACRO:
                first, last = inp(node_id, LOOP_FIRST), inp(node_id, LOOP_LAST)
                include = inp(node_id, LOOP_INCLUDE_LAST, optional=True)
                for _ in range(first, last + (1 if include else 0)):
                    self.run(self.nexts.get(node_id, {}).get(LOOP_BODY))
                slot = LOOP_DONE
            elif case == "GlobalMacro" and n["MacroId"] == FOR_EACH_DICT_MACRO:
                for i, (k, v) in enumerate(list(inp(node_id, FOR_EACH_DICT).items())):
                    self.outputs[(node_id, FOR_EACH_KEY)] = k
                    self.outputs[(node_id, FOR_EACH_VALUE)] = v
                    self.outputs[(node_id, FOR_EACH_INDEX)] = i
                    self.run(self.nexts.get(node_id, {}).get(LOOP_BODY))
                slot = LOOP_DONE
            elif case == "LocalFunction":
                f = self.functions[n["FunctionId"]["Guid"]]
                args = {i["Id"]["Guid"]: inp(node_id, i["Id"]["Guid"]) for i in f["Inputs"]}
                sub = _Run(sim, f["GraphData"], args, self.functions)
                sub.run()
                for pid, v in sub.end_values.items():
                    self.outputs[(node_id, pid)] = v
            elif case == "CapabilityInvocation":
                cap = sim.capabilities[n["CapabilityId"]["Guid"]]
                args = {}
                for i in cap["Signature"]["Inputs"]:
                    v = inp(node_id, i["Id"]["Guid"], optional=True)
                    if v is None and i.get("DefaultValue") is not None:
                        v = _fixed_value(i["DefaultValue"])
                    args[i["Id"]["Guid"]] = v
                functions = {f["Id"]["Guid"]: f for f in cap["Implementation"].get("LocalFunctions") or []}
                sub = _Run(sim, cap["Implementation"]["GraphData"], args, functions)
                sub.run()
                for pid, v in sub.end_values.items():
                    self.outputs[(node_id, pid)] = v
            elif case in ("MoveRevoluteJointNode", "MovePrismaticJointNode", "MoveBallJointNode"):
                target = {"MoveRevoluteJointNode": "Target Angle", "MovePrismaticJointNode": "Target Offset",
                          "MoveBallJointNode": "Target Rotation"}[case]
                joint = inp(node_id, "Joint")
                sim.moves[joint] = inp(node_id, target)
                self.outputs[(node_id, port("Process"))] = (case, joint)
            else:
                raise ValueError(f"cannot execute {case}")
            node_id = self.nexts.get(node_id, {}).get(slot)

    def _ik(self, node_id):
        sim, inp = self.sim, self.inp
        end = inp(node_id, "End Effector")
        root = inp(node_id, "Root Joint", optional=True)
        chain = ik_solver.chain_to(sim.joints, sim.parents, end, root)
        init = {}
        for key in INITIAL.values():
            init.update(inp(node_id, key, optional=True) or {})
        start_values = [init.get(c.key, sim.state[c.key]) for c in chain]
        res = ik_solver.solve(chain, inp(node_id, "Destination"), start_values,
                              inp(node_id, "Iterations"), inp(node_id, "Orientation Weight"),
                              sim.base(chain[0].key))
        for kind, out in zip(("REVOLUTE", "PRISMATIC", "BALL"), IK_OUTPUTS):
            self.outputs[(node_id, port(out))] = {c.key: v for c, v in zip(chain, res) if c.kind == kind}


def _fixed_value(v):
    case = v["$case"]
    if case in ("Int", "Double", "Bool"):
        return v["Value"]
    if case == "ValueWithUnit":
        return v["Value"]["SiValue"]
    raise ValueError(case)


class _Sim:
    def __init__(self, model, state, subject_pose):
        self.joints, self.parents, self.fixed_parts = ik_solver.rig_from_model(model["HierarchyRoots"])
        self.by_joint_id = {joint_id(e): e for e in self.joints}
        self.by_point_id = {attachment_point_id(e): e for e in self.fixed_parts}
        self.by_point_id.update({attachment_point_id(e): e for e in self.joints})
        self.state = state
        self.subject_pose = np.eye(4) if subject_pose is None else subject_pose
        self.moves = {}
        self.capabilities = {c["Signature"]["Id"]["Guid"]: c for c in model.get("CustomCapabilities") or []}

    def base(self, first):
        above, j = [], self.parents[first]
        while j is not None:
            above.insert(0, self.joints[j])
            j = self.parents[j]
        return ik_solver.forward(above, [self.state[a.key] for a in above])[1][-1] if above else None

    def point_pose(self, point):
        """Current pose of an attachment point (element id) in the equipment."""
        offset, joint = self.fixed_parts[point] if point in self.fixed_parts else (np.eye(4), point)
        if joint is None:
            return offset
        chain = ik_solver.chain_to(self.joints, self.parents, joint)
        return ik_solver.forward(chain, [self.state[c.key] for c in chain])[1][-1] @ offset


def simulate(capability, model, destination, state, duration=10.0, inputs=None, subject_pose=None):
    """Runs a generated capability like dProB would: returns {element id: target value} of its
    Move processes. destination: 4x4; state: {element id: current value (SI, 3x3 for ball)};
    inputs: {input name: value} overriding the defaults; subject_pose: the equipment's global pose.
    Only the nodes the generator writes are supported."""
    sig = capability["Signature"]
    impl = capability["Implementation"]
    given = {"Destination": destination, "Duration": duration, **(inputs or {})}
    input_values = {}
    sim = _Sim(model, state, subject_pose)
    for i in sig["Inputs"]:
        name = i["Name"].get("Text")
        if i["Id"]["Guid"] == port("Self"):
            input_values[i["Id"]["Guid"]] = "self"
        elif name in given:
            input_values[i["Id"]["Guid"]] = given[name]
        elif i.get("DefaultValue") is not None:
            input_values[i["Id"]["Guid"]] = _fixed_value(i["DefaultValue"])
    functions = {f["Id"]["Guid"]: f for f in impl.get("LocalFunctions") or []}
    _Run(sim, impl["GraphData"], input_values, functions).run()
    return sim.moves


def simulate_type_capability(implementation, model, inputs, state, subject_pose=None):
    """Runs an equipment type implementation (for example the Crane's Move Arm) that may invoke
    the generated custom capabilities. inputs: {input slot id: value}. Returns the moves."""
    sim = _Sim(model, state, subject_pose)
    impl = implementation["Implementation"]
    functions = {f["Id"]["Guid"]: f for f in impl.get("LocalFunctions") or []}
    _Run(sim, impl["GraphData"], inputs, functions).run()
    return sim.moves
