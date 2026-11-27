"""Graphs from dProB's sample crane, written as code: the Crane type's Lift and Deliver, its
state variables, and the Rotate Joint function that the IK capabilities use.

Node ids are UUIDv5 of the graph's name and the node's key, so every export writes the same
graphs. Built-in port ids are UUIDv5 (ISO OID namespace) of the port name, as dProB makes them;
ports whose names are unknown are GUID constants. Positions are the editor positions of the
sample (pixels).
"""

import math
import uuid

ISO_OID = uuid.UUID("6ba7b812-9dad-11d1-80b4-00c04fd430c8")
MOVE_ARM = "00000000-0000-8000-8000-000000000001"
LIFT = "00000000-0000-8000-8000-000000000002"
DELIVER = "00000000-0000-8000-8000-000000000003"
PROCESSES_OUTPUT = "e11ed511-9104-51d8-be5c-fdf6acd9f01b"     # the Crane capabilities' output

# ports whose names are unknown
PARENT_OUT = "36e09047-dcf7-53cc-aa7e-303d24aaf2b3"           # Get Attached Parent: has a parent
BRANCH_FALSE = "aab46f1a-8265-57f1-8c49-64546422c547"         # Branch: condition false
HOOK_OFFSET = "fe6d9670-99dc-5d31-b56a-d967fc3bd2ce"          # Lift's input Hook offset

# equipment state variables: id, name, type
CARGO = ("294fea15-6192-40db-a53d-aaeb2115b8bf", "Current Cargo", "Subject")
CARGO_HOOK_OFFSET = ("7ae56a76-1c6d-4891-b8e9-ab6af471d8af", "Current Cargo Hook Offset", "LocalPose")

# Rotate Joint function: its id, inputs and output
ROTATE_JOINT_ID = "15f9ce8a-831e-42a1-b588-36336804154d"
ROTATE_JOINT_JOINT = "38c9945b-208f-4c2a-baa6-9b81e9951854"
ROTATE_JOINT_ANGLE = "7e6f808f-6057-4d44-aa4e-a1bf51d876ed"
ROTATE_JOINT_DURATION = "f8f66365-f020-4cd1-8b66-d5b5a0283b1e"
ROTATE_JOINT_PROCESS = "eacaef72-6d6e-416e-87b6-5154b60882d0"


def port(name):
    return str(uuid.uuid5(ISO_OID, name))


def A(name):
    return {"Type": name, "$case": "Atomic"}


def _id(guid):
    return {"Guid": guid}


def _unit(case, si):
    return {"Value": {"SiValue": float(si), "$case": case}, "$case": "ValueWithUnit"}


def _string(text):
    return {"Value": text, "$case": "String"}


def _slot(name):
    """A port id: a GUID as it is, else the id of the built-in port with that name."""
    return name if len(name) == 36 and name[8] == "-" else port(name)


