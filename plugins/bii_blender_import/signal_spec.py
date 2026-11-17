"""Signal-Baukasten: which signal combinations exist and how they look. Pure Python, no bpy.

A railway signal in the Lageplan is a head shape (Bauform) plus extras: Zs 1 or
Zs 7 on top, Zs 3 above and Zs 3v below the head, Zs 6, an additional light,
Sh 1, the kind of operation (Bedienung) and the mast. The other signals (Zs
indicators and signs, Lf 6/Lf 7, Ne 2/3/4, Lichtsperrsignale) are Bauformen too,
with their own elements, e.g. the Kennziffer of a speed sign. The DB_Signale
asset library has one composite asset per Bauform; its Geometry Nodes modifier
"Signal" has one input per element below, and the menu "Signalbild" lights the
lamps of an aspect.

This module is the single source of truth for that: the Signal panel uses it
for its key, the preview and the modifier inputs, and the asset build uses it
for the inputs and the menus of the composite assets.

A configuration is a dict {element key: option value}; config() fills in the
defaults. Menu inputs on the modifier store the index of the option.
"""

from dataclasses import dataclass, field

# ---------------------------------------------------------------- Bauformen


@dataclass(frozen=True)
class Bauform:
    key: str
    label: str          # shown in the panel
    asset: str          # asset (object) name in DB_Signale.blend
    system: str         # "Ks", "H/V" or "" (other signals)
    kind: str           # "haupt", "vor", "mehr" (Mehrabschnittssignal) or "zusatz" (other signals)
    group: str = ""     # panel group
    short: str = ""     # short panel label

    @property
    def is_main_signal(self):
        return self.kind in ("haupt", "vor", "mehr")


BAUFORMEN = [
    Bauform("ks_haupt", "Ks-Hauptsignal", "Ks-Hauptsignal", "Ks", "haupt", "Ks", "Haupt"),
    Bauform("ks_vor", "Ks-Vorsignal", "Ks-Vorsignal", "Ks", "vor", "Ks", "Vor"),
    Bauform("ks_mehr", "Ks-Mehrabschnittssignal", "Ks-Mehrabschnittssignal", "Ks", "mehr", "Ks", "Mehrabschnitt"),
    Bauform("hv_haupt", "H/V-Hauptsignal", "HV-Hauptsignal", "H/V", "haupt", "H/V", "Haupt"),
    Bauform("hv_vor", "H/V-Vorsignal", "HV-Vorsignal", "H/V", "vor", "H/V", "Vor"),
    Bauform("hv_mehr", "H/V-Mehrabschnittssignal", "HV-Mehrabschnittssignal", "H/V", "mehr", "H/V", "Mehrabschnitt"),
    Bauform("zs1", "Ersatzsignal Zs1", "Ersatzsignal Zs1", "", "zusatz", "Zusatzsignale", "Zs1"),
    Bauform("zs2", "Richtungsanzeiger Zs2/Zs2v", "Richtungsanzeiger Zs2", "", "zusatz", "Zusatzsignale", "Zs2"),
    Bauform("zs3", "Geschwindigkeitsanzeiger Zs3", "Geschwindigkeitsanzeiger Zs3", "", "zusatz", "Zusatzsignale", "Zs3"),
    Bauform("zs3v", "Geschwindigkeitsvoranzeiger Zs3v", "Geschwindigkeitsvoranzeiger Zs3v", "", "zusatz",
            "Zusatzsignale", "Zs3v"),
    Bauform("zs6", "Gleiswechselanzeiger Zs6", "Gleiswechselanzeiger Zs6", "", "zusatz", "Zusatzsignale", "Zs6"),
    Bauform("lf6", "Geschwindigkeits-Ankündesignal Lf6", "Geschwindigkeits-Ankündesignal Lf6", "", "zusatz",
            "Langsamfahrsignale", "Lf6"),
    Bauform("lf7", "Geschwindigkeitssignal Lf7", "Geschwindigkeitssignal Lf7", "", "zusatz",
            "Langsamfahrsignale", "Lf7"),
    Bauform("ne2", "Vorsignaltafel Ne2", "Vorsignaltafel Ne2", "", "zusatz", "Nebensignale", "Ne2"),
    Bauform("ne3", "Vorsignalbake Ne3", "Vorsignalbake Ne3", "", "zusatz", "Nebensignale", "Ne3"),
    Bauform("ne4", "Schachbretttafel Ne4", "Schachbretttafel Ne4", "", "zusatz", "Nebensignale", "Ne4"),
    Bauform("ls", "Lichtsperrsignal Sh0/Sh1", "Lichtsperrsignal", "", "zusatz", "Rangiersignale", "Ls"),
]
GROUPS = ["Ks", "H/V", "Zusatzsignale", "Langsamfahrsignale", "Nebensignale", "Rangiersignale"]
BY_KEY = {b.key: b for b in BAUFORMEN}
BY_ASSET = {b.asset: b for b in BAUFORMEN}

