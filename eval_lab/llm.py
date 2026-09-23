"""
eval_lab/llm.py — Opus 5.5 as a scenario author and as an adversary. Never as a judge of truth.

    LLM proposal -> JSON (structured output) -> parse -> schema + semantic
    validation (scenario.validate) -> novelty filter -> accepted scenario

WHAT THE MODEL MAY DO
    Propose situations, variants, dangerous interactions, bug hypotheses.

WHAT IT MAY NOT DO
    Decide what is true. Anything it claims about the correct behaviour is
    stored as an explicit oracle *authored by the LLM* (`lineage.oracle_author`
    = "llm"). When JARVIS contradicts such a claim, triage files it under
    "oracle-disputed" — a person decides. Only a HARD property of the project
    can turn a model-written scenario into a JARVIS candidate bug.

WHY THE WORLD COMES BACK AS A STRING
    A JSON schema strict enough for structured outputs cannot describe the
    open `world` object of every surface. So the schema guarantees the
    envelope (surface from a closed list, rationale, claim) and carries
    `world`/`stimulus`/`events` as JSON strings, which are then parsed and put
    through exactly the validators every other generator goes through. A
    scenario the lab would refuse from a human is refused from the model.

THE BOUNDARY AND THE MINIMUM ARE NOT ASKED OF THE MODEL
    Phase 6 wants: a plausible wrong decision, then one variable changed to
    find the frontier, then the smallest counter-example. The model does the
    first (imagination). The lab does the other two deterministically —
    mutate.py around each failing proposal, triage.minimise on what fails —
    because a frontier or a minimum the lab computed is a fact, and one the
    model asserted is another claim.

REPRODUCIBILITY
    `Cassette` records every (request, response). Replaying it gives the same
    scenarios, offline, for free — an LLM has no seed, a cassette is its seed.

OPTIONAL
    `anthropic` is imported lazily and only here. JARVIS never needs it; the
    lab runs without it (replay, fake provider). See requirements-llm.txt.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import scenario as sc

MODEL = "claude-opus-5-5"
# $ per million tokens, claude-opus-5-5 (claude-api skill, cached 2026-06-24).
PRICE = {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00}

LLM_SURFACES = ("policy", "situation", "routing", "sequence", "router")

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "scenarios": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "surface": {"type": "string", "enum": list(LLM_SURFACES)},
                    "family": {"type": "string"},
                    "hypothesis": {"type": "string"},
                    "world_json": {"type": "string"},
                    "stimulus_json": {"type": "string"},
                    "events_json": {"type": "string"},
                    "claim_json": {"type": "string"},
                },
                "required": ["surface", "family", "hypothesis", "world_json",
                             "stimulus_json", "events_json", "claim_json"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scenarios"],
    "additionalProperties": False,
}


class LLMUnavailable(RuntimeError):
    pass


# ── providers ────────────────────────────────────────────────────────────────

@dataclass
class Usage:
    calls: int = 0
    failures: int = 0
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @property
    def usd(self) -> float:
        return (self.input * PRICE["input"] + self.output * PRICE["output"]
                + self.cache_read * PRICE["cache_read"] + self.cache_write * PRICE["cache_write"]) / 1e6

    def add(self, u: dict) -> None:
        self.calls += 1
        self.input += u.get("input_tokens", 0) or 0
        self.output += u.get("output_tokens", 0) or 0
        self.cache_read += u.get("cache_read_input_tokens", 0) or 0
        self.cache_write += u.get("cache_creation_input_tokens", 0) or 0


class AnthropicProvider:
    """Claude Opus 5.5 through the official SDK. Retries 429/5xx/connection
    errors itself (max_retries); what still fails comes back as an error
    record, never as an exception into the generation loop."""

    def __init__(self, model: str = MODEL, effort: str = "high", max_retries: int = 4):
        try:
            import anthropic
        except ImportError as e:
            raise LLMUnavailable("pip install -r eval_lab/requirements-llm.txt") from e
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=max_retries)
        self.model = model
        # Opus 5.5 defaults to "medium"; adversarial search is intelligence-
        # sensitive work, so it is set explicitly. Thinking cannot be disabled
        # on this model and is left to its adaptive default.
        self.effort = effort
        # Structured outputs (output_config.format) are guaranteed JSON. If the
        # model refuses the parameter (a 400 naming the format), the provider
        # switches ONCE to "schema in the prompt" and says so: the text is then
        # parsed leniently, and everything still goes through the same
        # validators — nothing downstream is relaxed.
        self.structured = True
        self.notes: list[str] = []

    def _call(self, system: str, user: str, schema: dict):
        config = {"effort": self.effort}
        if self.structured:
            config["format"] = {"type": "json_schema", "schema": schema}
        else:
            user = (user + "\n\nREPONDS UNIQUEMENT par un objet JSON conforme a ce schema, sans texte "
                    "autour :\n" + json.dumps(schema, ensure_ascii=False))
        return self.client.messages.create(
            model=self.model,
            max_tokens=16000,
            # The large static part (JARVIS description, vocabulary, examples)
            # is identical on every call: cached once, read at $0.20/M
            # afterwards. Nothing volatile precedes it.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config=config,
        )

    @staticmethod
    def _detail(e) -> str:
        """The API's own words — a bare status code explains nothing."""
        body = getattr(e, "body", None)
        msg = (body.get("error", {}).get("message") if isinstance(body, dict) else None) \
            or getattr(e, "message", None) or str(e)
        rid = getattr(getattr(e, "response", None), "headers", {}).get("request-id", "")
        return f"{msg} (request-id {rid})" if rid else str(msg)

    def complete(self, system: str, user: str, schema: dict | None = None) -> dict:
        a = self._anthropic
        schema = schema or OUTPUT_SCHEMA
        for attempt in (1, 2):
            try:
                r = self._call(system, user, schema)
                break
            except a.RateLimitError as e:
                return {"error": "rate_limit", "detail": self._detail(e)}
            except a.BadRequestError as e:
                detail = self._detail(e)
                about_format = any(w in detail.lower() for w in
                                   ("output_config", "format", "json_schema", "structured"))
                if attempt == 1 and self.structured and about_format:
                    self.structured = False
                    self.notes.append(f"sorties structurees refusees par {self.model} : {detail} "
                                      "-> schema dans le prompt")
                    continue
                return {"error": "http_400", "detail": detail}
            except a.APIStatusError as e:
                return {"error": f"http_{e.status_code}", "detail": self._detail(e)}
            except a.APIConnectionError as e:
                return {"error": "network", "detail": str(e)[:300]}
        usage = r.usage.to_dict() if hasattr(r.usage, "to_dict") else dict(r.usage)
        if r.stop_reason == "refusal":
            return {"error": "refusal", "usage": usage, "detail": str(getattr(r, "stop_details", ""))}
        if r.stop_reason == "max_tokens":
            return {"error": "max_tokens", "usage": usage}
        text = next((b.text for b in r.content if b.type == "text"), "")
        if not self.structured:
            text = extract_json(text)
        return {"text": text, "usage": usage, "request_id": getattr(r, "_request_id", None),
                "structured": self.structured}


