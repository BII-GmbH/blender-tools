"""Coordinate math for georeferencing plans. Pure Python, no bpy.

Plans give their grid crosses in Gauss-Kruger (DHDN or DB_REF, Bessel
ellipsoid) or in ETRS89/UTM. This module converts those to WGS84 lat/lon,
and lat/lon to the local scene meters of the Umgebung extension.

Transverse Mercator uses the Kruger n-series (sub-millimeter inside a zone).
The datum shift is the 7-parameter Helmert transformation that PROJ uses
for these systems (towgs84, position vector convention), accurate to about
1 m for DHDN and a few decimeters for DB_REF. ETRS89 counts as WGS84.
"""

import math

# a, f
BESSEL = (6377397.155, 1 / 299.1528128)
GRS80 = (6378137.0, 1 / 298.257222101)
WGS84 = (6378137.0, 1 / 298.257223563)

# Datum: ellipsoid, towgs84 (dx, dy, dz in m, rx, ry, rz in arc seconds,
# scale in ppm), position vector convention as in PROJ.
DATUMS = {
    'DHDN': (BESSEL, (598.1, 73.7, 418.2, 0.202, 0.045, -2.455, 6.7)),
    'DB_REF': (BESSEL, (584.9636, 107.7175, 413.8067,
                        1.1155214628, 0.2824339890, -3.1384490633, -7.992235)),
    'ETRS89': (GRS80, None),
}

# Coordinate systems a plan can use. Gauss-Kruger zone and UTM zone come
# from the leading digits of the easting where the plan writes them.
CRS_ITEMS = [
    ('DB_REF', "DB_REF (Gauß-Krüger)",
     "Lagesystem of DB Netz plans: DB_REF, DB_REF2003, DB_REF2016, DB-Referenzsystem. "
     "Gauß-Krüger values with a 7-digit Rechtswert (EPSG:5682 to 5685)"),
    ('DHDN', "DHDN (Gauß-Krüger)",
     "Gauß-Krüger of the Landesvermessung before ETRS89: DHDN, DHDN 90, GK, Bessel, "
     "Potsdam datum. 7-digit Rechtswert (EPSG:31466 to 31469)"),
    ('ETRS89', "ETRS89 / UTM",
     "Today's Landesvermessung: ETRS89, UTM, EPSG:25832 / 25833. 8-digit easting with "
     "the zone in front (32xxxxxx), or 6 digits and the zone set below"),
]

