"""Math for placing plans by Kilometrierung and joining sheets. Pure Python, no bpy.

DB Lagepläne often have no grid crosses, only the Kilometrierung of the
Strecke (hectometer labels like 5,3 / 5,4 along the track) and landmarks
labeled with Strecke and km ("EÜ (Weg) 5240/5,504/1616"). The plan is placed
by a rigid fit in the ground plane, with the Maßstab locked: rotation about
the vertical axis and a shift.

Observations, all in scene meters (XY):

- POINT: a plan point and the scene point it belongs on (landmarks from the
  3D cursor, the shared point of a match line).
- DIR: a plan point that must lie on a line through a scene point, measured
  along one direction only (the km of a point fixes its position along the
  track, not across it).
- LINE: a plan point that must lie on one of the scene's track lines; the
  nearest point is taken again in every iteration (points on the plan track
  without km, and the cross-track part of km points).

KmLine maps km to scene positions along a Strecke, built from the
Kilometrierung lines of the Umgebung extension.
"""

import math
import re

# ---------------------------------------------------------------- km input

_KM_RE = re.compile(r"^\s*(-?\d+)(?:[.,](\d*))?\s*(?:\+\s*(\d+(?:[.,]\d*)?))?\s*$")
_LANDMARK_RE = re.compile(
    r"(?<!\d)(\d{4})\s*/\s*(-?\d+[.,]\d+(?:\s*\+\s*\d+(?:[.,]\d+)?)?)")


def parse_km(text):
    """km value of a label as written on DB plans, or None.

    "5,3" and "5.3" are km 5.3, "5,504" km 5.504. "13,4+23,05" (DB notation
    for hectometer plus meters) is km 13.42305. Returns km in kilometers.
    """
    m = _KM_RE.match(text or "")
    if not m:
        return None
    whole, frac, plus = m.groups()
    km = abs(int(whole)) + (float("0." + frac) if frac else 0.0)
    if plus:
        km += float(plus.replace(",", ".")) / 1000.0
    return -km if whole.startswith("-") else km


def parse_label(text):
    """(Strecke, km) from a landmark label like "EÜ (Weg) 5240/5,504/1616".

    Without Strecke number, only a km: (None, km). (None, None) if the text
    has neither.
    """
    km = parse_km(text)
    if km is not None:
        return None, km
    m = _LANDMARK_RE.search(text or "")
    if m:
        return m.group(1), parse_km(m.group(2))
    return None, None


def format_km(km):
    """km the way DB plans write it: 5,3 for hectometers, else 5,504."""
    if abs(km * 10 - round(km * 10)) < 1e-6:
        return f"{km:.1f}".replace(".", ",")
    return f"{km:.3f}".replace(".", ",")


# ---------------------------------------------------------------- polylines

def cumulative(points):
    cum = [0.0]
    for a, b in zip(points, points[1:]):
        cum.append(cum[-1] + math.dist(a, b))
    return cum


def nearest_on_polyline(points, x, y):
    """(distance, (qx, qy), unit tangent, segment index, t) of the point of a
    polyline nearest to (x, y)."""
    best = None
    for i in range(len(points) - 1):
        (ax, ay), (bx, by) = points[i], points[i + 1]
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        if length2 < 1e-18:
            continue
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length2))
        qx, qy = ax + t * dx, ay + t * dy
        d = math.hypot(x - qx, y - qy)
        if best is None or d < best[0]:
            length = math.sqrt(length2)
            best = (d, (qx, qy), (dx / length, dy / length), i, t)
    return best


def _insert_stations(points, stations, tolerance=0.01):
    """(points, cumulative stations) with a vertex added at each station."""
    points = [tuple(p) for p in points]
    cum = cumulative(points)
    extra = sorted(s for s in stations if 0 < s < cum[-1]
                   and min(abs(s - c) for c in cum) > tolerance)
    if not extra:
        return points, cum
    out_p, out_c = [points[0]], [0.0]
    j = 0
    for i in range(1, len(points)):
        while j < len(extra) and extra[j] < cum[i]:
            t = (extra[j] - cum[i - 1]) / (cum[i] - cum[i - 1])
            (ax, ay), (bx, by) = points[i - 1], points[i]
            out_p.append((ax + t * (bx - ax), ay + t * (by - ay)))
            out_c.append(extra[j])
            j += 1
        out_p.append(points[i])
        out_c.append(cum[i])
    return out_p, out_c


