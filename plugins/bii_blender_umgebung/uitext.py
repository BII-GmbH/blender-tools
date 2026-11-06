"""Hinweistexte in Panels, die sich an die Breite der Seitenleiste anpassen.

Blender bricht ``layout.label`` nicht um: Ein langer Text wird abgeschnitten, ein
fest in zwei Zeilen geteilter Text bleibt auch in einer breiten Seitenleiste
zweizeilig. :func:`label` misst den Text mit der Oberflächenschrift und verteilt
ihn auf so viele Zeilen, wie die aktuelle Breite verlangt.
"""

import blf
import bpy

# Platz, den Panelrand, Boxrahmen und Symbol vom Bereich abziehen (in Punkten
# bei 100 % Oberflächengröße)
MARGIN = 46.0
ICON = 22.0


def _scale(context):
    try:
        scale = context.preferences.system.ui_scale
    except AttributeError:
        scale = 0.0
    return scale if scale > 0.0 else 1.0        # ohne Fenster (Hintergrund) ist der Wert 0


def _font_size(context, scale):
    try:
        points = context.preferences.ui_styles[0].widget.points
    except (AttributeError, IndexError):
        points = 11
    return points * scale


def _lines(text, width, measure):
    """Wortweise auf Zeilen verteilen; ein einzelnes zu langes Wort bleibt ganz."""
    lines, current = [], ""
    for word in text.split():
        candidate = word if not current else current + " " + word
        if current and measure(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def label(layout, text, icon='NONE', context=None, scale_y=0.8):
    """Text als Label, umbrochen auf die Breite der aktuellen Seitenleiste."""
    context = context or bpy.context
    region = getattr(context, "region", None)
    scale = _scale(context)
    if region is None or region.width <= 1:
        layout.label(text=text, icon=icon)
        return
    font = 0
    blf.size(font, _font_size(context, scale))
    width = region.width - (MARGIN + (ICON if icon != 'NONE' else 0.0)) * scale
    lines = _lines(text, max(40.0 * scale, width), lambda t: blf.dimensions(font, t)[0])
    col = layout.column(align=True)
    col.scale_y = scale_y if len(lines) > 1 else 1.0
    for number, line in enumerate(lines):
        if icon == 'NONE':
            col.label(text=line)
        else:
            # Folgezeilen um die Symbolbreite einrücken
            col.label(text=line, icon=icon if number == 0 else 'BLANK1')