def extract_json(text: str) -> str:
    """The outermost JSON object in a free-text answer (fences, prose around it).
    Returns the text unchanged when there is none: intake then files it as
    unparseable, which is the honest outcome."""
    start = text.find("{")
    if start < 0:
        return text
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text


class Cassette:
    """Record or replay provider responses, keyed by a hash of the request."""

    def __init__(self, path: Path, inner=None):
        self.path = Path(path)
        self.inner = inner
        self.tape: dict[str, dict] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.tape[rec["key"]] = rec["response"]

    @staticmethod
    def key(system: str, user: str) -> str:
        return hashlib.sha256((system + "\x00" + user).encode("utf-8")).hexdigest()[:24]

    def complete(self, system: str, user: str, schema: dict | None = None) -> dict:
        k = self.key(system, user)
        if k in self.tape:
            return {**self.tape[k], "replayed": True}
        if self.inner is None:
            return {"error": "not_in_cassette"}
        resp = self.inner.complete(system, user, schema) if schema else self.inner.complete(system, user)
        if "text" in resp:                      # only successes are worth replaying
            self.tape[k] = resp
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"key": k, "response": resp}, ensure_ascii=False) + "\n")
        return resp


# ── prompts ──────────────────────────────────────────────────────────────────

def _jarvis_description() -> str:
    """What the model is told about JARVIS: the rules as the code states them."""
    from .space import thresholds
    th = thresholds()
    return f"""\
JARVIS est un assistant vocal 24/7. Tu travailles sur trois couches deterministes.

1. context/situation.py — faits du telephone -> une situation, dans cet ordre :
   DRIVING (activite IN_VEHICLE avec confiance >= {th['vehicle_confidence']:.0f}, ou un nom Bluetooth
   contenant un motif de voiture : car, auto, vehicle, carplay, android auto, sync, uconnect, mylink, carkit)
   -> ASLEEP (heures calmes {th['quiet_start_hour']:.0f}h-{th['quiet_end_hour']:.0f}h ET ecran eteint ET inactif >= {th['asleep_idle_s']:.0f} s)
   -> MEETING (Ne pas deranger, ou sonnerie SILENT) -> ACTIVE (ecran allume ou inactif < {th['active_idle_s']:.0f} s) -> IDLE.
   Donnees telephone de plus de {th['device_ttl_s']:.0f} s = perimees : UNKNOWN, sauf presence locale du poste
   (local_presence_s < {th['active_idle_s']:.0f} -> ACTIVE, sinon IDLE). Route audio : casque -> HEADSET,
   telephone frais -> PHONE, sinon desktop_audio -> DESKTOP, sinon NONE.
2. context/policy.py — priorite x situation -> canal :
             CRITICAL   IMPORTANT      USEFUL         TRIVIAL
   DRIVING   INTERRUPT  VOICE          DEFER          DROP
   MEETING   NOTIFY     NOTIFY_SILENT  DEFER          DROP
   ASLEEP    INTERRUPT  DEFER          DEFER          DROP
   ACTIVE    INTERRUPT  VOICE          NOTIFY_SILENT  DROP
   IDLE      INTERRUPT  NOTIFY         NOTIFY_SILENT  DROP
   UNKNOWN   INTERRUPT  NOTIFY         DEFER          DROP
   Silence apres livraison : IMPORTANT {th['cooldown_important_s']:.0f} s, USEFUL {th['cooldown_useful_s']:.0f} s (-> DEFER).
   VOICE/INTERRUPT sans route -> NOTIFY ; NOTIFY sans route -> DEFER. DEFER va dans une file,
   relue quand la situation change ; la politique est rejouee a la sortie. Promesse : differe != perdu.
   Le check-in proactif de main.py met la CHAINE "proactive" en file ; server/alerts.py vide la file
   et ne notifie que les payloads dict {{"title","text"}}. Les alertes du moniteur sont IMPORTANT.
3. server/targeting.py — quel appareil execute une commande :
   1 EXPLICITE (la phrase nomme un appareil : "sur mon PC", "sur mon telephone", "ici", "l'autre appareil")
     ; hors ligne ou incapable -> refus, jamais de substitution
   2 CAPACITE (un seul appareil en ligne sait faire) 3 ORIGINE (l'appareil qui a parle, s'il sait faire)
   4 AMBIGU -> question. Une origine inconnue n'est jamais choisie par defaut. Un appareil sans signe
   de vie depuis {th['device_stale_s']:.0f} s est hors ligne.
4. server/routing.py ActionRouter — ce qui entoure la decision quand Gemini appelle un outil :
   si AUCUN appareil ne declare l'outil, il s'execute localement comme en V1 ; sinon jamais localement.
   Le modele peut passer target_device (pc/android/here/other) : un indice, valide comme une phrase,
   retire des parametres avant l'action. L'origine et la phrase du tour ne valent que 180 s.
   Un appareil declare mais sans canal de commande ouvert -> refus explique, jamais un autre appareil.
"""


