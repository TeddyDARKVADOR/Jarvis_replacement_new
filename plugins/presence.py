"""
plugins/presence.py — the door JARVIS walks through to use his own face.

`presence/` decides what JARVIS's face and body do, and `avatar/` plays it. Both
were finished and verified before this file existed, and nothing carried a
decision from the first to the second: `presence.parse()` had no caller, and the
desktop client's `set_intent()` waited for an `avatar` event nobody emitted.
This is that half — a tool, a validation, and one event on a socket that is
already there.

WHY A TOOL AND NOT A BLOCK IN THE ANSWER
    `presence/README.md` documents a fenced ```jarvis-presence``` block at the
    end of a reply, and that is the right form for an assistant that writes text
    and then has it spoken. `director.strip()` exists for exactly that pipeline,
    and takes the block back out before the voice sees it.

    This JARVIS has no such pipeline. `main.py` opens Gemini Live with
    `response_modalities=["AUDIO"]`: the model SPEAKS, and the text that reaches
    us is `output_transcription` — a transcript of audio the user already heard.
    A block in that stream is a block JARVIS read out loud, and removing it
    afterwards changes the log, not the room.

    So the directive arrives the only way a model can say something nobody
    hears: as a function call. Its arguments ARE the directive, and
    `presence.parse()` reads them unchanged — it already accepts a bare object,
    a path it grew for a model that forgot its fence and that turns out to be
    the main road here. One definition of the form, and one reader of it.

WHY THIS FILE VALIDATES AND DOES NOT RESOLVE
    Resolving a directive into a `Performance` needs the catalogue, and the
    catalogue is a property of the body that will play it — which lives on the
    client, and may not be the body this host has a manifest for. So this does
    the one thing only it can do, which is refuse junk before it reaches the
    wire, and forwards what JARVIS actually asked for. The client resolves it
    against its own `avatar/manifest.json`, and a gesture that host has not
    installed degrades through `FALLBACK_CHAIN` there, where the answer is true.

    What crosses the wire is therefore JARVIS's request, not a rendering of it.
    Two clients with different bodies both obey, each as well as its body
    allows.

WHY EVERYTHING IS IN ONE FILE
    The transport briefly lived in `server/`, and does not belong there:
    `server/__init__.py` states that nothing — "nor any action or plugin" —
    imports that package, and the one-way arrow is what keeps `python main.py`
    on a desktop the program it was before the 24/7 layer existed. A plugin
    reaching into `server/` would have been the first exception, made for
    convenience, which is how such invariants die.

    So this is one file, which is also what `docs/V2_CONTRACT.md` asks for: a
    capability Gemini can call is a plugin, and a plugin is removable by
    deleting it. Doing so costs the tool at the next start and nothing else —
    JARVIS keeps a face that follows his machine state, resolved on the client
    by `presence.Director` with no server involved at all.

WHY THE VOCABULARY IS GENERATED AND NOT WRITTEN
    The gesture list below is read from `presence.catalogue()`, which reads
    `avatar/manifest.json`. A hand-written list goes stale the moment a Mixamo
    clip is installed, and the failure is silent and backwards: the model is
    offered a gesture that is not installed, picks it, the catalogue quietly
    substitutes the nearest one it can play — and the body looks *less* capable
    precisely because someone added a capability.

    `presence.director.prompt_fragment()` makes the same argument at length for
    the system prompt. Its text cannot be reused here, because it teaches the
    fenced form — the one thing that must not happen. What is reused is the
    tables underneath, so there is one vocabulary and two phrasings of it.
"""
from __future__ import annotations

import json
import time

# Imported at module level ON PURPOSE. `presence/` is an optional package, and
# if it has been deleted this import raises — which makes `discover_plugins()`
# log the file and skip it, so the tool is never offered to a model that has no
# body to drive. The alternative, a guarded import and an inert tool, would
# advertise a capability that cannot be cashed. See core/plugin_loader.py.
from presence import Expression, Gaze, Gesture, Posture, catalogue, parse
from presence.affect import SocialMode

# ── the wire ─────────────────────────────────────────────────────────────────

#: The `type` on /ws. A client that predates this ignores it and carries on
#: (PROTOCOL.md section 4) — which is the whole reason this is one more event
#: rather than a channel of its own.
EVENT_TYPE = "avatar"

