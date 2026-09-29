import uuid
import bpy
import json
import zipfile
import io
import os
import math
import bmesh
from mathutils import Vector
from math import degrees
from bpy_extras.io_utils import ExportHelper
from collections import defaultdict, deque

def menu_func_export(self, context):
    self.layout.operator(ExportDProBRailAssetOperator.bl_idname, text="dProB Rail Asset (.dasset)")

def register():
    bpy.utils.register_class(ExportDProBRailAssetOperator)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)

def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.utils.unregister_class(ExportDProBRailAssetOperator)

def resample_and_polniearize(obj, handle_distance, merge_threshold):
    """
    Duplicate 'obj', apply all its modifiers on the duplicate,
    and set the duplicate's origin to its geometry's median.
    Returns the new, prepared object.
    """
    # 1. Duplicate the object and its data
    clone = obj.copy()
    clone.data = obj.data.copy()
    obj.users_collection[0].objects.link(clone)

    # 2. If it’s a curve, remove any bevel/taper/profile settings
    if clone.type == 'CURVE':
        cd = clone.data
        # zero out bevel depth and resolution
        cd.bevel_depth = 0.0
        cd.bevel_resolution = 0
        # disable any extrude, taper, or bevel objects
        cd.extrude = 0.0
        cd.taper_object = None
        cd.bevel_object = None
        cd.offset = 0.0

    # 3. Select & activate the clone
    bpy.ops.object.select_all(action='DESELECT')
    clone.select_set(True)
    bpy.context.view_layer.objects.active = clone

    # 4. Convert to mesh (bakes all modifiers, and now there’s no curve profile)
    bpy.ops.object.convert(target='MESH')

    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    raw_polylines = extract_polylines_from_mesh(clone.data, distance=merge_threshold)

    sampled_polylines = [
        resample_polyline_at_fixed_interval(pl, target_step=handle_distance)
        for pl in raw_polylines
    ]

    rails = []
    for poly in sampled_polylines:
        margin_in = 2 * poly[0] - poly[1]
        margin_out = 2 * poly[-1] - poly[-2]
        rails.append([margin_in] + poly + [margin_out])

    bpy.data.objects.remove(clone, do_unlink=True)
    return rails

def extract_polylines_from_mesh(mesh, sharp_angle_threshold=90, distance=1e-1):
    """
    Given a Mesh datablock whose edges form one or more 1D graphs,
    return a list of polylines (each a list of (x,y,z) coords).
    
    Splits occur at vertices of valence != 2.
    """
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=distance)

    # Build adjacency map: vertex index -> set of connected vertex indices
    adjacency = {}
    for edge in bm.edges:
        v1, v2 = edge.verts[0].index, edge.verts[1].index
        adjacency.setdefault(v1, set()).add(v2)
        adjacency.setdefault(v2, set()).add(v1)

    # Build edge set for tracking which edges we've used
    unused_edges = {frozenset((e.verts[0].index, e.verts[1].index)) for e in bm.edges}
    polylines = []

    def walk(start_idx, prev_idx=None):
        path = [start_idx]
        current = start_idx

        while True:
            neighbors = [v for v in adjacency[current] if v != prev_idx]
            if len(neighbors) != 1:
                break

            next_idx = neighbors[0]
            edge_key = frozenset((current, next_idx))
            if edge_key not in unused_edges:
                break

            # Check angle at current vertex
            if prev_idx is not None:
                bm.verts.ensure_lookup_table()
                v_prev = bm.verts[prev_idx].co
                v_curr = bm.verts[current].co
                v_next = bm.verts[next_idx].co

                dir1 = (v_curr - v_prev).normalized()
                dir2 = (v_next - v_curr).normalized()

                angle = dir1.angle(dir2, any)
                if degrees(angle) > sharp_angle_threshold:
                    break  # split at sharp turn

            unused_edges.remove(edge_key)
            path.append(next_idx)
            prev_idx, current = current, next_idx

        return path

    # 1. Handle endpoints first (valence 1)
    endpoints = [vid for vid, nbrs in adjacency.items() if len(nbrs) == 1]
    for ep in endpoints:
        for neighbor in adjacency[ep]:
            edge_key = frozenset((ep, neighbor))
            if edge_key in unused_edges:
                unused_edges.remove(edge_key)
                path = walk(neighbor, prev_idx=ep)
                polylines.append([ep] + path)

    # 2. Handle remaining edges (loops or chains with valence=2)
    while unused_edges:
        edge = next(iter(unused_edges))
        a, b = tuple(edge)
        unused_edges.remove(edge)
        forward = walk(b, prev_idx=a)
        backward = walk(a, prev_idx=b)
        backward.reverse()
        full = backward[:-1] + [a] + forward
        polylines.append(full)

    # Convert vertex indices to positions
    result = []
    bm.verts.ensure_lookup_table()
    for poly in polylines:
        coords = [bm.verts[i].co.copy() for i in poly]
        result.append(coords)

    bm.free()
    return result