def _format_rules() -> str:
    return f"""\
FORMAT DE SORTIE. Chaque scenario : surface (policy | situation | routing | sequence | router), family (courte),
hypothesis (pourquoi JARVIS pourrait se tromper ici tout en paraissant raisonnable), et quatre CHAINES JSON :

world_json    policy/situation/sequence : {{"time": {{"local": "2026-09-23T14:00:00"}},
              "phone": {{"age_s": 0, "screen_on": true, "idle_seconds": 5, "dnd": false, "headset": true,
              "ringer": "NORMAL|VIBRATE|SILENT|UNKNOWN", "activity": "STILL|WALKING|RUNNING|CYCLING|IN_VEHICLE|UNKNOWN",
              "activity_confidence": 90, "bluetooth_devices": ["..."], "network": "wifi:x", "place": "maison"}},
              "system": {{"desktop_audio": true, "local_presence_s": 120}},
              "delivered": [{{"priority": "IMPORTANT", "ago_s": 60}}], "policy": {{"wake_for_critical": true}}}}
              (omets "phone" pour "aucun telephone" ; age_s null = jamais rapporte)
              routing : {{"devices": [{{"id": "desktop-01", "type": "pc|android|unknown", "caps": ["open_app"],
              "online": true, "last_seen_ago_s": 0}}], "turn": {{"origin": "desktop-01"}}}}
              router : comme routing, plus "channel": true|false par appareil (canal de commande ouvert),
              "turn": {{"origin": "phone-01", "text": "phrase dite", "ago_s": 12}}, "local_tools": ["open_app"]
stimulus_json policy : {{"priority": "CRITICAL|IMPORTANT|USEFUL|TRIVIAL"}} ; situation : {{}} ;
              routing : {{"text": "phrase de l'utilisateur", "capability": "open_app", "model_hint": ""}} ; sequence : {{}}
              router : {{"tool": "open_app", "parameters": {{"app": "chrome"}}, "target_device": ""}}
events_json   sequence seulement, sinon "[]" : operations
              {{"op": "world", "phone": {{...}} ou null, "at": "HH:MM"}} {{"op": "advance", "s": 301}}
              {{"op": "decide", "priority": "IMPORTANT", "payload": "proactive", "push_if_deferred": true,
               "deliver_if_speaks": true}} {{"op": "push", "priority": "USEFUL", "payload": "x"}}
              {{"op": "release"}} {{"op": "alerts", "alerts": ["[MONITOR_ALERT] sujet\\nHeadline: titre"]}}
claim_json    ce que TU penses etre le bon comportement, en chemins de trace :
              policy/situation : {{"situation": "MEETING", "channel": "NOTIFY_SILENT", "speaks": false}}
              routing : {{"kind": "device|clarify|unavailable", "device": "phone-01"}}
              router : {{"executed_on": ["phone-01"]}} (["local"], ou [] pour un refus)
              sequence : {{"steps.3.lost": []}} ; "{{}}" si tu n'affirmes rien.
Vocabulaires fermes : {", ".join(sc.PRIORITIES)} / {", ".join(sc.SITUATIONS)} / {", ".join(sc.CHANNELS)}.
Ta revendication n'est PAS la verite : elle sera confrontee au code et une personne tranchera.
"""