#: How long a directive is worth delivering, and the reason every event carries
#: a `ts`.
#:
#: `dashboard.broadcast()` appends to `_history` and replays the last 50 to any
#: client that connects. That is right for a transcript and wrong for a face: a
#: laptop that reconnects at noon would be handed the expression JARVIS chose at
#: nine and wear it for twenty-five seconds, as a reaction to nothing.
#:
#: An id-and-dedupe scheme like `server/notify.py`'s would be the wrong tool — a
#: face is not a message, and showing it twice is harmless. Showing it late is
#: not. So the event carries the wall clock it was decided at, and the client
#: drops anything older than the intent's own lifetime.
#:
#: This mirrors `presence.director.INTENT_TTL_S` and
#: `client_desktop.protocol.AVATAR_FRESH_SECONDS`. None of the three can import
#: the other two — `presence/` is deletable, and the client runs on a machine
#: that has never seen this repository — so a check in `server/selftest.py`
#: compares them and fails on any drift. That is what makes the copy safe rather
#: than fragile.
FRESH_S = 25.0

#: The keys a directive may carry. Anything else the model invents is dropped
#: rather than forwarded: an unknown key costs nothing on the wire, and a
#: hallucinated one that happens to collide with a future field costs an
#: afternoon. `presence.director._coerce` reads exactly these.
_ALLOWED = (
    "expression", "intensity", "gesture", "gaze", "posture", "reason",
    "valence", "arousal", "attention", "confidence", "urgency",
    "socialMode", "social_mode", "emotion",
)

#: Same cap `presence.director._coerce` puts on `reason`. It is a debugging
#: line, not a second channel for the model to talk on.
_MAX_REASON = 200

_WORDS = ("expression", "gesture", "gaze", "posture", "socialMode", "social_mode")


def _number(value) -> float | None:  # noqa: ANN001
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(key: str, value: float) -> float:
    """Valence is the only axis that goes negative. Everything else is 0..1,
    including `intensity`, which `presence` clamps again on arrival."""
    low = -1.0 if key == "valence" else 0.0
    return max(low, min(1.0, value))


def _clean(raw: dict) -> dict:
    """The directive, reduced to what it is allowed to say.

    Numbers are coerced and clamped rather than trusted, because a model that
    answers `"valence": 4` is not an error worth refusing — it is a model that
    meant "very pleasant" and used a scale it invented. Refusing it would cost a
    correct face; clamping it costs nothing anyone can see.
    """
    out: dict = {}
    for key in _ALLOWED:
        if key not in raw:
            continue
        value = raw[key]
        if value is None:
            continue

        if key in _WORDS:
            out[key] = str(value).strip().lower()[:40]

        elif key == "reason":
            text = str(value)[:_MAX_REASON].strip()
            if text:
                out[key] = text

        elif key == "emotion":
            # Either a nested {valence, arousal} or a single word — both are
            # forms `presence.parse` reads, so both are carried through rather
            # than normalised into one. The parser is the authority on shape.
            if isinstance(value, dict):
                inner = {}
                for axis in ("valence", "arousal"):
                    number = _number(value.get(axis))
                    if number is not None:
                        inner[axis] = _clamp(axis, number)
                if inner:
                    out[key] = inner
            else:
                out[key] = str(value).strip().lower()[:40]

        else:
            number = _number(value)
            if number is not None:
                out[key] = _clamp(key, number)
    return out


def build_event(raw: dict, *, now: float | None = None) -> dict | None:
    """The wire form of a directive, or None when it was not one.

    None is a common answer and not a failure: a model given a body has to be
    free to ask for something unrecognisable, and the reflex path — the face
    that follows the machine state — is already correct without any directive at
    all. Returning None is what keeps a hallucinated word costing a plainer face
    and never an error.
    """
    if not isinstance(raw, dict):
        return None

    clean = _clean(raw)
    if not clean:
        return None

    # The authority on "is this a directive at all" is the parser that will read
    # it on the other side. Asking it here means this end can never forward
    # something the client would then silently drop — and it means there is one
    # definition of the form, not two that agree until they do not.
    if parse(json.dumps(clean)) is None:
        return None

    return {
        "type": EVENT_TYPE,
        "ts": float(now if now is not None else time.time()),
        "directive": clean,
    }


def is_fresh(event: dict, *, now: float | None = None) -> bool:
    """Whether this event still describes the present.

    Written beside `FRESH_S` and beside the reason the guard exists, so that the
    client's copy of the rule and this one are the same sentence.

    An event with no `ts` counts as fresh: the only producer that omits one is a
    server older than this guard, and refusing those would be a client that
    stopped working against a server that had done nothing wrong.
    """
    stamp = event.get("ts")
    if stamp is None:
        return True
    try:
        age = float(now if now is not None else time.time()) - float(stamp)
    except (TypeError, ValueError):
        return True
    return age <= FRESH_S


