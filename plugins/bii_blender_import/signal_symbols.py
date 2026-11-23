"""Draw the Lageplan symbols of railway signals. Pure Python with numpy, no bpy.

The symbols follow the signal symbols of the Signallageplan for the H/V and Ks
systems (heads, Zs 1 / Zs 7 marks, Zs 3 / Zs 3v triangles, Zs 6 square, ...).
They are drawn from simple shapes, so this runs in Blender's own Python: the
Signal panel draws its key and the preview of a combination with it, and the
DB_Signale asset build uses it for the asset previews.

A symbol is a list of elements, stacked from the foot of the mast upwards, for
example [("foot",), ("stem", 14), ("ks_head", "bell", "full", False), ("chev",)].
signal_spec.symbol_elements() builds this list for a signal configuration.

draw() returns preview pixels.
"""

import math

import numpy as np

SIZE = 256
SUPERSAMPLE = 3
LINE = 1.4


class Canvas:
    def __init__(self):
        self.prims = []  # ("poly", pts, value) or ("circ", cx, cy, r, value); value 0 = black, 1 = white

    def poly(self, pts, val=0):
        self.prims.append(("poly", [tuple(p) for p in pts], val))

    def circ(self, cx, cy, r, val=0):
        self.prims.append(("circ", cx, cy, r, val))

    def rect(self, x0, y0, x1, y1, val=0):
        self.poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], val)

    def line(self, pts, w=LINE, val=0):
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            d = np.array([x1 - x0, y1 - y0], float)
            n = np.array([-d[1], d[0]]) / (np.linalg.norm(d) or 1) * w / 2
            self.poly([(x0 + n[0], y0 + n[1]), (x1 + n[0], y1 + n[1]), (x1 - n[0], y1 - n[1]), (x0 - n[0], y0 - n[1])], val)
            self.circ(x0, y0, w / 2, val)
            self.circ(x1, y1, w / 2, val)

    def ring(self, cx, cy, r, w=LINE * 0.9):
        self.circ(cx, cy, r, 0)
        self.circ(cx, cy, r - w, 1)

    def bounds(self):
        xs, ys = [], []
        for p in self.prims:
            if p[0] == "poly":
                xs += [q[0] for q in p[1]]
                ys += [q[1] for q in p[1]]
            else:
                xs += [p[1] - p[3], p[1] + p[3]]
                ys += [p[2] - p[3], p[2] + p[3]]
        return min(xs), min(ys), max(xs), max(ys)

    def _fit(self, size):
        """(scale, ox, oy): symbol units to pixels of a square image of size, fitted into 84 % of it."""
        x0, y0, x1, y1 = self.bounds()
        scale = 0.84 * size / max(x1 - x0, y1 - y0)
        return scale, size / 2 - (x0 + x1) / 2 * scale, size / 2 - (y0 + y1) / 2 * scale

    def pixel(self, x, y, size=SIZE):
        """(column, row from the top) of a point of the symbol in the image of gray(size)."""
        scale, ox, oy = self._fit(size)
        return x * scale + ox, size - (y * scale + oy)

    def gray(self, size=SIZE, supersample=SUPERSAMPLE):
        """Gray pixels (1 = paper, 0 = ink) of a square image, symbol fitted into 84 % of it,
        rows from the bottom."""
        w = h = size
        nw, nh = w * supersample, h * supersample
        img = np.ones((nh, nw), np.float32)
        scale, ox, oy = self._fit(nw)
        for p in self.prims:
            if p[0] == "poly":
                pts = [(q[0] * scale + ox, q[1] * scale + oy) for q in p[1]]
                _fill_poly(img, pts, p[2])
            else:
                _fill_circ(img, p[1] * scale + ox, p[2] * scale + oy, p[3] * scale, p[4])
        return img.reshape(h, supersample, w, supersample).mean(axis=(1, 3))

    def render(self, size=SIZE):
        """Return RGBA float pixels (size x size), rows from the bottom (Blender's order)."""
        img = self.gray(size)
        rgba = np.ones((size, size, 4), np.float32)
        rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = img
        return rgba


def _window(img, x0, y0, x1, y1):
    nh, nw = img.shape
    i0, i1 = max(int(np.floor(y0)), 0), min(int(np.ceil(y1)) + 1, nh)
    j0, j1 = max(int(np.floor(x0)), 0), min(int(np.ceil(x1)) + 1, nw)
    if i0 >= i1 or j0 >= j1:
        return None
    ys, xs = np.mgrid[i0:i1, j0:j1].astype(np.float32) + 0.5
    return i0, i1, j0, j1, xs, ys