class _Graph:
    def __init__(self, name):
        self.ns = uuid.uuid5(ISO_OID, "bii tools " + name)
        self.ids, self.nodes, self.meta = {}, [], []
        self.exec, self.data, self.fixed = [], [], []

    def _new(self, key, x, y):
        nid = str(uuid.uuid5(self.ns, key))
        self.ids[key] = nid
        self.meta.append({"Node": _id(nid), "Description": None, "Xpos": float(x), "Ypos": float(y)})
        return nid

    def start(self, x, y):
        self._new("start", x, y)

    def end(self, key, x, y):
        self._new(key, x, y)

    def add(self, key, x, y, case, atomic=True, **fields):
        body = {"Id": _id(self._new(key, x, y)), **fields, "$case": case}
        self.nodes.append({"Node": body, "$case": "AtomicNode"} if atomic else body)

    def input(self, key, x, y, name, slot=None):
        self.add(key, x, y, "InputGetter", atomic=False, InputSlotId=_id(slot or port(name)), DisplayName=name,
                 FromOuterContext=False, IsOptional=False)

    def var_get(self, key, x, y, var):
        self.add(key, x, y, "EquipmentVariableDeferredGetter", atomic=False, VariableId=_id(var[0]),
                 CachedVariableName=var[1], VarType=A(var[2]))

    def var_set(self, key, x, y, var):
        self.add(key, x, y, "EquipmentVariableSetter", atomic=False, VariableId=_id(var[0]),
                 CachedVariableName=var[1], VarType=A(var[2]))

    def move_arm(self, key, x, y):
        self.add(key, x, y, "CapabilityInvocation", atomic=False,
                 Collection={"Type": {"Identifier": {"Id": "Crane"}, "$case": "BuiltIn"}, "$case": "EquipmentType"},
                 CapabilityId=_id(MOVE_ARM))

    def then(self, a, b, slot="Successor"):
        self.exec.append({"FromNode": _id(self.ids[a]), "SuccessionSlotId": _id(_slot(slot)),
                          "ToNode": _id(self.ids[b])})

    def link(self, a, out, b, inp):
        self.data.append({"FromNode": _id(self.ids[a]), "OutputSlotId": _id(_slot(out)),
                          "ToNode": _id(self.ids[b]), "InputSlotId": _id(_slot(inp))})

    def set(self, key, inp, value):
        self.fixed.append({"Node": _id(self.ids[key]), "InputSlotId": _id(_slot(inp)), "Value": value})

    def json(self, outputs, frames=()):
        return {"Outputs": outputs, "LocalVariables": [], "Nodes": self.nodes, "Frames": list(frames),
                "FirstNodeId": _id(self.ids["start"]), "ExecutionConnections": self.exec,
                "DataConnections": self.data, "FixedInputs": self.fixed, "EditorMetadata": self.meta,
                "InputPortTypeConstraints": [], "OutputPortTypeConstraints": [], "InvalidExecutionConnections": [],
                "InvalidDataConnections": [], "ViewportData": {"PanX": 0.0, "PanY": 0.0, "Zoom": 1.0},
                "OutputSections": [], "VariableSections": []}


_PROCESSES = [{"Id": _id(PROCESSES_OUTPUT),
               "Name": {"LocalizationKey": "BII.ScriptableTemplates.Atomic.Processes", "$case": "Localized"},
               "Type": {"ElementType": A("Process"), "$case": "List"}, "Description": None, "IsOptional": False}]


def _implementation(capability_id, g, graph):
    return {"CapabilityId": _id(capability_id),
            "Implementation": {"EndNodeIds": [_id(g.ids["end"])], "GraphData": graph, "LocalFunctions": [],
                               "IndicatorData": [], "ShowInButtonWheel": True}}


def state_variables():
    """The Crane's equipment state variables, which Lift and Deliver use."""
    hook_offset = {"Position": {"X": 0.0, "Y": 0.0, "Z": 0.0, "$case": "LocalPosition"},
                   "Rotation": {"X": 0.0, "Y": 0.0, "Z": 0.0, "W": 1.0, "$case": "LocalRotation"},
                   "$case": "LocalPose"}
    return [{"Name": name, "Id": _id(vid), "SlotType": A(type_), "InitialValue": initial, "Description": None,
             "IsPublic": False}
            for (vid, name, type_), initial in ((CARGO, None), (CARGO_HOOK_OFFSET, hook_offset))]