# ---------------------------------------------------------------- elements


@dataclass(frozen=True)
class Element:
    key: str
    label: str                  # modifier input name and panel label
    options: tuple              # ((value, label), ...); a bool element has False / True
    applies: tuple              # Bauform keys that have this element
    default: object
    description: str = ""
    boolean: bool = False
    number: tuple = None        # (min, max) of a number element (no options)

    def values(self):
        if self.number:
            return list(range(self.number[0], self.number[1] + 1))
        return [v for v, _ in self.options]

    def option_label(self, value):
        return dict(self.options)[value]


_ALL = tuple(b.key for b in BAUFORMEN)
_SIGNALS = tuple(b.key for b in BAUFORMEN if b.is_main_signal)
_MAIN = ("ks_haupt", "ks_mehr", "hv_haupt", "hv_mehr")
_DIST = ("ks_vor", "ks_mehr", "hv_vor", "hv_mehr")

ELEMENTS = [
    Element("bedienung", "Bedienung",
            (("stellwerk", "stellwerksbedient"), ("stellwerk_zug", "stellwerks- oder zugbedient"),
             ("zug", "zugbedient")),
            _SIGNALS, "stellwerk",
            "Kopf gefüllt, halb gefüllt oder leer; Blocksignale haben das Mastschild weiß-gelb"),
    Element("hp", "Hp",
            (("hp01", "Hp0/Hp1"), ("hp012", "Hp0/Hp1/Hp2"), ("hp02", "Hp0/Hp2")),
            ("hv_haupt", "hv_mehr"), "hp012",
            "Signalbegriffe des H/V-Hauptsignals: ein oder zwei Lichtpunkte, Strich für Hp0/Hp2"),
    Element("kopf", "Kopf",
            (("zs1", "Zs1"), ("zs7", "Zs7"), ("ohne", "ohne")),
            _MAIN, "zs1", "Zeichen über dem Kopf: ^ Ersatzsignal Zs1, V Vorsichtsignal Zs7"),
    Element("zs3", "Zs3",
            (("ohne", "ohne"), ("licht", "Lichtsignal"), ("tafel", "Tafel")),
            _MAIN, "ohne", "Geschwindigkeitsanzeiger über dem Kopf: Dreieck gefüllt (Licht) oder leer (Tafel)"),
    Element("zs3v", "Zs3v",
            (("ohne", "ohne"), ("licht", "Lichtsignal"), ("tafel", "Tafel")),
            _DIST, "ohne", "Geschwindigkeitsvoranzeiger unter dem Kopf: Dreieck gefüllt (Licht) oder leer (Tafel)"),
    Element("zs6", "Zs6",
            ((False, "ohne"), (True, "Zs6")),
            _MAIN, False, "Gleiswechselanzeiger: Quadrat mit Diagonale unter dem Kopf", boolean=True),
    Element("licht", "Zusatzlicht",
            (("ohne", "ohne"), ("oben", "oben links"), ("unten", "unten links")),
            ("ks_haupt", "ks_vor", "ks_mehr", "hv_vor", "hv_mehr"), "ohne",
            "Kleiner Kreis am Kopf: Kennlicht (Hauptsignal), verkürzter Bremsweg (oben) oder "
            "Vorsignalwiederholer (unten)"),
    Element("sh1", "Sh1",
            ((False, "ohne"), (True, "Sh1")),
            _MAIN, False, "Hauptsperrsignal: Rangiersignal Sh 1 im Kopf (Striche im Symbol)", boolean=True),
    Element("art", "Art",
            (("zs2", "Zs2 Richtungsanzeiger"), ("zs2v", "Zs2v Richtungsvoranzeiger")),
            ("zs2",), "zs2", "Quadrat gefüllt (Zs2) oder leer (Zs2v)"),
    Element("ausfuehrung", "Ausführung",
            (("licht", "Lichtsignal"), ("tafel", "Tafel")),
            ("zs3", "zs3v"), "licht", "Dreieck gefüllt (Lichtsignal) oder leer (Tafel)"),
    Element("streifen", "Streifen",
            ((1, "1 Streifen"), (2, "2 Streifen"), (3, "3 Streifen"), (4, "4 Streifen"), (5, "5 Streifen")),
            ("ne3",), 1, "Anzahl der schrägen Streifen; die Bake nächst dem Vorsignal hat einen"),
    Element("bauart", "Bauart",
            (("hoch", "hoch"), ("niedrig", "niedrig"), ("kennlicht", "mit Rangierkennlicht")),
            ("ls",), "hoch", "Hohes Ls am Mast oder niedriges Ls (langer oder kurzer Stiel im Symbol)"),
    Element("kennziffer", "Kennziffer", (), ("zs3", "zs3v", "lf6", "lf7"), 0,
            "Zahl aus dem Plan: Geschwindigkeit = Kennziffer x 10 km/h; 0 = leere Tafel", number=(0, 16)),
    Element("mast", "Mast",
            ((True, "Mast"), (False, "ohne Mast")),
            _ALL, True, "Mast oder Pfosten, Fußplatte und Mastschilder; ohne z. B. auf einer Signalbrücke "
                        "oder zum Anbauen an einen anderen Mast", boolean=True),
]
ELEMENT = {e.key: e for e in ELEMENTS}


