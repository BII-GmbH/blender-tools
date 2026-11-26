"""Tests for signal_spec and signal_symbols (pure Python with numpy).

Run: python -m unittest discover tests/bii_blender_import
(also inside Blender via run_in_blender.py).
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "bii_blender_import"))

try:
    import numpy  # noqa: F401
except ImportError:
    raise unittest.SkipTest("numpy missing")

import signal_spec as spec  # noqa: E402
import signal_symbols as symbols  # noqa: E402


class SpecTest(unittest.TestCase):
    def test_defaults_are_valid(self):
        for b in spec.BAUFORMEN:
            cfg = spec.config(b.key)
            self.assertTrue(spec.valid(b.key, cfg), b.key)

    def test_unknown_option_rejected(self):
        with self.assertRaises(ValueError):
            spec.config("ks_haupt", zs3="neon")
        with self.assertRaises(KeyError):
            spec.config("ks_vor", sh1=True)  # distant signals have no Sh 1

    def test_modifier_round_trip(self):
        for b in spec.BAUFORMEN:
            for cfg in spec.combinations(b.key)[::37]:
                values = spec.modifier_values(b.key, cfg)
                self.assertEqual(spec.config_from_modifier(b.key, values), cfg)

    def test_config_from_menu_item_names(self):
        cfg = spec.config_from_modifier("ks_haupt", {"Zs3": "Tafel", "Kopf": "Zs7", "Sh1": True})
        self.assertEqual((cfg["zs3"], cfg["kopf"], cfg["sh1"]), ("tafel", "zs7", True))

    def test_exclusions(self):
        cfg = spec.config("hv_haupt", hp="hp02")
        self.assertFalse(spec.allowed("hv_haupt", "sh1", True, cfg))
        self.assertFalse(spec.allowed("ks_haupt", "licht", "unten", spec.config("ks_haupt")))
        self.assertTrue(spec.allowed("ks_vor", "licht", "unten", spec.config("ks_vor")))
        fixed = spec.fix("hv_haupt", dict(cfg, sh1=True))
        self.assertTrue(spec.valid("hv_haupt", fixed))

    def test_aspects_follow_config(self):
        self.assertNotIn("Hp0 + Sh1", spec.aspects("ks_haupt", spec.config("ks_haupt")))
        self.assertIn("Hp0 + Sh1", spec.aspects("ks_haupt", spec.config("ks_haupt", sh1=True)))
        self.assertIn("Hp0 + Zs7", spec.aspects("ks_haupt", spec.config("ks_haupt", kopf="zs7")))
        self.assertNotIn("Hp2", spec.aspects("hv_haupt", spec.config("hv_haupt", hp="hp01")))
        for b in spec.BAUFORMEN:
            full = spec.all_aspects(b.key)
            self.assertEqual(full[0], "Aus")
            for cfg in spec.combinations(b.key)[::53]:
                got = spec.aspects(b.key, cfg)
                self.assertEqual(got, [a for a in full if a in got])  # subset, same order

    def test_other_signals(self):
        cfg = spec.config("lf7", kennziffer=10)
        self.assertEqual(spec.modifier_values("lf7", cfg), {"Kennziffer": 10, "Mast": True})
        self.assertEqual(spec.config_from_modifier("lf7", {"Kennziffer": 99})["kennziffer"], 16)  # clamped
        with self.assertRaises(ValueError):
            spec.config("lf7", kennziffer=17)
        self.assertFalse(spec.shown("zs3", "kennziffer", spec.config("zs3", ausfuehrung="licht")))
        self.assertTrue(spec.shown("zs3", "kennziffer", spec.config("zs3", ausfuehrung="tafel")))
        self.assertEqual(spec.all_aspects("ls"), ["Aus", "Sh0", "Sh1"])
        self.assertEqual(spec.symbol_elements("ne3", spec.config("ne3", streifen=2))[-1], ("bake", 2))
        groups = {b.group for b in spec.BAUFORMEN}
        self.assertEqual(groups, set(spec.GROUPS))

    def test_object_names(self):
        n = spec.object_name
        self.assertEqual(n("ks_vor", spec.config("ks_vor", zs3v="licht")), "Vorsignal Ks1/Ks2/Zs3v")
        self.assertEqual(n("ks_haupt", spec.config("ks_haupt", kopf="ohne", sh1=True, zs3="tafel")),
                         "Hauptsperrsignal Hp0/Ks1/Zs3-Tafel/Sh1")
        self.assertEqual(n("hv_haupt", spec.config("hv_haupt", bedienung="zug", hp="hp01", kopf="ohne")),
                         "Blocksignal zugbedient Hp0/Hp1")
        self.assertEqual(n("lf7", spec.config("lf7", kennziffer=8)), "Geschwindigkeitssignal 80 km/h Lf7")
        self.assertEqual(n("ne3", spec.config("ne3", streifen=3)), "Vorsignalbake 3 Streifen Ne3")
        for b in spec.BAUFORMEN:  # mast aside, different configurations get different names
            names = {}
            for cfg in spec.combinations(b.key):
                key = tuple(sorted((k, v) for k, v in cfg.items() if k != "mast"))
                names.setdefault(n(b.key, cfg), key)
                self.assertEqual(names[n(b.key, cfg)], key, n(b.key, cfg))

    def test_symbol_of_06F(self):
        # Ks-Mehrabschnittssignal, half filled, Zs 3 and Zs 3v lights, no Zs 1 (signals 06F/06FF)
        cfg = spec.config("ks_mehr", bedienung="stellwerk_zug", kopf="ohne", zs3="licht", zs3v="licht")
        els = spec.symbol_elements("ks_mehr", cfg)
        self.assertIn(("ks_head", "caps", "half", False), els)
        self.assertIn(("tri_up", True), els)
        self.assertIn(("tri_down", True), els)
        self.assertNotIn(("chev",), els)


class SymbolsTest(unittest.TestCase):
    def test_every_combination_draws(self):
        for b in spec.BAUFORMEN:
            for cfg in spec.combinations(b.key)[::97]:
                px = symbols.draw(spec.symbol_elements(b.key, cfg), 48)
                self.assertEqual(px.shape, (48, 48, 4))
                self.assertLess(px[..., 0].min(), 0.5, f"{b.key} {cfg}: no ink")

    def test_option_icons(self):
        cfg = spec.config("ks_haupt")
        for e in spec.elements("ks_haupt"):
            for v, _, _ in spec.options("ks_haupt", e.key, cfg):
                els = spec.option_symbol("ks_haupt", e.key, v, cfg)
                if els:
                    symbols.draw(els, 32)

    def test_sh1_part_below_the_fill(self):
        # plan symbol (signal ZU8): half filled Ks head, then the Sh 1 part with two empty fields
        # inside the same, longer outline; the fill stays above it
        plain = symbols.build([("ks_head", "bell", "half", False)])
        sh = symbols.build([("ks_head", "bell", "half", True)])
        self.assertAlmostEqual(sh.bounds()[3] - plain.bounds()[3], symbols.SH_PART, places=3)
        g = sh.gray(200, 1)                                   # rows from the bottom
        col = sh.pixel(2.5, 0, 200)[0]                             # right half, where the fill is
        rows = [sh.pixel(0, y, 200)[1] for y in (symbols.SH_PART * 0.25, symbols.SH_PART * 0.75, 8.0)]
        fields = [g[200 - int(round(r)), int(round(col))] for r in rows]
        self.assertGreater(fields[0], 0.5)                     # Sh 1 fields empty
        self.assertGreater(fields[1], 0.5)
        self.assertLess(fields[2], 0.5)                        # head above: filled

    def test_zs2_and_ne2(self):
        self.assertEqual(spec.symbol_elements("zs2", spec.config("zs2")), [("square", True)])
        self.assertEqual(spec.symbol_elements("ne2", spec.config("ne2"))[-1], ("ne2",))
        self.assertEqual(spec.symbol_elements("ne2", spec.config("ne2"))[0], ("foot",))

    def test_deterministic(self):
        els = spec.symbol_elements("hv_mehr", spec.config("hv_mehr", zs3="licht", kopf="zs7"))
        self.assertTrue((symbols.draw(els, 64) == symbols.draw(els, 64)).all())



class LegendTest(unittest.TestCase):
    def test_anchor_is_the_foot(self):
        els = spec.symbol_elements("ks_haupt", spec.config("ks_haupt"))
        x, y = symbols.anchor(els, 100)
        self.assertAlmostEqual(x, 50.0, places=3)  # middle of the foot bar
        self.assertGreater(y, 85)                  # at the bottom (rows from the top)
        px = symbols.draw(els, 100)[::-1, :, 0]
        self.assertLess(px[int(round(y)), int(round(x))], 0.5)  # on ink

    def test_entries_and_render(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow missing")
        import signal_legend as legend
        rows = legend.entries()
        self.assertEqual([b.key for b, _ in rows], [b.key for b in spec.BAUFORMEN])
        codes = {c for _, tiles in rows for _, c, _ in tiles}
        self.assertIn('zs3="tafel"', codes)
        self.assertIn("streifen=5", codes)
        self.assertNotIn("mast=False", codes)  # not visible in the symbol
        for _, tiles in rows:  # no two tiles of a Bauform look the same
            self.assertEqual(len({json.dumps(els) for _, _, els in tiles}), len(tiles))
        w, h = legend.render().size
        self.assertGreater(h, w)


if __name__ == "__main__":
    unittest.main()