def lift(hook_point_id, hook_name):
    """The Crane's Lift on the hook's attachment point: moves the hook to the cargo (Move Arm),
    attaches the cargo and remembers it and its offset."""
    g = _Graph('lift')
    g.start(-896, 32)
    g.end('end', 2464, 64)
    g.input('in_cargo', 1184, 128, 'Cargo')
    g.add('attachment_point_getter', 1184, 160, 'AttachmentPointGetter', atomic=False, AttachmentPointId=hook_point_id, CachedPointName=hook_name)
    g.add('attach_to_attachment_point', 1248, 64, 'AttachToAttachmentPointNode')
    g.add('equality', -32, 256, 'EqualityNode', T=A('Subject'))
    g.add('append', 160, -64, 'AppendNode', T=A('Subject'))
    g.add('attachment_point_getter2', 1472, 544, 'AttachmentPointGetter', atomic=False, AttachmentPointId=hook_point_id, CachedPointName=hook_name)
    g.add('abort', 832, -128, 'AbortNode')
    g.move_arm('capability_invocation', 832, 64)
    g.add('unpack_position', 0, 640, 'UnpackPositionNode', T=A('GlobalPosition'))
    g.add('attachment_point_get_global_pose', 1504, 512, 'AttachmentPointGetGlobalPoseNode')
    g.input('in_cargo2', -736, 416, 'Cargo')
    g.var_set('equipment_variable_setter', 1632, 64, CARGO)
    g.add('get_attached_parent', -288, 160, 'GetAttachedParentNode')
    g.add('branch', 480, 32, 'BranchNode')
    g.add('is_cargo', 160, 512, 'IsCargoNode')
    g.add('and', 416, 448, 'AndNode')
    g.add('make_global', 320, 352, 'MakeGlobalNode', TLocal=A('LocalPose'), TGlobal=A('GlobalPose'))
    g.add('read_current_pose', 1504, 352, 'ReadCurrentPoseNode')
    g.add('append2', 2272, 256, 'AppendNode', T=A('Process'))
    g.input('in_cargo3', 1600, 128, 'Cargo')
    g.add('pack_position', 192, 640, 'PackPositionNode', T=A('GlobalPosition'))
    g.add('equality2', 224, 448, 'EqualityNode', T=A('LocalPose'))
    g.add('unpack_bounds', -192, 640, 'UnpackBoundsNode', TPos=A('GlobalPosition'), TOff=A('GlobalOffset'), TBou=A('GlobalBounds'))
    g.add('format_string', 480, -96, 'FormatStringNode', T=A('Subject'))
    g.input('in_cargo4', 128, 544, 'Cargo')
    g.add('identity', 64, 480, 'IdentityNode', T=A('LocalPose'))
    g.add('and2', 320, 96, 'AndNode')
    g.add('reroute', 1088, 256, 'Reroute', atomic=False, Exec=False)
    g.add('append3', 1888, 256, 'AppendNode', T=A('Process'))
    g.var_get('equipment_variable_deferred_getter', -544, 96, CARGO)
    g.add('get_subject_bounds', -704, 576, 'GetSubjectBoundsNode')
    g.input('in_cargo5', -736, 608, 'Cargo')
    g.add('and3', 160, 192, 'AndNode')
    g.input('in_cargo6', 1440, 384, 'Cargo')
    g.add('read_current_pose2', -704, 384, 'ReadCurrentPoseNode')
    g.add('has_value', 128, 96, 'HasValueNode', T=A('Subject'))
    g.input('in_self', -64, 288, 'Self')
    g.input('in_hook_offset', 288, 384, 'Hook offset', slot=HOOK_OFFSET)
    g.var_set('equipment_variable_setter2', 2016, 64, CARGO_HOOK_OFFSET)
    g.add('reroute2', -32, 128, 'Reroute', atomic=False, Exec=False)
    g.add('pack_pose', 384, 640, 'PackPoseNode', TPos=A('GlobalPosition'), TRot=A('GlobalRotation'), TPose=A('GlobalPose'))
    g.add('empty_list', -32, -64, 'EmptyListNode', T=A('Subject'))
    g.input('in_duration', 800, 192, 'Duration')
    g.add('unpack_position2', 0, 736, 'UnpackPositionNode', T=A('GlobalPosition'))
    g.add('append4', 1504, 256, 'AppendNode', T=A('Process'))
    g.add('append5', 160, -32, 'AppendNode', T=A('Subject'))
    g.input('in_self2', 800, 128, 'Self')
    g.add('make_global2', -448, 576, 'MakeGlobalNode', TLocal=A('LocalBounds'), TGlobal=A('GlobalBounds'))
    g.add('deferred_branch', 576, 320, 'DeferredBranchNode', T=A('GlobalPose'))
    g.input('in_hook_offset2', 192, 448, 'Hook offset', slot=HOOK_OFFSET)
    g.add('relative_to', 1760, 416, 'RelativeToNode', TGlobal=A('GlobalPose'), TLocal=A('LocalPose'))
    g.input('in_self3', 128, -32, 'Self')
    g.then('capability_invocation', 'attach_to_attachment_point')
    g.then('attach_to_attachment_point', 'equipment_variable_setter')
    g.then('equipment_variable_setter', 'equipment_variable_setter2')
    g.then('equipment_variable_setter2', 'end')
    g.then('start', 'branch')
    g.then('branch', 'capability_invocation', BRANCH_FALSE)
    g.then('branch', 'abort', 'Then')
    g.link('read_current_pose2', 'Pose', 'make_global', 'Parent')
    g.link('in_cargo2', 'Value', 'read_current_pose2', 'Subject')
    g.link('in_self2', 'Value', 'capability_invocation', 'Self')
    g.link('in_duration', 'Value', 'capability_invocation', 'Duration')
    g.link('in_cargo', 'Value', 'attach_to_attachment_point', 'Subject')
    g.link('attachment_point_getter', 'AttachmentPoint', 'attach_to_attachment_point', 'Attachment Point')
    g.link('capability_invocation', 'Processes', 'reroute', 'Input')
    g.link('attach_to_attachment_point', 'Process', 'append4', 'Item')
    g.link('in_cargo3', 'Value', 'equipment_variable_setter', 'ValueIn')
    g.link('reroute', 'Output', 'append4', 'List')
    g.link('equipment_variable_setter', 'Process', 'append3', 'Item')
    g.link('append4', 'New List', 'append3', 'List')
    g.link('append3', 'New List', 'append2', 'List')
    g.link('equipment_variable_setter2', 'Process', 'append2', 'Item')
    g.link('append2', 'New List', 'end', 'Processes')
    g.link('format_string', 'Result', 'abort', 'Error Message')
    g.link('append5', 'New List', 'format_string', 'Arguments')
    g.link('append', 'New List', 'append5', 'List')
    g.link('empty_list', 'List', 'append', 'List')
    g.link('in_self3', 'Value', 'append', 'Item')
    g.link('in_cargo6', 'Value', 'read_current_pose', 'Subject')
    g.link('read_current_pose', 'Pose', 'relative_to', 'Global')
    g.link('attachment_point_getter2', 'AttachmentPoint', 'attachment_point_get_global_pose', 'Attachment Point')
    g.link('attachment_point_get_global_pose', 'Pose', 'relative_to', 'Reference')
    g.link('relative_to', 'Local', 'equipment_variable_setter2', 'ValueIn')
    g.link('get_attached_parent', 'Parent', 'equality', 'A')
    g.link('in_self', 'Value', 'equality', 'B')
    g.link('has_value', 'HasValue', 'and2', 'A')
    g.link('and2', 'Result', 'branch', 'Condition')
    g.link('equipment_variable_deferred_getter', 'Value', 'get_attached_parent', 'Subject')
    g.link('get_attached_parent', PARENT_OUT, 'and3', 'A')
    g.link('equality', 'Result', 'and3', 'B')
    g.link('and3', 'Result', 'and2', 'B')
    g.link('in_hook_offset2', 'Value', 'equality2', 'A')
    g.link('identity', 'Output', 'equality2', 'B')
    g.link('in_cargo4', 'Value', 'is_cargo', 'Subject')
    g.link('is_cargo', 'Is Cargo', 'and', 'B')
    g.link('equality2', 'Result', 'and', 'A')
    g.link('and', 'Result', 'deferred_branch', 'Pick A')
    g.link('read_current_pose2', 'Rotation', 'pack_pose', 'Rotation')
    g.link('pack_position', 'Position', 'pack_pose', 'Position')
    g.link('deferred_branch', 'Result', 'capability_invocation', 'Destination')
    g.link('make_global', 'Global', 'deferred_branch', 'B')
    g.link('in_hook_offset', 'Value', 'make_global', 'Local')
    g.link('get_subject_bounds', 'Bounds', 'make_global2', 'Local')
    g.link('read_current_pose2', 'Pose', 'make_global2', 'Parent')
    g.link('make_global2', 'Global', 'unpack_bounds', 'Bounds')
    g.link('unpack_bounds', 'Max', 'unpack_position2', 'Position')
    g.link('unpack_bounds', 'Center', 'unpack_position', 'Position')
    g.link('unpack_position', 'X', 'pack_position', 'X')
    g.link('unpack_position', 'Z', 'pack_position', 'Z')
    g.link('unpack_position2', 'Y', 'pack_position', 'Y')
    g.link('pack_pose', 'Pose', 'deferred_branch', 'A')
    g.link('in_cargo5', 'Value', 'get_subject_bounds', 'Subject')
    g.link('equipment_variable_deferred_getter', 'Value', 'reroute2', 'Input')
    g.link('reroute2', 'Output', 'has_value', 'Input')
    g.link('reroute2', 'Output', 'append5', 'Item')
    g.set('pack_position', 'X', _unit('Length', 0.0))
    g.set('format_string', 'Format', _string('{0} is already lifting {1}'))
    g.set('pack_position', 'Z', _unit('Length', 0.0))
    g.set('attach_to_attachment_point', 'Duration', _unit('Duration', 0.001))
    graph = g.json(_PROCESSES)
    return _implementation(LIFT, g, graph)


