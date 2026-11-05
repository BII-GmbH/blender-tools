"""Convert a STEP file to binary glTF (.glb), keeping colors and names.

This is the converter behind File > Import > STEP (import_step.py). It also
runs from the command line inside Blender, which provides OCP through the
installed extension and is needed for hidden-part removal:

    blender -b --python step2glb.py -- input.step output.glb [options]

Print the assembly tree with the paths that --exclude takes:

    blender -b --python step2glb.py -- input.step --list

--deflection  Max chord error as a fraction of the model's bounding-box
              diagonal (measured after filtering). Larger = fewer triangles.
--angle       Max angle in degrees between adjacent mesh normals on curved
              surfaces. Larger = fewer triangles. This limit does not depend
              on feature size, so small curved details such as embossed
              text keep their shape.
--up          Up axis of the STEP model: z (default) or y.
--exclude     Instance paths from --list; drops these instances and
              everything below them.
--max-size    Drop bodies whose bounding-box diagonal is below this, meters.
--hidden      Drop bodies that no ray from outside reaches.
"""

import argparse
import math
import os
import random
import shutil
import sys
import tempfile
from dataclasses import dataclass, field

from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.IFSelect import IFSelect_RetDone
from OCP.IMeshTools import IMeshTools_Parameters
from OCP.Message import Message_ProgressRange
from OCP.RWGltf import RWGltf_CafWriter
from OCP.RWMesh import RWMesh_CoordinateSystem
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label, TDF_Tool
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_DocumentTool

try:  # OCP >= 7.9 moved collection types into OCP.collections
    from OCP.collections import (
        IndexedDataMap_TCollection_AsciiString_TCollection_AsciiString as FileInfoMap,
        Sequence_TDF_Label as LabelSequence,
    )
except ImportError:
    from OCP.TColStd import TColStd_IndexedDataMapOfStringString as FileInfoMap
    from OCP.TDF import TDF_LabelSequence as LabelSequence

MODEL_UNIT_M = 0.001  # STEP geometry is converted to millimeters on import


# --- Reading -----------------------------------------------------------------

def read_step(path):
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    XCAFDoc_DocumentTool.SetLengthUnit_s(doc, MODEL_UNIT_M)

    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    reader.SetLayerMode(True)
    if reader.ReadFile(path) != IFSelect_RetDone:
        raise RuntimeError(f"Cannot read STEP file: {path}")
    if not reader.Transfer(doc, Message_ProgressRange()):
        raise RuntimeError("STEP transfer failed")
    return doc


def label_name(label):
    attr = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        return attr.Get().ToExtString()
    return ""


def label_key(label):
    entry = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, entry)
    return entry.ToCString()


def components(shape_tool, label):
    seq = LabelSequence()
    shape_tool.GetComponents_s(label, seq, False)
    return [seq.Value(i) for i in range(1, seq.Length() + 1)]


def referred(shape_tool, component):
    ref = TDF_Label()
    shape_tool.GetReferredShape_s(component, ref)
    return ref


def free_labels(shape_tool):
    seq = LabelSequence()
    shape_tool.GetFreeShapes(seq)
    return [seq.Value(i) for i in range(1, seq.Length() + 1)]


def explore(shape, kind):
    exp = TopExp_Explorer(shape, kind)
    while exp.More():
        yield exp.Current()
        exp.Next()


def split_bodies(shape):
    """Solids of a part; sheet parts without solids fall back to shells."""
    return list(explore(shape, TopAbs_SOLID)) or list(explore(shape, TopAbs_SHELL)) or [shape]


def diagonal(shape, box=None):
    box = box or Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    return 0.0 if box.IsVoid() else math.sqrt(box.SquareExtent())


# --- Assembly model ------------------------------------------------------------

# An instance path is a tuple of indices: the free shape, then the component
# index at each assembly level. Unlike names, it is unique.

@dataclass
class Node:
    """One instance in the assembly tree, for choosing what to import."""
    path: tuple
    instance: str
    product: str
    is_assembly: bool
    size: float  # bounding-box diagonal, meters


def assembly_tree(doc):
    """Every instance in the assembly, depth first."""
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    sizes, nodes = {}, []

    def walk(label, path, instance):
        key = label_key(label)
        if key not in sizes:
            sizes[key] = diagonal(shape_tool.GetShape_s(label)) * MODEL_UNIT_M
        is_assembly = shape_tool.IsAssembly_s(label)
        nodes.append(Node(path, instance, label_name(label), is_assembly, sizes[key]))
        if is_assembly:
            for i, comp in enumerate(components(shape_tool, label)):
                walk(referred(shape_tool, comp), path + (i,), label_name(comp))

    for i, root in enumerate(free_labels(shape_tool)):
        walk(root, (i,), "")
    return nodes