def elements(bauform):
    """Elements of a Bauform, in panel order."""
    return [e for e in ELEMENTS if bauform in e.applies]


def config(bauform, **values):
    """Full configuration of a Bauform: defaults overridden by values."""
    cfg = {e.key: e.default for e in elements(bauform)}
    for k, v in values.items():
        if k not in cfg:
            raise KeyError(f"{bauform} has no element {k}")
        if v not in ELEMENT[k].values():
            raise ValueError(f"{k}: unknown option {v!r}")
        cfg[k] = v
    return cfg


def shown(bauform, key, cfg):
    """Whether the panel shows element key (the Kennziffer only on signs)."""
    if key == "kennziffer" and cfg.get("ausfuehrung", "tafel") != "tafel":
        return False
    return True


def allowed(bauform, key, value, cfg):
    """Whether option value of element key fits the rest of cfg."""
    b = BY_KEY[bauform]
    if key == "licht" and value == "unten" and (b.kind != "vor" or b.system == "H/V"):
        return False                        # Vorsignalwiederholer light: Ks distant signals only
    if b.system == "H/V" and key == "sh1" and value and cfg.get("hp") == "hp02":
        return False                        # no DB screen for Hp0/Hp2 with Sh 1
    if b.system == "H/V" and key == "hp" and value == "hp02" and cfg.get("sh1"):
        return False
    return True


def options(bauform, key, cfg):
    """[(value, label, allowed)] of an element for the panel."""
    return [(v, label, allowed(bauform, key, v, cfg)) for v, label in ELEMENT[key].options]


def valid(bauform, cfg):
    return all(allowed(bauform, k, v, cfg) for k, v in cfg.items())


def fix(bauform, cfg):
    """Replace options that do not fit the rest by the element's default (or the first that fits)."""
    cfg = dict(cfg)
    for e in elements(bauform):
        if not allowed(bauform, e.key, cfg[e.key], cfg):
            fits = [v for v in e.values() if allowed(bauform, e.key, v, cfg)]
            cfg[e.key] = e.default if e.default in fits else fits[0]
    return cfg


def combinations(bauform):
    """All valid configurations of a Bauform (for tests and later symbol detection);
    number elements keep their default."""
    els = elements(bauform)
    out = [{}]
    for e in els:
        vals = [e.default] if e.number else e.values()
        out = [dict(c, **{e.key: v}) for c in out for v in vals]
    return [c for c in out if valid(bauform, c)]

# ---------------------------------------------------------------- modifier values


def modifier_values(bauform, cfg):
    """{input name: value} for the "Signal" modifier: menu index or bool."""
    out = {}
    for e in elements(bauform):
        v = cfg[e.key]
        out[e.label] = v if (e.boolean or e.number) else e.values().index(v)
    return out