def deliver():
    """The Crane's Deliver: moves the cargo to the Destination (Move Arm) and detaches it."""
    g = _Graph('deliver')
    g.start(-1888, 128)
    g.end('end', 1600, 128)
    g.add('reroute', -1248, 64, 'Reroute', atomic=False, Exec=False)
    g.add('invert', 192, 224, 'InvertNode', T=A('LocalPose'))
    g.add('equality', -992, 352, 'EqualityNode', T=A('Subject'))
    g.add('make_global', 352, 192, 'MakeGlobalNode', TLocal=A('LocalPose'), TGlobal=A('GlobalPose'))
    g.var_get('equipment_variable_deferred_getter', -1504, 192, CARGO)
    g.add('append', -672, 480, 'AppendNode', T=A('Subject'))
    g.add('format_string', -480, 384, 'FormatStringNode', T=A('Subject'))
    g.input('in_self', -704, 512, 'Self')
    g.add('has_value', -832, 224, 'HasValueNode', T=A('Subject'))
    g.add('and', -640, 256, 'AndNode')
    g.var_get('equipment_variable_deferred_getter2', -64, 192, CARGO_HOOK_OFFSET)
    g.add('reroute2', 1024, 64, 'Reroute', atomic=False, Exec=False)
    g.input('in_self2', 704, 192, 'Self')
    g.add('branch', -480, 128, 'BranchNode')
    g.add('empty_list', -672, 448, 'EmptyListNode', T=A('Subject'))
    g.add('abort', -224, 352, 'AbortNode')
    g.input('in_destination', 320, 256, 'Destination')
    g.add('append2', 1408, 320, 'AppendNode', T=A('Process'))
    g.add('and2', -800, 288, 'AndNode')
    g.add('get_attached_parent', -1248, 256, 'GetAttachedParentNode')
    g.move_arm('capability_invocation', 736, 128)
    g.add('reroute3', 992, 320, 'Reroute', atomic=False, Exec=False)
    g.input('in_self3', -1024, 352, 'Self')
    g.add('detach_from_subject', 1152, 128, 'DetachFromSubjectNode')
    g.input('in_duration', 704, 256, 'Duration')
    g.then('start', 'branch')
    g.then('capability_invocation', 'detach_from_subject')
    g.then('branch', 'capability_invocation', 'Then')
    g.then('branch', 'abort', BRANCH_FALSE)
    g.then('detach_from_subject', 'end')
    g.link('in_self2', 'Value', 'capability_invocation', 'Self')
    g.link('in_destination', 'Value', 'make_global', 'Parent')
    g.link('make_global', 'Global', 'capability_invocation', 'Destination')
    g.link('in_duration', 'Value', 'capability_invocation', 'Duration')
    g.link('equipment_variable_deferred_getter', 'Value', 'has_value', 'Input')
    g.link('format_string', 'Result', 'abort', 'Error Message')
    g.link('append', 'New List', 'format_string', 'Arguments')
    g.link('empty_list', 'List', 'append', 'List')
    g.link('in_self', 'Value', 'append', 'Item')
    g.link('equipment_variable_deferred_getter', 'Value', 'reroute', 'Input')
    g.link('reroute', 'Output', 'reroute2', 'Input')
    g.link('reroute2', 'Output', 'detach_from_subject', 'Subject')
    g.link('detach_from_subject', 'Process', 'append2', 'Item')
    g.link('reroute3', 'Output', 'append2', 'List')
    g.link('capability_invocation', 'Processes', 'reroute3', 'Input')
    g.link('equipment_variable_deferred_getter', 'Value', 'get_attached_parent', 'Subject')
    g.link('get_attached_parent', 'Parent', 'equality', 'A')
    g.link('in_self3', 'Value', 'equality', 'B')
    g.link('has_value', 'HasValue', 'and', 'A')
    g.link('get_attached_parent', PARENT_OUT, 'and2', 'A')
    g.link('equality', 'Result', 'and2', 'B')
    g.link('and2', 'Result', 'and', 'B')
    g.link('append2', 'New List', 'end', 'Processes')
    g.link('equipment_variable_deferred_getter2', 'Value', 'invert', 'Input')
    g.link('invert', 'Result', 'make_global', 'Local')
    g.link('and', 'Result', 'branch', 'Condition')
    g.set('format_string', 'Format', _string('{0} is not carrying any cargo.'))
    g.set('detach_from_subject', 'Duration', _unit('Duration', 0.001))
    graph = g.json(_PROCESSES)
    return _implementation(DELIVER, g, graph)