class KmLine:
    """A polyline in scene meters with the km of each vertex.

    kms runs monotonic along the line, up or down.
    """

    def __init__(self, points, kms, name=""):
        if len(points) < 2 or len(points) != len(kms):
            raise ValueError("A km line needs 2 or more points, each with its km")
        self.points = [tuple(p) for p in points]
        self.kms = list(kms)
        self.name = name

    @classmethod
    def from_anchors(cls, points, anchors, name=""):
        """km of each vertex, interpolated along the line between anchors.

        anchors: (station in meters from the first point, km). Beyond the
        first and last anchor the km goes on at 1 km per 1000 m. Anchors
        between vertices become vertices, so their km stays exact.
        """
        points, cum = _insert_stations(points, [s for s, _ in anchors])
        anchors = sorted(anchors)
        if not anchors:
            raise ValueError("No km on the line")
        if len(anchors) == 1:
            s0, k0 = anchors[0]
            anchors = [(s0, k0), (s0 + 1000.0, k0 + 1.0)]
        sign = 1.0 if anchors[-1][1] >= anchors[0][1] else -1.0
        kms = []
        j = 0
        for s in cum:
            while j < len(anchors) - 2 and s > anchors[j + 1][0]:
                j += 1
            (s0, k0), (s1, k1) = anchors[j], anchors[j + 1]
            if s < anchors[0][0]:
                kms.append(anchors[0][1] + sign * (s - anchors[0][0]) / 1000.0)
            elif s > anchors[-1][0]:
                kms.append(anchors[-1][1] + sign * (s - anchors[-1][0]) / 1000.0)
            else:
                t = (s - s0) / (s1 - s0) if s1 > s0 else 0.0
                kms.append(k0 + t * (k1 - k0))
        return cls(points, kms, name)

    @property
    def km_range(self):
        return min(self.kms[0], self.kms[-1]), max(self.kms[0], self.kms[-1])

    def covers(self, km, margin=0.0):
        lo, hi = self.km_range
        return lo - margin <= km <= hi + margin

    def position(self, km, margin=0.05):
        """((x, y), unit tangent toward rising km) at a km, or None.

        Up to margin km beyond the ends the line is extended straight.
        """
        if not self.covers(km, margin):
            return None
        ks = self.kms
        rising = ks[-1] >= ks[0]
        n = len(ks)
        for i in range(n - 1):
            k0, k1 = ks[i], ks[i + 1]
            last = i == n - 2
            first = i == 0
            inside = min(k0, k1) <= km <= max(k0, k1)
            before = first and ((km < k0) if rising else (km > k0))
            after = last and ((km > k1) if rising else (km < k1))
            if not (inside or before or after) or k1 == k0:
                continue
            t = (km - k0) / (k1 - k0)
            (ax, ay), (bx, by) = self.points[i], self.points[i + 1]
            dx, dy = bx - ax, by - ay
            length = math.hypot(dx, dy) or 1.0
            sign = 1.0 if k1 > k0 else -1.0
            return (ax + t * dx, ay + t * dy), (sign * dx / length, sign * dy / length)
        return None

    def km_at(self, x, y):
        """(km, distance) of the line point nearest to (x, y)."""
        d, _, _, i, t = nearest_on_polyline(self.points, x, y)
        return self.kms[i] + t * (self.kms[i + 1] - self.kms[i]), d


def ticks_to_anchors(points, km_start, km_end, ticks, interval_m):
    """Anchors (station, km) of a Kilometrierung line from its tick marks.

    The Umgebung extension stores the km at both ends of a line and draws a
    tick every interval_m of km. The ticks carry no values, but they lie on
    round km: each one gets the round value nearest to where the km would be
    counting on from the tick before. So km jumps of a piecewise fit are
    kept to within the interval.
    """
    cum = cumulative(points)
    total = cum[-1]
    sign = 1.0 if km_end >= km_start else -1.0
    step = interval_m / 1000.0 if interval_m > 0 else 0.0
    stations = []
    for x, y in ticks:
        d, _, _, i, t = nearest_on_polyline(points, x, y)
        if d < 1.0:
            stations.append(cum[i] + t * (cum[i + 1] - cum[i]))
    anchors = [(0.0, km_start)]
    for s in sorted(stations):
        if not step or s < 0.5 or s > total - 0.5:
            continue
        prev_s, prev_km = anchors[-1]
        predicted = prev_km + sign * (s - prev_s) / 1000.0
        km = round(predicted / step) * step
        if abs(km - predicted) < 0.4 * step and (km - prev_km) * sign > 0:
            anchors.append((s, round(km, 6)))
    if total - anchors[-1][0] > 0.5:
        anchors.append((total, km_end))
    return anchors


