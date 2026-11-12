"""Tests for plan_km, placing plans by Kilometrierung and joining sheets. Pure Python.

Run: python -m unittest discover tests/bii_blender_import
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins",
                                "bii_blender_import"))
import plan_km  # noqa: E402
from plan_km import Observation  # noqa: E402


def arc(n=60, radius=800.0):
    """A curved track: quarter circle, 1257 m long."""
    return [(radius * math.sin(a), radius * (1 - math.cos(a)))
            for a in (i * (math.pi / 2) / (n - 1) for i in range(n))]


def move(points, angle, shift, pivot=(0.0, 0.0)):
    out = []
    for x, y in points:
        rx, ry = plan_km.rotate_about(x, y, angle, pivot)
        out.append((rx + shift[0], ry + shift[1]))
    return out


class ParseTest(unittest.TestCase):

    def test_km_labels(self):
        self.assertAlmostEqual(plan_km.parse_km("5,3"), 5.3)
        self.assertAlmostEqual(plan_km.parse_km(" 5.504 "), 5.504)
        self.assertAlmostEqual(plan_km.parse_km("13,4+23,05"), 13.42305)
        self.assertAlmostEqual(plan_km.parse_km("-0,2"), -0.2)
        self.assertAlmostEqual(plan_km.parse_km("12"), 12.0)
        self.assertIsNone(plan_km.parse_km(""))
        self.assertIsNone(plan_km.parse_km("EÜ"))

    def test_landmark_label(self):
        self.assertEqual(plan_km.parse_label("EÜ (Weg) 5240/5,504/1616"), ("5240", 5.504))
        self.assertEqual(plan_km.parse_label("5,4"), (None, 5.4))
        self.assertEqual(plan_km.parse_label("Weiche 12"), (None, None))

    def test_format(self):
        self.assertEqual(plan_km.format_km(5.3), "5,3")
        self.assertEqual(plan_km.format_km(5.504), "5,504")


class KmLineTest(unittest.TestCase):

    def test_anchors_and_position(self):
        pts = [(0, 0), (1000, 0)]
        line = plan_km.KmLine.from_anchors(pts, [(0, 5.0), (1000, 6.0)])
        (x, y), t = line.position(5.3)
        self.assertAlmostEqual(x, 300)
        self.assertEqual(t, (1.0, 0.0))
        km, d = line.km_at(450, 3)
        self.assertAlmostEqual(km, 5.45)
        self.assertAlmostEqual(d, 3)
        self.assertIsNone(line.position(7.0))
        self.assertIsNotNone(line.position(6.04))  # straight on beyond the end

    def test_falling_km(self):
        line = plan_km.KmLine.from_anchors([(0, 0), (0, 500)], [(0, 3.0), (500, 2.5)])
        (x, y), t = line.position(2.9)
        self.assertAlmostEqual(y, 100)
        self.assertEqual(t, (0.0, -1.0))  # toward rising km

    def test_ticks_keep_a_km_jump(self):
        # 1000 m of line, km 5,000 at the start, but the Kilometrierung jumps:
        # the ticks at 300 m and later count 20 m short (km 5,3 at 320 m).
        pts = [(float(x), 0.0) for x in range(0, 1001, 50)]
        ticks = [(100, 0), (200, 0), (320, 0), (420, 0), (520, 0)]
        anchors = plan_km.ticks_to_anchors(pts, 5.0, 5.98, ticks, 100)
        kms = [round(k, 3) for _, k in anchors]
        self.assertEqual(kms, [5.0, 5.1, 5.2, 5.3, 5.4, 5.5, 5.98])
        line = plan_km.KmLine.from_anchors(pts, anchors)
        self.assertAlmostEqual(line.position(5.3)[0][0], 320)


class RigidFitTest(unittest.TestCase):

    def setUp(self):
        self.track = arc()
        self.line = plan_km.KmLine.from_anchors(self.track, [(0, 5.0), (
            plan_km.cumulative(self.track)[-1], 5.0 + plan_km.cumulative(self.track)[-1] / 1000)])
        # The plan: the track turned by 40° and shifted, as if roughly placed.
        self.angle, self.shift = math.radians(40), (120.0, -80.0)

    def plan_point(self, km):
        (x, y), _ = self.line.position(km)
        return move([(x, y)], self.angle, self.shift)[0]

    def km_obs(self, km, weight=1.0):
        pos, tangent = self.line.position(km)
        p = self.plan_point(km)
        return [Observation('DIR', f"km {km}", p, pos, tangent, weight=weight),
                Observation('LINE', f"km {km}", p, lines=[self.track], weight=weight)]

    def check(self, fit, tolerance=0.01):
        for km in (5.1, 5.5, 6.0):
            got = fit.apply(*self.plan_point(km))
            want = self.line.position(km)[0]
            self.assertLess(math.dist(got, want), tolerance, f"km {km}")

    def test_two_km_points(self):
        fit = plan_km.fit_rigid(self.km_obs(5.2) + self.km_obs(5.9))
        self.check(fit)
        self.assertFalse(fit.weak)
        self.assertLess(fit.rms, 1e-3)

    def test_upside_down_plan(self):
        self.angle = math.radians(180)
        fit = plan_km.fit_rigid(self.km_obs(5.2) + self.km_obs(5.9))
        self.check(fit)

    def test_one_km_point_and_track_points(self):
        obs = self.km_obs(5.4)
        for km in (5.2, 5.6, 5.8):  # clicked on the plan track, km unknown
            obs.append(Observation('LINE', "track", self.plan_point(km), lines=[self.track]))
        fit = plan_km.fit_rigid(obs)
        self.check(fit, tolerance=0.05)
        self.assertNotIn('ROTATION', fit.weak)

    def test_track_points_alone_leave_the_position_along_open(self):
        # On a straight track, track points fix rotation and the cross shift only.
        track = [(0.0, 0.0), (1000.0, 0.0)]
        obs = [Observation('LINE', "t", (x, 5.0 + 0.01 * x), lines=[track])
               for x in (100, 400, 700)]
        fit = plan_km.fit_rigid(obs)
        self.assertIn('SHIFT', fit.weak)
        self.assertAlmostEqual(fit.apply(400, 9)[1], 0.0, places=3)  # onto the track
        self.assertLess(abs(fit.apply(400, 9)[0] - 400), 0.5)  # along: stays

    def test_landmark_outweighs_km(self):
        # OSM km off by 8 m; the bridge (landmark) is right.
        obs = self.km_obs(5.2) + self.km_obs(5.9)
        for o in obs:
            if o.kind == 'DIR':
                o.q = (o.q[0] + 8 * o.n[0], o.q[1] + 8 * o.n[1])
        bridge = 5.504
        obs.append(Observation('POINT', "EÜ", self.plan_point(bridge),
                               self.line.position(bridge)[0], weight=50))
        fit = plan_km.fit_rigid(obs)
        got = fit.apply(*self.plan_point(bridge))
        self.assertLess(math.dist(got, self.line.position(bridge)[0]), 0.5)

    def test_wrong_km_shows_in_residuals_and_spacing(self):
        obs = self.km_obs(5.2) + self.km_obs(5.6)
        # The third point is km 5,9 on the plan, typed as 5,8.
        pos, tangent = self.line.position(5.8)
        p = self.plan_point(5.9)
        obs.append(Observation('DIR', "P3", p, pos, tangent))
        fit = plan_km.fit_rigid(obs)
        self.assertGreater(max(o.residual for o in fit.observations), 20)
        warnings = plan_km.spacing_warnings([
            ("P1", 5.2, self.plan_point(5.2), self.line.position(5.2)[0]),
            ("P2", 5.6, self.plan_point(5.6), self.line.position(5.6)[0]),
            ("P3", 5.8, p, pos)])
        self.assertEqual(len(warnings), 1)
        self.assertIn("P3", warnings[0])


class MatchTest(unittest.TestCase):

    def test_match_line_picks_the_side(self):
        # Neighbor sheet left of the match line x = 0; ours must go right of it.
        other = ((0.0, 10.0), (0.0, 60.0))
        other_center = (-200.0, 0.0)
        # Our sheet lies somewhere, turned: its match line runs along its own y axis.
        angle, shift = math.radians(70), (500.0, 300.0)
        own_local = [(0.0, 10.0), (0.0, -40.0)]  # second point clicked the other way
        own = move(own_local, angle, shift)
        own_center = move([(150.0, 0.0)], angle, shift)[0]
        a, pivot, t = plan_km.match_transform(own, other, False, own_center, other_center)
        p1 = plan_km.rotate_about(*own[0], a, pivot)
        self.assertAlmostEqual(p1[0] + t[0], 0.0, places=6)
        self.assertAlmostEqual(p1[1] + t[1], 10.0, places=6)
        c = plan_km.rotate_about(*own_center, a, pivot)
        self.assertGreater(c[0] + t[0], 0)  # right of the line

    def test_two_shared_points(self):
        other = ((0.0, 0.0), (100.0, 0.0))
        own = move([(0.0, 0.0), (100.0, 0.0)], 1.0, (30.0, 40.0))
        a, pivot, t = plan_km.match_transform(own, other, True)
        for p, q in zip(own, other):
            r = plan_km.rotate_about(*p, a, pivot)
            self.assertAlmostEqual(r[0] + t[0], q[0], places=6)
            self.assertAlmostEqual(r[1] + t[1], q[1], places=6)


if __name__ == "__main__":
    unittest.main()