def deliver(ui, raw: dict) -> bool:  # noqa: ANN001
    """Put a directive on /ws through whatever UI JARVIS is wearing.

    Returns True only when an event was actually handed to the transport, so the
    caller can say "no body is listening" instead of guessing.

    `ui` is `server.headless_ui.HeadlessUI` on a server and `ui.JarvisUI` on the
    desktop. The desktop one has no `emit_event` and never will: that JARVIS
    draws his own window and has no client to tell. The absence of the method is
    a supported answer and not a fault, which is why it is checked rather than
    assumed — and why nothing here imports either of those classes.
    """
    event = build_event(raw)
    if event is None:
        return False
    emit = getattr(ui, "emit_event", None)
    if not callable(emit):
        return False
    try:
        emit(event)
    except Exception:
        # A face that fails to reach the wire must never take down the turn that
        # produced it. The sentence JARVIS is saying matters more than the
        # expression he wanted to say it with.
        return False
    return True


# ── the tool ─────────────────────────────────────────────────────────────────


def _installed_gestures() -> list[str]:
    """What this body can actually do today, in the order the catalogue lists.

    Falls back to the full vocabulary if the manifest cannot be read: an
    unreadable manifest is already reported by `presence.catalogue()` and by the
    selftest, and offering the full list degrades into `FALLBACK_CHAIN` rather
    than into an empty menu.
    """
    try:
        return [g.value for g in catalogue().vocabulary]
    except Exception:
        return [g.value for g in Gesture]


def _vocabulary(enum) -> list[str]:  # noqa: ANN001
    return [member.value for member in enum]


_GESTURES = _installed_gestures()
_EXPRESSIONS = _vocabulary(Expression)
_GAZES = _vocabulary(Gaze)
_POSTURES = _vocabulary(Posture)
_MODES = _vocabulary(SocialMode)


#: Written to the model in French, because `presence.director.prompt_fragment`
#: chose French for everything JARVIS reads about his own body, and a second
#: language here would be a second voice.
_DESCRIPTION = f"""Donne a ton visage et a ton corps l'etat qui va avec ce que tu dis.

Tu as un vrai visage et un vrai corps a l'ecran. Personne ne choisit leur
expression a ta place. Appelle cet outil AU MOMENT ou ta reaction compte autant
que ta reponse : une mauvaise nouvelle, une plaisanterie, un doute, une action
irreversible. N'annonce jamais cet appel a voix haute et ne le commente pas —
il ne s'entend pas, il se voit.

DEUX FACONS DE L'APPELER, au choix.

1. DECRIRE TON ETAT (preferee). Donne les axes ; ton visage, ton regard, ta
   posture et le rythme de tes gestes en decoulent tout seuls. C'est plus riche
   qu'un nom de visage, et plus proche de ce que tu viens deja de calculer pour
   ecrire ta phrase.
       valence 0.45, arousal 0.25, attention 0.9, confidence 0.75

2. NOMMER UN VISAGE, quand tu veux exactement celui-la et aucun autre.
       expression "serious", intensity 0.8

Le geste se declare dans les deux cas : un etat faconne un mouvement, il ne le
designe pas.

Cet outil est facultatif et doit le rester. Sans appel, ton corps suit ton etat
machine — ecoute, reflexion, parole — ce qui est correct la plupart du temps.
Ne l'appelle que quand un visage neutre serait faux. Reste sobre : l'ironie
legere te va mieux que la joie, et tu ne montres jamais de colere a
l'utilisateur."""