def resample_polyline_at_fixed_interval(polyline, target_step=5.0):
    """
    Given an ordered list of Vector points (the polyline),
    returns a new list of Vector points sampled so that:
      - the first and last points are included,
      - the distance between consecutive points is constant,
      - that constant is total_length // target_step (rounded up) exactly partitioned.

    :param polyline: list[Vector] original vertices
    :param target_step: preferred spacing in meters
    :return: list[Vector] resampled points
    """
    if len(polyline) < 2:
        return [p.copy() for p in polyline]

    # 1. Compute segment lengths and cumulative lengths
    seg_lens = []
    for i in range(len(polyline) - 1):
        seg_lens.append((polyline[i+1] - polyline[i]).length)
    total_length = sum(seg_lens)

    # 2. Determine number of intervals (must be at least 1)
    count = max(1, int(round(total_length / target_step)))
    # Recompute step so it partitions exactly
    step = total_length / count

    resampled = [polyline[0].copy()]
    distances = [0.0]  # cumulative along resampled

    # 3. Walk through target distances 1*step, 2*step, ..., (count-1)*step
    seg_idx = 0
    seg_acc = 0.0  # how far we've walked along current segment

    for n in range(1, count):
        target_d = n * step
        # Advance seg_idx until cumulative segment length surpasses target_d
        while seg_idx < len(seg_lens) and (seg_acc + seg_lens[seg_idx]) < target_d:
            seg_acc += seg_lens[seg_idx]
            seg_idx += 1
        # if we've run out of segments, clamp to last point
        if seg_idx >= len(seg_lens):
            resampled.append(polyline[-1].copy())
            distances.append(total_length)
            continue

        # We know target_d lies within segment seg_idx between
        # original points polyline[seg_idx] and polyline[seg_idx+1]
        local_t = (target_d - seg_acc) / seg_lens[seg_idx]
        p = polyline[seg_idx].lerp(polyline[seg_idx+1], local_t)
        resampled.append(p.copy())
        distances.append(target_d)

    # 4. Finally append the exact last point
    resampled.append(polyline[-1].copy())
    distances.append(total_length)

    return resampled

class ExportDProBRailAssetOperator(bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.dprobrailasset"
    bl_label = "Export dProB Rail Asset (.dasset)"
    bl_description = 'Export splines in dProB Rail Asset format'
    filename_ext = ".dasset"

    export_selected_only: bpy.props.BoolProperty(
         name="Selected Only",
         description="Export only selected objects (vs all curve objects)",
         default=True,
    )

    east: bpy.props.FloatProperty(
         name="East GeoLocation",
         default=0,
         step=1,
    )

    north: bpy.props.FloatProperty(
         name="North GeoLocation",
         default=0,
         step=1,
    )

    elevation: bpy.props.FloatProperty(
         name="Elevation GeoLocation",
         default=0,
         step=1,
    )

    apply_geolocation: bpy.props.BoolProperty(
         name="Subtract geolocation from coordinates",
         default=True,
    )

    handle_distance: bpy.props.FloatProperty(
         name="Resolution",
         min=1,
         default=5,
         step=1,
    )

    merge_threshold: bpy.props.FloatProperty(
         name="Merge Threshold",
         default=0.1,
         step=0.05,
    )

    @classmethod
    def description(cls, context, properties):
        return "Export splines in dProB Rail Asset format"

    def execute(self, context):
        if context.space_data is None or context.space_data.type == 'FILE_BROWSER':
            return {'CANCELLED'}
        if not self.filepath:
            self.report({'ERROR'}, "No filepath provided.")
            return {'CANCELLED'}

        if self.export_selected_only:
            curve_objs = [obj for obj in context.selected_objects]
        else:
            curve_objs = [obj for obj in bpy.data.objects if obj.type == 'CURVE']

        # Get filename without extension
        file_base_name = os.path.splitext(os.path.basename(self.filepath))[0]

        export_data = {
            "Name": file_base_name,
            "GeoLocation": {
                "East": self.east,
                "Elevation": self.elevation,
                "North": self.north
            },
            "Rails": []
        }

        amount_done = 0
        for obj in curve_objs:
            for sampled_pts in resample_and_polniearize(obj, self.handle_distance, self.merge_threshold):
                spline_data = {
                    "Name": obj.name,
                    "SplineHandles": 
                        [{"X": pt.x - self.east, "Y": pt.z - self.elevation, "Z": pt.y - self.north} for pt in sampled_pts] if self.apply_geolocation else [{"X": pt.x, "Y": pt.z, "Z": pt.y} for pt in sampled_pts],
                    "SplineType": "Centripetal"
                }
                export_data["Rails"].append(spline_data)
                amount_done += 1

        json_str = json.dumps(export_data, indent=4)

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr("model.json", json_str)
            zip_file.writestr("dProB_asset_metadata.json", '{"Format":"Rails","ProductVersion":"Simulation 2024.2.9"}')
            zip_file.writestr("asset_guid.txt", str(uuid.uuid4()))

        try:
            with open(self.filepath, "wb") as f:
                f.write(zip_buffer.getvalue())
        except Exception as e:
            self.report({'ERROR'}, f"Failed to write file: {e}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Exported {amount_done} curve(s).")
        return {'FINISHED'}

    @classmethod
    def poll(cls, context):
        return context.space_data is not None and context.space_data.type != 'FILE_BROWSER'