def build_system() -> str:
    return _jarvis_description() + "\n" + _format_rules()


def build_user(mode: str, n: int, examples: list[dict], feedback: dict) -> str:
    ex = "\n".join(sc.dumps({k: s[k] for k in ("surface", "world", "stimulus", "events") if k in s})
                   for s in examples)
    fb = json.dumps(feedback, ensure_ascii=False)
    if mode == "adversary":
        task = (f"Trouve {n} situations ou JARVIS pourrait prendre une MAUVAISE decision tout en semblant "
                "raisonnable. Priorite aux INTERACTIONS entre politique, routage, delivrance et sequences "
                "d'evenements : frontieres de seuils, transitions de situation, reconnexions (telephone qui "
                "revient, appareil perime qui reapparait), files differees relues au mauvais moment, appareils "
                "partiellement disponibles (en ligne sans canal, capacite perdue), pertes silencieuses (un "
                "message qui disparait sans erreur). Prefere les surfaces sequence et router. Chaque scenario "
                "vise une hypothese de bug precise et DIFFERENTE des autres et des causes deja connues "
                "ci-dessous : les redecouvrir ne compte pas.")
    else:
        task = (f"Propose {n} situations realistes et variees de la vie quotidienne de l'utilisateur, en "
                "couvrant ce qui manque d'apres la couverture ci-dessous.")
    return f"{task}\n\nEXEMPLES VALIDES :\n{ex}\n\nETAT DU LABO (couverture, causes connues, rejets) :\n{fb}\n"