PLUGIN = {
    "name": "set_presence",
    "description": _DESCRIPTION,
    "parameters": {
        "type": "OBJECT",
        "properties": {
            # ── forme 1 : l'etat interieur ───────────────────────────────────
            "valence": {
                "type": "NUMBER",
                "description": "-1.0 desagreable -> +1.0 agreable.",
            },
            "arousal": {
                "type": "NUMBER",
                "description": "0.0 calme -> 1.0 active.",
            },
            "attention": {
                "type": "NUMBER",
                "description": "0.0 ailleurs -> 1.0 entierement sur l'utilisateur.",
            },
            "confidence": {
                "type": "NUMBER",
                "description": "0.0 hesitant -> 1.0 assure.",
            },
            "urgency": {
                "type": "NUMBER",
                "description": "0.0 rien ne presse -> 1.0 il faut agir.",
            },
            "social_mode": {
                "type": "STRING",
                "enum": _MODES,
                "description": (
                    "Le registre. Il plafonne l'expression : un sourire a 0.9 "
                    "pendant une confirmation de suppression n'est pas "
                    "expressif, il est faux."
                ),
            },
            # ── forme 2 : le visage nomme ────────────────────────────────────
            "expression": {
                "type": "STRING",
                "enum": _EXPRESSIONS,
                "description": (
                    "Le visage, quand tu veux celui-la et aucun autre. Laisse "
                    "vide si tu as decrit ton etat : il vaut mieux le deriver."
                ),
            },
            "intensity": {
                "type": "NUMBER",
                "description": "0.0 a 1.0. Accompagne `expression`. Defaut 0.5.",
            },
            # ── les deux formes ──────────────────────────────────────────────
            "gesture": {
                "type": "STRING",
                "enum": _GESTURES,
                "description": (
                    "Ce que le corps fait. Ceux-ci sont installes sur ce "
                    "corps-ci : " + ", ".join(_GESTURES) + "."
                ),
            },
            "gaze": {
                "type": "STRING",
                "enum": _GAZES,
                "description": "Ou tu regardes.",
            },
            "posture": {
                "type": "STRING",
                "enum": _POSTURES,
                "description": "La tenue du buste. Derivee de l'etat si absente.",
            },
            "reason": {
                "type": "STRING",
                "description": (
                    "Pourquoi, en quelques mots. Jamais affiche, jamais lu a "
                    "voix haute : c'est la seule chose qui rende un visage faux "
                    "explicable apres coup."
                ),
            },
        },
        "required": [],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:  # noqa: ANN001
    """Hand the directive to the body, and say as little as possible about it.

    The return value goes back to Gemini as the function response, and anything
    quotable there is something it may decide to read out. "ok" is not quotable.
    A failure returns the same word for the same reason: a JARVIS who announces
    that his face did not update has just turned a missing expression into a
    sentence, which is worse than the missing expression.

    What actually diagnoses this goes to stdout, beside the `📞 set_presence`
    line main.py already prints, and therefore into journalctl — which is where
    a body that is not moving gets investigated. Those lines are ASCII on
    purpose: a Windows console is cp1252, `print` of anything outside it raises,
    and the loader would then hand Gemini an error string it might read out.
    """
    raw = parameters if isinstance(parameters, dict) else {}
    try:
        delivered = deliver(player, raw)
    except Exception as exc:
        _log(f"directive non transmise ({exc})")
        return "ok"

    reason = str(raw.get("reason", ""))[:80]
    if delivered:
        _log(_summary(raw) + (f" : {reason}" if reason else ""))
    else:
        # Three ways to land here, and telling them apart is the whole point of
        # printing it: no client is connected, this host draws its own window
        # (desktop `ui.JarvisUI` has no `emit_event`), or the arguments were not
        # a directive at all. The first two are normal.
        _log(f"aucune destination pour {_summary(raw)}")
    return "ok"


def _log(line: str) -> None:
    """One diagnostic line, forced into ASCII first.

    `reason` is written by the model and will contain accents, and a Windows
    console is cp1252: `print` of anything outside it raises. The loader's net
    would catch that and hand Gemini an error string, which it might then read
    out — a JARVIS announcing a console encoding problem because he chose a
    face. So the line is flattened, and never allowed to raise on its own.
    """
    try:
        print("[presence] " + line.encode("ascii", "replace").decode("ascii"),
              flush=True)
    except Exception:
        pass


def _summary(raw: dict) -> str:
    """The directive in one line, in the form it was asked for.

    Prints the axes when JARVIS described a state and the word when he named a
    face, because which of the two forms he used is the first thing worth
    knowing — a model reaching for `expression` every turn is the failure mode
    the affect form exists to avoid.
    """
    expression = str(raw.get("expression", "")).strip()
    if expression:
        line = f"{expression} {float(raw.get('intensity', 0.5)):.2f}"
    else:
        axes = [f"{key[:3]} {float(raw[key]):+.2f}"
                for key in ("valence", "arousal", "attention",
                            "confidence", "urgency")
                if isinstance(raw.get(key), (int, float))]
        line = " ".join(axes) or "etat vide"
    gesture = str(raw.get("gesture", "")).strip()
    return line + (f" | {gesture}" if gesture else "")