def find_km(lines, km, margin=0.05):
    """(line, (x, y), tangent) of the first line covering km, or None.

    A line covering the km itself wins over one that only reaches it with
    the margin.
    """
    for m in (0.0, margin):
        for line in lines:
            if line.covers(km, m):
                pos = line.position(km, margin)
                if pos:
                    return line, pos[0], pos[1]
    return None


# ---------------------------------------------------------------- rigid fit

class Observation:
    """One measurement for fit_rigid; see the module docstring.

    p: plan point in scene meters at the plan's current place. q, n: scene
    point and unit direction (DIR); q (POINT). lines: polylines (LINE).
    """

    def __init__(self, kind, name, p, q=None, n=None, lines=None, weight=1.0):
        self.kind = kind
        self.name = name
        self.p = tuple(p)
        self.q = tuple(q) if q is not None else None
        self.n = tuple(n) if n is not None else None
        self.lines = lines
        self.weight = weight
        self.residual = -1.0


def _rows(obs, px, py):
    """(direction, target point) rows of one observation at the moved point."""
    if obs.kind == 'POINT':
        return [((1.0, 0.0), obs.q), ((0.0, 1.0), obs.q)]
    if obs.kind == 'DIR':
        return [(obs.n, obs.q)]
    best = None
    for line in obs.lines:
        hit = nearest_on_polyline(line, px, py)
        if hit and (best is None or hit[0] < best[0]):
            best = hit
    if best is None:
        return []
    _, q, (tx, ty), _, _ = best
    return [((-ty, tx), q)]


class RigidFit:
    """Result of fit_rigid: turn by angle about pivot, then shift by (tx, ty)."""

    def __init__(self, angle, pivot, shift, observations, weak):
        self.angle = angle
        self.pivot = pivot
        self.shift = shift
        self.observations = observations
        self.weak = weak  # set of 'ROTATION', 'ALONG' ... the data leave open

    def apply(self, x, y):
        c, s = math.cos(self.angle), math.sin(self.angle)
        dx, dy = x - self.pivot[0], y - self.pivot[1]
        return (self.pivot[0] + c * dx - s * dy + self.shift[0],
                self.pivot[1] + s * dx + c * dy + self.shift[1])

    @property
    def rms(self):
        res = [o.residual for o in self.observations if o.residual >= 0]
        return math.sqrt(sum(r * r for r in res) / len(res)) if res else 0.0


def _solve3(m, v):
    """3x3 linear system by Cramer's rule; None if singular."""
    def det(a):
        return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
                - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
                + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    d = det(m)
    if abs(d) < 1e-300:
        return None
    out = []
    for k in range(3):
        a = [row[:] for row in m]
        for i in range(3):
            a[i][k] = v[i]
        out.append(det(a) / d)
    return out


def fit_rigid(observations, iterations=60, damping=1e-6):
    """Least-squares rotation about the vertical axis plus shift, scale locked.

    Gauss-Newton; LINE observations take their nearest line point again in
    every step, so the plan track slides onto the scene track. A tiny damping
    toward no movement keeps what the data leave open (say, the position
    along a straight track without any km) where the plan lies now; such
    degrees of freedom are reported in RigidFit.weak. Returns None without
    observations.
    """
    obs = [o for o in observations if o.weight > 0]
    if not obs:
        return None
    wsum = sum(o.weight for o in obs)
    cx = sum(o.p[0] * o.weight for o in obs) / wsum
    cy = sum(o.p[1] * o.weight for o in obs) / wsum
    lever2 = max(1.0, sum(o.weight * ((o.p[0] - cx) ** 2 + (o.p[1] - cy) ** 2)
                          for o in obs) / wsum)
    angle, tx, ty = _initial_guess(obs, (cx, cy))
    ata = None
    for _ in range(iterations):
        c, s = math.cos(angle), math.sin(angle)
        ata = [[0.0] * 3 for _ in range(3)]
        atb = [0.0] * 3
        for o in obs:
            dx, dy = o.p[0] - cx, o.p[1] - cy
            px, py = cx + c * dx - s * dy + tx, cy + s * dx + c * dy + ty
            ddx, ddy = -s * dx - c * dy, c * dx - s * dy  # d(moved point)/d(angle)
            for (nx, ny), (qx, qy) in _rows(o, px, py):
                r = nx * (px - qx) + ny * (py - qy)
                row = (nx * ddx + ny * ddy, nx, ny)
                for i in range(3):
                    atb[i] -= o.weight * row[i] * r
                    for j in range(3):
                        ata[i][j] += o.weight * row[i] * row[j]
        # Damping toward the current place, in comparable units (angle * lever).
        damped = [row[:] for row in ata]
        damped[0][0] += damping * wsum * lever2
        damped[1][1] += damping * wsum
        damped[2][2] += damping * wsum
        # Pull the parameters back toward zero, not only the step.
        rhs = [atb[0] - damping * wsum * lever2 * angle, atb[1] - damping * wsum * tx,
               atb[2] - damping * wsum * ty]
        step = _solve3(damped, rhs)
        if step is None:
            return None
        angle += step[0]
        tx += step[1]
        ty += step[2]
        if abs(step[0]) * math.sqrt(lever2) < 1e-6 and math.hypot(step[1], step[2]) < 1e-6:
            break
    fit = RigidFit(angle, (cx, cy), (tx, ty), obs, _weak_dofs(ata, wsum, lever2))
    _set_residuals(fit)
    return fit


