"""
pilot/model.py — ce que le pilote voit : des éléments numérotés et une capture.

Un élément vient de l'arbre d'accessibilité du système (UI Automation, AT-SPI,
AX) : un rôle, un nom, une valeur, un rectangle à l'écran. Le modèle désigne un
élément par son numéro ; le code agit dessus par l'API d'accessibilité
(invoquer, écrire, cocher…) et ne clique à des coordonnées qu'en dernier
recours. Pas de liste d'applications : ce qui est à l'écran est ce qui est
proposé.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Les rôles proposés au modèle : ce qu'on peut actionner, plus les textes qui
#: donnent le contexte (titres, libellés, messages).
INTERACTIVE = {"button", "edit", "hyperlink", "menuitem", "listitem", "tabitem", "checkbox", "radiobutton",
               "combobox", "treeitem", "dataitem", "splitbutton", "slider", "spinner", "document", "menu",
               "headeritem", "togglebutton", "link", "entry", "textbox", "pushbutton", "page tab"}
CONTEXT = {"text", "image", "group", "heading", "label", "statictext", "static", "paragraph", "cell"}
MAX_ELEMENTS = 160


@dataclass
class Element:
    id: int
    role: str
    name: str
    value: str = ""
    enabled: bool = True
    focused: bool = False
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)    # gauche, haut, droite, bas — pixels écran
    handle: object = field(default=None, repr=False, compare=False)

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.rect
        return (left + right) // 2, (top + bottom) // 2

    def render(self, origin: tuple[int, int, int, int]) -> str:
        """« [12] button "Envoyer" @(812,903) » — coordonnées sur 0..1000 dans la capture."""
        x, y = to_norm(self.center, origin)
        bits = [f"[{self.id}] {self.role}", f'"{_clip(self.name, 80)}"' if self.name else '""']
        if self.value and self.value != self.name:
            bits.append(f"value={_clip(self.value, 60)!r}")
        if not self.enabled:
            bits.append("(disabled)")
        if self.focused:
            bits.append("(focused)")
        bits.append(f"@({x},{y})")
        return " ".join(bits)


@dataclass
class Window:
    title: str = ""
    app: str = ""
    pid: int = 0
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)


@dataclass
class Observation:
    window: Window
    windows: list[str]
    elements: list[Element]
    screen: tuple[int, int, int, int]                 # la zone capturée : gauche, haut, droite, bas
    image: bytes = b""                                # JPEG de la capture, réduite
    backend: str = ""
    notes: list[str] = field(default_factory=list)

    def element(self, eid) -> Element | None:
        try:
            eid = int(eid)
        except (TypeError, ValueError):
            return None
        return next((e for e in self.elements if e.id == eid), None)

    def render(self) -> str:
        lines = [f"Foreground window: {self.window.title!r} (app: {self.window.app or '?'})",
                 "Other windows: " + ("; ".join(_clip(w, 50) for w in self.windows[:15]) or "none")]
        if self.elements:
            lines.append(f"UI elements of the foreground window ({self.backend}; coordinates 0-1000 on the "
                         "screenshot):")
            lines += [e.render(self.screen) for e in self.elements]
        else:
            lines.append(f"No accessibility tree ({self.backend or 'none'}): use the screenshot and coordinates.")
        lines += [f"Note: {n}" for n in self.notes]
        return "\n".join(lines)


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def to_norm(point: tuple[int, int], origin: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = origin
    w, h = max(1, right - left), max(1, bottom - top)
    return (max(0, min(1000, round((point[0] - left) * 1000 / w))),
            max(0, min(1000, round((point[1] - top) * 1000 / h))))


def from_norm(x: float, y: float, origin: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = origin
    return (round(left + max(0.0, min(1000.0, float(x))) * (right - left) / 1000),
            round(top + max(0.0, min(1000.0, float(y))) * (bottom - top) / 1000))


def keep(elements: list[Element], screen: tuple[int, int, int, int], limit: int = MAX_ELEMENTS) -> list[Element]:
    """Les éléments utiles, visibles dans la zone, sans doublon, numérotés de 1 à n. Pur, testable."""
    left, top, right, bottom = screen
    seen, out = set(), []
    for e in elements:
        role = e.role.lower()
        l, t, r, b = e.rect
        if r - l < 2 or b - t < 2 or r <= left or l >= right or b <= top or t >= bottom:
            continue
        if role not in INTERACTIVE and not (role in CONTEXT and e.name.strip()):
            continue
        key = (role, e.name.strip(), e.center)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    # Les actionnables d'abord quand il faut couper : un texte de plus vaut moins qu'un bouton.
    if len(out) > limit:
        act = [e for e in out if e.role.lower() in INTERACTIVE]
        ctx = [e for e in out if e.role.lower() not in INTERACTIVE]
        keep_ids = {id(e) for e in (act + ctx)[:limit]}
        out = [e for e in out if id(e) in keep_ids]
    for i, e in enumerate(out, 1):
        e.id = i
    return out
