"""
server/targeting.py — which device runs this, decided in code.

Four rules, in this order, and the order is the whole safety property:

    1. EXPLICIT      the user named a device      "ouvre Chrome sur mon PC"
    2. CAPABILITY    only one device can do it    photo -> the phone
    3. ORIGIN        generic command              origin runs it
    4. AMBIGUOUS     several could, none named    ask, never guess

**Rule 3 is the one the brief is really about.** "ouvre le navigateur" from the
phone must open the browser *on the phone*, and the fact that the PC also has a
browser — and is currently connected, and has a better one — is not a reason to
send it there. A command with no stated destination belongs to the device that
issued it.

**Rule 4 never guesses.** When two devices could both do something and nothing
in the sentence or the capabilities picks one, this returns a question. An
assistant that quietly picks the wrong machine is worse than one that asks,
because the user finds out afterwards.

**And a named target is never substituted.** If the user says "sur mon PC" and
the PC is offline, the answer is "your PC is not connected" — not the phone.
That is `TargetKind.UNAVAILABLE`, and `routing.py` turns it into a sentence
rather than into a different device.

WHY THE PHRASE MATCHING IS HERE AND NOT IN THE MODEL
    Gemini is better than a regex at understanding "mets ça sur l'autre écran
    du salon". It is also capable of deciding, fluently and wrongly, that a
    command should run somewhere the user never mentioned. So the model may
    *suggest* a target — `routing.py` passes it in as `model_hint` — and this
    module validates it against the same rules. The deterministic path is the
    one that decides.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from .devices import DeviceInfo, DeviceRegistry, DeviceType


class Hint(str, Enum):
    """What the sentence said about where to run, if anything."""

    PC = "pc"
    ANDROID = "android"
    HERE = "here"
    OTHER = "other"


class TargetKind(str, Enum):
    DEVICE = "device"
    CLARIFY = "clarify"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Resolution:
    kind: TargetKind
    device: DeviceInfo | None = None
    #: Which rule fired. Goes straight into the log line — see `observability`.
    rule: str = ""
    #: For CLARIFY: the question to put to the user, already phrased.
    question: str = ""
    #: For UNAVAILABLE: what was asked for and why it cannot happen.
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.kind is TargetKind.DEVICE


# ── phrase detection ─────────────────────────────────────────────────────────
#
# Accent- and case-insensitive, longest match first. Written as explicit
# alternations rather than something clever because the cost of a false positive
# here is a command running on the wrong machine.

_PC_PATTERNS = (
    r"\bsur (?:mon|le|l'|cet?) ?(?:pc|ordi|ordinateur|desktop|poste)\b",
    r"\bsur (?:mon )?ordinateur portable\b",
    r"\bsur (?:mon )?laptop\b",
    r"\bon (?:my|the) (?:pc|computer|desktop|laptop)\b",
    r"\bdepuis (?:mon|le) (?:pc|ordinateur)\b",
)

_ANDROID_PATTERNS = (
    # "portable" alone: in French usage this is the mobile phone, and the brief
    # groups it with "sur le telephone". "ordinateur portable" is matched above
    # and wins by being checked first.
    r"\bsur (?:mon|le|l'|ce) ?(?:telephone|tel|portable|mobile|smartphone|android)\b",
    r"\bon (?:my|the) (?:phone|mobile|android|smartphone)\b",
    r"\bavec (?:mon|le) (?:telephone|portable|smartphone)\b",
)

_HERE_PATTERNS = (
    r"\b(?:ici|sur cet appareil|sur ce peripherique)\b",
    r"\b(?:here|on this device)\b",
)

_OTHER_PATTERNS = (
    r"\bsur l'autre (?:appareil|ecran|machine)?\b",
    r"\bon the other (?:device|one|machine)\b",
)

_HINTS: tuple[tuple[Hint, tuple[str, ...]], ...] = (
    # PC first: "ordinateur portable" must not be eaten by the "portable" rule.
    (Hint.PC, _PC_PATTERNS),
    (Hint.ANDROID, _ANDROID_PATTERNS),
    (Hint.OTHER, _OTHER_PATTERNS),
    (Hint.HERE, _HERE_PATTERNS),
)


def _fold(text: str) -> str:
    """Lower-case and strip accents, so "téléphone" and "telephone" match."""
    lowered = (text or "").lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def detect_hint(text: str) -> Hint | None:
    """The device the sentence names, or None if it names none."""
    folded = _fold(text)
    for hint, patterns in _HINTS:
        for pattern in patterns:
            if re.search(pattern, folded):
                return hint
    return None


def hint_to_type(hint: Hint | None, origin: DeviceInfo | None,
                 registry: DeviceRegistry) -> DeviceType | None:
    """Turn a phrase into a device type, resolving the relative ones.

    `HERE` and `OTHER` are relative to the origin, which is why they cannot be
    handled by the regex table alone.
    """
    if hint is Hint.PC:
        return DeviceType.PC
    if hint is Hint.ANDROID:
        return DeviceType.ANDROID
    if hint is Hint.HERE:
        return origin.device_type if origin else None
    if hint is Hint.OTHER:
        if origin is None:
            return None
        others = {
            d.device_type for d in registry.online()
            if d.device_id != origin.device_id
            and d.device_type is not DeviceType.UNKNOWN
        }
        # "the other device" is only meaningful when there is exactly one.
        return others.pop() if len(others) == 1 else None
    return None


# ── the resolver ─────────────────────────────────────────────────────────────


def _pick(devices: list[DeviceInfo]) -> DeviceInfo | None:
    return devices[0] if len(devices) == 1 else None


def _label(device_type: DeviceType) -> str:
    return {"pc": "le PC", "android": "le téléphone"}.get(
        device_type.value, device_type.value
    )


def resolve(
    registry: DeviceRegistry,
    *,
    text: str = "",
    origin: DeviceInfo | None = None,
    capability: str = "",
    model_hint: str = "",
) -> Resolution:
    """Decide which device runs this. Never raises, never guesses silently.

    `capability` is the action about to run. `model_hint` is what Gemini
    proposed, if anything — it is treated exactly like a phrase in the text and
    validated the same way, never trusted on its own.
    """
    hint = detect_hint(text)
    if hint is None and model_hint:
        hint = detect_hint(model_hint) or _parse_model_hint(model_hint)

    # ── 1. EXPLICIT ──────────────────────────────────────────────────────────
    if hint is not None:
        wanted = hint_to_type(hint, origin, registry)
        if wanted is None:
            return Resolution(
                TargetKind.CLARIFY,
                rule="explicit-unresolved",
                question="Sur le PC ou sur le téléphone ?",
            )
        candidates = registry.online_of_type(wanted)
        if not candidates:
            known = [d for d in registry.all() if d.device_type is wanted]
            return Resolution(
                TargetKind.UNAVAILABLE,
                rule="explicit-offline",
                detail=(
                    f"{_label(wanted).capitalize()} n'est pas connecté."
                    if known
                    else f"Aucun appareil de type {wanted.value} n'est appairé."
                ),
            )
        if capability:
            able = [d for d in candidates if d.can(capability)]
            if not able:
                return Resolution(
                    TargetKind.UNAVAILABLE,
                    rule="explicit-incapable",
                    detail=(
                        f"{_label(wanted).capitalize()} ne sait pas faire "
                        f"« {capability} »."
                    ),
                )
            candidates = able
        chosen = _pick(candidates)
        if chosen is None:
            return Resolution(
                TargetKind.CLARIFY,
                rule="explicit-multiple",
                question="Lequel de ces appareils ? "
                + ", ".join(d.label for d in candidates),
            )
        return Resolution(TargetKind.DEVICE, device=chosen, rule="explicit")

    # ── 2. CAPABILITY ────────────────────────────────────────────────────────
    if capability:
        able = registry.with_capability(capability)
        if not able:
            offline_able = [
                d for d in registry.with_capability(capability, online_only=False)
            ]
            return Resolution(
                TargetKind.UNAVAILABLE,
                rule="capability-none",
                detail=(
                    f"Aucun appareil connecté ne sait faire « {capability} »."
                    if offline_able
                    else f"Aucun appareil ne déclare « {capability} »."
                ),
            )
        if len(able) == 1:
            # Exactly one device can do this. That decides it, and it overrides
            # the origin: "prends une photo" from the PC goes to the phone
            # because the PC has no camera, not because anyone said so.
            return Resolution(TargetKind.DEVICE, device=able[0], rule="capability-only")

        # ── 3. ORIGIN ────────────────────────────────────────────────────────
        if origin is not None and origin.can(capability):
            online_ids = {d.device_id for d in registry.online()}
            if origin.device_id in online_ids:
                return Resolution(TargetKind.DEVICE, device=origin, rule="origin")

        # ── 4. AMBIGUOUS ─────────────────────────────────────────────────────
        return Resolution(
            TargetKind.CLARIFY,
            rule="ambiguous",
            question="Sur le PC ou sur le téléphone ?",
        )

    # No capability named: this is a plain command, and the origin owns it.
    if origin is not None and origin.device_type is not DeviceType.UNKNOWN:
        return Resolution(TargetKind.DEVICE, device=origin, rule="origin")

    # An origin we cannot identify gets a question, never a default. Today that
    # is every unmodified Android build; it is also exactly the case where a
    # silent default would be a command running on the wrong machine.
    return Resolution(
        TargetKind.CLARIFY,
        rule="unknown-origin",
        question="Sur quel appareil : le PC ou le téléphone ?",
    )


def _parse_model_hint(raw: str) -> Hint | None:
    """Accept a bare device type from the model, e.g. `target_device="pc"`."""
    folded = _fold(raw).strip()
    if folded in ("pc", "desktop", "computer", "ordinateur"):
        return Hint.PC
    if folded in ("android", "phone", "telephone", "mobile"):
        return Hint.ANDROID
    if folded in ("here", "ici", "origin"):
        return Hint.HERE
    if folded in ("other", "autre"):
        return Hint.OTHER
    return None
