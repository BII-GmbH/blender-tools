"""Textures for the equipment FBX: every texture packed into the FBX, as PNG or JPEG.

dProB reads embedded textures (FBX Video nodes with content). Blender's FBX exporter embeds an
image only when it can read its file or packed data, and keeps its format. Before the export,
every image the rig's materials use is checked:
- PNG or JPEG with a readable file or packed data: exported as it is.
- Any other format (TIFF, TGA, EXR, BMP, WebP, ...), or no readable source (generated, missing
  file, edited and not saved): written as PNG into a temporary folder and used instead.
The materials point to the substitutes only during the export.
"""

import os

import bpy
import numpy as np

from .rig_props import RigError

KEEP_FORMATS = {"PNG", "JPEG"}
KEEP_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def _node_trees(tree, seen):
    if tree is None or tree in seen:
        return
    seen.add(tree)
    yield tree
    for n in tree.nodes:
        if n.type == "GROUP":
            yield from _node_trees(n.node_tree, seen)


def image_nodes(objs):
    """Image texture nodes of the materials of objs: [(node, image)]."""
    seen, out = set(), []
    for o in objs:
        for slot in getattr(o, "material_slots", ()):
            m = slot.material
            if m is None or not m.use_nodes:
                continue
            for tree in _node_trees(m.node_tree, seen):
                for n in tree.nodes:
                    if n.type == "TEX_IMAGE" and n.image is not None:
                        out.append((n, n.image))
    return out


def _readable_source(img):
    if img.packed_file is not None:
        return True
    path = bpy.path.abspath(img.filepath, library=img.library)
    return bool(img.filepath) and os.path.isfile(path)


def needs_png(img):
    """Why the image must be converted, or None if it can be embedded as it is."""
    if img.source not in {"FILE", "GENERATED"}:
        raise RigError(f"texture {img.name}: {img.source.lower()} images cannot be exported; "
                       "use a single image")
    if img.source == "GENERATED":
        return "generated"
    if img.is_dirty:
        return "edited, not saved"
    if not _readable_source(img):
        return "file missing"
    ext = os.path.splitext(img.filepath)[1].lower()
    if img.packed_file is None and ext not in KEEP_EXTENSIONS:
        return f"{ext or 'no extension'}"
    if img.file_format not in KEEP_FORMATS:
        return img.file_format.lower()
    return None


def _linear_to_srgb(c):
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(np.clip(c, 0, None), 1 / 2.4) - 0.055)


def write_png(img, path):
    """Writes the image's pixels as an 8-bit RGBA PNG; returns the new image datablock."""
    w, h = img.size
    if not img.has_data or w == 0 or h == 0:
        raise RigError(f"texture {img.name} has no pixels (file missing?); fix or remove it")
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    color_space = img.colorspace_settings.name
    is_data = img.colorspace_settings.is_data
    if img.is_float and not is_data:
        # float buffers are linear; an 8-bit color texture is stored sRGB-encoded
        px = px.reshape(-1, 4)
        px[:, :3] = _linear_to_srgb(px[:, :3])
        px = px.reshape(-1)
        color_space = "sRGB"
    out = bpy.data.images.new(os.path.splitext(os.path.basename(path))[0], w, h, alpha=True)
    try:
        out.colorspace_settings.name = color_space
    except TypeError:
        pass
    out.pixels.foreach_set(np.clip(px, 0.0, 1.0))
    out.filepath_raw = path
    out.file_format = "PNG"
    out.save()
    return out


class StagedTextures:
    """Context manager: materials use PNG substitutes during the export, then their own images."""

    def __init__(self, objs, folder):
        self.objs = objs
        self.folder = folder
        self.swaps = []          # (node, original image)
        self.created = []
        self.converted = {}      # original image name -> reason

    def __enter__(self):
        substitutes = {}
        names = set()
        try:
            for node, img in image_nodes(self.objs):
                if img not in substitutes:
                    reason = needs_png(img)
                    if reason is None:
                        substitutes[img] = img
                        continue
                    base = bpy.path.clean_name(os.path.splitext(img.name)[0]) or "texture"
                    name, i = base, 1
                    while name.lower() in names:
                        i += 1
                        name = f"{base}_{i}"
                    names.add(name.lower())
                    new = write_png(img, os.path.join(self.folder, name + ".png"))
                    self.created.append(new)
                    substitutes[img] = new
                    self.converted[img.name] = reason
                if substitutes[img] is not img:
                    self.swaps.append((node, img))
                    node.image = substitutes[img]
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc):
        for node, img in self.swaps:
            node.image = img
        self.swaps = []
        for img in self.created:
            bpy.data.images.remove(img)
        self.created = []


def check_fbx(root_node):
    """Errors about the textures of a parsed FBX: each must be embedded and PNG or JPEG."""
    from . import fbx_binary
    objects = root_node.first("Objects")
    if objects is None:
        return []
    media = {}
    for v in objects.all("Video"):
        content = v.first("Content")
        rel = v.first("RelativeFilename")
        data = content.props[0] if content is not None and content.props else b""
        media[rel.props[0] if rel is not None else ""] = data
    errors = []
    for t in objects.all("Texture"):
        rel = t.first("RelativeFilename")
        name = rel.props[0] if rel is not None and rel.props else ""
        if os.path.splitext(name)[1].lower() not in KEEP_EXTENSIONS:
            errors.append(f"texture {name or fbx_binary.model_name(t.props[1])} is not PNG or JPEG")
        data = media.get(name, b"")
        if not data:
            errors.append(f"texture {name} is not embedded")
        elif not (data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8")):
            errors.append(f"texture {name} is embedded but not PNG or JPEG data")
    return errors