@dataclass
class Part:
    """A part definition (product) and the bodies it is made of."""
    label: object
    bodies: list
    sizes: list  # bounding-box diagonal per body, in meters
    keep: set = field(default_factory=set)


@dataclass
class Occurrence:
    """One placed instance of a part."""
    part: Part
    path: tuple
    location: object


def collect(doc):
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    parts, occurrences = {}, []

    def walk(label, path, location):
        if shape_tool.IsAssembly_s(label):
            for i, comp in enumerate(components(shape_tool, label)):
                walk(referred(shape_tool, comp), path + (i,),
                     location.Multiplied(shape_tool.GetLocation_s(comp)))
            return
        key = label_key(label)
        if key not in parts:
            bodies = split_bodies(shape_tool.GetShape_s(label))
            parts[key] = Part(label, bodies, [diagonal(b) * MODEL_UNIT_M for b in bodies])
        occurrences.append(Occurrence(parts[key], path, location))

    for i, root in enumerate(free_labels(shape_tool)):
        walk(root, (i,), TopLoc_Location())
    return shape_tool, parts, occurrences


def print_tree(doc, max_depth):
    for node in assembly_tree(doc):
        depth = len(node.path) - 1
        if depth <= max_depth:
            kind = "assembly" if node.is_assembly else "part"
            name = f"{node.instance} [{node.product}]" if node.instance else f"[{node.product}]"
            print(f"{'  ' * depth}{'/'.join(map(str, node.path))}  {name} {kind}, "
                  f"size {node.size:.3f} m", flush=True)


# --- Meshing -------------------------------------------------------------------

def mesh_bodies(bodies, deflection_mm, angle):
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for body in bodies:
        BRepTools.Clean_s(body)  # drop any triangulation stored in the STEP file
        builder.Add(compound, body)
    params = IMeshTools_Parameters()
    params.Deflection = deflection_mm
    params.Angle = math.radians(angle)
    params.InParallel = True
    BRepMesh_IncrementalMesh(compound, params, Message_ProgressRange())


def triangles(body, trsf):
    """World-space triangles of a meshed body as (a, b, c) point tuples."""
    out = []
    for face in explore(body, TopAbs_FACE):
        loc = TopLoc_Location()
        poly = BRep_Tool.Triangulation_s(TopoDS.Face(face), loc)
        if poly is None:
            continue
        t = trsf.Multiplied(loc.Transformation())
        pts = []
        for i in range(1, poly.NbNodes() + 1):
            p = poly.Node(i).Transformed(t)
            pts.append((p.X(), p.Y(), p.Z()))
        flip = face.Orientation() == TopAbs_REVERSED
        for i in range(1, poly.NbTriangles() + 1):
            tri = poly.Triangle(i)
            a, b, c = (pts[tri.Value(k) - 1] for k in (1, 2, 3))
            out.append((a, c, b) if flip else (a, b, c))
    return out


def _triangle_count(body):
    total = 0
    for face in explore(body, TopAbs_FACE):
        poly = BRep_Tool.Triangulation_s(TopoDS.Face(face), TopLoc_Location())
        total += poly.NbTriangles() if poly is not None else 0
    return total


# --- Hidden-body test ------------------------------------------------------------

def _fibonacci_dirs(n):
    dirs = []
    for i in range(n):
        z = 1 - 2 * (i + 0.5) / n
        r = math.sqrt(1 - z * z)
        phi = i * math.pi * (3 - math.sqrt(5))
        dirs.append((r * math.cos(phi), r * math.sin(phi), z))
    return dirs


