"""
One palette, dark, and no light variant — carried over from `ui/Theme.kt`.

JARVIS is a lit core on a near-black field; a light theme would not be the same
design with different values, it would be a different design.

The greys are blue-shifted (0x05070C, not 0x0A0A0A). The reason given on Android
was OLED, and it does not apply to this laptop's IPS panel — but the *result*
does: a neutral near-black beside a saturated cyan core reads slightly brown on
any display, and a few points of blue is what stops that. Keeping the identical
values also means the two clients are recognisably one product, which is the
whole point of porting the palette rather than picking a new one.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor

# ── the palette ──────────────────────────────────────────────────────────────
SKY = QColor(0x7D, 0xD3, 0xFC)
INK = QColor(0x05, 0x07, 0x0C)
PANEL = QColor(0x0A, 0x10, 0x1A)
TEXT = QColor(0xCB, 0xD7, 0xEA)
MUTED = QColor(0x6B, 0x7A, 0x93)
OUTLINE = QColor(0x2A, 0x35, 0x47)
OUTLINE_FAINT = QColor(0x14, 0x1C, 0x2A)
SURFACE_VARIANT = QColor(0x11, 0x1A, 0x28)
ERROR = QColor(0xF8, 0x71, 0x71)
WARN = QColor(0xFB, 0xBF, 0x6B)
OK = QColor(0x6E, 0xE7, 0xB7)

#: Hex forms, for the handful of places that want a stylesheet.
HEX = {
    "sky": "#7DD3FC",
    "ink": "#05070C",
    "panel": "#0A101A",
    "text": "#CBD7EA",
    "muted": "#6B7A93",
    "outline": "#2A3547",
    "outline_faint": "#141C2A",
    "surface_variant": "#111A28",
    "error": "#F87171",
    "warn": "#FBBF6B",
    "ok": "#6EE7B7",
}


def alpha(colour: QColor, value: float) -> QColor:
    """A copy of `colour` at `value` opacity. Qt has no `copy(alpha=)`."""
    out = QColor(colour)
    out.setAlphaF(max(0.0, min(1.0, value)))
    return out


PANEL_STYLESHEET = f"""
QWidget {{
    background: {HEX['ink']};
    color: {HEX['text']};
    font-family: "Segoe UI", sans-serif;
    font-size: 12px;
}}
/* QLabel derives from QFrame in Qt, so any unscoped `QFrame {{ border }}`
   rule paints a box around every single label. Resetting here means a frame
   style can be written without having to remember that. */
QLabel {{
    border: none;
    background: transparent;
}}
QLabel#title {{
    color: {HEX['sky']};
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 3px;
}}
QLabel#state {{
    font-size: 11px;
    letter-spacing: 1px;
}}
QLabel#spoken {{
    color: {HEX['text']};
    font-size: 13px;
}}
QLabel#hint {{
    color: {HEX['muted']};
    font-size: 11px;
}}
QLineEdit {{
    background: {HEX['surface_variant']};
    border: 1px solid {HEX['outline']};
    border-radius: 6px;
    padding: 7px 10px;
    color: {HEX['text']};
    selection-background-color: {HEX['sky']};
    selection-color: {HEX['ink']};
}}
QLineEdit:focus {{
    border: 1px solid {HEX['sky']};
}}
QPushButton {{
    background: {HEX['surface_variant']};
    border: 1px solid {HEX['outline']};
    border-radius: 6px;
    padding: 6px 10px;
    color: {HEX['text']};
}}
QPushButton:hover {{
    border: 1px solid {HEX['sky']};
    color: {HEX['sky']};
}}
QPushButton:pressed {{
    background: {HEX['panel']};
}}
QPushButton:disabled {{
    color: {HEX['muted']};
    border: 1px solid {HEX['outline_faint']};
}}
QPushButton#danger:hover {{
    border: 1px solid {HEX['error']};
    color: {HEX['error']};
}}
QPushButton#confirm:hover {{
    border: 1px solid {HEX['ok']};
    color: {HEX['ok']};
}}
QScrollArea, QListWidget, QPlainTextEdit {{
    background: {HEX['panel']};
    border: 1px solid {HEX['outline_faint']};
    border-radius: 6px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {HEX['outline']};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QFrame#separator {{
    background: {HEX['outline_faint']};
    max-height: 1px;
    border: none;
}}
"""
