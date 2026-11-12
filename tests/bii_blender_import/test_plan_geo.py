"""Tests for plan_geo, the coordinate math of the plan import. Pure Python.

Run: python -m unittest discover tests/bii_blender_import
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins",
                                "bii_blender_import"))
import plan_geo  # noqa: E402

# Reference values from PROJ 9.8.1 (pyproj 3.8.0), with the towgs84
# parameters of plan_geo.DATUMS in the proj strings, for example
# "+proj=tmerc +lon_0=9 +k=1 +x_0=3500000 +ellps=bessel +towgs84=..." for DB_REF
# zone 3. ETRS89 counts as WGS84 (towgs84=0,0,0).
REFERENCES = [
    # crs, easting, northing, utm_zone, lat, lon
    ('DB_REF', 3588400, 5520000, 32, 49.810029344, 10.227131898),
    ('DHDN', 4590250, 5820000, 32, 52.505684017, 13.327636628),
    ('ETRS89', 32588400, 5520000, 32, 49.825915493, 10.229064788),
    ('ETRS89', 390000, 5820000, 33, 52.518995313, 13.378811770),
]

DEG_PER_MM = 1e-3 / 111_000  # about one millimeter in latitude


class GridToWgs84Test(unittest.TestCase):

    def test_matches_proj(self):
        for crs, e, n, zone, lat, lon in REFERENCES:
            with self.subTest(crs=crs, easting=e):
                got_lat, got_lon = plan_geo.grid_to_wgs84(crs, e, n, zone)
                self.assertAlmostEqual(got_lat, lat, delta=DEG_PER_MM)
                self.assertAlmostEqual(got_lon, lon, delta=DEG_PER_MM / math.cos(
                    math.radians(lat)))

    def test_round_trip_in_millimeters(self):
        for crs, e, n, zone, lat, lon in REFERENCES:
            with self.subTest(crs=crs, easting=e):
                grid_zone, _ = plan_geo.split_zone(crs, e, zone)
                back = plan_geo.wgs84_to_grid(crs, lat, lon, grid_zone,
                                              zone_prefix=e >= 1e7 or crs != 'ETRS89')
                self.assertAlmostEqual(back[0], e, delta=0.005)
                self.assertAlmostEqual(back[1], n, delta=0.005)

    def test_rejects_rechtswert_without_zone(self):
        with self.assertRaises(ValueError):
            plan_geo.grid_to_wgs84('DB_REF', 588400, 5520000)


class SceneProjectionTest(unittest.TestCase):

    def test_round_trip(self):
        proj = plan_geo.SceneProjection(50.0, 9.0)
        for x, y in ((0.0, 0.0), (1234.5, -987.6), (-5000.0, 4000.0)):
            lat, lon = proj.to_geographic(x, y)
            bx, by = proj.from_geographic(lat, lon)
            self.assertAlmostEqual(bx, x, delta=1e-4)
            self.assertAlmostEqual(by, y, delta=1e-4)

    def test_origin_is_zero(self):
        self.assertEqual(plan_geo.SceneProjection(50.0, 9.0).from_geographic(50.0, 9.0),
                         (0.0, 0.0))


def _grid_points(fit, sheet_points):
    return [plan_geo.apply_similarity(fit, x, y) for x, y in sheet_points]


SHEET = [(0.1, 0.1), (0.5, 0.1), (0.5, 0.4), (0.1, 0.4)]
# 1:1000, turned by 2 degrees, at a Gauss-Krüger position.
TRUE_FIT = (1000 * math.cos(math.radians(2)), 1000 * math.sin(math.radians(2)),
            3588000.0, 5520000.0)


class FitTest(unittest.TestCase):

    def test_similarity_recovers_transform(self):
        fit = plan_geo.fit_similarity(SHEET, _grid_points(TRUE_FIT, SHEET))
        for got, want in zip(fit, TRUE_FIT):
            self.assertAlmostEqual(got, want, places=4)

    def test_similarity_needs_two_points(self):
        self.assertIsNone(plan_geo.fit_similarity(SHEET[:1], SHEET[:1]))

    def test_invert_similarity(self):
        u, v = plan_geo.apply_similarity(TRUE_FIT, 0.3, 0.2)
        x, y = plan_geo.invert_similarity(TRUE_FIT, u, v)
        self.assertAlmostEqual(x, 0.3, places=9)
        self.assertAlmostEqual(y, 0.2, places=9)

    def test_weighted_guesses_only_fill_open_axis(self):
        # Rechtswerte read from the plan, Hochwerte guessed 30 m too far north:
        # Maßstab, rotation and east-west come from the Rechtswerte, the
        # guesses only shift north-south.
        grid = _grid_points(TRUE_FIT, SHEET)
        w = plan_geo.GUESS_WEIGHT
        points = [(x, y, e, 1.0, n + 30.0, w) for (x, y), (e, n) in zip(SHEET, grid)]
        fit = plan_geo.fit_similarity_weighted(points)
        self.assertAlmostEqual(plan_geo.similarity_scale(fit), 1000, places=3)
        self.assertAlmostEqual(math.degrees(plan_geo.similarity_angle(fit)), 2, places=5)
        e, n = plan_geo.apply_similarity(fit, 0.3, 0.25)
        want_e, want_n = plan_geo.apply_similarity(TRUE_FIT, 0.3, 0.25)
        self.assertAlmostEqual(e, want_e, delta=1e-3)
        self.assertAlmostEqual(n, want_n + 30.0, delta=1e-3)

    def test_weighted_equals_plain_without_guesses(self):
        grid = _grid_points(TRUE_FIT, SHEET)
        points = [(x, y, e, 1.0, n, 1.0) for (x, y), (e, n) in zip(SHEET, grid)]
        for got, want in zip(plan_geo.fit_similarity_weighted(points), TRUE_FIT):
            self.assertAlmostEqual(got, want, places=4)

    def test_value_with_weight_zero_is_left_out(self):
        # A point whose Hochwert was not entered yet (0 in the field) must not
        # pull the fit towards Hochwert 0.
        grid = _grid_points(TRUE_FIT, SHEET)
        points = [(x, y, e, 1.0, n, 1.0) for (x, y), (e, n) in zip(SHEET[:3], grid[:3])]
        (x, y), (e, _) = SHEET[3], grid[3]
        points.append((x, y, e, 1.0, 0, 0.0))
        for got, want in zip(plan_geo.fit_similarity_weighted(points), TRUE_FIT):
            self.assertAlmostEqual(got, want, places=4)


class FixesScaleAndRotationTest(unittest.TestCase):

    def _points(self, sheet, read_e=True, read_n=True):
        grid = _grid_points(TRUE_FIT, sheet)
        return [(x, y, e, float(read_e), n, float(read_n)) for (x, y), (e, n) in zip(sheet, grid)]

    def test_two_full_points_fix_it(self):
        self.assertTrue(plan_geo.fixes_scale_and_rotation(self._points(SHEET[:2])))

    def test_one_full_point_does_not(self):
        self.assertFalse(plan_geo.fixes_scale_and_rotation(self._points(SHEET[:1] * 2)))

    def test_rechtswerte_of_two_crosses_in_a_row_do_not(self):
        # Only Rechtswerte read, from 2 crosses: the rotation is left to the guesses.
        self.assertFalse(plan_geo.fixes_scale_and_rotation(
            self._points(SHEET[:2], read_n=False)))

    def test_rechtswerte_of_three_crosses_in_a_line_do_not(self):
        line = [(0.1, 0.1), (0.3, 0.1), (0.5, 0.1)]
        self.assertFalse(plan_geo.fixes_scale_and_rotation(self._points(line, read_n=False)))

    def test_rechtswerte_of_three_crosses_not_in_a_line_do(self):
        self.assertTrue(plan_geo.fixes_scale_and_rotation(
            self._points(SHEET[:3], read_n=False)))


class RoundToGridTest(unittest.TestCase):

    def test_finer_grid_than_the_read_values_suggest(self):
        # Crosses read at 3588000 and 3589000 suggest 1000 m; a prediction of
        # 3588300.4 lies on the 100 m grid, not on the 1000 m one.
        self.assertEqual(plan_geo.round_to_grid(3588300.4, 1000), 3588300)

    def test_coarse_grid_when_near(self):
        self.assertEqual(plan_geo.round_to_grid(3588997.0, 1000), 3589000)

    def test_off_every_grid_rounds_to_meters(self):
        self.assertEqual(plan_geo.round_to_grid(3588333.4, 1000), 3588333)

    def test_no_spacing(self):
        self.assertEqual(plan_geo.round_to_grid(3588300.4, 0), 3588300)


class CheckAxesTest(unittest.TestCase):

    def test_square_grid_has_no_warnings(self):
        self.assertEqual(plan_geo.check_axes(SHEET, _grid_points(TRUE_FIT, SHEET)), [])

    def test_swapped_values_are_mirrored(self):
        grid = [(n, e) for e, n in _grid_points(TRUE_FIT, SHEET)]
        warnings = plan_geo.check_axes(SHEET, grid)
        self.assertTrue(any("mirrored" in w for w in warnings), warnings)

    def test_value_off_by_grid_steps(self):
        grid = _grid_points(TRUE_FIT, SHEET)
        # Hochwerte read with twice the spacing: their Maßstab is 1:2000.
        grid = [(e, 5520000 + 2 * (n - 5520000)) for e, n in grid]
        warnings = plan_geo.check_axes(SHEET, grid)
        self.assertTrue(any("do not fit together" in w for w in warnings), warnings)

    def test_needs_three_points(self):
        self.assertEqual(plan_geo.check_axes(SHEET[:2], _grid_points(TRUE_FIT, SHEET[:2])), [])


class CheckCoordinatesTest(unittest.TestCase):

    def test_gauss_krueger_zone(self):
        info, warnings = plan_geo.check_coordinates('DB_REF', [(3588400, 5520000)], 32)
        self.assertEqual(warnings, [])
        self.assertIn("zone 3", info)

    def test_six_digit_easting_in_gauss_krueger(self):
        _, warnings = plan_geo.check_coordinates('DHDN', [(582450, 5520000)], 32)
        self.assertTrue(any("UTM easting" in w for w in warnings), warnings)

    def test_swapped(self):
        _, warnings = plan_geo.check_coordinates('DHDN', [(5520000, 3588400)], 32)
        self.assertTrue(any("swapped" in w for w in warnings), warnings)

    def test_mixed_zones(self):
        _, warnings = plan_geo.check_coordinates(
            'DHDN', [(3588400, 5520000), (4410000, 5520000)], 32)
        self.assertTrue(any("different zones" in w for w in warnings), warnings)

    def test_utm_six_digits_uses_zone_setting(self):
        info, warnings = plan_geo.check_coordinates('ETRS89', [(390000, 5820000)], 33)
        self.assertEqual(warnings, [])
        self.assertIn("UTM zone 33", info)

    def test_hochwert_not_read_is_not_checked(self):
        info, warnings = plan_geo.check_coordinates('DB_REF', [(3588400, None)], 32)
        self.assertEqual(warnings, [])
        self.assertIn("zone 3", info)


if __name__ == "__main__":
    unittest.main()
