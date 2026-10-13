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
    assert sorted(importers) == ["main.py", "plugins/cognition_memory.py"], importers
    lines = (BASE_DIR / "main.py").read_text(encoding="utf-8").splitlines()
    uses = [i for i, l in enumerate(lines) if "self._cognition." in l]
    assert len(uses) == 6, f"{len(uses)} usages de self._cognition.* (6 attendus)"
    for i in uses:
        assert lines[i - 1].strip() == "if self._cognition:", f"ligne {i + 1} non gardée"
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
        sys.modules.pop("plugins.cognition_memory", None)
        plugin = importlib.import_module("plugins.cognition_memory")
        out = plugin.run({"query": "x"})
        assert "not available" in out, out
    finally:
        sys.modules.pop("cognition", None)
        sys.modules.update(saved)
    return "JARVIS démarre sans le paquet ; le plugin répond « indisponible »"


# ── 4–5. Schéma et persistance ───────────────────────────────────────────────

@check("4 schéma créé, versionné, FTS5 opérationnel")
def _schema():
    s = Store(TMP / "schema.db")
    assert s.schema_version == 1
    tables = {r["name"] for r in s.query("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("events", "memories", "memories_fts", "memory_links", "constitution_versions"):
        assert t in tables, t
    s.close()
    s = Store(TMP / "schema.db")                  # réouverture : pas de remigration
    assert s.schema_version == 1
    s.close()
    return "user_version=1, 5 tables"


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


@check("7 preuve : ce qui a été dit est reconnu, accents et casse ignorés")
def _evidence():
    said = ["Au fait, ma sœur s'appelle Ayşe et elle a 31 ans", "j'adore les lasagnes"]
    cases = {
        "Ayse": "user_stated",
        "sister Ayse, 31 years old": "user_stated",          # traduit par le modèle
        "loves lasagna": "user_stated",                       # même radical
        "sister Ayse lives in Paris": "model_inferred",       # Paris jamais dit
        "sister Ayse, 34 years old": "model_inferred",        # nombre faux
        "works as a nurse": "model_inferred",                 # rien de dit
    }
    got = {v: policy.source_for_model_save(v, said) for v in cases}
    wrong = {v: g for v, g in got.items() if g[0] != cases[v]}
    assert not wrong, wrong
    return ("traduction, radical et noms propres reconnus ; ville ajoutée, âge faux "
            "et métier inventé -> non vérifiés")


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
    c.observe_tool("web_search", {"query": "x"}, "ok")
    c.on_save_memory("notes", "k", "v")
    c.archive_session("résumé")
    assert c.prompt_block() == ""
    assert "indisponible" in c.recall("x")
    assert c.stats() == {}
    return "6 entrées, 0 exception propagée, valeurs neutres"


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
    c = attach(_J(), db_path=TMP / "attach.db", legacy_path=TMP / "absent.json")
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
