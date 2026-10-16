"""
pilot/a11y.py — le moteur d'accessibilité de ce système, et l'observation complète.

    obs = observe(with_image=True)     fenêtre active, fenêtres, éléments numérotés, capture
    msg = act(element, "click"|"set_text"|"focus", value)

Windows : UI Automation · Linux : AT-SPI · macOS : AX. Un moteur absent ou en
panne n'arrête rien : l'observation dit pourquoi, et le modèle travaille sur la
capture, avec des coordonnées.
"""
from __future__ import annotations

import sys
import time

from pilot import screen
from pilot.model import Element, Observation, Window, keep


def backend():
    """(nom, module) ; le module expose foreground, windows, snapshot, act, focus_window."""
    if sys.platform == "win32":
        from pilot import a11y_windows as m
        return "UI Automation", m
    if sys.platform == "darwin":
        from pilot import a11y_macos as m
        return "macOS AX", m
    from pilot import a11y_linux as m
    return "AT-SPI", m


def observe(with_image: bool = True, retries: int = 3) -> Observation:
    notes: list[str] = []
    try:
        name, mod = backend()
    except Exception as e:
        name, mod = "", None
        notes.append(f"pas d'arbre d'accessibilité ({type(e).__name__})")
    win, raw, titles = Window(), [], []
    if mod is not None:
        for attempt in range(retries):
            try:
                win, raw = mod.snapshot()
                titles = [t for t in mod.windows() if t != win.title]
                break
            except Exception as e:         # une fenêtre qui change pendant la lecture : on relit
                if attempt == retries - 1:
                    notes.append(f"arbre illisible : {type(e).__name__}")
                time.sleep(0.5)
    zone, image, note = screen.capture(win.rect) if with_image else ((0, 0, 1920, 1080), b"", "")
    if note:
        notes.append(note)
    if not win.rect or win.rect == (0, 0, 0, 0):
        win.rect = zone
    return Observation(win, titles, keep(raw, zone), zone, image, name, notes)


def act(el: Element, kind: str, value: str = "") -> str | None:
    try:
        _, mod = backend()
        return mod.act(el, kind, value)
    except Exception:
        return None


def focus_window(title: str) -> bool:
    try:
        _, mod = backend()
        return bool(mod.focus_window(title))
    except Exception:
        return False
