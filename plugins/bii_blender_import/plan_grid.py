"""Find the coordinate grid (Gitterkreuze) of a scanned or PDF plan. Pure numpy, no bpy.

A grid cross is a small cross whose two strokes follow the grid directions; the crosses lie on a
square lattice (e.g. every 100 m). find_grid() finds the isolated crosses, the grid direction and
spacing from them, fits the lattice and then checks every lattice node on the sheet, so crosses
that touch other linework are found too.

All positions are pixels of the given image (column, row from the top).
"""

import math

import numpy as np

# cross arms on DB Lagepläne: about 5 mm on paper (±2.6 mm from the center)
ARM_MM = 2.6
SPACINGS_M = (100.0, 50.0, 200.0, 250.0, 500.0, 25.0, 1000.0)


def _block_any(ink, k):
    h, w = (ink.shape[0] // k) * k, (ink.shape[1] // k) * k
    return ink[:h, :w].reshape(h // k, k, w // k, k).any(axis=(1, 3))


def _components(mask, max_size):
    """Bounding boxes (x0, y0, x1, y1, count) of 8-connected components of mask no larger than
    max_size in both directions (larger ones are skipped early)."""
    h, w = mask.shape
    seen = np.zeros_like(mask)
    out = []
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys.tolist(), xs.tolist()):
        if seen[y0, x0]:
            continue
        seen[y0, x0] = True
        todo = [(y0, x0)]
        bx0 = bx1 = x0
        by0 = by1 = y0
        n = 0
        big = False
        while todo:
            y, x = todo.pop()
            n += 1
            if x < bx0:
                bx0 = x
            elif x > bx1:
                bx1 = x
            if y < by0:
                by0 = y
            elif y > by1:
                by1 = y
            if not big and (bx1 - bx0 > max_size or by1 - by0 > max_size):
                big = True
            for yy in (y - 1, y, y + 1):
                if 0 <= yy < h:
                    for xx in (x - 1, x, x + 1):
                        if 0 <= xx < w and mask[yy, xx] and not seen[yy, xx]:
                            seen[yy, xx] = True
                            todo.append((yy, xx))
        if not big:
            out.append((bx0, by0, bx1, by1, n))
    return out


def _ray(ink, x, y, angle, r0, r1):
    """Fraction of inked samples (within 1 px across) along a ray from (x, y)."""
    h, w = ink.shape
    c, s = math.cos(angle), math.sin(angle)
    hits = total = 0
    for t in range(int(r0), int(r1) + 1):
        xx, yy = int(round(x + t * c)), int(round(y + t * s))
        if not (1 <= xx < w - 1 and 1 <= yy < h - 1):
            return 0.0
        total += 1
        if ink[yy - 1:yy + 2, xx - 1:xx + 2].any():
            hits += 1
    return hits / total if total else 0.0


def cross_score(ink, x, y, angle, arm):
    """How much (x, y) looks like a grid cross with strokes at angle and angle + 90°: the weakest of
    its four arms (fraction inked), so a single line or a corner scores low."""
    return min(_ray(ink, x, y, angle + k * math.pi / 2, arm * 0.2, arm) for k in range(4))


def _best_angle(ink, x, y, arm):
    """(score, angle) of the best cross through (x, y), angle in [0, 90°)."""
    best = (0.0, 0.0)
    for deg in range(0, 90, 2):
        a = math.radians(deg)
        s = cross_score(ink, x, y, a, arm)
        if s > best[0]:
            best = (s, a)
    if best[0] > 0:
        for d in (-1.0, -0.5, 0.5, 1.0):
            a = best[1] + math.radians(d)
            s = cross_score(ink, x, y, a, arm)
            if s > best[0]:
                best = (s, a)
    return best


def _refine(ink, x, y, angle, arm, radius):
    """Position near (x, y) with the best cross score (1 px steps)."""
    best = (cross_score(ink, x, y, angle, arm), x, y)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            s = cross_score(ink, x + dx, y + dy, angle, arm)
            if s > best[0] or (s == best[0] and dx * dx + dy * dy < (best[1] - x) ** 2 + (best[2] - y) ** 2):
                best = (s, x + dx, y + dy)
    return best


def _center(ink, x, y, angle, arm):
    """Sub-pixel center: the mean of the ink near both strokes, projected on each stroke's normal."""
    r = int(arm)
    h, w = ink.shape
    x0, y0, x1, y1 = max(0, int(x) - r), max(0, int(y) - r), min(w, int(x) + r + 1), min(h, int(y) + r + 1)
    yy, xx = np.nonzero(ink[y0:y1, x0:x1])
    if len(xx) < 5:
        return x, y
    px, py = xx + x0 - x, yy + y0 - y
    d1 = np.array([math.cos(angle), math.sin(angle)])
    d2 = np.array([-d1[1], d1[0]])
    a1 = px * d1[0] + py * d1[1]          # along stroke 1
    a2 = px * d2[0] + py * d2[1]          # along stroke 2
    on1 = (np.abs(a2) < 3) & (np.abs(a1) > 4)   # pixels of stroke 1 (away from the middle)
    on2 = (np.abs(a1) < 3) & (np.abs(a2) > 4)
    o2 = a2[on1].mean() if on1.sum() > 4 else 0.0   # stroke 1 offset along its normal d2
    o1 = a1[on2].mean() if on2.sum() > 4 else 0.0
    return x + o1 * d1[0] + o2 * d2[0], y + o1 * d1[1] + o2 * d2[1]


def find_grid(gray, dpi, massstab=None, spacing_m=None, threshold=128, min_score=0.6, progress=None):
    """The coordinate grid of a plan image.

    gray: uint8 pixels (row 0 at the top), dpi: pixels per inch of that image, massstab: e.g. 1000
    (with it, the spacing is checked in meters), spacing_m: force a grid spacing in meters.
    Returns None when no grid is found, else a dict:
      origin (x, y), a (dx, dy), b (dx, dy): lattice node (i, j) = origin + i * a + j * b,
      spacing_px, spacing_m (or None), angle (degrees of a, image x towards y),
      crosses: [{"i", "j", "x", "y", "score"}] found at lattice nodes,
      rms_px, candidates (isolated crosses found first)."""
    ink = np.asarray(gray) < threshold
    h, w = ink.shape
    arm = ARM_MM / 25.4 * dpi
    k = max(1, int(round(arm / 10)))           # coarse image: arms about 10 px
    coarse = _block_any(ink, k)
    size_lo, size_hi = 1.2 * arm / k, 2.6 * arm / k
    boxes = [b for b in _components(coarse, int(size_hi) + 2)
             if size_lo <= max(b[2] - b[0], b[3] - b[1]) <= size_hi]
    if progress:
        progress(f"{len(boxes)} cross-sized marks")
    cands = []
    for x0, y0, x1, y1, n in boxes:
        cx, cy = (x0 + x1 + 1) / 2 * k, (y0 + y1 + 1) / 2 * k
        s, a = _best_angle(ink, cx, cy, arm)
        if s >= min_score * 0.8:
            s, cx, cy = _refine(ink, int(cx), int(cy), a, arm, max(2, k))
            if s >= min_score:
                cands.append((cx, cy, a, s))
    if progress:
        progress(f"{len(cands)} crosses")
    if len(cands) < 3:
        return None
    # grid direction: the most common cross angle (mod 90°)
    degs = np.array([math.degrees(c[2]) % 90 for c in cands])
    hist = [(np.sum(np.abs(((degs - d + 45) % 90) - 45) < 2.5), d) for d in np.arange(0, 90, 0.5)]
    angle = math.radians(max(hist)[1])
    sel = [c for c in cands if abs(((math.degrees(c[2]) - math.degrees(angle) + 45) % 90) - 45) < 3]
    if len(sel) < 3:
        return None
    u = np.array([math.cos(angle), math.sin(angle)])
    v = np.array([-u[1], u[0]])
    P = np.array([(c[0], c[1]) for c in sel])
    # spacing: pairs along one grid direction lie at whole multiples of it
    options = []
    px_per_m = None
    if massstab:
        px_per_m = dpi / 0.0254 / massstab
        for m in ([spacing_m] if spacing_m else SPACINGS_M):
            options.append((m, m * px_per_m))
    else:
        d = P[None, :, :] - P[:, None, :]
        al = np.abs(np.concatenate([d @ u, d @ v]).ravel())
        al = al[al > 4 * arm]
        options = [(None, s) for s in np.unique(np.round(al[:200], 0))]
    fits = []
    for m, s in options:
        if s < 3 * arm:
            continue
        fit = _fit(P, u * s, v * s, arm)
        if fit and fit["inliers"] >= 3:
            fits.append((m, fit, s))
    if not fits:
        return None
    # a finer lattice also holds every cross of the real one: the largest spacing that explains
    # (nearly) as many crosses wins
    most = max(f[1]["inliers"] for f in fits)
    best = max((f for f in fits if f[1]["inliers"] >= 0.9 * most), key=lambda f: f[2])
    m, fit, s = best
    o, a, b = fit["origin"], fit["a"], fit["b"]
    # every lattice node on the sheet
    corners = np.array([[0, 0], [w, 0], [0, h], [w, h]], float)
    M = np.column_stack([a, b])
    ij = np.linalg.solve(M, (corners - o).T).T
    i0, i1 = int(math.floor(ij[:, 0].min())), int(math.ceil(ij[:, 0].max()))
    j0, j1 = int(math.floor(ij[:, 1].min())), int(math.ceil(ij[:, 1].max()))
    ang_a = math.atan2(a[1], a[0])
    crosses = []
    for i in range(i0, i1 + 1):
        for j in range(j0, j1 + 1):
            x, y = o + i * a + j * b
            if not (arm < x < w - arm and arm < y < h - arm):
                continue
            sc, bx, by = _refine(ink, int(round(x)), int(round(y)), ang_a, arm, max(3, int(arm * 0.15)))
            if sc >= min_score:
                cx, cy = _center(ink, bx, by, ang_a, arm)
                crosses.append({"i": i, "j": j, "x": float(cx), "y": float(cy), "score": round(float(sc), 3)})
    if len(crosses) < 3:
        return None
    final = _lsq([(c["i"], c["j"], c["x"], c["y"]) for c in crosses])
    o, a, b, rms = final
    return {"origin": tuple(map(float, o)), "a": tuple(map(float, a)), "b": tuple(map(float, b)),
            "spacing_px": float((np.linalg.norm(a) + np.linalg.norm(b)) / 2), "spacing_m": m,
            "angle": math.degrees(math.atan2(a[1], a[0])), "crosses": crosses, "rms_px": float(rms),
            "candidates": len(sel)}


def _lsq(rows):
    """Least-squares lattice (origin, a, b, rms) through (i, j, x, y) rows."""
    A = np.array([[1.0, i, j] for i, j, _, _ in rows])
    X = np.array([x for _, _, x, _ in rows])
    Y = np.array([y for _, _, _, y in rows])
    cx, *_ = np.linalg.lstsq(A, X, rcond=None)
    cy, *_ = np.linalg.lstsq(A, Y, rcond=None)
    res = np.hypot(A @ cx - X, A @ cy - Y)
    return (np.array([cx[0], cy[0]]), np.array([cx[1], cy[1]]), np.array([cx[2], cy[2]]),
            float(np.sqrt((res ** 2).mean())))


def _fit(P, a, b, arm):
    """Lattice through most of the points P for basis a, b (start: each point as origin)."""
    tol = max(0.4 * arm, 0.01 * np.linalg.norm(a))
    best = None
    for p in P:
        o, aa, bb = p.astype(float), a.astype(float), b.astype(float)
        inl = None
        # the start direction is only good to about 1°: allow more far away, then refit
        for slack in (0.03, 0.01, 0.0):
            M = np.column_stack([aa, bb])
            if abs(np.linalg.det(M)) < 0.25 * np.linalg.norm(a) ** 2:
                break               # the refit collapsed (points on one line)
            ij = (np.linalg.inv(M) @ (P - o).T).T
            r = ij - np.round(ij)
            err = np.linalg.norm((M @ r.T).T, axis=1)
            dist = np.linalg.norm(P - o, axis=1)
            inl = err < tol + slack * dist
            idx = np.round(ij)
            rows = [(idx[k, 0], idx[k, 1], P[k, 0], P[k, 1]) for k in np.nonzero(inl)[0]]
            if len({q[0] for q in rows}) >= 2 and len({q[1] for q in rows}) >= 2 and len(rows) >= 3:
                o, aa, bb, _ = _lsq(rows)
            elif rows:
                o = np.mean([np.array([x, y]) - i * aa - j * bb for i, j, x, y in rows], axis=0)
        if inl is None:
            continue
        n = int(inl.sum())
        if best is None or n > best[0]:
            best = (n, o, aa, bb, inl, idx)
    if best is None:
        return None
    n, o, aa, bb, inl, idx = best
    if n < 3:
        return None
    rows = [(idx[k, 0], idx[k, 1], P[k, 0], P[k, 1]) for k in np.nonzero(inl)[0]]
    if len({q[0] for q in rows}) < 2 or len({q[1] for q in rows}) < 2:
        return {"origin": o, "a": aa, "b": bb, "inliers": n, "rms": 0.0}
    o, a2, b2, rms = _lsq(rows)
    return {"origin": o, "a": a2, "b": b2, "inliers": n, "rms": rms}


# ---------------------------------------------------------------- lines and labels

def lines(grid, width, height):
    """Grid lines inside the image: [{"family": "i" or "j", "index", "p0", "p1"}].
    Family "i": the line of constant i (through nodes (i, *)), running along b; "j": along a."""
    o, a, b = (np.array(grid[k]) for k in ("origin", "a", "b"))
    corners = np.array([[0, 0], [width, 0], [0, height], [width, height]], float)
    ij = np.linalg.solve(np.column_stack([a, b]), (corners - o).T).T
    out = []
    for fam, col, d in (("i", 0, b), ("j", 1, a)):
        lo, hi = int(math.ceil(ij[:, col].min())), int(math.floor(ij[:, col].max()))
        for n in range(lo, hi + 1):
            base = o + n * (a if fam == "i" else b)
            ts = []
            dd = d / np.linalg.norm(d)
            # clip the infinite line base + t * dd to the image rectangle
            for k, lim in ((0, 0.0), (0, float(width)), (1, 0.0), (1, float(height))):
                if abs(dd[k]) > 1e-9:
                    t = (lim - base[k]) / dd[k]
                    p = base + t * dd
                    if -1e-6 <= p[0] <= width + 1e-6 and -1e-6 <= p[1] <= height + 1e-6:
                        ts.append(t)
            if len(ts) >= 2:
                out.append({"family": fam, "index": n, "p0": tuple(base + min(ts) * dd),
                            "p1": tuple(base + max(ts) * dd)})
    return out


def _strip(ink, p0, p1, half):
    """Ink sampled along the segment p0-p1 (1 px steps) and +-half px across it.
    Rows: across (row half = on the line, row 0 = the side of the normal (-dy, dx) negative),
    columns: along. Not mirrored: it reads like the image turned so the line runs left to right."""
    p0, p1 = np.array(p0, float), np.array(p1, float)
    L = np.linalg.norm(p1 - p0)
    d = (p1 - p0) / L
    nrm = np.array([-d[1], d[0]])
    t = np.arange(0, int(L))
    s = np.arange(-half, half + 1)
    X = p0[0] + t[None, :] * d[0] + s[:, None] * nrm[0]
    Y = p0[1] + t[None, :] * d[1] + s[:, None] * nrm[1]
    xi, yi = np.round(X).astype(int), np.round(Y).astype(int)
    h, w = ink.shape
    ok = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
    out = np.zeros(xi.shape, bool)
    out[ok] = ink[yi[ok], xi[ok]]
    return out, d, nrm


def _runs(profile, gap):
    """(start, end) of runs where profile > 0, bridging gaps up to gap."""
    idx = np.nonzero(profile)[0]
    if not len(idx):
        return []
    runs, s, e = [], idx[0], idx[0]
    for k in idx[1:]:
        if k - e > gap:
            runs.append((s, e + 1))
            s = k
        e = k
    runs.append((s, e + 1))
    return runs


def _text_run(S, mm, first_only):
    """(t0, t1) of number-like ink runs in a strip (rows across, columns along)."""
    runs = _runs(S.sum(axis=0) > 0, int(1.2 * mm))
    out = []
    for t0, t1 in (runs[:1] if first_only else runs):
        if not (5 * mm <= t1 - t0 <= 26 * mm):
            continue
        piece = S[:, t0:t1]
        rows = np.nonzero(piece.any(axis=1))[0]
        height = rows[-1] - rows[0] + 1
        # italic digits touch each other: judge by size and ink density, not by gaps
        if 1.2 * mm <= height <= 4.5 * mm and 0.04 <= piece.mean() <= 0.4:
            out.append((t0, t1))
    return out


def label_candidates(gray, grid, dpi, threshold=128, whole_lines=True):
    """Places where a coordinate label may be printed. On DB plans the number runs along a grid
    line, often just beyond the end of a cross arm. Returns
    [{"family", "index", "center": (x, y), "near_cross", "strip"}]: family/index name the grid line the
    number belongs to ("i": the line of constant i, "j": of constant j); strip is the piece of the
    image turned so the line runs left to right (uint8, white paper); the text in it can be upside
    down. whole_lines: also search the lines between the crosses (more candidates)."""
    ink = np.asarray(gray) < threshold
    h, w = ink.shape
    mm = dpi / 25.4
    a, b = np.array(grid["a"]), np.array(grid["b"])
    ua, ub = a / np.linalg.norm(a), b / np.linalg.norm(b)
    half = int(4.5 * mm)
    out = []

    def add(S, p0, d, t0, t1, fam, idx, near):
        m = int(1.5 * mm)
        a0, a1 = max(0, t0 - m), min(S.shape[1], t1 + m)
        mid = p0 + (t0 + t1) / 2 * d
        for c in out:
            if c["family"] == fam and c["index"] == idx and math.dist(c["center"], mid) < 5 * mm:
                return
        out.append({"family": fam, "index": int(idx), "center": (float(mid[0]), float(mid[1])),
                    "near_cross": near, "strip": np.where(S[:, a0:a1], 0, 255).astype(np.uint8)})

    for c in grid["crosses"]:
        center = np.array([c["x"], c["y"]])
        for d, fam, idx in ((ua, "j", c["j"]), (-ua, "j", c["j"]), (ub, "i", c["i"]), (-ub, "i", c["i"])):
            p0 = center + (ARM_MM + 0.4) * mm * d
            S, _d, _n = _strip(ink, p0, center + 34 * mm * d, half)
            for t0, t1 in _text_run(S, mm, True):
                if t0 <= 16 * mm:
                    add(S, p0, d, t0, t1, fam, idx, True)
    if whole_lines:
        for ln in lines(grid, w, h):
            p0 = np.array(ln["p0"])
            S, d, _n = _strip(ink, ln["p0"], ln["p1"], half)
            for t0, t1 in _text_run(S, mm, False):
                add(S, p0, d, t0, t1, ln["family"], ln["index"], False)
    return out


def contact_sheet(strips, width=900, scale=1.0):
    """One image (uint8 gray) with the given strips numbered 1..n, each shown as printed and turned
    by 180 degrees, so a reader can read the upright one. Returns (pixels, number positions)."""
    rows = []
    for s in strips:
        if scale != 1.0:
            ys = (np.arange(int(s.shape[0] * scale)) / scale).astype(int)
            xs = (np.arange(int(s.shape[1] * scale)) / scale).astype(int)
            s = s[ys][:, xs]
        s = s[:, :width - 60]
        pair = np.full((2 * s.shape[0] + 6, width), 255, np.uint8)
        pair[:s.shape[0], 60:60 + s.shape[1]] = s
        pair[s.shape[0] + 6:, 60:60 + s.shape[1]] = s[::-1, ::-1]
        pair[s.shape[0] + 2:s.shape[0] + 4, 60:] = 200
        rows.append(pair)
    sep = np.full((10, width), 255, np.uint8)
    sep[4:6] = 0
    parts, tops = [], []
    y = 0
    for r in rows:
        tops.append(y)
        parts += [r, sep]
        y += r.shape[0] + sep.shape[0]
    return (np.vstack(parts) if parts else np.full((10, width), 255, np.uint8)), tops


# ---------------------------------------------------------------- values of the lines

def solve(grid, readings, spacing_m, expected=None, tolerance=1.0, y_down=True):
    """Rechtswert and Hochwert of every grid cross from the numbers read on the grid lines.

    readings: [(family, index, value)], family/index as in label_candidates; value the number read.
    spacing_m: grid spacing in meters. expected: rough (easting, northing) of the project (e.g. of
    the scene origin), tells eastings from northings; without it the larger family of values... is
    decided by consistency only.
    Each line's number must fit value = c + step * index with step = +-spacing_m; a reading that
    does not fit the others is an outlier (a misread). The plan must not come out mirrored.

    Returns {"ok", "message", "easting": (family, c, step), "northing": (...), "used", "outliers",
    "crosses": [(x, y, easting, northing)], "candidates": [...]}. ok is False when the numbers are
    not enough or allow more than one placement; then nothing is guessed."""
    a, b = np.array(grid["a"]), np.array(grid["b"])
    Minv = np.linalg.inv(np.column_stack([a, b]))      # (x, y) -> (i, j)
    vals = [(f, int(n), float(v)) for f, n, v in readings]
    if not vals:
        return {"ok": False, "message": "No numbers read"}
    # which numbers are eastings: by the expected position, else by the line family
    if expected is not None:
        kind = {k: ("E" if abs(v - expected[0]) < abs(v - expected[1]) else "N") for k, (_, _, v) in enumerate(vals)}
    else:
        fams = {f for f, _, _ in vals}
        if len(fams) < 2:
            return {"ok": False, "message": "Numbers on one grid direction only; give the expected position"}
        big = {f: np.median([v for ff, _, v in vals if ff == f]) for f in fams}
        e_fam = min(big, key=big.get)          # Rechtswert < Hochwert in Germany (GK, UTM with zone)
        kind = {k: ("E" if f == e_fam else "N") for k, (f, _, _) in enumerate(vals)}
    result = {"used": [], "outliers": []}
    lines = {}
    for coord in ("E", "N"):
        rows = [vals[k] for k in kind if kind[k] == coord]
        if not rows:
            return {"ok": False, "message": f"No {'Rechtswert' if coord == 'E' else 'Hochwert'} read",
                    "used": [], "outliers": []}
        # the family: the one most readings of this coordinate are on
        fam = max({f for f, _, _ in rows}, key=lambda f: sum(1 for r in rows if r[0] == f))
        on = [r for r in rows if r[0] == fam]
        result["outliers"] += [r for r in rows if r[0] != fam]
        best = None
        for step in (spacing_m, -spacing_m):
            for _f, n0, v0 in on:
                c = v0 - step * n0
                fit = [r for r in on if abs(c + step * r[1] - r[2]) <= tolerance]
                if best is None or len(fit) > len(best[2]):
                    best = (c, step, fit)
        c, step, fit = best
        result["outliers"] += [r for r in on if r not in fit]
        result["used"] += fit
        both = len({r[1] for r in fit}) >= 2
        lines[coord] = (fam, c, step if both else None, fit)
    if lines["E"][0] == lines["N"][0]:
        return dict(result, ok=False, message="Rechtswert and Hochwert lie on the same grid direction")
    # unknown direction (one line read): the plan must not be mirrored
    options = []
    for se in ([lines["E"][2]] if lines["E"][2] else [spacing_m, -spacing_m]):
        for sn in ([lines["N"][2]] if lines["N"][2] else [spacing_m, -spacing_m]):
            def value(coord, s, i, j):
                fam, c0, _, fit = lines[coord]
                n0 = fit[0][1]
                c = fit[0][2] - s * n0
                return c + s * (i if fam == "i" else j)
            # d(E, N)/d(i, j)
            G = np.array([[value("E", se, 1, 0) - value("E", se, 0, 0), value("E", se, 0, 1) - value("E", se, 0, 0)],
                          [value("N", sn, 1, 0) - value("N", sn, 0, 0), value("N", sn, 0, 1) - value("N", sn, 0, 0)]])
            det = np.linalg.det(G @ Minv)
            if (det < 0) if y_down else (det > 0):   # with image y running down, a proper map has det < 0
                options.append((se, sn, value))
    result["candidates"] = [(o[0], o[1]) for o in options]
    if len(options) != 1:
        return dict(result, ok=False, message=(
            "The numbers allow two placements (turned by 180 degrees): read one more number on "
            "another line" if len(options) > 1 else "The numbers would mirror the plan: a misread?"))
    se, sn, value = options[0]
    crosses = [(c["x"], c["y"], value("E", se, c["i"], c["j"]), value("N", sn, c["i"], c["j"]))
               for c in grid["crosses"]]
    fe, fn = lines["E"][0], lines["N"][0]
    ce = lines["E"][3][0][2] - se * lines["E"][3][0][1]
    cn = lines["N"][3][0][2] - sn * lines["N"][3][0][1]
    return dict(result, ok=True, message=f"{len(result['used'])} numbers fit, {len(result['outliers'])} left out",
                easting=(fe, ce, se), northing=(fn, cn, sn), crosses=crosses)