def config_from_modifier(bauform, values):
    """Configuration from {input name: menu index, menu item name or bool}; missing inputs give defaults."""
    cfg = config(bauform)
    for e in elements(bauform):
        if e.label not in values:
            continue
        v = values[e.label]
        if e.boolean:
            cfg[e.key] = bool(v)
        elif e.number:
            cfg[e.key] = max(e.number[0], min(e.number[1], int(v)))
        elif isinstance(v, str):
            labels = [label for _, label in e.options]
            cfg[e.key] = e.values()[labels.index(v)] if v in labels else e.default
        else:
            cfg[e.key] = e.values()[int(v)]
    return cfg

# ---------------------------------------------------------------- aspects (Signalbild)


def _hp_terms(cfg):
    return {"hp01": ["Hp1"], "hp012": ["Hp1", "Hp2"], "hp02": ["Hp2"]}[cfg.get("hp", "hp012")]


def all_aspects(bauform):
    """Every aspect the "Signalbild" menu of the composite asset offers, in menu order ("Aus" first)."""
    b = BY_KEY[bauform]
    extra = ["Hp0 + Sh1", "Hp0 + Zs1", "Hp0 + Zs7"]
    if bauform == "ks_haupt":
        return ["Aus", "Hp0", "Ks1", *extra, "Kennlicht"]
    if bauform == "ks_vor":
        return ["Aus", "Ks1", "Ks2"]
    if bauform == "ks_mehr":
        return ["Aus", "Hp0", "Ks1", "Ks2", *extra]
    if bauform == "hv_haupt":
        return ["Aus", "Hp0", "Hp1", "Hp2", *extra]
    if bauform == "hv_vor":
        return ["Aus", "Vr0", "Vr1", "Vr2"]
    if bauform == "hv_mehr":
        return ["Aus", "Hp0", *[f"{h} + {v}" for h in ("Hp1", "Hp2") for v in ("Vr0", "Vr1", "Vr2")], *extra]
    if bauform == "zs1":
        return ["Aus", "Zs1"]
    if bauform == "ls":
        return ["Aus", "Sh0", "Sh1"]
    return ["Aus"]  # signs and dark indicators


def aspects(bauform, cfg):
    """Aspects that light something in this configuration (subset of all_aspects, same order)."""
    out = []
    for a in all_aspects(bauform):
        if a in ("Hp0 + Sh1",) and not cfg.get("sh1"):
            continue
        if a == "Hp0 + Zs1" and cfg.get("kopf") != "zs1":
            continue
        if a == "Hp0 + Zs7" and cfg.get("kopf") != "zs7":
            continue
        if a == "Kennlicht" and cfg.get("licht") != "oben":
            continue
        if BY_KEY[bauform].system == "H/V" and "Hp" in a and a != "Hp0" and not a.startswith("Hp0 "):
            if a.split(" ")[0] not in _hp_terms(cfg):
                continue
        out.append(a)
    return out

# ---------------------------------------------------------------- symbol


def _single_symbol(bauform, cfg):
    """Symbols of the other signals (Zs, Lf, Ne, Ls)."""
    F, S = ("foot",), lambda h: ("stem", h)
    if bauform == "zs1":
        return [("chev",)]
    if bauform == "zs2":
        return [("square", True), ("hv", 1)] if cfg["art"] == "zs2" else [("square", False)]
    if bauform == "zs3":
        return [("tri_up", cfg["ausfuehrung"] == "licht")]
    if bauform == "zs3v":
        return [("tri_down", cfg["ausfuehrung"] == "licht")]
    if bauform == "zs6":
        return [("zs6",)]
    if bauform == "lf6":
        return [F, S(14), ("lf6",)]
    if bauform == "lf7":
        return [F, S(14), ("lf7",)]
    if bauform == "ne2":
        return [("ne2",)]
    if bauform == "ne3":
        return [F, S(6), ("bake", cfg["streifen"])]
    if bauform == "ne4":
        return [F, S(14), ("ne4",)]
    if bauform == "ls":
        return {"hoch": [F, S(16), ("ls",)], "niedrig": [F, S(4), ("ls",)],
                "kennlicht": [F, S(4), ("ls",), ("kenn",)]}[cfg["bauart"]]
    raise KeyError(bauform)