# ── from model output to accepted scenarios ──────────────────────────────────

@dataclass
class Intake:
    proposed: int = 0
    unparseable: int = 0
    invalid: int = 0
    duplicate: int = 0
    stale: int = 0              # valid but brings nothing new
    accepted: list = field(default_factory=list)
    reasons: dict = field(default_factory=dict)

    def reject(self, bucket: str, why: str) -> None:
        setattr(self, bucket, getattr(self, bucket) + 1)
        self.reasons[why[:80]] = self.reasons.get(why[:80], 0) + 1


def intake(text: str, *, mode: str, batch: int, cov, seen: set, min_novelty: float = 0.02) -> Intake:
    out = Intake()
    try:
        items = json.loads(text)["scenarios"]
    except (ValueError, KeyError, TypeError) as e:
        out.reject("unparseable", f"sortie illisible : {e}")
        return out
    for i, it in enumerate(items):
        out.proposed += 1
        try:
            world = json.loads(it["world_json"] or "{}")
            stimulus = json.loads(it["stimulus_json"] or "{}")
            events = json.loads(it["events_json"] or "[]")
            claim = json.loads(it["claim_json"] or "{}")
            if not all(isinstance(x, dict) for x in (world, stimulus, claim)) or not isinstance(events, list):
                raise ValueError("types")
        except (ValueError, KeyError, TypeError) as e:
            out.reject("unparseable", f"JSON interne : {e}")
            continue
        s = sc.make(it["surface"], world=world, stimulus=stimulus, events=events,
                    tier=sc.SURFACE_TIER.get(it["surface"], "fast"),
                    expected=claim, oracle="explicit" if claim else "implicit",
                    source="llm" if mode == "generate" else "adversarial",
                    family=f"llm.{mode}.{str(it.get('family') or 'x')[:40]}", difficulty=3,
                    notes=str(it.get("hypothesis") or "")[:500],
                    lineage={"generator": {"strategy": f"llm-{mode}", "model": MODEL, "batch": batch,
                                           "index": i}, "oracle_author": "llm"})
        try:
            sc.validate(s)
        except sc.Invalid as e:
            out.reject("invalid", "; ".join(e.args[0])[:80])
            continue
        if s["id"] in seen:
            out.reject("duplicate", "deja vu")
            continue
        seen.add(s["id"])
        from .coverage import features
        f = features(s)
        novelty = sum(1 for x in f if x not in cov.seen) / max(1, len(f))
        if novelty < min_novelty and mode == "generate":
            out.reject("stale", "rien de nouveau")
            continue
        # Novelty against everything seen when it arrived (the 1 709 + what the
        # model already proposed). Not part of the id: provenance, not content.
        s["lineage"]["generator"]["novelty"] = round(novelty, 4)
        out.accepted.append(s)
    return out