def find_visible(bodies, two_sided, scene_diag):
    """Return the keys of the bodies that an outside ray can reach.

    bodies maps key -> list of world triangles; two_sided holds the keys of
    sheet bodies, which have no inside. For sample points on each body, rays
    go out over the hemisphere above the surface; a ray that hits nothing has
    escaped, so the body is visible.
    """
    try:
        from mathutils import Vector
        from mathutils.bvhtree import BVHTree
    except ImportError:
        raise RuntimeError("Hidden-part removal needs Blender. "
                           "Run: blender -b --python step2glb.py -- ...")

    verts, polys = [], []
    for tris in bodies.values():
        for tri in tris:
            n = len(verts)
            verts.extend(tri)
            polys.append((n, n + 1, n + 2))
    bvh = BVHTree.FromPolygons(verts, polys, all_triangles=True)

    dirs = [Vector(d) for d in _fibonacci_dirs(48)]
    eps = scene_diag * 1e-5
    far = scene_diag * 2
    rng = random.Random(1)
    visible = set()
    for key, tris in bodies.items():
        geo = []
        for a, b, c in tris:
            a, b, c = Vector(a), Vector(b), Vector(c)
            normal = (b - a).cross(c - a)
            area = normal.length
            if area > 0:
                geo.append((a, b, c, normal / area, area))
        if not geo:
            continue
        samples = rng.choices(geo, weights=[g[4] for g in geo], k=min(256, max(24, len(geo))))
        if key in two_sided:
            samples += [(a, b, c, -n, w) for a, b, c, n, w in samples]
        for a, b, c, n, _ in samples:
            u, v = rng.random(), rng.random()
            if u + v > 1:
                u, v = 1 - u, 1 - v
            origin = a + (b - a) * u + (c - a) * v + n * eps
            ray_dirs = [d for d in dirs if d.dot(n) > 0.1]
            rng.shuffle(ray_dirs)
            if any(bvh.ray_cast(origin, d, far)[0] is None for d in ray_dirs[:12]):
                visible.add(key)
                break
    return visible


# --- Filtering -----------------------------------------------------------------

@dataclass
class Filter:
    exclude: set = field(default_factory=set)  # instance paths; drops their subtrees
    max_size: float = None  # drop bodies with a smaller bounding-box diagonal, meters
    hidden: bool = False  # drop bodies that no ray from outside reaches


def apply_filter(doc, flt, deflection, angle):
    """Drop what the filter removes, mesh the rest, return stats."""
    shape_tool, parts, occurrences = collect(doc)
    dropped = {}  # (occurrence index, body index) -> reason
    for oi, occ in enumerate(occurrences):
        excluded = any(occ.path[:len(p)] == p for p in flt.exclude)
        for bi, size in enumerate(occ.part.sizes):
            if excluded:
                dropped[oi, bi] = "excluded"
            elif flt.max_size is not None and size < flt.max_size:
                dropped[oi, bi] = "small"
    alive = [(oi, bi) for oi, occ in enumerate(occurrences)
             for bi in range(len(occ.part.bodies)) if (oi, bi) not in dropped]
    if not alive:
        raise RuntimeError("Nothing left to import: every part is filtered out")

    # Relative deflection is measured on what survives the filter.
    box = Bnd_Box()
    for oi, bi in alive:
        occ = occurrences[oi]
        BRepBndLib.Add_s(occ.part.bodies[bi].Moved(occ.location), box)
    scene_diag = math.sqrt(box.SquareExtent())

    unique = {(id(occurrences[oi].part), bi): occurrences[oi].part.bodies[bi] for oi, bi in alive}
    mesh_bodies(unique.values(), deflection * scene_diag, angle)

    if flt.hidden:
        world, two_sided = {}, set()
        for oi, bi in alive:
            occ = occurrences[oi]
            world[oi, bi] = triangles(occ.part.bodies[bi], occ.location.Transformation())
            if occ.part.bodies[bi].ShapeType() != TopAbs_SOLID:
                two_sided.add((oi, bi))
        print(f"Testing {len(world)} bodies for visibility...", flush=True)
        visible = find_visible(world, two_sided, scene_diag)
        for key in alive:
            if key not in visible:
                dropped[key] = "hidden"

    # A part body kept in any instance stays in all instances of that part.
    for oi, bi in alive:
        if (oi, bi) not in dropped:
            occurrences[oi].part.keep.add(bi)

    builder = BRep_Builder()
    for part in parts.values():
        if part.keep and len(part.keep) < len(part.bodies):
            compound = TopoDS_Compound()
            builder.MakeCompound(compound)
            for bi in sorted(part.keep):
                builder.Add(compound, part.bodies[bi])
            shape_tool.SetShape(part.label, compound)
    prune(shape_tool, {label_key(p.label) for p in parts.values() if p.keep}, flt.exclude)
    shape_tool.UpdateAssemblies()

    written = [o for o in occurrences if not any(o.path[:len(p)] == p for p in flt.exclude)]
    tris = sum(_triangle_count(o.part.bodies[bi]) for o in written for bi in o.part.keep)
    reasons = {r: sum(1 for v in dropped.values() if v == r) for r in ("excluded", "small", "hidden")}
    return {"total": sum(len(o.part.bodies) for o in occurrences),
            "kept": sum(len(o.part.keep) for o in written), "triangles": tris,
            "scene_diag": scene_diag, "dropped": reasons}