def rotate_joint():
    """The Rotate Joint function (LocalFunctionAssetModel): turns a revolute joint to an angle,
    the short way round when the joint is unlimited."""
    g = _Graph('rotate joint')
    g.start(32, 128)
    g.end('end', 2208, 128)
    g.add('move_revolute_joint', 1952, 128, 'MoveRevoluteJointNode')
    g.add('subtract', 704, 352, 'SubtractNode', TA=A('Angle'), TB=A('Angle'), TRes=A('Angle'))
    g.add('or', 1248, 288, 'OrNode')
    g.input('in_target_angle', 672, 352, 'Target Angle', slot=ROTATE_JOINT_ANGLE)
    g.add('reroute', 1120, 224, 'Reroute', atomic=False, Exec=False)
    g.add('abs', 832, 320, 'AbsNode', T=A('Angle'))
    g.add('add', 1056, 512, 'AddNode', TA=A('Angle'), TB=A('Angle'), TRes=A('Angle'))
    g.add('read_current_revolute_joint_state', 448, 384, 'ReadCurrentRevoluteJointStateNode')
    g.add('modulo', 928, 480, 'ModuloNode', TA=A('Angle'), TB=A('Angle'), TRes=A('Angle'))
    g.input('in_joint', 1920, 192, 'Joint', slot=ROTATE_JOINT_JOINT)
    g.input('in_target_angle2', 1504, 224, 'Target Angle', slot=ROTATE_JOINT_ANGLE)
    g.add('modulo2', 1152, 544, 'ModuloNode', TA=A('Angle'), TB=A('Angle'), TRes=A('Angle'))
    g.add('compare', 992, 288, 'CompareNode', TA=A('Angle'), TB=A('Angle'))
    g.add('subtract2', 1280, 576, 'SubtractNode', TA=A('Angle'), TB=A('Angle'), TRes=A('Angle'))
    g.add('add2', 832, 448, 'AddNode', TA=A('Angle'), TB=A('Angle'), TRes=A('Angle'))
    g.input('in_joint2', 416, 320, 'Joint', slot=ROTATE_JOINT_JOINT)
    g.input('in_duration', 1920, 256, 'Duration', slot=ROTATE_JOINT_DURATION)
    g.add('deferred_branch', 1536, 192, 'DeferredBranchNode', T=A('Angle'))
    g.add('get_revolute_joint_constraints', 448, 192, 'GetRevoluteJointConstraintsNode')
    g.add('add3', 1408, 416, 'AddNode', TA=A('Angle'), TB=A('Angle'), TRes=A('Angle'))
    g.then('start', 'move_revolute_joint')
    g.then('move_revolute_joint', 'end')
    g.link('read_current_revolute_joint_state', 'Angle', 'subtract', 'B')
    g.link('subtract', 'Difference', 'abs', 'Input')
    g.link('abs', 'Output', 'compare', 'A')
    g.link('compare', 'A < B', 'or', 'B')
    g.link('or', 'Result', 'deferred_branch', 'Pick A')
    g.link('subtract', 'Difference', 'add2', 'A')
    g.link('add2', 'Sum', 'modulo', 'Dividend')
    g.link('modulo', 'Remainder', 'add', 'A')
    g.link('add', 'Sum', 'modulo2', 'Dividend')
    g.link('modulo2', 'Remainder', 'subtract2', 'A')
    g.link('get_revolute_joint_constraints', 'Has Limits', 'reroute', 'Input')
    g.link('reroute', 'Output', 'or', 'A')
    g.link('subtract2', 'Difference', 'add3', 'B')
    g.link('add3', 'Sum', 'deferred_branch', 'B')
    g.link('read_current_revolute_joint_state', 'Angle', 'add3', 'A')
    g.link('deferred_branch', 'Result', 'move_revolute_joint', 'Target Angle')
    g.link('in_joint2', 'Value', 'get_revolute_joint_constraints', 'Joint')
    g.link('in_joint2', 'Value', 'read_current_revolute_joint_state', 'Joint')
    g.link('in_target_angle', 'Value', 'subtract', 'A')
    g.link('in_target_angle2', 'Value', 'deferred_branch', 'A')
    g.link('in_joint', 'Value', 'move_revolute_joint', 'Joint')
    g.link('in_duration', 'Value', 'move_revolute_joint', 'Duration')
    g.link('move_revolute_joint', 'Process', 'end', ROTATE_JOINT_PROCESS)
    g.set('add2', 'B', _unit('Angle', math.pi))
    g.set('modulo2', 'Divisor', _unit('Angle', 2 * math.pi))
    g.set('compare', 'B', _unit('Angle', math.pi))
    g.set('subtract2', 'B', _unit('Angle', math.pi))
    g.set('add', 'B', _unit('Angle', 2 * math.pi))
    g.set('modulo', 'Divisor', _unit('Angle', 2 * math.pi))
    graph = g.json([{'Id': _id(ROTATE_JOINT_PROCESS), 'Name': {'Text': 'Process', '$case': 'Plain'}, 'Type': A('Process'), 'Description': None, 'IsOptional': False}], frames=[{'Frame': _id('e343d32d-568f-4e87-93bb-94ca82624100'), 'Name': 'Normalize Angle diff between [-180°.. 180°) so the joint takes the shortest path (if it is unlimited)', 'Xpos': 320.0, 'Ypos': 96.0, 'Width': 1472.0, 'Height': 576.0, 'ColorR': 0.627451, 'ColorG': 0.627451, 'ColorB': 0.627451, 'ColorA': 1.0}])
    inputs = [(ROTATE_JOINT_JOINT, "Joint", "RigJoint"), (ROTATE_JOINT_ANGLE, "Target Angle", "Angle"),
              (ROTATE_JOINT_DURATION, "Duration", "Duration")]
    return {"Id": _id(ROTATE_JOINT_ID), "Name": "Rotate Joint",
            "Inputs": [{"Id": _id(i), "Name": {"Text": n, "$case": "Plain"}, "Type": A(t), "DefaultValue": None,
                        "Description": None, "IsOptional": False} for i, n, t in inputs],
            "EndNodeIds": [_id(g.ids["end"])], "GraphData": graph, "InputSections": []}
