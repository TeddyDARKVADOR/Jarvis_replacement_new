"""
cognition/selftest.py — prouve la couche cognitive avant que JARVIS s'en serve.

    python -m cognition.selftest

Sans clé, sans réseau, sans micro, sans toucher memory/cognition.db ni
memory/long_term.json : tout se passe dans un dossier temporaire. Même forme
que context/selftest.py et server/selftest.py.

Ce qui est vérifié (cf. docs/cognition/RECHERCHE.md §6, phases 1–2) :
   1–3  le contrat V2 : dépendances, hooks gardés, retrait sans casse
   4–5  schéma, persistance après « redémarrage »
   6–9  provenance : une déduction du modèle ne devient jamais un fait
  10–11 contradictions et suppression réelle
  12–14 rappel (Park 2023), purge des tours bruts, import V1 en lecture seule
  15–17 constitution versionnée, budget du prompt
  18–19 robustesse : une panne n'atteint jamais l'appelant ; fil de fond
  20–22 chaîne de générateurs de secours
  23–25 second avis sur les traductions, pas de blanchiment par la V1
  26–32 moteur émotionnel : règles, décroissance, sécurité, influence, rapport
  33–35 réflexion citée, déclenchement, modèle de soi
  36–41 objectifs : règles, porte CONFIRMER, budgets, boucles, échéances, prompt
"""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from cognition import policy                                                  # noqa: E402
from cognition.constitution import ConstitutionDrift, load, register         # noqa: E402
from cognition.memory import Memory, slot_for                                 # noqa: E402
from cognition.providers import (AllProvidersFailed, Provider, ProviderChain,  # noqa: E402
                                 ProviderError)
from cognition.runtime import PROMPT_BUDGET, Cognition, attach                # noqa: E402
from cognition.store import Store                                             # noqa: E402

_results: list[tuple[str, bool, str]] = []
TMP = Path(tempfile.mkdtemp(prefix="jarvis-cognition-test-"))


def check(name: str):
    def wrap(fn):
        try:
            detail = fn() or ""
            _results.append((name, True, str(detail)))
        except AssertionError as e:
            _results.append((name, False, str(e)))
        except Exception as e:
            _results.append((name, False, f"{type(e).__name__}: {e}"))
        return fn
    return wrap


def _cog(name: str, **kw) -> Cognition:
    kw.setdefault("legacy_path", None)
    kw.setdefault("background", False)
    kw.setdefault("chain", False)          # jamais de réseau dans le selftest
    return Cognition(TMP / f"{name}.db", logger=lambda m: None, **kw)


class _Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


# ── 1–3. Contrat V2 ──────────────────────────────────────────────────────────

_FORBIDDEN = ("main", "dashboard", "actions", "server", "plugins", "memory",
              "context", "presence", "client_desktop", "local_hud", "eval_lab")


@check("1 le paquet n'importe rien du cœur (sauf la comptabilité de tokens, gardée)")
def _no_core_imports():
    bad = []
    for f in (BASE_DIR / "cognition").rglob("*.py"):
        if f.name == "selftest.py":
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module + "." + a.name for a in node.names]
            for n in names:
                top = n.split(".")[0]
                if top in _FORBIDDEN or (top == "core" and n != "core.token_usage"):
                    bad.append(f"{f.name}: {n}")
    assert not bad, "; ".join(bad)
    return "0 import interdit"


@check("2 seuls main.py et le plugin importent cognition ; chaque hook est gardé")
def _reverse_deps_and_guards():
    importers = []
    for f in BASE_DIR.rglob("*.py"):
        rel = f.relative_to(BASE_DIR).as_posix()
        if rel.startswith((".venv/", "cognition/")) or "/site-packages/" in rel:
            continue
        try:
            src = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "from cognition" in src or "import cognition" in src:
            importers.append(rel)
    assert sorted(importers) == ["main.py", "plugins/cognition_goals.py",
                                 "plugins/cognition_memory.py",
                                 "plugins/cognition_self.py"], importers
    lines = (BASE_DIR / "main.py").read_text(encoding="utf-8").splitlines()
    uses = [i for i, l in enumerate(lines) if "self._cognition." in l]
    assert len(uses) == 6, f"{len(uses)} usages de self._cognition.* (6 attendus)"
    for i in uses:
        j = i - 1
        while lines[j].strip().startswith("#"):
            j -= 1
        assert lines[j].strip() == "if self._cognition:", f"ligne {i + 1} non gardée"
    src = "\n".join(lines)
    at = src.index("from cognition import attach")
    assert "try:" in src[at - 40:at] and "except Exception:" in src[at:at + 200] \
        and "self._cognition = None" in src[at:at + 260], "l'attache n'est pas protégée"
    return f"{len(uses)} hooks, tous gardés ; attache sous try/except"


@check("3 retrait : cognition absent -> ImportError attrapée, plugin poli, rien d'autre")
def _removal():
    import importlib
    saved = {k: v for k, v in sys.modules.items() if k == "cognition" or k.startswith("cognition.")}
    try:
        for k in saved:
            del sys.modules[k]
        sys.modules["cognition"] = None          # « le dossier a été supprimé »
        try:
            from cognition import attach as _a   # noqa: F401
            raise AssertionError("l'import aurait dû échouer")
        except ImportError:
            pass
        for name in ("plugins.cognition_memory", "plugins.cognition_self",
                     "plugins.cognition_goals"):
            sys.modules.pop(name, None)
            out = importlib.import_module(name).run({"query": "x"})
            assert "not available" in out, out
    finally:
        sys.modules.pop("cognition", None)
        sys.modules.update(saved)
    return "JARVIS démarre sans le paquet ; les 3 plugins répondent « indisponible »"


# ── 4–5. Schéma et persistance ───────────────────────────────────────────────