def feedback(cov, known, reasons: dict) -> dict:
    uncovered = [f"{k[1]}/{k[2]}" for k in cov.seen if k[0] == "cell" and len(k) == 4]
    return {
        "cellules_politique_atteintes": len(set(uncovered)),
        "causes_deja_connues": sorted(known)[:20],
        "motifs_de_rejet_frequents": dict(sorted(reasons.items(), key=lambda x: -x[1])[:8]),
    }


def campaign(provider, *, mode: str, batches: int, per_batch: int, seed_corpus: list[dict],
             known, max_usd: float, sink, log=print, transcript: Path | None = None) -> tuple[Usage, Intake]:
    """Ask, validate, run, repeat — within a dollar budget, surviving errors.

    `known`: human-readable descriptions of causes already found, shown to the
    model so it looks elsewhere. `transcript`: every request and response,
    verbatim, one JSON line per batch.
    """
    """Ask, validate, run, repeat — within a dollar budget, surviving errors."""
    import random
    from .coverage import Coverage
    from .runner import run_one
    rng = random.Random(0)
    cov, seen = Coverage(), set()
    for s in seed_corpus:
        cov.add(s)
        seen.add(s["id"])
    usage, total = Usage(), Intake()
    system = build_system()
    consecutive_errors = 0
    worst_call = 0.0           # the most expensive call so far: the next one may cost as much
    for b in range(batches):
        if usage.usd + worst_call > max_usd or usage.usd >= max_usd:
            log(f"  budget : {usage.usd:.2f} $ depenses, un lot de plus pourrait couter {worst_call:.2f} $ "
                f"(plafond {max_usd:.2f} $) — arret propre")
            break
        before = usage.usd
        examples = rng.sample(seed_corpus, min(4, len(seed_corpus)))
        user = build_user(mode, per_batch, examples, feedback(cov, known, total.reasons))
        resp = provider.complete(system, user)
        if "usage" in resp and not resp.get("replayed"):
            usage.add(resp["usage"])
        worst_call = max(worst_call, usage.usd - before)
        if transcript is not None:
            with Path(transcript).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"batch": b, "system_sha": Cassette.key(system, ""), "user": user,
                                     "response": resp.get("text"), "error": resp.get("error"),
                                     "detail": resp.get("detail"), "structured": resp.get("structured"),
                                     "usage": resp.get("usage"), "replayed": bool(resp.get("replayed")),
                                     "request_id": resp.get("request_id")}, ensure_ascii=False) + "\n")
        if "error" in resp:
            usage.failures += 1
            consecutive_errors += 1
            log(f"  lot {b} : {resp['error']} — {resp.get('detail') or 'sans detail'}")
            if consecutive_errors >= 3:
                log("  3 erreurs de suite — arret, rien n'est perdu (cassette + resultats)")
                break
            time.sleep(min(30, 2 ** consecutive_errors))
            continue
        consecutive_errors = 0
        got = intake(resp["text"], mode=mode, batch=b, cov=cov, seen=seen)
        for bucket in ("proposed", "unparseable", "invalid", "duplicate", "stale"):
            setattr(total, bucket, getattr(total, bucket) + getattr(got, bucket))
        for k, v in got.reasons.items():
            total.reasons[k] = total.reasons.get(k, 0) + v
        for s in got.accepted:
            rec = run_one(s)
            cov.add(s, rec)
            total.accepted.append(s)
            sink(s, rec)
        log(f"  lot {b} : {got.proposed} proposes, {len(got.accepted)} acceptes "
            f"({got.invalid} invalides, {got.unparseable} illisibles, {got.duplicate} doublons, "
            f"{got.stale} sans nouveaute) — {usage.usd:.3f} $")
    return usage, total