def prune(shape_tool, alive_parts, exclude):
    """Remove excluded instances and those whose part has nothing left."""
    # Excluded instances go first, by path. In a sub-assembly that is used
    # more than once this removes the instance from every use.
    doomed = []

    def find(label, path):
        for i, comp in enumerate(components(shape_tool, label)):
            if path + (i,) in exclude:
                doomed.append(comp)
            else:
                find(referred(shape_tool, comp), path + (i,))

    roots = free_labels(shape_tool)
    for i, root in enumerate(roots):
        if shape_tool.IsAssembly_s(root):
            find(root, (i,))
    for comp in doomed:
        shape_tool.RemoveComponent(comp)

    memo = {}

    def alive(label):
        key = label_key(label)
        if key not in memo:
            if shape_tool.IsAssembly_s(label):
                memo[key] = False
                for comp in components(shape_tool, label):
                    if alive(referred(shape_tool, comp)):
                        memo[key] = True
                    else:
                        shape_tool.RemoveComponent(comp)
            else:
                memo[key] = key in alive_parts
        return memo[key]

    root_keys = {label_key(r) for r in roots}
    for root in roots:
        alive(root)
    # Labels cut loose from the tree now show up as free shapes; delete them.
    while orphans := [f for f in free_labels(shape_tool) if label_key(f) not in root_keys]:
        for label in orphans:
            shape_tool.RemoveShape(label, False)


# --- Writing -------------------------------------------------------------------

def write_glb(doc, path, up):
    # OCCT's glTF writer fails on Windows paths longer than 260 characters,
    # so write to a short temp path and move the result into place.
    fd, tmp = tempfile.mkstemp(suffix=".glb")
    os.close(fd)
    try:
        _write_glb(doc, tmp, up)
        shutil.move(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _write_glb(doc, path, up):
    writer = RWGltf_CafWriter(TCollection_AsciiString(path), True)
    conv = writer.ChangeCoordinateSystemConverter()
    conv.SetInputLengthUnit(MODEL_UNIT_M)
    system = RWMesh_CoordinateSystem.RWMesh_CoordinateSystem_Yup if up == "y" \
        else RWMesh_CoordinateSystem.RWMesh_CoordinateSystem_Zup
    conv.SetInputCoordinateSystem(system)
    writer.SetMergeFaces(True)  # one primitive per color instead of one per CAD face
    if not writer.Perform(doc, FileInfoMap(), Message_ProgressRange()):
        raise RuntimeError(f"Cannot write glTF file: {path}")


# --- Conversion ------------------------------------------------------------------

def convert(doc, glb_path, deflection=0.002, angle=30.0, up="z", flt=None):
    """Filter, mesh and write a document from read_step. Returns report lines.

    The document is changed, so read it again for another conversion.
    """
    stats = apply_filter(doc, flt or Filter(), deflection, angle)
    write_glb(doc, glb_path, up)
    diag = stats["scene_diag"]
    removed = ", ".join(f"{n} {r}" for r, n in stats["dropped"].items() if n)
    return [
        f"bodies kept: {stats['kept']} of {stats['total']}, triangles: {stats['triangles']}",
        f"deflection {deflection} x {diag * MODEL_UNIT_M:.2f} m = {deflection * diag:.1f} mm, "
        f"angle {angle} deg, up {up}",
    ] + ([f"removed: {removed}"] if removed else [])


def main(argv):
    p = argparse.ArgumentParser(description="Convert STEP to GLB with colors.")
    p.add_argument("input")
    p.add_argument("output", nargs="?")
    p.add_argument("--deflection", type=float, default=0.002)
    p.add_argument("--angle", type=float, default=30.0)
    p.add_argument("--up", choices=["y", "z"], default="z")
    p.add_argument("--exclude", nargs="+", default=[], metavar="PATH",
                   help="instance paths from --list, such as 0/2")
    p.add_argument("--max-size", type=float, help="drop smaller bodies, meters")
    p.add_argument("--hidden", action="store_true", help="drop hidden bodies (needs Blender)")
    p.add_argument("--list", type=int, nargs="?", const=3, metavar="DEPTH",
                   help="print the assembly tree and exit")
    a = p.parse_args(argv)
    doc = read_step(a.input)
    if a.list is not None:
        print_tree(doc, a.list)
        return
    if not a.output:
        p.error("output is required")
    flt = Filter({tuple(int(i) for i in path.split("/")) for path in a.exclude},
                 a.max_size, a.hidden)
    report = convert(doc, a.output, a.deflection, a.angle, a.up, flt)
    print(f"Wrote {a.output}")
    for line in report:
        print(f"  {line}")


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
