"""
pilot/inputs.py — clavier et souris, quand l'accessibilité ne suffit pas.

pyautogui sous Windows, macOS et X11. Sous Wayland, une application ne peut pas
injecter d'événements chez les autres : wdotool (portail RemoteDesktop/libei
sous GNOME et KDE, protocoles wlr ailleurs), sinon ydotool, sinon wtype pour le
texte. Absent : on le dit, on ne fait pas semblant.

Le texte passe par le presse-papiers (Ctrl+V, ⌘V sur macOS) dès qu'il sort de
l'ASCII : pyautogui.write perd les accents. Le presse-papiers d'avant est remis.
pyautogui.FAILSAFE reste actif : la souris jetée dans un coin de l'écran arrête
net le pilote.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time

from pilot.screen import wayland

PASTE = ("command", "v") if sys.platform == "darwin" else ("ctrl", "v")


class InputUnavailable(RuntimeError):
    pass


def _wayland_tool() -> str:
    for tool in ("wdotool", "ydotool"):
        if shutil.which(tool):
            return tool
    raise InputUnavailable("Wayland : installez wdotool (ou ydotool) pour que JARVIS clique et tape")


def _pg():
    try:
        import pyautogui
    except Exception as e:
        raise InputUnavailable(f"pyautogui indisponible ({type(e).__name__})") from e
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    return pyautogui


def click(x: int, y: int, button: str = "left", double: bool = False) -> str:
    if wayland():
        tool = _wayland_tool()
        if tool == "wdotool":                  # syntaxe de xdotool
            subprocess.run([tool, "mousemove", str(x), str(y)], timeout=5)
            subprocess.run([tool, "click", "--repeat", "2" if double else "1", "1" if button == "left" else "3"],
                           timeout=5)
        else:                                  # ydotool 1.x
            subprocess.run([tool, "mousemove", "--absolute", "-x", str(x), "-y", str(y)], timeout=5)
            for _ in range(2 if double else 1):
                subprocess.run([tool, "click", "0xC0" if button == "left" else "0xC1"], timeout=5)
        return f"clic {button} en ({x},{y}) via {tool}"
    pg = _pg()
    pg.click(x, y, clicks=2 if double else 1, button=button)
    return f"{'double ' if double else ''}clic {button} en ({x},{y})"


def _clipboard_paste(text: str) -> None:
    import pyperclip
    try:
        before = pyperclip.paste()
    except Exception:
        before = None
    pyperclip.copy(text)
    time.sleep(0.05)
    _pg().hotkey(*PASTE)
    time.sleep(0.15)
    if before is not None:
        try:
            pyperclip.copy(before)
        except Exception:
            pass


def type_text(text: str) -> str:
    if wayland():
        if shutil.which("wtype"):
            subprocess.run(["wtype", text], timeout=30)
            return f"tapé ({len(text)} caractères) via wtype"
        tool = _wayland_tool()
        subprocess.run([tool, "type", text], timeout=30)
        return f"tapé ({len(text)} caractères) via {tool}"
    if text.isascii():
        _pg().write(text, interval=0.01)
    else:
        try:
            _clipboard_paste(text)
        except ImportError:
            _pg().write(text, interval=0.01)
    return f"tapé ({len(text)} caractères)"


def keys(combo: str) -> str:
    """« ctrl+l », « enter », « alt+tab », « cmd+space »."""
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    alias = {"cmd": "command", "win": "winleft", "super": "winleft", "return": "enter", "esc": "escape",
             "del": "delete", "ctl": "ctrl", "control": "ctrl", "option": "alt"}
    parts = [alias.get(p, p) for p in parts]
    if not parts:
        return "aucune touche"
    if wayland():
        if _wayland_tool() != "wdotool":
            raise InputUnavailable("Wayland : les raccourcis clavier demandent wdotool (ydotool ne prend que des codes)")
        subprocess.run(["wdotool", "key", "+".join(parts)], timeout=5)
        return f"touches {'+'.join(parts)} via wdotool"
    pg = _pg()
    pg.hotkey(*parts) if len(parts) > 1 else pg.press(parts[0])
    return f"touches {'+'.join(parts)}"


def scroll(x: int, y: int, direction: str, amount: int = 5) -> str:
    clicks = max(1, min(30, int(amount))) * (1 if direction in ("up", "haut") else -1)
    if wayland():
        if _wayland_tool() != "wdotool":
            raise InputUnavailable("Wayland : le défilement demande wdotool")
        subprocess.run(["wdotool", "mousemove", str(x), str(y)], timeout=5)
        subprocess.run(["wdotool", "click", "--repeat", str(abs(clicks)), "4" if clicks > 0 else "5"], timeout=5)
        return f"défilement {direction} via wdotool"
    pg = _pg()
    pg.moveTo(x, y)
    pg.scroll(clicks * 100 if sys.platform == "darwin" else clicks * 120)
    return f"défilement {direction} ({abs(clicks)})"
