"""
pilot/a11y_linux.py — l'arbre de la fenêtre active, par AT-SPI (GNOME, KDE, Xfce…).

AT-SPI 2 sur le bus d'accessibilité (paquets système : python3-gi,
gir1.2-atspi-2.0, at-spi2-core). GTK, Qt, LibreOffice, Firefox et les
applications Electron lancées avec l'accessibilité (--force-renderer-
accessibility) l'exposent. La fenêtre active est le cadre dont l'état contient
ACTIVE. Parcours en largeur, MAX_NODES au plus (chaque nœud est un aller-retour
D-Bus).

Actions : Action.do_action(0) (le « clic » de l'élément), EditableText
.set_text_contents, Component.grab_focus.
"""
from __future__ import annotations

from collections import deque

from pilot.model import Element, Window

MAX_NODES = 700
ROLES = {"push button": "button", "toggle button": "togglebutton", "check box": "checkbox",
         "radio button": "radiobutton", "combo box": "combobox", "menu item": "menuitem", "check menu item": "menuitem",
         "radio menu item": "menuitem", "page tab": "tabitem", "list item": "listitem", "table cell": "cell",
         "tree item": "treeitem", "link": "hyperlink", "entry": "edit", "text": "edit", "password text": "edit",
         "spin button": "spinner", "document web": "document", "document text": "document", "document frame": "document",
         "label": "text", "static": "text", "heading": "heading", "paragraph": "paragraph", "image": "image",
         "icon": "image", "slider": "slider", "menu": "menu"}


def _atspi():
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    return Atspi


def _frames():
    A = _atspi()
    desktop = A.get_desktop(0)
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None:
            continue
        for j in range(app.get_child_count()):
            win = app.get_child_at_index(j)
            if win is not None:
                yield app, win


def _active():
    A = _atspi()
    for app, win in _frames():
        try:
            if win.get_state_set().contains(A.StateType.ACTIVE):
                return app, win
        except Exception:
            continue
    return None, None


def _rect(node) -> tuple[int, int, int, int]:
    A = _atspi()
    try:
        r = node.get_extents(A.CoordType.SCREEN)
        return r.x, r.y, r.x + r.width, r.y + r.height
    except Exception:
        return 0, 0, 0, 0


def foreground() -> Window:
    app, win = _active()
    if win is None:
        return Window()
    try:
        pid = app.get_process_id()
    except Exception:
        pid = 0
    return Window(win.get_name() or "", app.get_name() or "", pid, _rect(win))


def windows() -> list[str]:
    A = _atspi()
    out = []
    for _app, win in _frames():
        try:
            if win.get_state_set().contains(A.StateType.SHOWING) and win.get_name():
                out.append(win.get_name())
        except Exception:
            continue
    return out


def _text(node) -> str:
    try:
        t = node.get_text_iface()
        return t.get_text(0, min(t.get_character_count(), 500)) if t else ""
    except Exception:
        return ""


def snapshot() -> tuple[Window, list[Element]]:
    A = _atspi()
    app, win = _active()
    if win is None:
        return Window(), []
    info = foreground()
    out, queue, seen = [], deque([win]), 0
    while queue and seen < MAX_NODES:
        node = queue.popleft()
        seen += 1
        try:
            states = node.get_state_set()
            if not states.contains(A.StateType.SHOWING):
                continue
            role = node.get_role_name() or ""
            mapped = ROLES.get(role, role)
            value = _text(node) if mapped in ("edit", "document") else ""
            out.append(Element(0, mapped, node.get_name() or "", value,
                               states.contains(A.StateType.ENABLED) or states.contains(A.StateType.SENSITIVE),
                               states.contains(A.StateType.FOCUSED), _rect(node), node))
            for k in range(min(node.get_child_count(), 200)):
                child = node.get_child_at_index(k)
                if child is not None:
                    queue.append(child)
        except Exception:
            continue
    return info, out


def act(el: Element, kind: str, value: str = "") -> str | None:
    node = el.handle
    if node is None:
        return None
    try:
        if kind == "click":
            action = node.get_action_iface()
            if action and action.get_n_actions() > 0:
                action.do_action(0)
                return f"action « {action.get_action_name(0) or 'activer'} »"
            return None
        if kind == "set_text":
            editable = node.get_editable_text_iface()
            if editable:
                try:
                    node.get_component_iface().grab_focus()
                except Exception:
                    pass
                editable.set_text_contents(value)
                return "texte écrit"
            return None
        if kind == "focus":
            comp = node.get_component_iface()
            return "focus" if comp and comp.grab_focus() else None
    except Exception:
        return None
    return None


def focus_window(title: str) -> bool:
    """AT-SPI ne lève pas une fenêtre : wmctrl (X11) s'il est là, sinon l'élément reçoit le focus."""
    import shutil
    import subprocess
    if shutil.which("wmctrl"):
        return subprocess.run(["wmctrl", "-a", title], capture_output=True, timeout=5).returncode == 0
    for _app, win in _frames():
        if title.lower() in (win.get_name() or "").lower():
            comp = win.get_component_iface()
            return bool(comp and comp.grab_focus())
    return False