def _fill_poly(img, pts, val):
    px = [p[0] for p in pts]
    py = [p[1] for p in pts]
    w = _window(img, min(px), min(py), max(px), max(py))
    if w is None:
        return
    i0, i1, j0, j1, X, Y = w
    inside = np.zeros(X.shape, bool)
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if yi != yj:
            cond = ((yi > Y) != (yj > Y)) & (X < (xj - xi) * (Y - yi) / (yj - yi) + xi)
            inside ^= cond
        j = i
    img[i0:i1, j0:j1][inside] = val


def _fill_circ(img, cx, cy, r, val):
    w = _window(img, cx - r, cy - r, cx + r, cy + r)
    if w is None:
        return
    i0, i1, j0, j1, X, Y = w
    img[i0:i1, j0:j1][(X - cx) ** 2 + (Y - cy) ** 2 <= r * r] = val


def _tri(c, pts, filled):
    c.poly(pts, 0)
    if not filled:
        cx = sum(p[0] for p in pts) / 3
        cy = sum(p[1] for p in pts) / 3
        c.poly([(cx + (x - cx) * 0.62, cy + (y - cy) * 0.62) for x, y in pts], 1)


def _arc(cx, cy, r, a0, a1, n=24):
    return [(cx + r * math.cos(a0 + (a1 - a0) * i / n), cy + r * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]


def _outline(shape, y, extra=0.0):
    """Outline polygon and height of a signal head shape with its bottom at y; extra makes the
    straight part longer (room for the Sh 1 bars)."""
    if shape == "circle":
        return _arc(0, y + 5, 5, 0, 2 * math.pi, 48)[:-1], 10
    if shape == "bell":  # Ks Hauptsignal: rounded top
        return [(-5, y), (5, y)] + _arc(0, y + 6 + extra, 5, 0, math.pi), 11 + extra
    if shape == "cup":  # Ks Vorsignal: rounded bottom
        return _arc(0, y + 5, 5, math.pi, 2 * math.pi) + [(5, y + 10 + extra), (-5, y + 10 + extra)], 10 + extra
    if shape == "caps":  # Ks Mehrabschnittssignal: rounded top and bottom
        return _arc(0, y + 5, 5, math.pi, 2 * math.pi) + _arc(0, y + 12 + extra, 5, 0, math.pi), 17 + extra
    raise ValueError(shape)


SH_PART = 4.6  # length of the Sh 1 part of a Ks head


def _sh_part(shape, y):
    """(bottom, top) of the Sh 1 part of a Ks head with its bottom at y: the lower end of a
    Hauptsignal, the middle of a Mehrabschnittssignal."""
    if shape == "caps":
        return y + 8.5, y + 8.5 + SH_PART
    return y, y + SH_PART


def _inset(pts, w=1.0):
    """Shrink a polygon towards its center by about w."""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    hx, hy = (max(xs) - min(xs)) / 2, (max(ys) - min(ys)) / 2
    return [(cx + (x - cx) * (hx - w) / hx, cy + (y - cy) * (hy - w) / hy) for x, y in pts]


def _clip(pts, d):
    """Part of a polygon where d(x, y) >= 0, d linear (Sutherland-Hodgman with one edge)."""
    out = []
    for i, (x0, y0) in enumerate(pts):
        x1, y1 = pts[(i + 1) % len(pts)]
        d0, d1 = d(x0, y0), d(x1, y1)
        if d0 >= 0:
            out.append((x0, y0))
        if (d0 >= 0) != (d1 >= 0):
            t = d0 / (d0 - d1)
            out.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
    return out


def _clip_right(pts):
    """Part of a polygon with x >= 0."""
    return _clip(pts, lambda x, y: x)


def anchor(elements, size=SIZE):
    """(column, row from the top) of the signal's origin in draw(elements, size): the middle of the
    foot bar, or the bottom of the symbol without a mast. The 3D signal's origin belongs there."""
    return build(elements).pixel(0.0, 0.0, size)


def draw(elements, size=SIZE):
    """Draw a symbol from its elements; returns RGBA pixels (size x size), rows from the bottom."""
    return build(elements).render(size)


def _head(c, shape, fill, y, sh=False):
    """Draw a head shape filled ("full"), right half filled ("half") or empty; returns its height.

    sh: Ks head with Sh 1. The head gets longer by a part with two bars across it: on a
    filled head two white bars, on a half filled or empty head two empty fields divided by
    lines across the whole head (the fill stays outside that part).
    """
    pts, h = _outline(shape, y, SH_PART if sh else 0.0)
    c.poly(pts, 0)
    inner = _inset(pts)
    if fill != "full":
        c.poly(inner, 1)
        if fill == "half":
            c.poly(_clip_right(inner), 0)
    if sh:
        b0, b1 = _sh_part(shape, y)
        if fill == "full":
            for k in (0.3, 0.7):
                c.rect(-3.9, b0 + SH_PART * k - 0.4, 3.9, b0 + SH_PART * k + 0.4, 1)
        else:
            c.poly(_clip(_clip(inner, lambda x, yy: yy - b0), lambda x, yy: b1 - yy), 1)
            lines = (b0, (b0 + b1) / 2, b1) if shape == "caps" else ((b0 + b1) / 2, b1)
            for ly in lines:
                c.line([(-4.4, ly), (4.4, ly)], 0.9)
    return h


def build(elements):
    """Canvas with the drawing of a symbol."""
    c = Canvas()
    y = 0.0
    stem_top = None
    has_foot = any(el[0] == "foot" for el in elements)
    heads = []  # (y0, y1) of each head shape, for marks at its side
    for el in elements:
        kind, args = el[0], el[1:]
        if kind == "foot":
            c.line([(-5, 0), (5, 0)])
        elif kind == "stem":
            y += args[0]
        elif kind == "dots":  # light distant signal: two lights rising to the right
            zus = args[0] if args else False
            c.circ(-1.7, y + 2.8, 1.5)
            c.circ(1.7, y + 6, 1.5)
            if zus:
                c.ring(-4.2, y + 6, 1.5, 1.1)
            y += 7.5
        elif kind in ("dots_half", "dots_hollow"):
            # distant signal lights: lower left empty; upper right filled (half) or empty (hollow)
            c.ring(-1.7, y + 2.8, 1.5, 0.9)
            if kind == "dots_half":
                c.circ(1.7, y + 6, 1.5)
            else:
                c.ring(1.7, y + 6, 1.5, 0.9)
            y += 7.5
        elif kind == "hv_dots":  # ("hv_dots", fill, zus): distant signal lights rising to the right
            fill, zus = args
            if fill == "full":
                c.circ(-1.7, y + 2.8, 1.5)
            else:
                c.ring(-1.7, y + 2.8, 1.5, 0.9)
            if fill == "empty":
                c.ring(1.7, y + 6, 1.5, 0.9)
            else:
                c.circ(1.7, y + 6, 1.5)
            if zus:
                c.ring(-4.2, y + 6, 1.5, 1.1)
            y += 7.5
        elif kind == "tri_down":
            _tri(c, [(-4, y + 6), (4, y + 6), (0, y + 0.3)], args[0])
            y += 6.5
        else:
            if stem_top is None:
                stem_top = y
            y0 = y
            if kind == "ks_head":  # ("ks_head", shape, fill, sh): Ks head, shape bell / cup / caps
                shape, fill, sh = args
                y += _head(c, shape, fill, y, sh)
            elif kind == "hv_head":  # ("hv_head", circles, fill, bar): H/V main signal lights
                n, fill, bar = args
                for k in range(n):
                    y += _head(c, "circle", fill, y)
                if bar:
                    c.line([(6.8, y0 + 1.5), (6.8, y - 1.5)])
            elif kind == "hv":
                for k in range(args[0]):
                    c.circ(0, y + 5, 5)
                    y += 10
                if len(args) > 1 and args[1]:
                    c.line([(6.8, y0 + 1.5), (6.8, y - 1.5)])
            elif kind in ("hollow", "half"):
                # empty head (zugbedient) or right half filled (stellwerks- oder zugbedient)
                pts, h = _outline(args[0], y)
                inner = _inset(pts)
                c.poly(pts, 0)
                c.poly(inner, 1)
                if kind == "half":
                    c.poly(_clip_right(inner), 0)
                y += h
            elif kind == "shbox":
                c.rect(-4, y, 4, y + 6)
                c.rect(-3, y + 0.9, 3, y + 5.1, 1)
                c.line([(-3, y + 3), (3, y + 3)], 0.9)
                y += 6
            elif kind in ("ks_bell", "ks_caps", "ks_cup", "ks_shield"):
                sh = args[0] if args else False
                if kind == "ks_bell":
                    c.rect(-5, y, 5, y + 6)
                    c.circ(0, y + 6, 5)
                    y += 11
                elif kind == "ks_caps":
                    c.circ(0, y + 5, 5)
                    c.rect(-5, y + 5, 5, y + 12)
                    c.circ(0, y + 12, 5)
                    y += 17
                else:
                    c.circ(0, y + 5, 5)
                    c.rect(-5, y + 5, 5, y + 10)
                    y += 10
                if sh:
                    c.rect(-3.8, y0 + 1.6, 3.8, y0 + 5.2, 1)
                    c.line([(-3.8, y0 + 3.4), (3.8, y0 + 3.4)], 0.9)
            elif kind == "ls":
                c.rect(-4.5, y, 4.5, y + 6)
                c.rect(-3.5, y + 0.9, 3.5, y + 5.1, 1)
                c.line([(-3.5, y + 3), (3.5, y + 3)], 0.9)
                y += 6
            elif kind == "kenn":  # Rangierkennlicht: small circle at the top right corner of the Ls
                top = heads[-1][1] if heads else y
                c.ring(4.5, top + 0.6, 1.6)
            elif kind == "disc":
                c.ring(0, y + 5, 5, 1.2)
                c.line([(-3.4, y + 5), (3.4, y + 5)], 1.8)
                y += 10
            elif kind == "square":
                c.rect(-4, y, 4, y + 8)
                if not args[0]:
                    c.rect(-3, y + 1, 3, y + 7, 1)
                y += 8
            elif kind == "zs6":
                c.rect(-4, y, 4, y + 8)
                c.rect(-3, y + 1, 3, y + 7, 1)
                c.poly([(-3, y + 1), (3, y + 1), (-3, y + 7)])
                y += 8
            elif kind == "lf6":  # speed announcement sign: triangle pointing down, on the mast
                _tri(c, [(-4.5, y + 7.5), (4.5, y + 7.5), (0, y)], False)
                y += 7.5
            elif kind == "bake":  # Ne 3: tall board with args[0] stripes rising to the right
                n = args[0]
                c.rect(-3, y, 3, y + 13)
                c.rect(-2.1, y + 0.9, 2.1, y + 12.1, 1)
                for k in range(n):
                    yc = y + 6.5 + (k - (n - 1) / 2) * min(2.4, 9.0 / n)
                    c.line([(-2.1, yc - 1.4), (2.1, yc + 1.4)], 0.8)
                y += 13
            elif kind == "ne4":  # Schachbretttafel: 2 x 2 checker, top right and bottom left black
                c.rect(-4.5, y, 4.5, y + 10)
                c.rect(-3.6, y + 0.9, 3.6, y + 9.1, 1)
                c.rect(0, y + 5, 3.6, y + 9.1)
                c.rect(-3.6, y + 0.9, 0, y + 5)
                y += 10
            elif kind == "lf7":  # speed sign: upright rectangle
                c.rect(-4, y, 4, y + 10)
                c.rect(-2.9, y + 1.1, 2.9, y + 8.9, 1)
                y += 10
            elif kind == "ne2":  # Vorsignaltafel: board with a cross from corner to corner
                c.rect(-4, y, 4, y + 10)
                c.rect(-3.1, y + 0.9, 3.1, y + 9.1, 1)
                c.line([(-3.1, y + 0.9), (3.1, y + 9.1)], 0.8)
                c.line([(-3.1, y + 9.1), (3.1, y + 0.9)], 0.8)
                y += 10
            elif kind == "tri_up":
                _tri(c, [(-4.5, y + 0.8), (4.5, y + 0.8), (0, y + 7)], args[0])
                y += 7
            elif kind == "chev":
                c.line([(-4.5, y + 1), (0, y + 5.5), (4.5, y + 1)])
                y += 5.5
            elif kind == "vee":
                c.line([(-4.5, y + 6.5), (0, y + 2), (4.5, y + 6.5)])
                y += 6.5
            elif kind == "cross":
                c.line([(-4, y + 1), (4, y + 7)])
                c.line([(-4, y + 7), (4, y + 1)])
                y += 7
            elif kind == "zus":  # ("zus"[, "unten"]): additional light left of the last head, top or bottom
                if args and args[0] == "unten":
                    hy = heads[-1][0] + 1.2 if heads else y
                else:
                    hy = (heads[-1][1] if heads else y) - 1.2
                c.ring(-6.6, hy, 1.8)
            else:
                raise ValueError(kind)
            heads.append((y0, y))
    if stem_top is None:
        stem_top = y
    if has_foot and stem_top > 0:
        c.line([(0, 0), (0, stem_top)])
    return c
