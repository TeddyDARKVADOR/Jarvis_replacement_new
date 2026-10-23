"""
pilot/a11y_macos.py — l'arbre de la fenêtre active, par l'API d'accessibilité de macOS (AX).

PyObjC (pyobjc-framework-ApplicationServices, pyobjc-framework-Cocoa). Matthew
doit être autorisé dans Réglages → Confidentialité et sécurité →
Accessibilité ; sans cela l'arbre est vide, et la capture et les clics
prennent le relais.

NON VÉRIFIÉ : écrit d'après la documentation d'Apple, sans Mac pour le tester
(graphe : a_tester).
"""
from __future__ import annotations

from collections import deque

from pilot.model import Element, Window

MAX_NODES = 700
ROLES = {"AXButton": "button", "AXTextField": "edit", "AXTextArea": "edit", "AXSearchField": "edit",
         "AXLink": "hyperlink", "AXMenuItem": "menuitem", "AXMenuBarItem": "menuitem", "AXCheckBox": "checkbox",
         "AXRadioButton": "radiobutton", "AXPopUpButton": "combobox", "AXComboBox": "combobox", "AXTab": "tabitem",
         "AXRow": "listitem", "AXCell": "cell", "AXStaticText": "text", "AXImage": "image", "AXWebArea": "document",
         "AXSlider": "slider", "AXHeading": "heading"}


def _ax():
    import ApplicationServices as AS
    return AS


def _attr(el, name):
    AS = _ax()
    err, value = AS.AXUIElementCopyAttributeValue(el, name, None)
    return value if err == 0 else None


def _front():
    from AppKit import NSWorkspace
    AS = _ax()
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    pid = int(app.processIdentifier())
    return app, pid, AS.AXUIElementCreateApplication(pid)


def _rect(el) -> tuple[int, int, int, int]:
    AS = _ax()
    pos, size = _attr(el, "AXPosition"), _attr(el, "AXSize")
    if pos is None or size is None:
        return 0, 0, 0, 0
    ok1, p = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
    ok2, s = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
    if not (ok1 and ok2):
        return 0, 0, 0, 0
    return int(p.x), int(p.y), int(p.x + s.width), int(p.y + s.height)


def foreground() -> Window:
    app, pid, ax = _front()
    win = _attr(ax, "AXFocusedWindow")
    return Window(str(_attr(win, "AXTitle") or "") if win else "", str(app.localizedName() or ""), pid,
                  _rect(win) if win else (0, 0, 0, 0))


def windows() -> list[str]:
    from AppKit import NSWorkspace
    out = []
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.activationPolicy() == 0 and app.localizedName():
            out.append(str(app.localizedName()))
    return out


def snapshot() -> tuple[Window, list[Element]]:
    _app, _pid, ax = _front()
    win = _attr(ax, "AXFocusedWindow")
    info = foreground()
    if win is None:
        return info, []
    out, queue, seen = [], deque([win]), 0
    while queue and seen < MAX_NODES:
        node = queue.popleft()
        seen += 1
        role = str(_attr(node, "AXRole") or "")
        name = str(_attr(node, "AXTitle") or _attr(node, "AXDescription") or "")
        value = _attr(node, "AXValue")
        enabled = _attr(node, "AXEnabled")
        out.append(Element(0, ROLES.get(role, role.removeprefix("AX").lower()), name,
                           value if isinstance(value, str) else "", enabled is not False,
                           bool(_attr(node, "AXFocused")), _rect(node), node))
        for child in (_attr(node, "AXChildren") or [])[:200]:
            queue.append(child)
    return info, out


def act(el: Element, kind: str, value: str = "") -> str | None:
    AS = _ax()
    node = el.handle
    if node is None:
        return None
    if kind == "click":
        return "pressé" if AS.AXUIElementPerformAction(node, "AXPress") == 0 else None
    if kind == "set_text":
        AS.AXUIElementSetAttributeValue(node, "AXFocused", True)
        return "texte écrit" if AS.AXUIElementSetAttributeValue(node, "AXValue", value) == 0 else None
    if kind == "focus":
        return "focus" if AS.AXUIElementSetAttributeValue(node, "AXFocused", True) == 0 else None
    return None


def focus_window(title: str) -> bool:
    import subprocess
    script = f'tell application "{title}" to activate'
    return subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10).returncode == 0
