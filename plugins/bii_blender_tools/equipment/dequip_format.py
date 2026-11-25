""".dequip files: dProB scriptable equipment assets (format ScriptableEquipment_v2).

A .dequip is a zip archive (dProB's AssetSystem):
- asset_guid.txt               GUID of this asset file (new for every export)
- dProB_asset_metadata.json    {"Format": "ScriptableEquipment_v2", "ProductVersion": "..."}
- model.json                   EquipmentAssetModel (BII.Common.Equipment, EquipmentIO.cs)
- <geometry id>/<name>.fbx     the FBX that the hierarchy elements take their meshes from

model.json is Newtonsoft JSON of C# records: property names as in C#, Option<T> as the value or
null, enums as strings, and union cases (AssetUnion) as objects with a "$case" field.
"""

import json
import os
import tempfile
import uuid
import zipfile

FORMAT = "ScriptableEquipment_v2"
PRODUCT_VERSION = "release 2026.2.5"   # dProB version whose format this writes
GUID_ENTRY = "asset_guid.txt"
METADATA_ENTRY = "dProB_asset_metadata.json"
MODEL_ENTRY = "model.json"

JOINT_TYPES = ("FIXED", "REVOLUTE", "PRISMATIC", "BALL")


def new_id():
    return str(uuid.uuid4())


def vec(v):
    return {"X": float(v[0]), "Y": float(v[1]), "Z": float(v[2])}


def quat(q):
    return {"X": float(q[0]), "Y": float(q[1]), "Z": float(q[2]), "W": float(q[3])}


def pose(position, rotation_quat):
    return {"Position": vec(position), "Rotation": quat(rotation_quat)}


IDENTITY_POSE = pose((0, 0, 0), (0, 0, 0, 1))


def matrix3(m):
    """MatrixModel: the three columns of a 3x3 matrix."""
    return {f"C{i}": vec([m[0][i], m[1][i], m[2][i]]) for i in range(3)}


def joint(kind, axis=(1, 0, 0), minimum=None, maximum=None):
    """JointConstraintsModel. Revolute limits in radians (None: unlimited), prismatic in meters."""
    if kind == "FIXED":
        return {"$case": "Fixed"}
    if kind == "BALL":
        return {"$case": "Ball"}
    if kind == "REVOLUTE":
        limits = None if minimum is None else {"MinRadians": float(minimum), "MaxRadians": float(maximum)}
        return {"$case": "Revolute", "Axis": vec(axis), "Limits": limits}
    if kind == "PRISMATIC":
        return {"$case": "Prismatic", "Axis": vec(axis), "MinMeters": float(minimum), "MaxMeters": float(maximum)}
    raise ValueError(f"unknown joint type {kind!r}; one of {JOINT_TYPES}")


def fbx_source(geometry_id, model_id):
    return {
        "$case": "ImportedGeometry",
        "PivotOffset": IDENTITY_POSE,
        "GeometryReference": {"Id": geometry_id},
        "GeometryData": {"$case": "Fbx", "ModelId": int(model_id)},
    }


def element(name, element_id, source, element_pose, scale_and_shear, joint_constraint,
            visible=True, attachment_point=False, children=()):
    return {
        "Name": name,
        "Id": element_id,
        "GeometrySource": source,
        "Pose": element_pose,
        "GeometryScaleAndShear": matrix3(scale_and_shear),
        "JointConstraint": joint_constraint,
        "IsVisible": bool(visible),
        "IsAttachmentPoint": bool(attachment_point),
        "Children": list(children),
    }


def fbx_entry_name(geometry_id, name):
    return f"{geometry_id}/{name}.fbx"


def equipment(equipment_id, geometry_id, fbx_entry, roots):
    """EquipmentAssetModel without capabilities, equipment types and state variables."""
    return {
        "EquipmentId": equipment_id,
        "EquipmentTypeImplementations": [],
        "CustomCapabilities": [],
        "GeometryModels": [{
            "$case": "Fbx",
            "Reference": {"Id": geometry_id},
            "Asset": {"FbxFile": fbx_entry, "Textures": []},
        }],
        "HierarchyRoots": list(roots),
        "StateVariables": [],
        "RailVehicleData": None,
        "GroundVehicleData": None,
        "BulkLoadableData": None,
        "VehicleCollisionData": None,
    }


def walk(roots):
    for r in roots:
        yield r
        yield from walk(r.get("Children", ()))


def write(path, model, fbx_bytes, fbx_entry, metadata=None):
    """Write the .dequip zip; replaces path only when complete."""
    meta = {"Format": FORMAT, "ProductVersion": PRODUCT_VERSION}
    meta.update(metadata or {})
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".dequip", dir=folder)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(GUID_ENTRY, str(uuid.uuid4()))
            z.writestr(METADATA_ENTRY, json.dumps(meta))
            z.writestr(MODEL_ENTRY, json.dumps(model, indent=1))
            z.writestr(fbx_entry, fbx_bytes)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


class Dequip:
    def __init__(self, metadata, guid, model, files):
        self.metadata = metadata
        self.guid = guid
        self.model = model
        self.files = files       # {entry name: bytes} of the other entries


def read(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if METADATA_ENTRY not in names or MODEL_ENTRY not in names:
            raise ValueError(f"{path} is not a dProB asset (no {METADATA_ENTRY} or {MODEL_ENTRY})")
        metadata = json.loads(z.read(METADATA_ENTRY).decode("utf-8-sig"))
        if metadata.get("Format") != FORMAT:
            raise ValueError(f"{path} has format {metadata.get('Format')!r}; this reads {FORMAT} "
                             "(save it again in a current dProB first)")
        guid = z.read(GUID_ENTRY).decode("utf-8-sig").strip() if GUID_ENTRY in names else None
        model = json.loads(z.read(MODEL_ENTRY).decode("utf-8-sig"))
        files = {n: z.read(n) for n in names if n not in (GUID_ENTRY, METADATA_ENTRY, MODEL_ENTRY)}
    return Dequip(metadata, guid, model, files)
