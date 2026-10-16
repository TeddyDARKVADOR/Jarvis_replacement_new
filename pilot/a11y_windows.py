"""
pilot/a11y_windows.py — l'arbre de la fenêtre active, par UI Automation.

UI Automation (UIAutomationCore.dll, via comtypes) : ce que lisent les lecteurs
d'écran. Les applications de bureau, les pages web dans Edge et Chrome, et les
applications WebView2 (le nouveau Teams, Outlook…) l'exposent. L'arbre est lu en
un seul appel, propriétés mises en cache (FindAllBuildCache) : une page web ou
Teams ont des milliers de nœuds, et une lecture nœud par nœud coûterait des
secondes.

Actions par motifs UIA — Invoke (bouton, lien), Value.SetValue (champ),
Toggle, SelectionItem, ExpandCollapse, SetFocus — et seulement à défaut, un
clic au centre du rectangle (inputs.py).
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from pilot.model import Element, Window

ROLES = {50000: "button", 50001: "calendar", 50002: "checkbox", 50003: "combobox", 50004: "edit",
         50005: "hyperlink", 50006: "image", 50007: "listitem", 50008: "list", 50009: "menu", 50010: "menubar",
         50011: "menuitem", 50012: "progressbar", 50013: "radiobutton", 50014: "scrollbar", 50015: "slider",
         50016: "spinner", 50017: "statusbar", 50018: "tab", 50019: "tabitem", 50020: "text", 50021: "toolbar",
         50022: "tooltip", 50023: "tree", 50024: "treeitem", 50025: "custom", 50026: "group", 50027: "thumb",
         50028: "datagrid", 50029: "dataitem", 50030: "document", 50031: "splitbutton", 50032: "window",
         50033: "pane", 50034: "header", 50035: "headeritem", 50036: "table", 50037: "titlebar",
         50038: "separator", 50039: "semanticzoom", 50040: "appbar"}

P_BOUNDS, P_TYPE, P_NAME, P_FOCUS, P_ENABLED, P_OFFSCREEN, P_VALUE = 30001, 30003, 30005, 30008, 30010, 30022, 30045
PAT_INVOKE, PAT_SELECTION_ITEM, PAT_VALUE, PAT_EXPAND, PAT_TOGGLE = 10000, 10010, 10002, 10005, 10015
TREE_DESCENDANTS = 4

_local = threading.local()


def _uia():
    """Un client UIA par fil (COM) ; le module de types est généré une fois par comtypes."""
    if getattr(_local, "uia", None) is None:
        import comtypes
        import comtypes.client
        try:
            comtypes.CoInitialize()
        except OSError:
            pass
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation
        _local.uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
    return _local.uia


def _process_name(pid: int) -> str:
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        return ""


def foreground() -> Window:
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return Window(buf.value, _process_name(pid.value), pid.value, (rect.left, rect.top, rect.right, rect.bottom))


def windows() -> list[str]:
    """Les titres des fenêtres visibles, de premier plan en arrière-plan."""
    user32 = ctypes.windll.user32
    titles: list[str] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):    # GW_OWNER : pas les popups
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value.strip() and buf.value not in ("Program Manager",):
                titles.append(buf.value)
        return True

    user32.EnumWindows(each, 0)
    return titles


def snapshot() -> tuple[Window, list[Element]]:
    uia = _uia()
    win = foreground()
    root = uia.ElementFromHandle(ctypes.windll.user32.GetForegroundWindow())
    cache = uia.CreateCacheRequest()
    for prop in (P_BOUNDS, P_TYPE, P_NAME, P_FOCUS, P_ENABLED, P_OFFSCREEN, P_VALUE):
        cache.AddProperty(prop)
    cond = uia.CreateAndCondition(uia.ControlViewCondition, uia.CreatePropertyCondition(P_OFFSCREEN, False))
    found = root.FindAllBuildCache(TREE_DESCENDANTS, cond, cache)
    out = []
    for i in range(found.Length if found else 0):
        el = found.GetElement(i)
        try:
            r = el.CachedBoundingRectangle
            value = el.GetCachedPropertyValue(P_VALUE)
            out.append(Element(0, ROLES.get(el.CachedControlType, "custom"), el.CachedName or "",
                               value if isinstance(value, str) else "", bool(el.CachedIsEnabled),
                               bool(el.CachedHasKeyboardFocus), (r.left, r.top, r.right, r.bottom), el))
        except Exception:
            continue
    return win, out


def _pattern(el, pid: int, iface_name: str):
    from comtypes.gen import UIAutomationClient as U
    try:
        unk = el.GetCurrentPattern(pid)
        return unk.QueryInterface(getattr(U, iface_name)) if unk else None
    except Exception:
        return None


def act(el: Element, kind: str, value: str = "") -> str | None:
    """Agit par l'API d'accessibilité ; None si l'élément ne s'y prête pas (le clic prendra le relais)."""
    h = el.handle
    if h is None:
        return None
    if kind == "click":
        p = _pattern(h, PAT_INVOKE, "IUIAutomationInvokePattern")
        if p:
            p.Invoke()
            return "invoqué"
        p = _pattern(h, PAT_TOGGLE, "IUIAutomationTogglePattern")
        if p:
            p.Toggle()
            return "basculé"
        p = _pattern(h, PAT_SELECTION_ITEM, "IUIAutomationSelectionItemPattern")
        if p:
            p.Select()
            return "sélectionné"
        p = _pattern(h, PAT_EXPAND, "IUIAutomationExpandCollapsePattern")
        if p:
            p.Expand()
            return "déplié"
        return None
    if kind == "set_text":
        p = _pattern(h, PAT_VALUE, "IUIAutomationValuePattern")
        if p:
            try:
                h.SetFocus()
            except Exception:
                pass
            p.SetValue(value)
            return "texte écrit"
        return None
    if kind == "focus":
        h.SetFocus()
        return "focus"
    return None


def focus_window(title: str) -> bool:
    """Met au premier plan la première fenêtre dont le titre contient `title`."""
    user32 = ctypes.windll.user32
    wanted = title.lower()
    hit = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if user32.IsWindowVisible(hwnd) and wanted in buf.value.lower():
            hit.append(hwnd)
            return False
        return True

    user32.EnumWindows(each, 0)
    if not hit:
        return False
    hwnd = hit[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                      # SW_RESTORE
    # SetForegroundWindow est refusé à un processus d'arrière-plan. La touche Alt
    # simulée (l'astuce courante) affiche les info-bulles de raccourcis des menus
    # (mesuré) : on s'attache plutôt au fil de la fenêtre active.
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    me = ctypes.windll.kernel32.GetCurrentThreadId()
    attached = fg_thread != me and user32.AttachThreadInput(fg_thread, me, True)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(fg_thread, me, False)
    if user32.GetForegroundWindow() != hwnd:
        user32.SwitchToThisWindow(hwnd, True)
    return user32.GetForegroundWindow() == hwnd
