"""Symbol key of the signals as one PNG: every Bauform and every option as its Lageplan symbol.

No bpy (numpy and Pillow), so it also runs outside Blender. Each tile shows a symbol, a red dot at
the signal's origin (where the 3D signal's foot belongs on the plan), the option in words and the
same option as signal_spec values, e.g. zs3="tafel". People use it as a printed key; scripts and AI
agents compare it with crops of a plan and copy the values into place_signal().
"""

import numpy as np

try:
    from . import signal_spec as spec, signal_symbols as symbols
except ImportError:  # as a plain module (tests, the asset library build)
    import signal_spec as spec
    import signal_symbols as symbols

TILE = 150          # symbol pixels per tile
COLUMNS = 8
TEXT_H = 50         # three text lines under a tile
HEAD_H = 26         # Bauform heading above its tiles
MARGIN = 12
NOTE = ("Red dot: the signal's origin (foot of the mast). Place the signal where the dot is on the plan. "
        "Filled triangle = light signal (Lichtsignal), empty = sign (Tafel). "
        "Mast is not in the symbol: mast=False for signals on a Signalbrücke or on another signal's mast.")


def entries(bauformen=None):
    """[(bauform, [(label, code, elements)])]: per Bauform its default symbol, then each other option
    of each element on top of the default configuration. bauformen: keys or groups to include
    (e.g. ["ks_haupt", "Nebensignale"]); None for all."""
    out = []
    for b in spec.BAUFORMEN:
        if bauformen and b.key not in bauformen and b.group not in bauformen:
            continue
        base = spec.config(b.key)
        tiles = [("Standard", f'bauform="{b.key}"', spec.symbol_elements(b.key, base))]
        for e in spec.elements(b.key):
            if e.number:
                continue
            for value, label, _ok in spec.options(b.key, e.key, base):
                if value == base[e.key]:
                    continue
                cfg = spec.fix(b.key, dict(base, **{e.key: value}))
                if cfg[e.key] != value:
                    continue  # does not fit the default configuration
                els = spec.symbol_elements(b.key, cfg)
                if els == tiles[0][2]:
                    continue  # not visible in the symbol (Mast)
                code = f"{e.key}={value!r}".replace("'", '"')
                tiles.append((f"{e.label}: {label}", code, els))
        out.append((b, tiles))
    return out


FONTS = ("DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "LiberationSans-Regular.ttf", "Helvetica.ttc")


def _font(size):
    """A system font with umlauts; Pillow's own font as a fallback."""
    from PIL import ImageFont
    for name in FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow before 10.1
        return ImageFont.load_default()


def _fit_text(draw, text, font, width):
    """text shortened with … to fit width pixels."""
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _wrap(draw, text, font, width):
    lines, line = [], ""
    for word in text.split():
        if line and draw.textlength(line + " " + word, font=font) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + [line]


def _bands(rows, columns):
    """Rows of blocks [(x column, bauform, tiles)]: a Bauform with few tiles shares a row with the next."""
    bands, band, used = [], [], 0
    for b, tiles in rows:
        for start in range(0, len(tiles), columns):
            part = tiles[start:start + columns]
            if band and used + len(part) > columns:
                bands.append(band)
                band, used = [], 0
            band.append((used, b if start == 0 else None, part))
            used += len(part)
    return bands + ([band] if band else [])


def render(tile=TILE, columns=COLUMNS, bauformen=None):
    """The key as a Pillow RGB image (bauformen: see entries)."""
    from PIL import Image, ImageDraw

    rows = entries(bauformen)
    if not rows:
        raise ValueError(f"no Bauform matches {bauformen}; keys: {[b.key for b in spec.BAUFORMEN]}, "
                         f"groups: {spec.GROUPS}")
    bands = _bands(rows, columns)
    cell_w, cell_h = tile + 8, tile + TEXT_H
    width = MARGIN * 2 + columns * cell_w
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    small, code_font, normal, bold = _font(11), _font(10), _font(12), _font(13)
    note = _wrap(probe, NOTE, normal, width - 2 * MARGIN)
    height = MARGIN * 2 + len(note) * 16 + 10 + len(bands) * (HEAD_H + cell_h)
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    y = MARGIN
    for line in note:
        draw.text((MARGIN, y), line, fill="black", font=normal)
        y += 16
    y += 10
    for band in bands:
        for col, b, tiles in band:
            x = MARGIN + col * cell_w
            if b is not None:
                head = f'{b.label}   bauform="{b.key}"'
                if draw.textlength(head, font=bold) > len(tiles) * cell_w - 8:
                    head = f'bauform="{b.key}"'
                draw.text((x, y + 6), head, fill="black", font=bold)
            for i, (label, code, els) in enumerate(tiles):
                x0, y0 = x + i * cell_w, y + HEAD_H
                if b is not None and i == 0:
                    label = b.label
                gray = symbols.draw(els, tile)[::-1, :, 0]  # rows from the top
                img.paste(Image.fromarray((np.clip(gray, 0, 1) * 255).astype(np.uint8)).convert("RGB"), (x0, y0))
                ax, ay = symbols.anchor(els, tile)
                draw.ellipse((x0 + ax - 4, y0 + ay - 4, x0 + ax + 4, y0 + ay + 4), fill=(220, 0, 0))
                draw.rectangle((x0, y0, x0 + tile - 1, y0 + tile - 1), outline=(200, 200, 200))
                lines = _wrap(draw, label, small, cell_w - 8)[:2]
                for n, line in enumerate(lines):
                    draw.text((x0, y0 + tile + 2 + 14 * n), _fit_text(draw, line, small, cell_w - 8),
                              fill="black", font=small)
                draw.text((x0, y0 + tile + 31), _fit_text(draw, code, code_font, cell_w - 4),
                          fill=(0, 0, 160), font=code_font)
        y += HEAD_H + cell_h
    return img


def write(path, tile=TILE, columns=COLUMNS, bauformen=None):
    """Write the key as PNG; returns (width, height). bauformen: keys or groups to include (see
    entries), e.g. ["Ks"] for a smaller image."""
    img = render(tile, columns, bauformen)
    img.save(path)
    return img.size
