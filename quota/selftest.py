"""
quota/selftest.py — le registre commun des quotas, sans réseau.

    python -m quota.selftest

Le registre est mis dans un dossier jetable : memory/quota.json n'est jamais touché.
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import quota as Q

ROOT = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="jarvis-quota-"))
_results: list[tuple[str, bool, str]] = []

DAY_429 = ("429 RESOURCE_EXHAUSTED. {'error': {'details': [{'violations': [{'quotaId': "
           "'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaValue': '20'}]}]}}")
MIN_429 = ('429 {"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", '
           '"quotaValue": "15"}]}, {"retryDelay": "37s"}]}}')


def check(name: str):
    def deco(fn):
        Q.reset_for_tests(TMP / f"{len(_results)}.json")
        try:
            _results.append((name, True, str(fn() or "")))
        except AssertionError as e:
            _results.append((name, False, str(e)))
        except Exception as e:
            _results.append((name, False, f"{type(e).__name__}: {e}"))
        return fn
    return deco


@check("1 contrat V2 : bibliothèque standard seulement, rien de JARVIS")
def _contract():
    banned = ("main", "core", "actions", "plugins", "cognition", "server", "dashboard", "client_desktop", "memory",
              "resilience", "pilot", "meeting")
    for f in ROOT.glob("*.py"):
        for node in ast.parse(f.read_text(encoding="utf-8")).body:
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not [n for n in names if n.split(".")[0] in banned], (f.name, names)
    return "aucun import de JARVIS"


@check("2 lecture d'un 429 : quota du jour ou de la minute, limite, délai")
def _parse():
    assert Q.parse(DAY_429) == ("day", 20, None)
    assert Q.parse(MIN_429) == ("minute", 15, 37.0)
    assert Q.parse("429 RESOURCE_EXHAUSTED") == ("quota", None, None)
    assert Q.parse("503 UNAVAILABLE") == (None, None, None)
    return "PerDay/20, PerMinute/15 + retryDelay 37 s, 429 nu, 503 ignoré"


@check("3 quota du jour : épuisé jusqu'à minuit (Pacifique), limite apprise, écrit sur disque")
def _day():
    assert Q.allowed("gemini-2.5-flash")
    assert Q.note_error("models/gemini-2.5-flash", DAY_429) == "day"
    assert not Q.allowed("gemini-2.5-flash")
    assert Q.limits("gemini-2.5-flash") == (None, 20), "limite apprise du 429"
    data = json.loads(Q.PATH.read_text(encoding="utf-8"))
    assert data["rest"]["gemini-2.5-flash"] == Q.next_reset()
    assert Q.order(["gemini-2.5-flash", "gemini-2.5-flash-lite"]) == ["gemini-2.5-flash-lite"]
    return "écarté jusqu'à minuit Pacifique ; ordre de l'appelant gardé pour les autres"


@check("4 limites connues : 20 requêtes du jour → le modèle s'écarte AVANT le refus de Google")
def _proactive():
    for _ in range(20):
        assert Q.allowed("gemini-3.5-flash")
        Q.note_ok("gemini-3.5-flash")
        Q._minute["gemini-3.5-flash"].clear()      # on teste le jour, pas la minute
    assert not Q.allowed("gemini-3.5-flash"), "la 21e n'est pas tentée"
    for _ in range(15):
        Q.note_ok("gemini-3.5-flash-lite")
    assert not Q.allowed("gemini-3.5-flash-lite"), "15 par minute : la 16e attend"
    Q._minute["gemini-3.5-flash-lite"].clear()
    assert Q.allowed("gemini-3.5-flash-lite")
    return "21e requête du Flash à 20/jour évitée ; 16e de la minute évitée"


@check("5 minuit passé : les compteurs du jour repartent, les repos échus disparaissent")
def _new_day():
    Q.note_ok("gemini-2.5-flash-lite")
    st = Q._load()
    st["day"] = "2000-01-01"
    st["rest"]["old"] = time.time() - 10
    st["rest"]["still"] = time.time() + 3600
    st = Q._load()
    assert st["count"] == {} and "old" not in st["rest"] and "still" in st["rest"]
    return "compteurs remis à zéro ; repos encore valables gardés"


@check("6 tableau et phrase : requêtes du jour, limite, repos, heure de remise à zéro")
def _summary():
    Q.note_ok("gemini-3.5-flash-lite")
    Q.note_error("gemini-3.5-flash", DAY_429)
    text = Q.summary()
    assert "gemini-3.5-flash-lite : 1/500" in text and "épuisé jusqu'à" in text and "Remise à zéro" in text, text
    rows = {r["model"]: r for r in Q.status()}
    assert rows["gemini-3.5-flash-lite"]["left"] == 499
    return text.splitlines()[0]


@check("7 le vrai registre n'est pas touché par les selftests")
def _isolated():
    real = ROOT.parent / "memory" / "quota.json"
    assert Q.PATH != real and str(TMP) in str(Q.PATH)
    return f"registre de test : {Q.PATH.parent.name}/"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    width = max(len(n) for n, _, _ in _results)
    failed = 0
    print()
    for name, ok, detail in _results:
        failed += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<{width}}  {detail}")
    print(f"\n  {len(_results) - failed}/{len(_results)} passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
