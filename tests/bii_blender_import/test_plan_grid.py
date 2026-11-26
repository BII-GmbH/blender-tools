"""Tests for plan_grid (pure numpy): grid crosses, lines, solving the values.

Run: python -m unittest discover tests/bii_blender_import
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "bii_blender_import"))

try:
    import numpy as np
except ImportError:
    raise unittest.SkipTest("numpy missing")

import plan_grid  # noqa: E402

DPI = 200.0
MASSSTAB = 1000
SPACING = 100 / MASSSTAB / 0.0254 * DPI      # 100 m at 1:1000 in pixels


def line(img, x0, y0, x1, y1, width=1):
    n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
    for t in np.linspace(0, 1, n):
        x, y = x0 + t * (x1 - x0), y0 + t * (y1 - y0)
        img[int(round(y)) - width // 2:int(round(y)) + width // 2 + 1,
            int(round(x)) - width // 2:int(round(x)) + width // 2 + 1] = 0


def sheet(angle_deg, w=4200, h=2600, origin=(300.0, 250.0), touching=False, noise=0):
    """A white image with grid crosses every SPACING px, turned by angle_deg; returns
    (pixels, lattice basis a, b, origin)."""
    img = np.full((h, w), 255, np.uint8)
    a_ang = math.radians(angle_deg)
    a = np.array([math.cos(a_ang), math.sin(a_ang)]) * SPACING
    b = np.array([-a[1], a[0]])
    arm = plan_grid.ARM_MM / 25.4 * DPI
    o = np.array(origin)
    for i in range(-10, 11):
        for j in range(-10, 11):
            c = o + i * a + j * b
            if not (40 < c[0] < w - 40 and 40 < c[1] < h - 40):
                continue
            for d in (a, b):
                u = d / np.linalg.norm(d) * arm
                line(img, c[0] - u[0], c[1] - u[1], c[0] + u[0], c[1] + u[1])
            if touching and (i + j) % 3 == 0:     # a drawing line runs into the cross
                line(img, c[0] + 5, c[1] - 60, c[0] + 5, c[1] + 60)
    rng = np.random.default_rng(1)
    for _ in range(noise):                         # other short marks
        x, y = rng.integers(50, w - 50), rng.integers(50, h - 50)
        line(img, x, y, x + rng.integers(-30, 30), y + rng.integers(-30, 30))
    return img, a, b, o


class FindGridTest(unittest.TestCase):
    def check(self, angle, **kw):
        img, a, b, o = sheet(angle, **kw)
        g = plan_grid.find_grid(img, DPI, massstab=MASSSTAB)
        self.assertIsNotNone(g, f"angle {angle}")
        self.assertEqual(g["spacing_m"], 100.0)
        self.assertAlmostEqual(g["spacing_px"], SPACING, delta=1.0)
        # every cross on the sheet found, within 1.5 px
        M = np.column_stack([a, b])
        for c in g["crosses"]:
            ij = np.linalg.solve(M, np.array([c["x"], c["y"]]) - o)
            self.assertLess(np.abs(ij - np.round(ij)).max() * SPACING, 1.5)
        return g

    def test_angles(self):
        for angle in (0.0, 14.2, 37.0, 45.0, 71.5):
            g = self.check(angle)
            self.assertGreaterEqual(len(g["crosses"]), 12)

    def test_crosses_touching_lines(self):
        g = self.check(23.0, touching=True, noise=150)
        self.assertGreaterEqual(len(g["crosses"]), 12)

    def test_no_grid(self):
        img = np.full((800, 1600), 255, np.uint8)
        line(img, 100, 400, 1500, 420, width=3)
        self.assertIsNone(plan_grid.find_grid(img, DPI, massstab=MASSSTAB))


class SolveTest(unittest.TestCase):
    def grid(self, angle=20.0):
        img, a, b, o = sheet(angle)
        return plan_grid.find_grid(img, DPI, massstab=MASSSTAB)

    def truth(self, g, e0=3589000.0, n0=5546000.0, e_fam="i", se=100, sn=-100):
        """Values printed on line (family, index), for a plan with eastings on e_fam lines."""
        def value(fam, idx):
            return (e0 + se * idx) if fam == e_fam else (n0 + sn * idx)
        return value

    def test_solves_and_rejects_misread(self):
        g = self.grid()
        v = self.truth(g)
        reads = [("i", 0, v("i", 0)), ("i", 2, v("i", 2)), ("j", 1, v("j", 1)), ("j", 3, v("j", 3) + 1000)]
        s = plan_grid.solve(g, reads, 100.0)
        self.assertTrue(s["ok"], s["message"])
        self.assertEqual(s["outliers"], [("j", 3, v("j", 3) + 1000)])
        for c, (x, y, e, n) in zip(g["crosses"], s["crosses"]):
            self.assertEqual((e, n), (v("i", c["i"]), v("j", c["j"])))

    def test_one_number_per_direction_is_open(self):
        g = self.grid()
        v = self.truth(g)
        s = plan_grid.solve(g, [("i", 0, v("i", 0)), ("j", 1, v("j", 1))], 100.0)
        self.assertFalse(s["ok"])
        self.assertIn("180", s["message"])

    def test_mirrored_is_refused(self):
        g = self.grid()
        good = self.truth(g)
        bad = self.truth(g, sn=100)          # northings growing the wrong way: a mirror image
        reads = [("i", 0, good("i", 0)), ("i", 1, good("i", 1)), ("j", 0, bad("j", 0)), ("j", 1, bad("j", 1))]
        s_good = plan_grid.solve(g, [("i", 0, good("i", 0)), ("i", 1, good("i", 1)),
                                     ("j", 0, good("j", 0)), ("j", 1, good("j", 1))], 100.0)
        s_bad = plan_grid.solve(g, reads, 100.0)
        self.assertNotEqual(s_good["ok"], s_bad["ok"])

    def test_contact_sheet(self):
        strips = [np.full((40, 200), 255, np.uint8), np.full((30, 120), 255, np.uint8)]
        img, tops = plan_grid.contact_sheet(strips)
        self.assertEqual(len(tops), 2)
        self.assertEqual(img.dtype, np.uint8)


if __name__ == "__main__":
    unittest.main()