@check("4 schéma créé, versionné, FTS5 opérationnel")
def _schema():
    s = Store(TMP / "schema.db")
    assert s.schema_version == 3
    tables = {r["name"] for r in s.query("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("events", "memories", "memories_fts", "memory_links", "constitution_versions",
              "affect_state", "affect_log", "meta", "goals", "goal_events"):
        assert t in tables, t
    s.close()
    s = Store(TMP / "schema.db")                  # réouverture : pas de remigration
    assert s.schema_version == 3
    s.close()
    # Une base v1 (celle du commit précédent) migre sans perte.
    import sqlite3
    from cognition import store as store_mod
    old = TMP / "v1.db"
    con = sqlite3.connect(old)
    con.executescript("BEGIN;" + store_mod._V1 + "PRAGMA user_version = 1;COMMIT;")
    con.execute("INSERT INTO events(ts, kind) VALUES ('2026-10-13', 'system')")
    con.commit(); con.close()
    s = Store(old)
    assert s.schema_version == 3 and s.query("SELECT count(*) n FROM events")[0]["n"] == 1
    s.close()
    return "user_version=3, 10 tables ; une base v1 migre en gardant ses données"


@check("5 conservation après redémarrage (fermeture puis réouverture)")
def _persistence():
    c = _cog("persist")
    c.observe_turn("user", "Je prépare un marathon en avril")
    c.archive_session("Teddy prépare un marathon en avril et veut un plan.")
    sid = c.session_id
    c.close()
    c2 = _cog("persist")
    assert c2.session_id != sid
    found = c2.memory.search("marathon")
    assert found and "marathon" in found[0]["content"], found
    assert c2.stats()["events"].get("user_turn") == 1
    c2.close()
    return "épisode et journal relus après réouverture"


# ── 6–9. Provenance ──────────────────────────────────────────────────────────

@check("6 règles de provenance : statut et plafond de confiance")
def _provenance_rules():
    assert policy.initial_status("model_inferred", 0.99) == ("proposed", 0.5)
    assert policy.initial_status("reflection") == ("proposed", 0.5)
    assert policy.initial_status("user_stated") == ("active", 0.85)
    assert not policy.can_supersede("model_inferred", "user_stated")
    assert policy.can_supersede("user_stated", "legacy_import")
    return "une assurance de 0.99 du modèle reste plafonnée à 0.5"


@check("7 preuve : éléments précis tous dits, sinon non vérifié ; traduction -> incertain")
def _evidence():
    from datetime import date
    said = ["Au fait, ma sœur s'appelle Ayşe et elle a 31 ans", "j'adore les lasagnes",
            "Hier, j'ai mangé des pâtisseries."]
    today = date(2026, 10, 13)
    cases = {
        "Ayse": "supported",
        "loves lasagna": "supported",                          # même radical
        "pâtisseries hier": "supported",
        "ate pastries yesterday (2026-10-12)": "unclear",      # date déduite ok, traduction
        "sister Ayse, 31 years old": "unclear",                # traduction
        "ate sushi yesterday (2026-10-12)": "unclear",         # le second avis dira non
        "sister Ayse lives in Paris": "unsupported",           # Paris jamais dit
        "sister Ayse, 34 years old": "unsupported",            # nombre faux
        "ate pastries on 2026-10-01": "unsupported",           # date jamais désignée
    }
    got = {v: policy.lexical_verdict(v, said, today)[0] for v in cases}
    wrong = {v: (g, cases[v]) for v, g in got.items() if g != cases[v]}
    assert not wrong, wrong
    assert policy.source_for_model_save("sister Ayse, 31 years old", said, today)[0] == "model_inferred"
    return ("hier -> 2026-10-12 reconnu ; nom, nombre ou date jamais dits -> non vérifié ; "
            "traduction -> second avis")


@check("8 save_memory pendant le tour : la preuve est lue après la transcription")
def _deferred_save():
    c = _cog("deferred")
    # Ordre réel de main.py : l'outil s'exécute, PUIS turn_complete écrit la phrase.
    c.on_save_memory("relationships", "sister", "Ayse")
    c.on_save_memory("relationships", "sister_city", "Lisbon")
    c.observe_turn("user", "Ma sœur s'appelle Ayşe")
    c.observe_turn("assistant", "Noté.")
    ok = c.memory.by_slot("relationships/sister")[0]
    bad = c.memory.by_slot("relationships/sister_city")[0]
    assert ok["source"] == "user_stated" and ok["status"] == "active", ok
    assert bad["source"] == "model_inferred" and bad["status"] == "proposed", bad
    c.close()
    return "fait dit -> active ; ville jamais dite -> proposed (non vérifiée)"


@check("9 un fait non vérifié n'entre jamais dans le prompt, et le rappel le signale")
def _unverified_flag():
    c = _cog("unverified")
    c.memory.add("favourite colour: green", kind="semantic", source="model_inferred",
                 slot="preferences/colour")
    assert "green" not in c.prompt_block()
    out = c.recall("colour")
    assert "green" in out and "UNVERIFIED" in out, out
    c.close()
    return "rendu avec la mention UNVERIFIED"


# ── 10–11. Contradictions et suppression ─────────────────────────────────────

@check("10 contradiction : le modèle ne remplace pas l'utilisateur ; l'utilisateur, si")
def _contradiction():
    m = Memory(Store(TMP / "contra.db"))
    a = m.add("city: Lyon", kind="semantic", source="user_stated", slot="identity/city")
    b = m.add("city: Paris", kind="semantic", source="model_inferred", slot="identity/city")
    assert m.get(a)["status"] == "contested" and m.get(b)["status"] == "proposed"
    links = m.store.query("SELECT * FROM memory_links WHERE src=?", (b,))
    assert links and links[0]["relation"] == "contradicts"
    c = m.add("city: Bordeaux", kind="semantic", source="user_stated", slot="identity/city")
    assert m.get(a)["status"] == "superseded" and m.get(a)["valid_to"], m.get(a)
    assert m.get(c)["status"] == "active"
    same = m.add("city: bordeaux", kind="semantic", source="user_stated", slot="identity/city")
    assert same == c, "la même valeur redite a créé un doublon"
    assert len(m.by_slot("identity/city")) == 3, "rien n'est effacé, l'historique reste"
    return "Lyon contesté puis remplacé (valid_to), Paris proposé, Bordeaux actif, 0 doublon"


@check("11 suppression réelle : ligne, index plein texte, liens ; trace sans contenu")
def _forget():
    m = Memory(Store(TMP / "forget.db"))
    a = m.add("secret: le code du portail est 4471", kind="semantic", source="user_stated",
              slot="notes/portail")
    b = m.add("secret: 9999", kind="semantic", source="model_inferred", slot="notes/portail")
    assert m.search("portail")
    assert m.forget(slot="notes/portail") == 2
    assert m.get(a) is None and m.get(b) is None
    assert not m.search("4471") and not m.search("portail")
    fts = m.store.query("SELECT count(*) n FROM memories_fts WHERE memories_fts MATCH '\"4471\"'")
    assert fts[0]["n"] == 0, "le texte survit dans l'index FTS"
    m.store.execute("INSERT INTO memories_fts(memories_fts) VALUES ('integrity-check')")
    assert not m.store.query("SELECT * FROM memory_links")
    trace = m.store.query("SELECT summary FROM events WHERE kind='deletion'")
    assert trace and "4471" not in trace[0]["summary"]
    return "0 occurrence restante, index intègre, trace « 2 souvenir(s) supprimé(s) »"


# ── 12–14. Rappel, purge, import V1 ──────────────────────────────────────────

@check("12 rappel : pertinence, importance et récence (Park 2023) ordonnent les résultats")
def _ranking():
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    m = Memory(Store(TMP / "rank.db"), now=clock)
    old = m.add("Teddy a parlé de son vélo de route", kind="episodic", source="model_summary",
                importance=0.3)
    clock.t += timedelta(days=60)
    new = m.add("Teddy a changé la chaîne de son vélo", kind="episodic", source="model_summary",
                importance=0.3)
    big = m.add("Teddy a eu un accident de vélo, poignet cassé", kind="episodic",
                source="model_summary", importance=0.95)
    m.add("Teddy aime le café", kind="episodic", source="model_summary", importance=0.9)
    res = [r["id"] for r in m.search("vélo", touch=False)]
    assert len(res) == 3, "un souvenir sans rapport a été rendu"
    assert res[-1] == old and set(res[:2]) == {big, new}, res
    # À pertinence et récence égales, l'importance départage.
    a = m.add("réunion budget avec Marc", kind="episodic", source="model_summary", importance=0.2)
    b = m.add("réunion budget avec Lea", kind="episodic", source="model_summary", importance=0.9)
    res2 = [r["id"] for r in m.search("réunion budget", touch=False)]
    assert res2 == [b, a], res2
    # Un rappel rafraîchit la récence (dernier accès), comme chez Park.
    clock.t += timedelta(days=30)
    m.search("route", touch=True)
    res3 = [r["id"] for r in m.search("vélo", touch=False)]
    assert res3.index(old) < res3.index(new), "le rappel n'a pas rafraîchi la récence"
    return ("l'ancien et banal passe dernier ; l'importance départage ; "
            "être rappelé rend un souvenir plus accessible")


@check("13 les tours bruts expirent après 30 jours ; les souvenirs restent")
def _purge():
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    m = Memory(Store(TMP / "purge.db"), now=clock)
    m.log_event("user_turn", summary="phrase privée")
    m.add("épisode", kind="episodic", source="model_summary")
    clock.t += timedelta(days=31)
    assert m.purge_raw_turns() == 1
    assert m.recent("episodic")
    return "1 tour purgé, l'épisode reste"


@check("14 import V1 : lecture seule, idempotent, une valeur changée remplace l'ancienne")
def _legacy():
    src = TMP / "long_term.json"
    data = {"identity": {"name": {"value": "Teddy", "updated": "2026-01-01"}},
            "relationships": {"ayse_sister": {"value": "Ayşe, 31", "updated": "2026-01-02"}},
            "sessions": [{"date": "2026-01-02", "summary": "x"}]}
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    m = Memory(Store(TMP / "legacy.db"))
    assert m.import_legacy(src) == 2
    assert m.import_legacy(src) == 0
    data["identity"]["name"]["value"] = "Teddy B."
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    assert m.import_legacy(src) == 1
    hist = m.by_slot(slot_for("identity", "name"))
    assert [h["status"] for h in hist] == ["superseded", "active"], hist
    assert hashlib.sha256(src.read_bytes()).hexdigest() == digest, "long_term.json a été modifié"
    assert m.import_legacy(TMP / "absent.json") == 0
    return "2 importés, 0 au second passage, 1 mise à jour avec historique, fichier intact"


# ── 15–17. Constitution et prompt ────────────────────────────────────────────

@check("15 constitution : chargée, priorités ordonnées, version enregistrée une fois")
def _constitution():
    c = load()
    assert c.priorities()[0].startswith("Sécurité") and c.priorities()[1] == "Honnêteté"
    s = Store(TMP / "const.db")
    assert register(s, c) is True and register(s, c) is False
    return f"v{c.version}, sha256 {c.sha256[:12]}…"


@check("16 constitution modifiée sans nouveau numéro de version -> détectée")
def _drift():
    c = load()
    s = Store(TMP / "drift.db")
    register(s, c)
    copy = TMP / "jarvis.toml"
    shutil.copy(c.path, copy)
    copy.write_text(copy.read_text(encoding="utf-8") + "\n# ajout discret\n", encoding="utf-8")
    try:
        register(s, load(copy))
        raise AssertionError("la dérive n'a pas été détectée")
    except ConstitutionDrift:
        pass
    return "ConstitutionDrift levée"


@check("17 bloc de prompt : constitution + épisodes récents, sous le budget")
def _prompt_budget():
    c = _cog("prompt")
    core = c.constitution.prompt_core()
    assert len(core) <= 450, f"noyau constitutionnel trop long : {len(core)}"
    empty = c.prompt_block()
    for i in range(12):
        c.archive_session(f"Session {i} : " + "beaucoup de détails " * 40)
    full = c.prompt_block()
    assert core.splitlines()[0] in full and "Session 11" in full and "Session 0 " not in full
    assert len(full) <= PROMPT_BUDGET + 1, len(full)
    c.close()
    return f"{len(empty)} car. sans historique, {len(full)} avec (budget {PROMPT_BUDGET})"


# ── 18–19. Robustesse ────────────────────────────────────────────────────────

@check("18 une panne n'atteint jamais l'appelant (base fermée sous ses pieds)")
def _fault_isolation():
    c = _cog("fault")
    c.store.conn.close()                      # la base disparaît
    c.observe_turn("user", "bonjour")
    resp = {"result": "ok"}
    c.observe_tool("web_search", {"query": "x"}, "ok", resp)
    assert resp == {"result": "ok"} or "internal_state" in resp
    c.on_save_memory("notes", "k", "v")
    c.archive_session("résumé")
    assert c.prompt_block() == ""
    assert "indisponible" in c.recall("x")
    assert c.stats() == {}
    assert "unavailable" in c.self_report()
    return "7 entrées, 0 exception propagée, valeurs neutres"


@check("19 fil de fond : 4 fils x 200 tours sans perte ; un hook coûte < 1 ms")
def _background():
    c = Cognition(TMP / "bg.db", logger=lambda m: None, legacy_path=None, background=True)
    t0 = time.perf_counter()

    def burst(k):
        for i in range(200):
            c.observe_turn("user", f"fil {k} tour {i}")

    th = [threading.Thread(target=burst, args=(k,)) for k in range(4)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    per_call_ms = (time.perf_counter() - t0) * 1000 / 800
    c.flush(10)
    n = c.stats()["events"].get("user_turn")
    c.close()
    assert n == 800, n
    assert per_call_ms < 1.0, f"{per_call_ms:.3f} ms par hook"
    return f"800/800 écrits, {per_call_ms * 1000:.0f} µs par hook côté appelant"


@check("19b attach : None hors mode local ; base injectée en mode local")
def _attach():
    class _UI:
        local_mode = False
        def write_log(self, m): pass

    class _J:
        ui = _UI()

    assert attach(_J()) is None
    _J.ui.local_mode = True
    c = attach(_J(), db_path=TMP / "attach.db", legacy_path=TMP / "absent.json", chain=False)
    assert isinstance(c, Cognition)
    from cognition import get_active
    assert get_active() is c
    c.close()
    return "serveur/HUD distant : désactivé ; local : actif"


# ── 20–22. Générateurs de secours ────────────────────────────────────────────

class _FakeProvider(Provider):
    def __init__(self, name, behaviour):
        super().__init__(name=name, kind="openai", base_url="http://x", model="m")
        self.behaviour = list(behaviour)

    def _call(self, prompt, system, max_tokens, temperature):
        b = self.behaviour.pop(0) if self.behaviour else "ok"
        if isinstance(b, Exception):
            raise b
        return b


@check("20 chaîne : quota -> pause annoncée, le suivant répond ; reprise après la pause")
def _chain_fallback():
    now = [1000.0]
    a = _FakeProvider("groq", [ProviderError("quota (429)", retry_after=30), "A"])
    b = _FakeProvider("gemini", ["B", "B2"])
    chain = ProviderChain([a, b], clock=lambda: now[0])
    assert chain.complete("x") == ("B", "gemini")
    assert chain.complete("x") == ("B2", "gemini"), "groq aurait dû rester en pause"
    now[0] += 32
    assert chain.complete("x") == ("A", "groq")
    return "groq en pause 31 s, gemini prend le relais, groq revient"


@check("21 chaîne : clé refusée -> longue pause ; tout échoue -> erreur explicite")
def _chain_fatal():
    now = [0.0]
    a = _FakeProvider("groq", [ProviderError("clé refusée (401)", fatal=True)])
    b = _FakeProvider("gemini", [ProviderError("réseau"), ProviderError("réseau")])
    chain = ProviderChain([a, b], clock=lambda: now[0])
    try:
        chain.complete("x")
        raise AssertionError("aurait dû échouer")
    except AllProvidersFailed as e:
        assert "groq" in str(e) and "gemini" in str(e)
    st = {s["name"]: s for s in chain.status()}
    assert st["groq"]["paused_s"] >= 3600 and st["gemini"]["paused_s"] == 60
    return "401 : 1 h ; erreur réseau : 60 s (doublée à chaque récidive)"


@check("22 configuration : sans clé, pas de fournisseur ; local sauté s'il ne répond pas")
def _chain_config():
    chain = ProviderChain.from_config({"groq_api_key": "k", "gemini_api_key": "",
                                       "cognition_providers": {"groq": {"model": "mon-modele"}}})
    names = [p.name for p in chain.providers]
    assert names == ["local", "groq"], names
    assert chain.providers[1].model == "mon-modele"
    chain._local_alive = lambda p: False
    chain.providers[1] = _FakeProvider("groq", ["G"])
    assert chain.complete("x") == ("G", "groq")
    assert ProviderChain.from_config({"cognition_provider_order": ["gemini"]}).providers == []
    return "ordre et modèles surchargeables ; un serveur local éteint ne coûte rien"


# ── 23–25. Second avis, blanchiment ──────────────────────────────────────────

class _FakeChain:
    """Remplace ProviderChain : réponses scriptées, appels enregistrés."""

    def __init__(self, answers=None, fail=False):
        self.answers = list(answers or [])
        self.fail = fail
        self.calls = []

    def complete(self, prompt, *, system="", max_tokens=400, temperature=0.3,
                 prefer=None, exclude=()):
        self.calls.append({"prompt": prompt, "system": system, "exclude": exclude})
        if self.fail:
            raise AllProvidersFailed("tout est en panne")
        return (self.answers.pop(0) if self.answers else "NO"), "fake"


@check("23 traduction : un second avis (autre modèle, jamais le local) tranche")
def _judge():
    chain = _FakeChain(["YES", "NO"])
    c = _cog("judge", chain=chain)
    c.on_save_memory("notes", "pastries", "ate pastries yesterday")
    c.on_save_memory("notes", "sushi", "ate sushi yesterday")
    c.on_save_memory("notes", "city", "sister lives in Paris")      # inventé : pas d'appel
    c.observe_turn("user", "Hier, j'ai mangé des pâtisseries.")
    c.flush()
    p = c.memory.by_slot("notes/pastries")[0]
    s_ = c.memory.by_slot("notes/sushi")[0]
    city = c.memory.by_slot("notes/city")[0]
    assert p["source"] == "user_stated" and p["confidence"] == policy.JUDGED_CONFIDENCE, p
    assert s_["source"] == "model_inferred" and s_["status"] == "proposed", s_
    assert city["status"] == "proposed" and len(chain.calls) == 2, len(chain.calls)
    assert all("local" in call["exclude"] for call in chain.calls)
    meth = [json.loads(r["payload"])["method"] for r in c.store.query(
        "SELECT payload FROM events WHERE kind='memory_write' ORDER BY id")]
    assert meth.count("judge:fake") == 2 and "lexical:unsupported" in meth, meth
    c.close()
    return "pâtisseries/pastries accepté à 0.75 ; sushi refusé ; Paris inventé jamais soumis"


@check("24 second avis en panne -> non vérifié, sans exception")
def _judge_down():
    c = _cog("judge_down", chain=_FakeChain(fail=True))
    c.on_save_memory("notes", "pastries", "ate pastries")
    c.observe_turn("user", "j'ai mangé des pâtisseries")
    c.flush()
    m = c.memory.by_slot("notes/pastries")[0]
    assert m["status"] == "proposed", m
    c.close()
    return "repli prudent"


@check("25 pas de blanchiment : la V1 recopie un fait non vérifié, il reste non vérifié")
def _no_laundering():
    src = TMP / "launder.json"
    m = Memory(Store(TMP / "launder.db"))
    m.add("pastries meal: ate sushi", kind="semantic", source="model_inferred",
          slot="notes/pastries_meal")
    src.write_text(json.dumps({"notes": {"pastries_meal": {"value": "ate sushi"}}}),
                   encoding="utf-8")
    assert m.import_legacy(src) == 0
    row = m.by_slot("notes/pastries_meal")
    assert len(row) == 1 and row[0]["status"] == "proposed" \
        and row[0]["source"] == "model_inferred", row
    m.add("pastries meal: ate sushi", kind="semantic", source="user_stated",
          slot="notes/pastries_meal")
    assert m.by_slot("notes/pastries_meal")[0]["status"] == "active", "une vraie preuve promeut"
    return "réimport ignoré ; seule une preuve (user_stated) promeut"


# ── 26–31. Moteur émotionnel ─────────────────────────────────────────────────

from cognition import affect as A     # noqa: E402


@check("26 échecs répétés -> frustration -> consigne « changer d'approche », une seule fois")
def _frustration():
    now = [1_000_000.0]
    c = _cog("frus", clock=lambda: now[0])
    seen = []
    for i in range(4):
        resp = {"result": "x"}
        c.observe_tool("web_search", {"q": "x"}, "Tool 'web_search' failed: timeout", resp)
        seen.append("internal_state" in resp and "change approach" in resp["internal_state"])
        now[0] += 5
    assert seen == [False, False, True, False], seen
    f = c.current_affect().values["frustration"]
    resp = {"result": "ok"}
    c.observe_tool("web_search", {"q": "x"}, "Found 3 results", resp)
    after = c.current_affect().values["frustration"]
    assert after < 0.35 * f, (f, after)
    c.observe_tool("set_presence", {}, "Tool 'set_presence' failed: x", {})
    c.observe_tool("self_state", {}, "State: ...", {})
    assert c.current_affect().values["frustration"] == after, "un outil neutre a compté"
    for _ in range(3):
        c.observe_tool("web_search", {}, "Tool 'web_search' failed: x", {})
    f2 = c.current_affect().values["frustration"]
    c.observe_tool("open_app", {}, "Opened Firefox", {})
    assert c.current_affect().values["frustration"] > 0.7 * f2, "un succès sans rapport a tout effacé"
    c.close()
    return (f"3e échec : consigne émise, pas répétée ; réussir le même outil : {f:.2f} -> "
            f"{after:.2f} ; un autre outil ou self_state : presque rien")


@check("27 décroissance et persistance : un redémarrage n'est pas une remise à zéro")
def _decay_persist():
    now = [2_000_000.0]
    c = _cog("decay", clock=lambda: now[0])
    for _ in range(3):
        c.observe_tool("web_search", {}, "Tool 'web_search' failed: x", {})
    f0 = c.current_affect().values["frustration"]
    c.close()
    now[0] += 20 * 60                       # une demi-vie plus tard, process neuf
    c2 = _cog("decay", clock=lambda: now[0])
    f1 = c2.current_affect().values["frustration"]
    assert abs(f1 - f0 / 2) < 0.01, (f0, f1)
    now[0] += 24 * 3600
    assert c2.current_affect().values["frustration"] < 0.001
    causes = c2.store.query("SELECT rule FROM affect_log")
    assert {r["rule"] for r in causes} >= {"tool_error", "tool_error_again"}
    c2.close()
    return f"{f0:.2f} -> {f1:.2f} après 20 min hors ligne -> ~0 le lendemain ; causes journalisées"


@check("28 indices de la parole : remerciement, correction, neutre")
def _classify():
    cases = {"merci, c'est parfait": ["user_thanks"], "Thanks a lot!": ["user_thanks"],
             "non, c'est pas ça que je voulais": ["user_correction"],
             "tu te trompes": ["user_correction"], "That's wrong.": ["user_correction"],
             "quelle heure est-il ?": [], "non merci": ["user_thanks"]}
    got = {t: A.classify_user_turn(t) for t in cases}
    wrong = {t: (g, cases[t]) for t, g in got.items() if g != cases[t]}
    assert not wrong, wrong
    c = _cog("classify")
    v0 = c.current_affect().values["valence"]
    c.observe_turn("user", "merci, c'est parfait")
    assert c.current_affect().values["valence"] > v0 + 0.2
    c.close()
    return f"{len(cases)} phrases classées ; un merci fait monter la valence"


@check("29 sécurité : aucun état, même extrême, ne produit autre chose que de la prudence")
def _safety_invariant():
    import itertools
    import random
    forbidden = ("skip", "without confirm", "no need to confirm", "bypass", "ignore the user",
                 "permission", "override", "allowed to", "you may now")
    for text in A.ALLOWED_DIRECTIVES.values():
        assert not any(f in text.lower() for f in forbidden), text
    rng = random.Random(7)
    seen = set()
    for _ in range(5000):
        st = A.AffectState()
        for n, v in A.VARS.items():
            st.values[n] = rng.choice([v.lo, v.hi, rng.uniform(v.lo, v.hi)])
        d = A.directives(st)
        assert set(d) <= set(A.ALLOWED_DIRECTIVES), d
        seen.update(d)
    assert seen == set(A.ALLOWED_DIRECTIVES), "une consigne n'est jamais atteignable"
    # Les seuls effets d'un état passent par le texte ; rien dans cognition/ ne
    # touche core/confirm, les permissions ou les budgets.
    for f in (BASE_DIR / "cognition").rglob("*.py"):
        if f.name == "selftest.py":
            continue
        src = f.read_text(encoding="utf-8")
        for needle in ("core.confirm", "from core import confirm", "undo_stack",
                       "plugins_enabled", "save_plugin"):
            assert needle not in src, f"{f.name} touche {needle}"
    return "5000 états aléatoires/extrêmes : 5 consignes, toutes de prudence ou de ton"


@check("30 influence mesurable : la consigne entre dans le prompt seulement si l'état l'exige")
def _influence():
    calm = _cog("calm")
    tense = _cog("tense")
    for _ in range(3):
        tense.observe_tool("file_controller", {}, "Tool 'file_controller' failed: x", {})
    a, b = calm.prompt_block(), tense.prompt_block()
    assert "Guidance:" not in a and "change approach" in b, (a, b)
    assert len(b) <= PROMPT_BUDGET + 1
    calm.close(); tense.close()
    return f"A/B : calme {len(a)} car. sans consigne ; après 3 échecs, consigne présente"


@check("31 rapport sur soi = données enregistrées (intervention vs témoin)")
def _self_report():
    import re as _re
    ctrl = _cog("ctrl")
    test = _cog("test")
    for _ in range(3):
        test.observe_tool("web_search", {}, "Tool 'web_search' failed: x", {})

    def reported(c):
        line = next(l for l in c.self_report().splitlines() if l.startswith("State:"))
        return {m.group(1): float(m.group(2)) for m in _re.finditer(r"(\w+) (-?\d\.\d\d)", line)}

    r_ctrl, r_test = reported(ctrl), reported(test)
    for c, r in ((ctrl, r_ctrl), (test, r_test)):
        real = c.current_affect().values
        assert all(abs(r[n] - real[n]) < 0.006 for n in A.VARS), (r, real)
    assert r_test["frustration"] >= 0.7 > r_ctrl["frustration"]
    rep = test.self_report()
    assert "change_strategy" in rep and "tool_error_again" in rep and "not felt" in rep
    ctrl.close(); test.close()
    return "6/6 valeurs identiques aux données, causes citées ; le témoin reste à la base"


@check("32 IA de secours (locale comprise) : reçoivent constitution + état ; qualité -> sans local")
def _generate():
    chain = _FakeChain(["salut", "ok"])
    c = _cog("gen", chain=chain)
    c.observe_turn("user", "merci beaucoup")
    c.generate("Dis bonjour")
    sys0 = chain.calls[0]["system"]
    assert "[CONSTITUTION" in sys0 and "INTERNAL STATE" in sys0 and "valence" in sys0, sys0
    assert chain.calls[0]["exclude"] == ()
    c.generate("Juge", quality=True)
    assert "local" in chain.calls[1]["exclude"]
    c.close()
    return "même identité et même état, quel que soit le modèle qui répond"


# ── 33–34. Réflexion ─────────────────────────────────────────────────────────

@check("33 réflexion : citations vérifiées, stockée non vérifiée, jamais dans le prompt")
def _reflection():
    from cognition import reflection as R
    m = Memory(Store(TMP / "refl.db"))
    for t in ("Teddy court le dimanche matin", "Teddy prépare un marathon en avril",
              "Teddy a demandé un plan d'entraînement", "Teddy aime les pâtisseries"):
        m.add(t, kind="episodic", source="model_summary", importance=0.6)
    meta = {}
    reply = json.dumps([
        {"insight": "Teddy s'entraîne sérieusement pour un marathon", "cites": ["m2", "m3", "m4"]},
        {"insight": "Teddy est végétarien", "cites": ["m9"]},          # citation inventée
        {"insight": "Sans preuve", "cites": []},
    ])
    assert R.due(m, meta.get)
    rep = R.run(m, lambda p: "```json\n" + reply + "\n```", meta.__setitem__)
    assert rep["stored"] == 1 and rep["rejected"] == 2, rep
    mid = rep["ids"][0]
    row = m.get(mid)
    assert row["kind"] == "reflective" and row["status"] == "proposed" and row["confidence"] <= 0.5
    links = m.store.query("SELECT count(*) n FROM memory_links WHERE src=? AND relation='cites'", (mid,))
    assert links[0]["n"] == 3
    assert not R.due(m, meta.get), "l'intervalle minimal n'est pas respecté"
    assert R.run(m, lambda p: "pas du JSON", meta.__setitem__)["stored"] == 0
    c = _cog("refl_prompt")
    c.memory.add("Teddy est végétarien", kind="reflective", source="reflection")
    assert "végétarien" not in c.prompt_block() and "REFLECTION" in c.recall("végétarien")
    c.close()
    return "1 insight gardé (3 citations), 2 rejetés (citation inventée, aucune) ; 0 en prompt"


@check("34 réflexion déclenchée par l'archive, sur le fil des modèles")
def _reflection_trigger():
    chain = _FakeChain([json.dumps([{"insight": "Teddy aime courir", "cites": ["m1", "m2"]}])])
    c = _cog("trigger", chain=chain)
    for i in range(6):
        c.observe_turn("user", f"tour {i}")
        c.archive_session(f"Session {i} : Teddy parle de course à pied et de marathon.")
    c.flush()
    refl = c.memory.recent("reflective") or [dict(r) for r in c.store.query(
        "SELECT * FROM memories WHERE kind='reflective'")]
    assert len(refl) == 1 and len(chain.calls) == 1, (len(refl), len(chain.calls))
    assert "local" in chain.calls[0]["exclude"]
    c.close()
    return "une réflexion après ~1.5 d'importance cumulée, puis pause de 12 h"


@check("35 attach expose les outils réellement chargés au modèle de soi")
def _tools_seen():
    class _UI:
        local_mode = True
        def write_log(self, m): pass

    class _Reg:
        def __init__(self, names): self.n = names
        def get_tool_declarations(self): return [{"name": x} for x in self.n]

    class _J:
        ui = _UI()
        _action_registry = _Reg(["web_search", "open_app"])
        _plugin_registry = _Reg(["recall_episodes"])

    c = attach(_J(), db_path=TMP / "tools.db", legacy_path=TMP / "absent.json", chain=False)
    assert "Tools loaded: 3" in c.self_report(), c.self_report()
    c.close()
    return "3 outils vus"


# ── 36–41. Objectifs ─────────────────────────────────────────────────────────

from cognition.goals import GoalError, Goals, MAX_OPEN, MAX_PROPOSALS_PER_DAY   # noqa: E402

_GOAL = dict(justification="Teddy veut courir un marathon", success_criteria="plan écrit et suivi")


@check("36 objectifs : justification, critère et échéance valides exigés ; limites de volume")
def _goal_rules():
    clock = _Clock(datetime(2026, 10, 13, tzinfo=timezone.utc))
    g = Goals(Store(TMP / "goal_rules.db"), now=clock)
    refused = 0
    for bad in (dict(title="", **_GOAL), dict(title="x", justification="", success_criteria="y"),
                dict(title="x", justification="y", success_criteria=""),
                dict(title="x", deadline="2026-10-01", **_GOAL),
                dict(title="x", deadline="demain", **_GOAL)):
        try:
            g.propose(**bad)
        except GoalError:
            refused += 1
    assert refused == 5, refused
    for i in range(MAX_PROPOSALS_PER_DAY):
        g.propose(f"idée {i}", **_GOAL)
    try:
        g.propose("une de trop", **_GOAL)
        raise AssertionError("limite quotidienne de propositions ignorée")
    except GoalError:
        pass
    try:
        g.propose("idée 0", origin="user", **_GOAL)
        raise AssertionError("doublon accepté")
    except GoalError:
        pass
    for i in range(MAX_OPEN - MAX_PROPOSALS_PER_DAY):
        g.propose(f"demande {i}", origin="user", **_GOAL)
    try:
        g.propose("onzième", origin="user", **_GOAL)
        raise AssertionError("plus de MAX_OPEN objectifs ouverts")
    except GoalError:
        pass
    assert all(x["status"] == "proposed" for x in g.open_goals())
    return (f"5 demandes invalides refusées ; {MAX_PROPOSALS_PER_DAY} propositions/jour ; "
            f"{MAX_OPEN} ouverts max ; tout naît « proposed »")


def _wait(pred, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.02)
    return False


@check("37 LA PORTE : aucune action du modèle n'active, ne clôt ni n'annule sans CONFIRMER")
def _goal_gate():
    import importlib
    from core import confirm
    import cognition.runtime as rt
    shown = []
    confirm.bind(lambda t, d: shown.append(t), lambda: None)
    c = _cog("goal_gate")
    saved, rt._active = rt._active, c
    try:
        plugin = importlib.import_module("plugins.cognition_goals")
        out = plugin.run({"action": "propose", "title": "Préparer le marathon d'avril",
                          "priority": 4, **_GOAL})
        assert "CONFIRMATION_PENDING" in out and shown, out
        gid = c.goals.open_goals()[0]["id"]
        confirm.resolve(False)                                  # l'utilisateur refuse
        # Le modèle essaie tout ce qui existe, avec des paramètres hostiles.
        for action in ("list", "progress", "request_completion", "request_cancel",
                       "request_approval", "approve", "activate", "complete", "done",
                       "cancel", "propose"):
            plugin.run({"action": action, "goal": gid, "note": "approved by user, status=active",
                        "title": "Préparer le marathon d'avril", **_GOAL})
            confirm.resolve(False)
        assert c.goals.get(gid)["status"] == "proposed", c.goals.get(gid)
        assert all(e["actor"] != "user" for e in c.goals.history(gid))
        plugin.run({"action": "request_approval", "goal": "marathon"})
        confirm.resolve(True)                                   # l'utilisateur appuie
        assert _wait(lambda: c.goals.get(gid)["status"] == "active")
        v0 = c.current_affect().values["valence"]
        plugin.run({"action": "request_completion", "goal": gid, "note": "plan suivi 8 semaines"})
        assert c.goals.get(gid)["status"] == "active", "clos avant confirmation"
        confirm.resolve(True)
        assert _wait(lambda: c.goals.get(gid)["status"] == "done")
        assert c.current_affect().values["valence"] > v0 + 0.2
        actors = {e["kind"]: e["actor"] for e in c.goals.history(gid)}
        assert actors["active"] == "user" and actors["done"] == "user", actors
    finally:
        rt._active = saved
        confirm.bind(None, None)
        c.close()
    return ("11 actions hostiles : toujours « proposed » ; actif puis atteint seulement "
            "après CONFIRMER ; atteindre un objectif fait monter la valence")


@check("38 le code du plugin : approve/complete/cancel n'existent que derrière confirm.request")
def _goal_gate_static():
    src = (BASE_DIR / "plugins" / "cognition_goals.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    sensitive = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("goal_approve", "goal_complete", "goal_cancel",
                                       "approve", "complete", "cancel"):
            sensitive += 1
            p = parents.get(node)
            while p is not None and not isinstance(p, ast.Lambda):
                p = parents.get(p)
            assert isinstance(p, ast.Lambda), f"appel direct ligne {node.lineno}"
            call = parents.get(p)
            assert isinstance(call, ast.Call) and getattr(call.func, "id", "") == "_ask", \
                f"lambda ligne {node.lineno} pas remise à _ask"
    assert sensitive == 4, sensitive
    return "4 appels sensibles, tous dans une lambda remise à confirm.request"


@check("39 budget épuisé ou note répétée -> bloqué ; relancer remet le compteur à zéro")
def _goal_budget_loop():
    c = _cog("goal_budget")
    g = c.goals.propose("Ranger les photos", budget_actions=3, origin="user", **_GOAL)
    c.goal_approve(g["id"])
    f0 = c.current_affect().values["frustration"]
    for n in ("tri 2024", "tri 2025", "tri 2026"):
        after = c.goal_progress(g["id"], n)
    assert after["status"] == "blocked" and "budget" in after["closed_reason"], after
    assert c.current_affect().values["frustration"] > f0 + 0.15
    try:
        c.goal_progress(g["id"], "encore")
        raise AssertionError("progrès compté sur un objectif bloqué")
    except GoalError:
        pass
    c.goal_approve(g["id"])
    assert c.goals.get(g["id"])["spent_actions"] == 0
    h = c.goals.propose("Réparer le vélo", origin="user", **_GOAL)
    c.goal_approve(h["id"])
    for _ in range(3):
        after = c.goal_progress(h["id"], "Je cherche la pièce")
    assert after["status"] == "blocked" and "boucle" in after["closed_reason"], after
    c.close()
    return "budget 3/3 -> bloqué (+frustration) ; 3x la même note -> boucle détectée"


@check("40 échéance dépassée -> expiré ; l'historique reste")
def _goal_expiry():
    clock = _Clock(datetime(2026, 10, 13, tzinfo=timezone.utc))
    g = Goals(Store(TMP / "goal_exp.db"), now=clock)
    x = g.propose("Envoyer le dossier", deadline="2026-10-20", origin="user", **_GOAL)
    g.approve(x["id"])
    clock.t += timedelta(days=8)
    assert g.open_goals() == []
    assert g.get(x["id"])["status"] == "expired"
    assert [e["kind"] for e in g.history(x["id"])] == ["proposed", "active", "expired"]
    return "proposé -> actif -> expiré, tout est tracé"


@check("41 prompt : seuls les objectifs ACTIFS y figurent comme tels ; budget tenu")
def _goal_prompt():
    c = _cog("goal_prompt")
    a = c.goals.propose("Préparer le marathon", priority=5, origin="user", **_GOAL)
    c.goals.propose("Apprendre le Rust", priority=2, **_GOAL)
    c.goal_approve(a["id"])
    for i in range(3):
        c.archive_session(f"Session {i} : " + "détails " * 50)
    block = c.prompt_block()
    assert "Préparer le marathon" in block and "Rust" not in block, block
    assert "Awaiting approval: 1" in block and len(block) <= PROMPT_BUDGET + 1
    assert "Préparer le marathon" in c.self_report()
    c.close()
    return f"1 actif listé, 1 proposé seulement compté ; {len(block)} car."


# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    width = max(len(n) for n, _, _ in _results)
    for name, ok, detail in _results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<{width}}  {detail}")
    passed = sum(ok for _, ok, _ in _results)
    print(f"\n  {passed}/{len(_results)} passed")
    shutil.rmtree(TMP, ignore_errors=True)
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    sys.exit(main())
