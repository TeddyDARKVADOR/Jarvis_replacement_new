"""
presence/catalog.py — what this body can actually do, today, on this machine.

WHY AN INVENTORY EXISTS AT ALL
    `model.Gesture` lists thirty-two things JARVIS may want. A given body
    performs some subset of them: a floating holographic head has no arms, a
    fresh install has no Mixamo clips at all, and a robot mesh has four shape
    keys instead of fifty-two. Without an inventory, every one of those cases is
    a gesture request that silently does nothing — the worst failure mode there
    is, because the face looks *almost* right and nobody can say what is wrong.

    So the director asks this module before it promises anything, and a gesture
    that is not installed is downgraded on the way out, in writing, in the
    `requested_gesture` field the debug window prints.

WHERE THE INVENTORY COMES FROM
    `avatar/manifest.json` — the same file the renderer reads. One source of
    truth for both ends, which is the only way the Python side's idea of what is
    installed cannot drift from what the browser can actually play.

INSTALLING A GESTURE IS TWO STEPS AND NO CODE
    1. Drop the clip in `avatar/gestures/` (a .glb holding one animation —
       Mixamo exports FBX, and `avatar/gestures/README.md` gives the one-line
       conversion).
    2. Add its name to `gestures` in the manifest.

    That is the whole extension mechanism, and it is deliberately this small:
    the value of this design is the *number* of gestures that can be installed,
    so the cost of installing the hundredth has to be the same as the first.

WHY A MISSING MANIFEST IS NOT AN ERROR
    A checkout with no assets must still run, and must still show a face — see
    `avatar/js/body_procedural.js`. So the fallback here is not an empty
    catalogue, it is the procedural body's own capability set: head, torso, and
    the fifteen gestures `model.PROCEDURAL_GESTURES` can produce with no assets
    whatsoever. JARVIS is expressive out of the box and better once assets
    arrive, rather than broken until then.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .model import (
    FALLBACK_CHAIN,
    GESTURE_REQUIRES,
    PROCEDURAL_GESTURES,
    Gesture,
    RigPart,
)

BASE_DIR = Path(__file__).resolve().parent.parent
MANIFEST = BASE_DIR / "avatar" / "manifest.json"

#: How long a parsed manifest is trusted. Dropping a clip in and reloading the
#: panel should show it without restarting JARVIS, but re-reading a file on
#: every decision would put disk I/O on the path of every sentence.
_TTL_S = 5.0

#: What the procedural body performs with no assets whatsoever. Everything a
#: head and a torso can do; `catalog` returns this when the manifest is absent,
#: unreadable, or lists no gestures.
_PROCEDURAL_PARTS = frozenset({RigPart.HEAD, RigPart.TORSO})


@dataclass(frozen=True)
class Catalogue:
    """The inventory, resolved once and cached for `_TTL_S`."""

    #: Which parts of a body this model actually has.
    parts: frozenset[RigPart]
    #: Gesture clips present in `avatar/gestures/` AND named by the manifest.
    installed: frozenset[Gesture]
    #: Names in the manifest that match no `Gesture` member. Kept rather than
    #: dropped: a typo in the manifest is otherwise invisible, and this is what
    #: the selftest and the debug window report.
    unknown: tuple[str, ...]
    #: The model file the renderer will load, or "" for the procedural body.
    model: str
    #: True when nothing is installed and the procedural body is in charge.
    procedural: bool

    def can(self, gesture: Gesture) -> bool:
        """Is this gesture performable right now?

        Two ways to be yes, and the second is the one that matters. A clip being
        installed is the obvious one. The other is that `avatar/js/gestures.js`
        can produce the movement from arithmetic — a nod is three bones and a
        sine wave, and making JARVIS wait for someone to download `nod.glb`
        before he can agree with anything would be absurd. That is why a fresh
        checkout is expressive and not mute.

        Both still require the bones. A clip installed for a body that cannot
        carry it — `wave.glb` sitting beside a bust — is not performable, and
        neither is a procedural shrug on a floating head. `self.procedural` is
        therefore NOT consulted here: whether the mesh is downloaded or drawn
        from primitives changes what JARVIS looks like, never what he can do.
        """
        if GESTURE_REQUIRES.get(gesture, RigPart.HEAD) not in self.parts:
            return False
        if gesture in self.installed:
            return True
        return gesture.value in PROCEDURAL_GESTURES

    def resolve(self, gesture: Gesture) -> Gesture:
        """The nearest performable gesture, walking `FALLBACK_CHAIN`.

        Terminates: every chain ends at `IDLE` and `IDLE` is always performable,
        so this cannot return None and cannot loop. The selftest proves both.
        """
        if self.can(gesture):
            return gesture
        for candidate in FALLBACK_CHAIN.get(gesture, ()):
            if self.can(candidate):
                return candidate
        return Gesture.IDLE

    @property
    def vocabulary(self) -> tuple[Gesture, ...]:
        """Everything performable, in declaration order.

        This is what goes into the LLM's system prompt — see
        `director.prompt_fragment`. Offering a model a word it cannot cash is
        how you teach it to pick that word.
        """
        return tuple(g for g in Gesture if self.can(g))


_lock = threading.Lock()
_cached: tuple[float, Catalogue] | None = None


def _parse(raw: dict, gesture_dir: Path) -> Catalogue:
    parts: set[RigPart] = set()
    for name in raw.get("rig", {}).get("parts", []):
        try:
            parts.add(RigPart(str(name).strip().lower()))
        except ValueError:
            continue
    if not parts:
        parts = set(_PROCEDURAL_PARTS)

    model = str(raw.get("model", {}).get("file", "") or "").strip()
    procedural = not model

    installed: set[Gesture] = set()
    unknown: list[str] = []
    for name, spec in (raw.get("gestures") or {}).items():
        try:
            gesture = Gesture(str(name).strip().lower())
        except ValueError:
            unknown.append(str(name))
            continue
        # A clip that is named but absent is not installed. Checking the file
        # here rather than trusting the manifest is what stops a half-finished
        # install from presenting as a working one.
        clip = str((spec or {}).get("clip", "") or "").strip()
        if clip and not (gesture_dir / clip).is_file():
            continue
        installed.add(gesture)

    return Catalogue(
        parts=frozenset(parts),
        installed=frozenset(installed),
        unknown=tuple(unknown),
        model=model,
        procedural=procedural,
    )


def _procedural_catalogue() -> Catalogue:
    return Catalogue(
        parts=frozenset(_PROCEDURAL_PARTS),
        installed=frozenset(),
        unknown=(),
        model="",
        procedural=True,
    )


def catalogue(*, force: bool = False) -> Catalogue:
    """The inventory. Cheap to call — cached, and never raises.

    Never raises is load-bearing: this sits on the path of every spoken line,
    and a malformed manifest must cost JARVIS a plainer face, never a sentence.
    """
    global _cached
    now = time.monotonic()
    with _lock:
        if not force and _cached is not None and now - _cached[0] < _TTL_S:
            return _cached[1]

    try:
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        resolved = _parse(raw, MANIFEST.parent / "gestures")
    except Exception:
        resolved = _procedural_catalogue()

    with _lock:
        _cached = (now, resolved)
    return resolved