def symbol_elements(bauform, cfg):
    """Element list for signal_symbols.draw: the Lageplan symbol of this configuration."""
    b = BY_KEY[bauform]
    if not b.is_main_signal:
        return _single_symbol(bauform, cfg)
    fill = {"stellwerk": "full", "stellwerk_zug": "half", "zug": "empty"}[cfg["bedienung"]]
    els = [("foot",)]
    els.append(("stem", 6 if b.kind == "mehr" or cfg.get("zs3v", "ohne") != "ohne" else 14))
    zs3v = cfg.get("zs3v", "ohne")
    if zs3v != "ohne":
        els.append(("tri_down", zs3v == "licht"))
    zus = cfg.get("licht", "ohne") != "ohne"
    if b.system == "H/V":
        if b.kind in ("vor", "mehr"):
            els.append(("hv_dots", fill, zus))
        if b.kind == "vor":
            return els
        if cfg.get("zs6"):
            els.append(("zs6",))
        if cfg.get("sh1"):
            els.append(("shbox",))
        hp = cfg.get("hp", "hp012")
        els.append(("hv_head", 1 if hp == "hp01" else 2, fill, hp == "hp02"))
    else:
        if b.kind != "vor" and cfg.get("zs6"):
            els.append(("zs6",))
        shape = {"haupt": "bell", "vor": "cup", "mehr": "caps"}[b.kind]
        els.append(("ks_head", shape, fill, bool(cfg.get("sh1"))))
        if zus:
            els.append(("zus",))
    zs3 = cfg.get("zs3", "ohne")
    if zs3 != "ohne":
        els.append(("tri_up", zs3 == "licht"))
    kopf = cfg.get("kopf", "ohne")
    if kopf == "zs1":
        els.append(("chev",))
    elif kopf == "zs7":
        els.append(("vee",))
    return els


def option_symbol(bauform, key, value, cfg):
    """Element list of the icon for one option of the panel's key: the element drawn alone
    (on a short stem where it sits on the mast), or the whole head for Bauform-wide options."""
    if key in ("bedienung", "hp", "sh1"):
        # the head alone, with this option
        c = dict(cfg, **{key: value})
        for k, plain in (("zs3", "ohne"), ("zs3v", "ohne"), ("zs6", False), ("licht", "ohne"), ("kopf", "ohne")):
            if k in c:
                c[k] = plain
        return symbol_elements(bauform, c)
    if key == "kopf":
        return {"zs1": [("chev",)], "zs7": [("vee",)], "ohne": []}[value]
    if key == "zs3":
        return {"licht": [("tri_up", True)], "tafel": [("tri_up", False)], "ohne": []}[value]
    if key == "zs3v":
        return {"licht": [("tri_down", True)], "tafel": [("tri_down", False)], "ohne": []}[value]
    if key == "zs6":
        return [("zs6",)] if value else []
    if key == "licht":
        if value == "ohne":
            return []
        b = BY_KEY[bauform]
        if b.system == "H/V":
            return [("hv_dots", "full", True)]
        return [("ks_head", {"haupt": "bell", "vor": "cup", "mehr": "caps"}[b.kind], "full", False), ("zus",)]
    if key in ("art", "ausfuehrung", "streifen", "bauart"):
        return symbol_elements(bauform, dict(cfg, **{key: value}))
    if key == "mast":
        return [("foot",), ("stem", 14), ("lf7",)] if value else [("lf7",)]
    if key == "kennziffer":
        return []
    raise KeyError(key)


def name(bauform, cfg):
    """Readable name of a configuration, e.g. "Ks-Hauptsignal Zs3 Licht, Zs1"."""
    b = BY_KEY[bauform]
    parts = []
    for e in elements(bauform):
        v = cfg[e.key]
        if e.number:
            if v and shown(bauform, e.key, cfg):
                parts.append(f"{e.label} {v}")
            continue
        if v == e.default or v in ("ohne", False):
            continue
        if e.boolean:
            parts.append(e.label if v else f"ohne {e.label}")
        else:
            parts.append(f"{e.label} {e.option_label(v)}" if e.key not in ("bedienung", "kopf", "hp")
                         else e.option_label(v))
    return b.label + (" " + ", ".join(parts) if parts else "")
