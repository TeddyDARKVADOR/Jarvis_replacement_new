"""
e2e/server_flows.py — le serveur de bout en bout : un JARVIS, un faux Gemini,
un HUD et un téléphone qui lui parlent comme les vrais.

Un seul JARVIS (python -m server.run_headless, la commande d'Oracle) pour toute
la famille : les scénarios se suivent comme une vraie journée, chacun sur l'état
que le précédent a laissé. Si JARVIS ne démarre pas, les suivants sont comptés
rouges (« pas joué »), pour que le score garde toujours le même total.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from e2e import clients
from e2e.checks import NotApplicable, Report, Skip
from e2e.gemini import FakeGemini, LiveSession, declared_tools, resumption_handle, system_text, user_texts
from e2e.sandbox import FAKE_KEY, Jarvis, Sandbox

REPO = Path(__file__).resolve().parent.parent
#: Les outils dont les scénarios se servent : leur absence de la déclaration est déjà un échec.
NEEDED_TOOLS = {"reminder", "save_memory", "recall_memory", "capabilities", "skills",
                "computer_settings", "open_app", "shutdown_jarvis"}
REMINDER_TEXT = "e2e : arroser le basilic"
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"


@dataclass
class Ctx:
    work: Path
    fake: FakeGemini | None = None
    box: Sandbox | None = None
    jarvis: Jarvis | None = None
    bearer: str = ""
    hud: clients.Listener | None = None
    phone_ws: clients.Listener | None = None
    phone: clients.Listener | None = None
    device: clients.Listener | None = None
    reminder_due: datetime | None = None
    reminder_mark: int = 0
    notes: dict = field(default_factory=dict)

    @property
    def s(self) -> LiveSession:
        """La session Live en cours (la dernière ouverte)."""
        return self.fake.sessions[-1]


def flat(response) -> str:
    """La réponse d'un outil telle que Gemini la reçoit ({"result": …}), en texte."""
    if isinstance(response, dict) and set(response) == {"result"}:
        return str(response["result"])
    return json.dumps(response, ensure_ascii=False)


def _typed(kind: str, **match):
    def pred(m) -> bool:
        return isinstance(m, dict) and m.get("type") == kind and all(
            v in str(m.get(k, "")) for k, v in match.items())
    return pred