def _initial_guess(obs, pivot):
    """Start values (angle, tx, ty) for the Gauss-Newton steps.

    Gauss-Newton from the plan's current place can miss when the plan lies
    turned far off, say upside down after the import. Points with a scene
    target (POINT, and DIR, whose target is the km position) give the turn
    in closed form when there are two of them apart. One such point and
    points on the track give it from the track direction there; of its two
    directions the one closer to the current place wins. Else no movement.
    """
    anchors = [o for o in obs if o.kind in ('POINT', 'DIR')]
    if not anchors:
        return 0.0, 0.0, 0.0
    w = sum(o.weight for o in anchors)
    pc = (sum(o.p[0] * o.weight for o in anchors) / w, sum(o.p[1] * o.weight for o in anchors) / w)
    qc = (sum(o.q[0] * o.weight for o in anchors) / w, sum(o.q[1] * o.weight for o in anchors) / w)
    pairs = [(((o.p[0] - pc[0]) * o.weight, (o.p[1] - pc[1]) * o.weight),
              (o.q[0] - qc[0], o.q[1] - qc[1])) for o in anchors]
    spread = max(math.dist(o.p, pc) for o in anchors)
    if spread > 1.0:
        angle = initial_rotation(pairs)
    else:
        lines = [o for o in obs if o.kind == 'LINE' and math.dist(o.p, pc) > 1.0]
        if not lines:
            return 0.0, 0.0, 0.0
        best = None
        for line in lines[0].lines:
            hit = nearest_on_polyline(line, *qc)
            if hit and (best is None or hit[0] < best[0]):
                best = hit
        if best is None:
            return 0.0, 0.0, 0.0
        tangent = best[2]
        angles = []
        for sign in (1.0, -1.0):
            angles.append(initial_rotation([((o.p[0] - pc[0], o.p[1] - pc[1]),
                                             (sign * tangent[0], sign * tangent[1]))
                                            for o in lines]))
        angle = min(angles, key=lambda a: abs(_wrap(a)))
    moved = rotate_about(*pc, angle, pivot)
    return angle, qc[0] - moved[0], qc[1] - moved[1]


def _weak_dofs(ata, wsum, lever2):
    """Degrees of freedom the observations hardly fix.

    Rotation: its normal equation with the shift eliminated (Schur
    complement), as an effective lever; under 2 m the rotation is not fixed.
    Shift: the smaller eigenvalue of the shift block with the rotation
    eliminated.
    """
    weak = set()
    if ata is None:
        return {'ROTATION', 'SHIFT'}
    t = [[ata[1][1], ata[1][2]], [ata[2][1], ata[2][2]]]
    det_t = t[0][0] * t[1][1] - t[0][1] * t[1][0]
    if det_t > 1e-12 * wsum * wsum:
        inv = [[t[1][1] / det_t, -t[0][1] / det_t], [-t[1][0] / det_t, t[0][0] / det_t]]
        b = (ata[0][1], ata[0][2])
        schur = ata[0][0] - sum(b[i] * inv[i][j] * b[j] for i in range(2) for j in range(2))
    else:
        schur = 0.0
    if schur / wsum < 4.0:  # effective lever under 2 m
        weak.add('ROTATION')
    # Shift block with the rotation eliminated.
    a00 = ata[0][0]
    if a00 > 1e-12:
        s = [[t[i][j] - ata[0][i + 1] * ata[0][j + 1] / a00 for j in range(2)] for i in range(2)]
    else:
        s = t
    tr = s[0][0] + s[1][1]
    det = s[0][0] * s[1][1] - s[0][1] * s[1][0]
    smallest = tr / 2 - math.sqrt(max(0.0, tr * tr / 4 - det))
    if smallest < 0.05 * wsum / len(ata):
        weak.add('SHIFT')
    return weak


