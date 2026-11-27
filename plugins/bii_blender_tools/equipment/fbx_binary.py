"""Minimal reader for binary FBX files (versions 7.x), enough to read what dProB reads.

Blender's FBX exporter writes binary FBX 7.4. dProB reads the models, their local transforms,
their parent connections and the global settings from it; this module reads the same data so
the exporter can compute the equipment hierarchy exactly as dProB's FBX import does.
"""

import struct
import zlib

MAGIC = b"Kaydara FBX Binary  \x00"


class Node:
    __slots__ = ("name", "props", "children")

    def __init__(self, name, props, children):
        self.name = name
        self.props = props
        self.children = children

    def first(self, name):
        for c in self.children:
            if c.name == name:
                return c
        return None

    def all(self, name):
        return [c for c in self.children if c.name == name]

    def __repr__(self):
        return f"Node({self.name!r}, {self.props!r}, {len(self.children)} children)"


_ARRAY = {b"f": ("f", 4), b"d": ("d", 8), b"l": ("q", 8), b"i": ("i", 4), b"b": ("?", 1)}
_SCALAR = {b"Y": ("<h", 2), b"C": ("<?", 1), b"I": ("<i", 4), b"F": ("<f", 4), b"D": ("<d", 8), b"L": ("<q", 8)}


def _read_prop(data, pos):
    code = data[pos:pos + 1]
    pos += 1
    if code in _SCALAR:
        fmt, size = _SCALAR[code]
        return struct.unpack_from(fmt, data, pos)[0], pos + size
    if code in _ARRAY:
        length, encoding, clen = struct.unpack_from("<III", data, pos)
        pos += 12
        raw = data[pos:pos + clen]
        pos += clen
        if encoding == 1:
            raw = zlib.decompress(raw)
        fmt, size = _ARRAY[code]
        return list(struct.unpack(f"<{length}{fmt}", raw[:length * size])), pos
    if code in (b"S", b"R"):
        (length,) = struct.unpack_from("<I", data, pos)
        pos += 4
        raw = bytes(data[pos:pos + length])
        pos += length
        if code == b"S":
            return raw.decode("utf-8", errors="replace"), pos
        return raw, pos
    raise ValueError(f"unknown FBX property type {code!r} at {pos - 1}")


def _read_node(data, pos, wide):
    if wide:
        end, nprops, _plen = struct.unpack_from("<QQQ", data, pos)
        pos += 24
    else:
        end, nprops, _plen = struct.unpack_from("<III", data, pos)
        pos += 12
    name_len = data[pos]
    pos += 1
    if end == 0:
        return None, pos + name_len
    name = bytes(data[pos:pos + name_len]).decode("ascii", errors="replace")
    pos += name_len
    props = []
    for _ in range(nprops):
        value, pos = _read_prop(data, pos)
        props.append(value)
    children = []
    while pos < end:
        child, pos = _read_node(data, pos, wide)
        if child is None:
            break
        children.append(child)
    return Node(name, props, children), end


def parse(data):
    """Top-level nodes of a binary FBX file given as bytes."""
    if not data.startswith(MAGIC):
        raise ValueError("not a binary FBX file")
    (version,) = struct.unpack_from("<I", data, 23)
    wide = version >= 7500
    pos = 27
    nodes = []
    while pos < len(data):
        node, pos = _read_node(data, pos, wide)
        if node is None:
            break
        nodes.append(node)
    return Node("", [version], nodes)


def read(path):
    with open(path, "rb") as f:
        return parse(f.read())


def model_name(raw):
    """Object name of a Model's name property ("Cube\\x00\\x01Model" -> "Cube")."""
    return raw.split("\x00\x01")[0]


def properties70(node):
    """{name: values} of a node's Properties70 block."""
    block = node.first("Properties70") if node is not None else None
    if block is None:
        return {}
    return {p.props[0]: p.props[4:] for p in block.all("P") if p.props}