def _wait_until(cond, timeout: float, what: str, step: float = 0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = cond()
        if value:
            return value
        time.sleep(step)
    raise AssertionError(f"{what} : toujours pas après {timeout:.0f} s")


# ── les scénarios ────────────────────────────────────────────────────────────

def boot(c: Ctx) -> str:
    c.fake = FakeGemini(c.work / "gemini").start()
    c.box = Sandbox(REPO, c.work / "repo").build()
    c.box.install_shims()
    c.jarvis = Jarvis(c.box, c.fake).start()
    took = c.jarvis.wait_ready(120)
    code, body = clients.http("GET", c.jarvis.url("/health"))
    assert code == 200 and isinstance(body, dict) and body.get("ok") is True, f"/health → {code} {body!r:.80}"
    code, _ = clients.http("GET", c.jarvis.url("/status"))
    assert code == 401, f"/status sans jeton → {code}, attendu 401"
    c.bearer = clients.login(c.jarvis)
    code, snap = clients.http("GET", c.jarvis.url("/status"), token=c.bearer)
    assert code == 200 and isinstance(snap, dict) and "session_live" in snap, f"/status avec jeton → {code}"
    c.hud = clients.Listener(c.jarvis.url("/ws"), c.bearer)
    return f"{c.box.copied} fichiers copiés, /health en {took:.1f} s, 401 sans jeton, 200 avec celui de l'appareil"


def live_config(c: Ctx) -> str:
    s = c.fake.wait_session(1, 60)
    assert s.api_key == FAKE_KEY, "la clé envoyée n'est pas celle de config/api_key.json"
    assert "BidiGenerateContent" in s.path, s.path
    model = str(s.setup.get("model", ""))
    assert "live" in model, f"modèle inattendu : {model}"
    prompt = system_text(s.setup)
    assert "Your name is JARVIS" in prompt and "Testeur" in prompt, "identité absente du prompt système"
    tools = declared_tools(s.setup)
    missing = NEEDED_TOOLS - set(tools)
    assert not missing, f"outils non déclarés : {sorted(missing)}"
    assert len(tools) == len(set(tools)), "un outil est déclaré deux fois"
    assert "sessionResumption" in s.setup, "la reprise de session n'est pas demandée"
    assert resumption_handle(s.setup) is None, "premier démarrage avec un handle de reprise"
    return f"{model.split('/')[-1]}, {len(tools)} outils, prompt de {len(prompt)} caractères, reprise demandée"


def briefing(c: Ctx) -> str:
    text = c.s.wait_for(lambda m: any(re.search(r"greet|briefing|news", t, re.I) for t in user_texts(m)),
                        45, 0, "le briefing du matin")
    first = next(t for t in user_texts(text) if re.search(r"greet|briefing|news", t, re.I))
    return f"« {first[:70]}… »"


def typed_command(c: Ctx) -> str:
    phrase = "e2e : quelle heure est-il à Paris ?"
    mark, hmark = c.s.mark(), c.hud.mark()
    c.hud.send({"type": "command", "text": phrase})
    c.s.wait_text(phrase, 15, mark)
    c.hud.wait_for(_typed("sys", text=phrase), 10, hmark, "la ligne [Web] du journal")
    return "HUD /ws → file de commandes → clientContent, mot pour mot"


PHONE_STATE = {"battery_percent": 80, "battery_charging": False, "screen_on": True, "idle_seconds": 3,
               "headset": False, "ringer": "NORMAL", "dnd": False, "activity": "STILL", "network": "wifi"}


def phone_reports(c: Ctx) -> None:
    """Ce que le client Android envoie sur /ws, et renvoie régulièrement : son état.
    Sans lui, la politique de contexte n'a « aucun client joignable » et diffère la voix."""
    if c.phone_ws is None:
        c.phone_ws = clients.Listener(c.jarvis.url("/ws"), c.bearer)
    c.phone_ws.send({"type": "device_state", "state": PHONE_STATE})


def voice_and_transcript(c: Ctx) -> str:
    phone_reports(c)
    c.phone = clients.Listener(c.jarvis.url("/ws/phone-out"), c.bearer)
    hello = c.phone.wait_for(_typed("audio_format"), 10, 0, "l'annonce du format audio")
    assert hello.get("sample_rate") == 24000, hello
    pmark, hmark = c.phone.mark(), c.hud.mark()
    sent = c.s.say("Il est midi à Paris, monsieur.", 0.6)
    got = _wait_until(lambda: c.phone.bytes_since(pmark) >= sent and c.phone.bytes_since(pmark), 10,
                      f"la voix au téléphone ({c.phone.bytes_since(pmark)} / {sent} octets)")
    c.hud.wait_for(lambda m: _typed("log", text="midi à Paris")(m) and m.get("speaker") == "jarvis",
                   10, hmark, "la transcription au HUD")
    return f"{got} octets de voix sur /ws/phone-out, transcription « jarvis » sur /ws"


def reminder_set(c: Ctx) -> str:
    if os.name == "nt" and not ON_CI:
        raise Skip("créerait une vraie tâche planifiée Windows (schtasks) : joué en CI seulement")
    now = datetime.now()
    due = (now + timedelta(seconds=20)).replace(second=0, microsecond=0) + timedelta(minutes=1)
    c.reminder_mark = c.hud.mark()
    reply = flat(c.s.tool_call("reminder", {"date": f"{due:%Y-%m-%d}", "time": f"{due:%H:%M}",
                                            "message": REMINDER_TEXT}))
    assert "Reminder set" in reply, f"refusé : {reply[:160]}"
    c.reminder_due = due
    tried = [ln for ln in c.box.os_calls() if ln.startswith(("systemd-run", "at "))]
    how = ("aucun planificateur (comme Oracle) → confié à JARVIS" if "no system scheduler" in reply
           else "planifié par le système, et inscrit pour JARVIS")
    return f"pour {due:%H:%M} ({(due - now).seconds} s) : {how}" + (f" ; essayé : {tried[0].split()[0]}" if tried else "")


def memory_roundtrip(c: Ctx) -> str:
    saved = flat(c.s.tool_call("save_memory", {"category": "notes", "key": "e2e_code_cadenas",
                                               "value": "4217"}))
    found = flat(c.s.tool_call("recall_memory", {"query": "cadenas"}))
    assert "4217" in found, f"retenu ? « {saved[:80]} » ; retrouvé : « {found[:120]} »"
    return "save_memory → long_term.json → recall_memory retrouve 4217"


def capabilities_graph(c: Ctx) -> str:
    found = flat(c.s.tool_call("capabilities", {"action": "find", "query": "rappel"}))
    assert "rappel" in found.lower(), f"rien sur les rappels : {found[:120]}"
    hmark = c.hud.mark()
    shown = flat(c.s.tool_call("capabilities", {"action": "show", "graph": "general", "focus": "f.rappels"}))
    ev = c.hud.wait_for(_typed("show_graph"), 10, hmark, "l'événement show_graph pour le HUD")
    assert ev.get("graph") == "general" and ev.get("focus") == "f.rappels", ev
    return f"find trouve les rappels ; show → show_graph(general, f.rappels) sur /ws ({shown[:40]})"


def skills_list(c: Ctx) -> str:
    listed = flat(c.s.tool_call("skills", {"action": "list"}))
    names = sorted(p.parent.name for p in (REPO / "skills").glob("*/SKILL.md"))
    absent = [n for n in names if n not in listed]
    assert names and not absent, f"compétences absentes de la liste : {absent or 'aucune dans skills/'}"
    used = flat(c.s.tool_call("skills", {"action": "use", "name": names[0]}))
    assert len(used) > 200, f"instructions de {names[0]} trop courtes : {used[:120]}"
    return f"{len(names)} compétences listées, {names[0]} chargée ({len(used)} caractères)"


def confirmation_gate(c: Ctx) -> str:
    def wifi_changes() -> list[str]:
        return [ln for ln in c.box.os_calls() if re.fullmatch(r"nmcli radio wifi (on|off)", ln.strip())]

    hmark = c.hud.mark()
    first = flat(c.s.tool_call("computer_settings", {"action": "toggle_wifi"}))
    assert "CONFIRMATION_PENDING" in first, f"pas de porte de confirmation : {first[:160]}"
    banner = c.hud.wait_for(_typed("confirm"), 10, hmark, "le bandeau CONFIRMER")
    cid = banner.get("id")
    assert cid, f"bandeau sans id : {banner}"
    c.hud.send({"type": "confirmation_response", "confirmed": True, "id": "pas-le-bon-id"})
    c.hud.wait_for(_typed("sys", text="no longer matches"), 10, hmark, "le refus d'un mauvais id")
    c.hud.send({"type": "confirmation_response", "confirmed": False, "id": cid})
    c.hud.wait_for(_typed("sys", text="Cancelled"), 10, hmark, "l'annulation")
    time.sleep(0.5)
    assert not wifi_changes(), f"le Wi-Fi a été basculé sans CONFIRMER : {wifi_changes()}"
    if os.name == "nt":
        return "bandeau avec id ; un mauvais id ignoré ; ANNULER n'exécute rien (CONFIRMER non joué : vrai Wi-Fi)"
    hmark = c.hud.mark()
    second = flat(c.s.tool_call("computer_settings", {"action": "toggle_wifi"}))
    assert "CONFIRMATION_PENDING" in second, second[:160]
    banner = c.hud.wait_for(_typed("confirm"), 10, hmark, "le second bandeau")
    c.hud.send({"type": "confirmation_response", "confirmed": True, "id": banner.get("id")})
    c.hud.wait_for(_typed("sys", text="Confirmed"), 10, hmark, "l'exécution après CONFIRMER")
    changed = _wait_until(wifi_changes, 5, "nmcli après CONFIRMER")
    return f"mauvais id ignoré, ANNULER → rien, CONFIRMER → « {changed[0]} » (faux nmcli)"


def routed_to_phone(c: Ctx) -> str:
    code, body = clients.http("POST", c.jarvis.url("/api/device-register"),
                              {"device_id": "e2e-phone", "device_type": "android", "display_name": "Téléphone e2e",
                               "capabilities": ["open_app"], "protocol_version": 1}, token=c.bearer)
    assert code == 200 and isinstance(body, dict) and body.get("ok"), f"/api/device-register → {code} {body!r:.80}"
    c.device = clients.Listener(c.jarvis.url("/ws/device"), c.bearer, "&device_id=e2e-phone")
    phrase = "e2e : ouvre la calculatrice"
    mark = c.s.mark()
    c.device.send({"type": "command", "text": phrase})
    c.s.wait_text(phrase, 15, mark)
    result: dict = {}

    def model_calls_tool() -> None:
        try:
            result["r"] = c.s.tool_call("open_app", {"app_name": "Calculatrice"}, 40)
        except Exception as e:
            result["e"] = e

    t = threading.Thread(target=model_calls_tool, daemon=True)
    t.start()
    cmd = c.device.wait_for(_typed("device_command"), 15, 0, "la commande routée vers le téléphone")
    assert cmd.get("action") == "open_app" and (cmd.get("parameters") or {}).get("app_name") == "Calculatrice", cmd
    c.device.send({"type": "device_result", "id": cmd["id"], "result": "Calculatrice ouverte sur le téléphone e2e"})
    t.join(40)
    if "e" in result:
        raise result["e"]
    reply = flat(result.get("r"))
    assert "Calculatrice ouverte sur le téléphone e2e" in reply, f"Gemini a reçu : {reply[:160]}"
    return "commande du téléphone → open_app routé vers lui → son résultat rendu à Gemini"


def interrupt(c: Ctx) -> str:
    pmark, hmark = c.phone.mark(), c.hud.mark()
    first = c.s.voice(1.0)
    _wait_until(lambda: c.phone.bytes_since(pmark) >= first, 10, "le début de la réponse au téléphone")
    c.hud.send({"type": "interrupt"})
    c.hud.wait_for(_typed("sys", text="Interrupted"), 10, hmark, "l'interruption")
    after = c.phone.mark()
    rest = c.s.voice(2.0)
    time.sleep(1.5)
    leaked = c.phone.bytes_since(after)
    assert leaked == 0, f"{leaked} octets de la réponse interrompue joués quand même"
    c.s.end_turn()
    again = c.phone.mark()
    c.s.say("Je vous écoute.", 0.3)
    _wait_until(lambda: c.phone.bytes_since(again), 10, "la voix du tour suivant")
    return f"{first} octets joués, INTERRUPT, puis 0 des {rest} suivants ; le tour d'après est joué"


def reminder_delivered(c: Ctx) -> str:
    if c.reminder_due is None:
        raise Skip("pas de rappel posé (scénario précédent)")
    wait = max(5.0, (c.reminder_due - datetime.now()).total_seconds() + 25)
    phone_reports(c)                    # le téléphone renvoie son état, comme toutes les minutes
    card = c.hud.wait_for(lambda m: _typed("content", title="RAPPEL")(m) and REMINDER_TEXT in str(m.get("text")),
                          wait, c.reminder_mark, "la carte RAPPEL")
    late = (datetime.now() - c.reminder_due).total_seconds()
    c.hud.wait_for(lambda m: _typed("notification")(m) and REMINDER_TEXT in json.dumps(m, ensure_ascii=False),
                   10, c.reminder_mark, "la notification du téléphone")
    said = _wait_until(lambda: any("[REMINDER]" in t and REMINDER_TEXT in t
                                   for s in c.fake.sessions for m in s.inbox for t in user_texts(m)),
                       10, "la consigne vocale envoyée à Gemini")
    line = c.hud.wait_for(_typed("sys", text="Rappel livré"), 10, c.reminder_mark, "le journal de livraison")
    assert card and said
    return f"livré {late:.0f} s après l'heure : {line.get('text', '')[11:60]}"


def rotation(c: Ctx) -> str:
    s1 = c.s
    s1.resumable("e2e-handle-1")
    _wait_until(lambda: (clients.http("GET", c.jarvis.url("/status"), token=c.bearer)[1] or {}).get("session_resumable"),
                10, "/status session_resumable")
    hmark = c.hud.mark()
    count = len(c.fake.sessions)
    s1.go_away("1s")
    s2 = c.fake.wait_session(count + 1, 20)
    assert resumption_handle(s2.setup) == "e2e-handle-1", f"handle renvoyé : {resumption_handle(s2.setup)!r}"
    c.hud.wait_for(_typed("sys", text="conversation restored"), 10, hmark, "« conversation restored »")
    return "goAway → nouvelle session avec le handle e2e-handle-1 → « conversation restored »"


def crash_recovery(c: Ctx) -> str:
    count = len(c.fake.sessions)
    t0 = time.monotonic()
    c.s.close(1011, "e2e: internal error")
    s = c.fake.wait_session(count + 1, 45)
    handle = resumption_handle(s.setup)
    assert handle == "e2e-handle-1", f"la conversation est perdue : reconnecté avec le handle {handle!r}"
    return f"fermée en 1011, rouverte seule en {time.monotonic() - t0:.1f} s, conversation gardée (handle repris)"


def status_truth(c: Ctx) -> str:
    code, snap = clients.http("GET", c.jarvis.url("/status"), token=c.bearer)
    assert code == 200 and isinstance(snap, dict), code
    assert snap.get("session_live") is True, f"session_live = {snap.get('session_live')}"
    devices = [d.get("device_id") for d in snap.get("devices") or []]
    assert "e2e-phone" in devices, f"appareils : {devices}"
    tools = snap.get("tools") or {}
    assert (tools.get("actions") or 0) > 0, f"outils : {tools}"
    assert snap.get("session_resumable") is True, "reprise non armée"
    leaked = [k for k in ("gemini_api_key", FAKE_KEY, "device_token") if k in json.dumps(snap)]
    assert not leaked, f"/status laisse fuir : {leaked}"
    return f"session en ligne, {tools.get('actions')} actions, appareil e2e-phone, reprise armée, aucun secret"


def goodbye(c: Ctx) -> str:
    mark = c.s.mark()
    reply = flat(c.s.tool_call("shutdown_jarvis", {}))
    c.s.wait_for(lambda m: any("goodbye" in t.lower() for t in user_texts(m)), 15, mark, "l'au revoir")
    code = c.jarvis.proc.wait(20)
    assert code == 0, f"code de sortie {code}"
    return f"« {reply[:40]} » → au revoir demandé à Gemini → sortie 0"


def sigterm(c: Ctx) -> str:
    if os.name == "nt":
        raise NotApplicable("pas de SIGTERM ni de systemd sous Windows")
    j = Jarvis(c.box, c.fake, name="jarvis-sigterm").start()
    count = len(c.fake.sessions)
    j.wait_ready(120)
    c.fake.wait_session(count + 1, 60)
    time.sleep(1.0)
    t0 = time.monotonic()
    code = j.stop(20)
    took = time.monotonic() - t0
    after = len(c.fake.sessions)
    time.sleep(1.0)
    assert code == 0, f"code de sortie {code} après SIGTERM ({took:.1f} s) :\n{j.log_tail(8)}"
    assert took < 10, f"{took:.1f} s pour s'arrêter (systemd attend 20 s puis tue)"
    assert len(c.fake.sessions) == after == count + 1, "JARVIS s'est reconnecté après SIGTERM"
    return f"arrêté en {took:.1f} s, code 0, sans se reconnecter"


SCENARIOS = [
    (1, "démarrage comme sur Oracle : /health sans jeton, /status avec celui d'un appareil appairé", boot, True),
    (2, "la session Live s'ouvre avec la vraie config : clé, modèle, identité, outils, reprise", live_config, True),
    (3, "le briefing du premier démarrage part vers Gemini", briefing, False),
    (4, "une commande tapée dans le HUD arrive à Gemini, mot pour mot", typed_command, False),
    (5, "la voix de Gemini arrive au téléphone, sa transcription au HUD", voice_and_transcript, False),
    (6, "un rappel posé par Gemini est accepté, même sans planificateur système (Oracle)", reminder_set, False),
    (7, "save_memory puis recall_memory : JARVIS retient et retrouve", memory_roundtrip, False),
    (8, "capabilities : JARVIS lit son graphe, et « show » ouvre le panneau GRAPHE du HUD", capabilities_graph, False),
    (9, "skills : les compétences sont listées et chargées", skills_list, False),
    (10, "une action irréversible attend CONFIRMER : mauvais id ignoré, ANNULER n'exécute rien", confirmation_gate, False),
    (11, "une commande du téléphone s'exécute sur le téléphone (routage)", routed_to_phone, False),
    (12, "INTERRUPT coupe la réponse en cours, le tour suivant est joué", interrupt, False),
    (13, "le rappel arrive à l'heure : carte RAPPEL, notification, consigne vocale", reminder_delivered, False),
    (14, "Gemini annonce la fin de session : JARVIS se reconnecte et reprend la conversation", rotation, False),
    (15, "Gemini coupe en erreur (1011) : JARVIS revient seul et garde la conversation", crash_recovery, False),
    (16, "/status dit la vérité et ne laisse fuir aucun secret", status_truth, False),
    (17, "« au revoir » : JARVIS le fait dire à Gemini et s'arrête (code 0)", goodbye, False),
    (18, "SIGTERM (systemctl stop) arrête JARVIS vite, sans se reconnecter", sigterm, False),
]


def run(report: Report, work: Path) -> Ctx:
    c = Ctx(work=work)
    broken = ""
    try:
        for cid, label, fn, blocking in SCENARIOS:
            if broken:
                report.not_run(str(cid), label, broken)
                continue
            ok = report.run(str(cid), label, lambda fn=fn: fn(c))
            if not ok and blocking:
                broken = f"le scénario {cid} a échoué"
    finally:
        for listener in (c.hud, c.phone_ws, c.phone, c.device):
            if listener is not None:
                listener.close()
        if c.jarvis is not None and c.jarvis.alive():
            c.jarvis.stop(20)
        if c.fake is not None:
            c.fake.stop()
    return c
