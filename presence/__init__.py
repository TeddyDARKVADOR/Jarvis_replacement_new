"""
presence/ — JARVIS's face and body, decided here, drawn in `avatar/`.

Self-contained, exactly like `context/`: this package imports nothing from
`main.py`, `ui.py`, `core/`, `dashboard/`, `actions/` or `server/`, and the
selftest enforces it with the parser rather than with anyone's memory. Delete
the folder and JARVIS is what it was before — a voice with a 2D core.

    python -m presence.selftest

    model.py        le vocabulaire : Expression, Gesture, Gaze, Posture
    affect.py       l'etat interieur continu, d'ou tout le reste derive
    vocabulary.py   expression + intensite -> 52 coefficients ARKit
    catalog.py      ce qui est reellement installe, lu dans avatar/manifest.json
    director.py     etat + intention -> une Performance jouable
"""
from __future__ import annotations

from .affect import (
    Affect,
    SocialMode,
    expression_for,
    gaze_for,
    intensity_for,
    posture_for,
    stillness_for,
    tempo_for,
)
from .catalog import Catalogue, catalogue
from .director import Director, parse, prompt_fragment, strip
from .model import (
    Directive,
    Expression,
    Gaze,
    Gesture,
    Performance,
    Posture,
    RigPart,
)
from .vocabulary import ARKIT_52, face, viseme

__all__ = [
    "ARKIT_52",
    "Affect",
    "SocialMode",
    "Catalogue",
    "Directive",
    "Director",
    "Expression",
    "Gaze",
    "Gesture",
    "Performance",
    "Posture",
    "RigPart",
    "catalogue",
    "expression_for",
    "gaze_for",
    "intensity_for",
    "posture_for",
    "stillness_for",
    "tempo_for",
    "face",
    "parse",
    "prompt_fragment",
    "strip",
    "viseme",
]