def check_coordinates(crs, points, utm_zone):
    """Hints about entered coordinates: (info line, list of warnings).

    points: (easting, northing) as printed on the plan; northing None if
    it was not read. Checks the digit count against the chosen system, the
    zone, and swapped values. Only for Germany and its neighbors (northing
    5.2 to 6.1 million).
    """
    warnings = []
    if not points:
        return "", warnings
    gk = crs != 'ETRS89'
    zones = set()
    for e, n in points:
        if n is not None and 5.2e6 <= e <= 6.1e6 and not 5.2e6 <= n <= 6.1e6:
            warnings.append(f"{e:.0f} / {n:.0f}: Rechtswert and Hochwert swapped?")
            continue
        if n is not None and not 5.2e6 <= n <= 6.1e6:
            warnings.append(f"Hochwert {n:.0f} is outside Germany")
        if gk:
            if e >= 1e7:
                warnings.append(f"Rechtswert {e:.0f} has 8 digits: ETRS89 / UTM?")
            elif e < 1e6:
                warnings.append(f"Rechtswert {e:.0f} has 6 digits: a UTM easting, choose "
                                "ETRS89 / UTM. Gauß-Krüger plans print 7 digits; do not add "
                                "the zone digit yourself")
            elif not 2 <= e // 1e6 <= 5:
                warnings.append(f"Rechtswert {e:.0f}: no Gauß-Krüger zone 2 to 5 in front")
            else:
                zones.add(int(e // 1e6))
        else:
            if 1e6 <= e < 1e7:
                warnings.append(f"Easting {e:.0f} has 7 digits: Gauß-Krüger (DB_REF or DHDN)?")
            elif e >= 1e7:
                zones.add(int(e // 1e6))
            else:
                zones.add(utm_zone)
    if len(zones) > 1:
        warnings.append("The points are in different zones: " +
                        ", ".join(str(z) for z in sorted(zones)))
    info = ""
    if len(zones) == 1:
        zone = zones.pop()
        if gk:
            info = f"Gauß-Krüger zone {zone}, central meridian {zone * 3}° E"
        else:
            info = f"UTM zone {zone}, central meridian {zone * 6 - 183}° E"
    return info, warnings


class TransverseMercator:
    """Ellipsoidal transverse Mercator (Kruger series to n^4)."""

    def __init__(self, ellipsoid, lon0, k0, false_easting, false_northing=0.0):
        a, f = ellipsoid
        n = f / (2 - f)
        self.lon0 = math.radians(lon0)
        self.k0 = k0
        self.fe = false_easting
        self.fn = false_northing
        self.e = math.sqrt(f * (2 - f))
        self.A = a / (1 + n) * (1 + n**2 / 4 + n**4 / 64)
        self.alpha = (n / 2 - 2 * n**2 / 3 + 5 * n**3 / 16 + 41 * n**4 / 180,
                      13 * n**2 / 48 - 3 * n**3 / 5 + 557 * n**4 / 1440,
                      61 * n**3 / 240 - 103 * n**4 / 140,
                      49561 * n**4 / 161280)
        self.beta = (n / 2 - 2 * n**2 / 3 + 37 * n**3 / 96 - n**4 / 360,
                     n**2 / 48 + n**3 / 15 - 437 * n**4 / 1440,
                     17 * n**3 / 480 - 37 * n**4 / 840,
                     4397 * n**4 / 161280)
        self.delta = (2 * n - 2 * n**2 / 3 - 2 * n**3 + 116 * n**4 / 45,
                      7 * n**2 / 3 - 8 * n**3 / 5 - 227 * n**4 / 45,
                      56 * n**3 / 15 - 136 * n**4 / 35,
                      4279 * n**4 / 630)

    def forward(self, lat, lon):
        """lat, lon in degrees -> easting, northing in meters."""
        phi = math.radians(lat)
        lam = math.radians(lon) - self.lon0
        e = self.e
        t = math.sinh(math.atanh(math.sin(phi)) - e * math.atanh(e * math.sin(phi)))
        xi_ = math.atan2(t, math.cos(lam))
        eta_ = math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
        xi, eta = xi_, eta_
        for j, a in enumerate(self.alpha, 1):
            xi += a * math.sin(2 * j * xi_) * math.cosh(2 * j * eta_)
            eta += a * math.cos(2 * j * xi_) * math.sinh(2 * j * eta_)
        return (self.fe + self.k0 * self.A * eta,
                self.fn + self.k0 * self.A * xi)

    def inverse(self, easting, northing):
        """easting, northing in meters -> lat, lon in degrees."""
        xi = (northing - self.fn) / (self.k0 * self.A)
        eta = (easting - self.fe) / (self.k0 * self.A)
        xi_, eta_ = xi, eta
        for j, b in enumerate(self.beta, 1):
            xi_ -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
            eta_ -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
        chi = math.asin(math.sin(xi_) / math.cosh(eta_))
        phi = chi
        for j, d in enumerate(self.delta, 1):
            phi += d * math.sin(2 * j * chi)
        lam = math.atan2(math.sinh(eta_), math.cos(xi_))
        return math.degrees(phi), math.degrees(self.lon0 + lam)


def _to_ecef(ellipsoid, lat, lon, h=0.0):
    a, f = ellipsoid
    e2 = f * (2 - f)
    phi, lam = math.radians(lat), math.radians(lon)
    nu = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    return ((nu + h) * math.cos(phi) * math.cos(lam),
            (nu + h) * math.cos(phi) * math.sin(lam),
            (nu * (1 - e2) + h) * math.sin(phi))


def _from_ecef(ellipsoid, x, y, z):
    a, f = ellipsoid
    e2 = f * (2 - f)
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    phi = math.atan2(z, p * (1 - e2))
    for _ in range(10):
        nu = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
        h = p / math.cos(phi) - nu
        phi = math.atan2(z, p * (1 - e2 * nu / (nu + h)))
    return math.degrees(phi), math.degrees(lon)


def _helmert(params, x, y, z, inverse=False):
    dx, dy, dz, rx, ry, rz, ds = params
    sec = math.pi / (180 * 3600)
    rx, ry, rz, m = rx * sec, ry * sec, rz * sec, 1 + ds * 1e-6
    if not inverse:
        return (dx + m * (x - rz * y + ry * z),
                dy + m * (rz * x + y - rx * z),
                dz + m * (-ry * x + rx * y + z))
    # The rotation is tiny, so its inverse is its transpose.
    x, y, z = (x - dx) / m, (y - dy) / m, (z - dz) / m
    return (x + rz * y - ry * z,
            -rz * x + y + rx * z,
            ry * x - rx * y + z)


def datum_to_wgs84(datum, lat, lon):
    ellipsoid, params = DATUMS[datum]
    if params is None:
        return lat, lon
    return _from_ecef(WGS84, *_helmert(params, *_to_ecef(ellipsoid, lat, lon)))


def wgs84_to_datum(datum, lat, lon):
    ellipsoid, params = DATUMS[datum]
    if params is None:
        return lat, lon
    return _from_ecef(ellipsoid, *_helmert(params, *_to_ecef(WGS84, lat, lon), inverse=True))


def split_zone(crs, easting, utm_zone):
    """Zone and easting without zone digits.

    Gauss-Kruger: the first digit of the 7-digit Rechtswert is the zone.
    UTM: an 8-digit easting starts with the zone (32xxxxxx), a 6-digit one
    uses utm_zone.
    """
    if crs == 'ETRS89':
        if easting >= 1e7:
            zone = int(easting // 1e6)
            return zone, easting - zone * 1e6
        return utm_zone, easting
    zone = int(easting // 1e6)
    return zone, easting - zone * 1e6


def projection(crs, zone):
    if crs == 'ETRS89':
        return TransverseMercator(GRS80, zone * 6 - 183, 0.9996, 500000.0)
    return TransverseMercator(BESSEL, zone * 3, 1.0, 500000.0)


def grid_to_wgs84(crs, easting, northing, utm_zone=32):
    """Plan coordinates (as printed on the plan) -> WGS84 lat, lon."""
    zone, e = split_zone(crs, easting, utm_zone)
    if crs != 'ETRS89' and not 2 <= zone <= 5:
        raise ValueError(f"Rechtswert {easting:.0f} has no Gauss-Krüger zone 2 to 5 in front")
    if crs == 'ETRS89' and not 1 <= zone <= 60:
        raise ValueError(f"UTM zone {zone} does not exist")
    lat, lon = projection(crs, zone).inverse(e, northing)
    return datum_to_wgs84(crs, lat, lon)


def wgs84_to_grid(crs, lat, lon, zone, zone_prefix=True):
    """WGS84 lat, lon -> plan coordinates in the given zone."""
    lat, lon = wgs84_to_datum(crs, lat, lon)
    e, n = projection(crs, zone).forward(lat, lon)
    if crs != 'ETRS89' or zone_prefix:
        e += zone * 1e6
    return e, n


class SceneProjection:
    """Spherical transverse Mercator of the Umgebung extension (and BLOSM).

    Same formulas as Umgebung's projection.py, so plans land on the maps,
    buildings and tracks it builds around scene["lat"] / scene["lon"].
    """

    radius = 6378137.0

    def __init__(self, lat, lon):
        self.lat = lat
        self.lon = lon
        self.lat_rad = math.radians(lat)

    def from_geographic(self, lat, lon):
        lat = math.radians(lat)
        lon = math.radians(lon - self.lon)
        b = math.sin(lon) * math.cos(lat)
        b = max(-0.999999999, min(0.999999999, b))
        x = 0.5 * self.radius * math.log((1.0 + b) / (1.0 - b))
        y = self.radius * (math.atan(math.tan(lat) / math.cos(lon)) - self.lat_rad)
        return x, y

    def to_geographic(self, x, y):
        x = x / self.radius
        y = y / self.radius
        d = y + self.lat_rad
        lon = math.atan(math.sinh(x) / math.cos(d))
        lat = math.asin(math.sin(d) / math.cosh(x))
        return math.degrees(lat), self.lon + math.degrees(lon)


def default_zone(crs, lon):
    """Zone of a longitude: Gauss-Kruger 3° strips, UTM 6° strips."""
    if crs == 'ETRS89':
        return int((lon + 180) // 6) + 1
    return max(2, min(5, round(lon / 3)))


def fit_similarity(src, dst):
    """Least-squares 2D similarity transform from src to dst points.

    Returns (a, b, tx, ty) with dst = (a*x - b*y + tx, b*x + a*y + ty), or
    None with fewer than 2 distinct points. Coordinates are centered first,
    so large grid coordinates keep their precision.
    """
    n = len(src)
    if n < 2 or n != len(dst):
        return None
    sx = sum(p[0] for p in src) / n
    sy = sum(p[1] for p in src) / n
    dx = sum(p[0] for p in dst) / n
    dy = sum(p[1] for p in dst) / n
    num_a = num_b = den = 0.0
    for (x, y), (u, v) in zip(src, dst):
        x, y, u, v = x - sx, y - sy, u - dx, v - dy
        num_a += x * u + y * v
        num_b += x * v - y * u
        den += x * x + y * y
    if den < 1e-18:
        return None
    a, b = num_a / den, num_b / den
    return a, b, dx - (a * sx - b * sy), dy - (b * sx + a * sy)


GUESS_WEIGHT = 1e-4  # weight of guessed values against values read from the plan


def _weighted_mean(values, weights):
    total = sum(weights)
    return sum(v * w for v, w in zip(values, weights)) / total if total else 0.0


def _normal_equations(points):
    """Normal equations of the weighted similarity fit, in centered coordinates.

    Returns (ata, atb, (sx, sy, e0, n0)); unknowns (a, b, tx, ty), rows
    E = a x - b y + tx and N = b x + a y + ty.
    """
    sx = sum(p[0] for p in points) / len(points)
    sy = sum(p[1] for p in points) / len(points)
    # Means of the weighted values only: weight 0 means no value (0 in the field).
    e0 = _weighted_mean([p[2] for p in points], [p[3] for p in points])
    n0 = _weighted_mean([p[4] for p in points], [p[5] for p in points])
    ata = [[0.0] * 4 for _ in range(4)]
    atb = [0.0] * 4
    for x, y, e, we, n, wn in points:
        x, y = x - sx, y - sy
        for row, value, w in (((x, -y, 1.0, 0.0), e - e0, we), ((y, x, 0.0, 1.0), n - n0, wn)):
            if not w:
                continue
            for i in range(4):
                atb[i] += w * row[i] * value
                for j in range(4):
                    ata[i][j] += w * row[i] * row[j]
    return ata, atb, (sx, sy, e0, n0)


def fit_similarity_weighted(points):
    """Least-squares 2D similarity with a weight per value.

    points: (x, y, easting, easting weight, northing, northing weight).
    Guessed values get a tiny weight: they only decide what the values read
    from the plan leave open (with Rechtswerte only: the north-south shift).
    Weight 0 leaves a value out. Returns (a, b, tx, ty) like fit_similarity,
    or None if undetermined.
    """
    if len(points) < 2:
        return None
    ata, atb, (sx, sy, e0, n0) = _normal_equations(points)
    solution = _solve(ata, atb)
    if solution is None:
        return None
    a, b, tx, ty = solution
    return a, b, e0 + tx - a * sx + b * sy, n0 + ty - b * sx - a * sy


def fixes_scale_and_rotation(points):
    """True if the weighted values alone determine Maßstab and rotation.

    points as for fit_similarity_weighted, usually with only the values read
    from the plan weighted. The shift may stay open (Rechtswerte only leave
    the north-south shift to the guesses); scale and rotation must not.
    They do not with fewer than 3 values, or with values of one axis whose
    points all lie on one line: then the guesses turn the plan.

    Checks the normal equations of (a, b) with the shift (tx, ty) eliminated
    (Schur complement). tx and ty never share a row, so their block is
    diagonal.
    """
    if len(points) < 2:
        return False
    ata, _, _ = _normal_equations(points)
    s = [[ata[i][j] - sum(ata[i][k] * ata[k][j] / ata[k][k] for k in (2, 3) if ata[k][k] > 0)
          for j in (0, 1)] for i in (0, 1)]
    trace = s[0][0] + s[1][1]
    return trace > 0 and s[0][0] * s[1][1] - s[0][1] * s[1][0] > 1e-9 * trace * trace


def _solve(m, v):
    """Gaussian elimination with partial pivoting; None if singular."""
    n = len(v)
    m = [row[:] + [v[i]] for i, row in enumerate(m)]
    scale = max(abs(c) for row in m for c in row[:n]) or 1.0
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-15 * scale:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def fit_affine_axes(src, dst):
    """Least-squares affine fit, one plane per axis.

    Returns ((a1, b1), (a2, b2)): how easting and northing change per sheet
    meter in x and y, or None with fewer than 3 points or points on a line.
    """
    n = len(src)
    if n < 3:
        return None
    sx = sum(p[0] for p in src) / n
    sy = sum(p[1] for p in src) / n
    xx = sum((p[0] - sx) ** 2 for p in src)
    yy = sum((p[1] - sy) ** 2 for p in src)
    xy = sum((p[0] - sx) * (p[1] - sy) for p in src)
    det = xx * yy - xy * xy
    if det <= 1e-12 * max(xx * yy, 1e-30):
        return None
    grads = []
    for axis in (0, 1):
        m = sum(p[axis] for p in dst) / n
        xv = sum((s[0] - sx) * (d[axis] - m) for s, d in zip(src, dst))
        yv = sum((s[1] - sy) * (d[axis] - m) for s, d in zip(src, dst))
        grads.append(((yy * xv - xy * yv) / det, (xx * yv - xy * xv) / det))
    return tuple(grads)


def check_axes(src, dst):
    """Warnings when the entered values do not form a square grid on the sheet.

    A plan's grid is square and not mirrored, so easting and northing must
    change equally fast and at right angles across the sheet. If they do
    not, values are off by grid steps, swapped, or count the wrong way.
    Returns a list of warnings, empty if fine or not checkable.
    """
    grads = fit_affine_axes(src, dst)
    if grads is None:
        return []
    (a1, b1), (a2, b2) = grads
    scale_e, scale_n = math.hypot(a1, b1), math.hypot(a2, b2)
    if scale_e < 1e-9 or scale_n < 1e-9:
        return ["Rechtswert or Hochwert is the same for all points"]
    warnings = []
    if a1 * b2 - b1 * a2 < 0:
        warnings.append("The values are mirrored: Rechtswert and Hochwert swapped, or one "
                        "of them counts the wrong way across the sheet")
    ratio = scale_n / scale_e
    if abs(ratio - 1) > 0.03:
        warnings.append(f"Rechtswert and Hochwert do not fit together: Rechtswert gives "
                        f"Maßstab 1:{scale_e:.0f}, Hochwert 1:{scale_n:.0f}. Values of one of "
                        "them are off by grid steps; the axis whose Maßstab matches the plan "
                        "is right")
    cos = (a1 * a2 + b1 * b2) / (scale_e * scale_n)
    if abs(cos) > math.sin(math.radians(3)):
        warnings.append(f"The grid from the values is skewed by "
                        f"{abs(90 - math.degrees(math.acos(max(-1, min(1, cos))))):.0f}°: "
                        "a point has values of a neighboring cross")
    return warnings


def apply_similarity(fit, x, y):
    a, b, tx, ty = fit
    return a * x - b * y + tx, b * x + a * y + ty


def invert_similarity(fit, u, v):
    a, b, tx, ty = fit
    d = a * a + b * b
    u, v = u - tx, v - ty
    return (a * u + b * v) / d, (-b * u + a * v) / d


def similarity_scale(fit):
    return math.hypot(fit[0], fit[1])


def similarity_angle(fit):
    return math.atan2(fit[1], fit[0])


STANDARD_SPACINGS = (1000, 500, 250, 200, 100, 50, 25, 20, 10, 5, 1)
GRID_TOLERANCE = 0.05  # share of the spacing a prediction may be off a grid line


def round_to_grid(value, max_spacing):
    """A predicted grid value, rounded to the coarsest grid line it lies near.

    max_spacing is the spacing inferred from the values read so far. The
    real spacing can be any divisor of it: crosses 1000 m apart can lie on a
    100 m grid. So try the standard spacings that divide max_spacing, coarse
    to fine, and take the first one the value lies within GRID_TOLERANCE of.
    Else the value is only rounded to whole meters.
    """
    for s in STANDARD_SPACINGS:
        if s > max_spacing or max_spacing % s:
            continue
        rounded = round(value / s) * s
        if abs(value - rounded) <= GRID_TOLERANCE * s:
            return rounded
    return round(value)