def _set_residuals(fit):
    for o in fit.observations:
        px, py = fit.apply(*o.p)
        rows = _rows(o, px, py)
        if not rows:
            o.residual = -1.0
            continue
        o.residual = math.sqrt(sum((nx * (px - qx) + ny * (py - qy)) ** 2
                                   for (nx, ny), (qx, qy) in rows))


def initial_rotation(pairs):
    """Angle that turns plan directions onto scene directions, least squares.

    pairs: ((plan dx, dy), (scene dx, dy)) with weights folded into the
    lengths. 0 without usable pairs.
    """
    dot = sum(a[0] * b[0] + a[1] * b[1] for a, b in pairs)
    cross = sum(a[0] * b[1] - a[1] * b[0] for a, b in pairs)
    return math.atan2(cross, dot) if math.hypot(dot, cross) > 1e-9 else 0.0


def rotate_about(x, y, angle, pivot):
    c, s = math.cos(angle), math.sin(angle)
    dx, dy = x - pivot[0], y - pivot[1]
    return pivot[0] + c * dx - s * dy, pivot[1] + s * dx + c * dy


# ---------------------------------------------------------------- checks

def spacing_warnings(km_points, tolerance_m=3.0, tolerance_share=0.02):
    """Pairs of km points whose distance on the plan does not fit their km.

    km_points: (name, km, plan point in scene meters, scene point at that
    km). The plan point distance is true size (sheet times Maßstab); a
    mismatch means a wrong km or a wrong Maßstab. Only neighbors in km order
    are compared.
    """
    pts = sorted(km_points, key=lambda k: k[1])
    warnings = []
    for (na, ka, pa, qa), (nb, kb, pb, qb) in zip(pts, pts[1:]):
        on_plan = math.dist(pa, pb)
        on_line = math.dist(qa, qb)
        if abs(on_plan - on_line) > max(tolerance_m, tolerance_share * on_line):
            warnings.append(
                f"{na} ({format_km(ka)}) to {nb} ({format_km(kb)}): {on_line:.0f} m apart on "
                f"the Strecke, {on_plan:.0f} m on the plan. Check the km values or the Maßstab")
    return warnings


# ---------------------------------------------------------------- sheets

def match_transform(own, other, shared_second=False, own_center=None, other_center=None):
    """(angle, pivot, shift) that moves a sheet onto its neighbor.

    own, other: two points each, in scene meters, on this sheet and on the
    neighbor. With shared_second the second points are the same place too
    (two shared points: least squares, scale locked). Else they only give
    the direction of the match line (Anschlussblatt line): the first points
    are the shared place (where the track crosses the line), and of the two
    possible directions the one wins that puts the two sheet centers on
    opposite sides of the line. Without centers, the smaller turn wins.
    """
    (p1, p2), (q1, q2) = own, other
    a = math.atan2(q2[1] - q1[1], q2[0] - q1[0]) - math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    if shared_second:
        pivot = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
        target = ((q1[0] + q2[0]) / 2, (q1[1] + q2[1]) / 2)
        return _wrap(a), pivot, (target[0] - pivot[0], target[1] - pivot[1])
    candidates = [_wrap(a), _wrap(a + math.pi)]
    pivot = p1
    shift = (q1[0] - p1[0], q1[1] - p1[1])
    if own_center is not None and other_center is not None:
        lx, ly = q2[0] - q1[0], q2[1] - q1[1]

        def side(pt):
            return lx * (pt[1] - q1[1]) - ly * (pt[0] - q1[0])
        other_side = side(other_center)
        for angle in candidates:
            moved = rotate_about(*own_center, angle, pivot)
            moved = (moved[0] + shift[0], moved[1] + shift[1])
            if side(moved) * other_side < 0:
                return angle, pivot, shift
    return min(candidates, key=abs), pivot, shift


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def row_layout(sizes, gap):
    """x offsets of sheets laid out left to right, sizes (width, height)."""
    out, x = [], 0.0
    for w, _ in sizes:
        out.append(x)
        x += w + gap
    return out
