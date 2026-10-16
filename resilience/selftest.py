"""
resilience/selftest.py — un modèle en panne ne coupe rien, aucun appel ne pend.

    python -m resilience.selftest

Sans réseau ni clé : un faux `Models` joue les pannes (429, 503, 404, erreur
de requête) et on regarde qui est appelé, avec quel délai.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import resilience as R

_results: list[tuple[str, bool, str]] = []

# Le registre commun des quotas (quota/) dans un dossier jetable : les faux 429
# de ce test ne doivent jamais marquer de vrais modèles comme épuisés (vu le
# 2026-10-16 : memory/quota.json pollué par le selftest).
try:
    import tempfile

    import quota as _Q
    _QTMP = tempfile.mkdtemp(prefix="jarvis-quota-")
    _Q.reset_for_tests(Path(_QTMP) / "quota.json")
except Exception:
    _Q = None


def check(name: str):
    def deco(fn):
        R._cooldown.clear()
        if _Q is not None:
            _Q.reset_for_tests()
            try:
                _Q.PATH.unlink()
            except OSError:
                pass
        try:
            _results.append((name, True, str(fn() or "")))
        except AssertionError as e:
            _results.append((name, False, str(e)))
        except Exception as e:
            _results.append((name, False, f"{type(e).__name__}: {e}"))
        return fn
    return deco


class _Fake:
    """Un `Models` dont chaque modèle a un sort écrit d'avance."""

    def __init__(self, fate: dict[str, str]):
        self.fate, self.calls = fate, []

    def generate_content(self, *, model, contents, config=None):
        self.calls.append((R._bare(model), config))
        err = self.fate.get(R._bare(model))
        if err:
            raise RuntimeError(err)
        return f"ok:{R._bare(model)}"


_Fake.generate_content = R.wrap_sync(_Fake.generate_content)
R._log = lambda line: None


class _AFake(_Fake):
    async def generate_content(self, *, model, contents, config=None):
        return _Fake.generate_content.__wrapped__(self, model=model, contents=contents, config=config)


_AFake.generate_content = R.wrap_async(_AFake.generate_content)


@check("1 tout va bien : un seul appel, au modèle demandé, avec un délai")
def _happy():
    f = _Fake({})
    assert f.generate_content(model="gemini-3.1-flash-lite", contents="x") == "ok:gemini-3.1-flash-lite"
    assert len(f.calls) == 1
    cfg = f.calls[0][1]
    assert cfg["http_options"]["timeout"] == R.DEFAULT_TIMEOUT_MS, cfg


@check("2 quota épuisé : l'échelon suivant répond, le premier se repose")
def _quota():
    f = _Fake({"gemini-3.1-flash-lite": "429 RESOURCE_EXHAUSTED"})
    out = f.generate_content(model="gemini-3.1-flash-lite", contents="x")
    assert out == f"ok:{R.LADDERS[R.FAST][0]}", out
    assert R.resting("gemini-3.1-flash-lite")
    f.calls.clear()
    f.generate_content(model="gemini-3.1-flash-lite", contents="x")
    assert [m for m, _ in f.calls] == [R.LADDERS[R.FAST][0]], "le modèle au repos a été réessayé"


@check("2b quota du jour : repos jusqu'à minuit (Pacifique) ; quota de la minute : 1 min")
def _daily_quota():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    day = ("429 RESOURCE_EXHAUSTED {'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', "
           "'quotaValue': '20'}")
    minute = "429 RESOURCE_EXHAUSTED {'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'}"
    assert R.quota_rest(minute) == 60.0
    assert R.quota_rest("429 RESOURCE_EXHAUSTED") == R.QUOTA_REST_S
    at_22h = datetime(2026, 10, 16, 22, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert abs(R.seconds_until_pacific_midnight(at_22h) - (2 * 3600 + 60)) < 1
    f = _Fake({"gemini-3.5-flash": day})
    f.generate_content(model="gemini-3.5-flash", contents="x")
    t = {d["model"]: d["rest_s"] for d in R.status()}
    assert t["gemini-3.5-flash"] > 15 * 60 or R.seconds_until_pacific_midnight() <= 15 * 60, t
    return f"gemini-3.5-flash au repos {round(t['gemini-3.5-flash'] / 3600, 1)} h (jusqu'à minuit Pacifique)"


@check("3 503 puis 404 : deux repos de durées différentes")
def _rests():
    f = _Fake({"gemini-2.5-flash": "503 UNAVAILABLE", "gemini-3-flash-preview": "404 NOT_FOUND"})
    out = f.generate_content(model="models/gemini-2.5-flash", contents="x")
    assert out == "ok:gemini-3.6-flash", out
    t = {d["model"]: d["rest_s"] for d in R.status()}
    assert 1700 < t["gemini-2.5-flash"] <= R.UNAVAILABLE_REST_S, t
    assert t["gemini-3-flash-preview"] > R.UNAVAILABLE_REST_S, t


@check("4 erreur de requête : remonte telle quelle, aucun autre modèle")
def _bad_request():
    f = _Fake({"gemini-2.5-flash": "400 INVALID_ARGUMENT: bad schema"})
    try:
        f.generate_content(model="gemini-2.5-flash", contents="x")
        raise AssertionError("l'erreur a été avalée")
    except RuntimeError as e:
        assert "INVALID_ARGUMENT" in str(e)
    assert len(f.calls) == 1 and not R.status()


@check("5 tout en panne : la dernière erreur remonte, l'appelant la gère comme avant")
def _all_down():
    f = _Fake({m: "503 UNAVAILABLE" for m in R.LADDERS[R.SMART]})
    try:
        f.generate_content(model="gemini-2.5-flash", contents="x")
        raise AssertionError("aucune erreur")
    except RuntimeError:
        pass
    assert len(f.calls) == len(R.LADDERS[R.SMART])


@check("6 recherche ancrée : échelle REST de recherche, délai de l'appelant respecté")
def _search():
    cfg = {"tools": [{"google_search": {}}], "http_options": {"timeout": 45_000}}
    f = _Fake({"gemini-2.5-flash": "429"})
    assert f.generate_content(model="gemini-2.5-flash", contents="x", config=cfg) == "ok:gemini-3.5-flash"
    assert f.calls[0][1]["http_options"]["timeout"] == 45_000
    assert R.tier_of("gemini-2.5-flash", cfg) == R.SEARCH


@check("7 image ou TTS : pas d'échelle, juste un délai")
def _no_ladder():
    assert R.ladder_for("gemini-3.1-flash-image") == ["gemini-3.1-flash-image"]
    assert R.ladder_for("gemini-3.8-flash-tts") == ["gemini-3.8-flash-tts"]
    f = _Fake({"gemini-3.1-flash-image": "503"})
    try:
        f.generate_content(model="gemini-3.1-flash-image", contents="x")
        raise AssertionError("aucune erreur")
    except RuntimeError:
        assert len(f.calls) == 1


@check("8 image dans la requête : délai long")
def _long():
    f = _Fake({})
    f.generate_content(model="gemini-2.5-flash", contents=["décris", object()])
    assert f.calls[0][1]["http_options"]["timeout"] == R.LONG_TIMEOUT_MS


@check("9 asynchrone : même échelle")
def _async():
    f = _AFake({"gemini-2.5-flash": "429"})
    assert asyncio.run(f.generate_content(model="gemini-2.5-flash", contents="x")) == "ok:gemini-3-flash-preview"


@check("10 config pydantic du SDK : le délai s'ajoute sans perdre le reste")
def _pydantic():
    from google.genai import types
    cfg = types.GenerateContentConfig(temperature=0.2)
    out = R.with_timeout(cfg, 30_000)
    assert out.temperature == 0.2 and out.http_options.timeout == 30_000
    kept = R.with_timeout(types.GenerateContentConfig(http_options=types.HttpOptions(timeout=12_000)), 30_000)
    assert kept.http_options.timeout == 12_000


@check("11 Live : secours sur quota, pas sur clé refusée ni réseau")
def _live():
    main = "models/gemini-3.1-flash-live-preview"
    assert R.live_model(main) == main
    assert not R.note_live_failure(main, "API key not valid")
    assert not R.note_live_failure(main, "TimeoutError getaddrinfo failed")
    assert R.live_model(main) == main
    assert R.note_live_failure(main, "1011 RESOURCE_EXHAUSTED: quota")
    assert R.live_model(main) == R.LIVE_MODELS[1]


@check("12 install() sur le vrai SDK : une fois, idempotent")
def _install():
    from google.genai import models
    assert R.install() and R.install()
    assert getattr(models.Models.generate_content, "__resilience__", False)
    assert getattr(models.AsyncModels.generate_content, "__resilience__", False)
    assert not getattr(models.Models.generate_content.__wrapped__, "__resilience__", False), "double enveloppe"


@check("13 attach : main.py reçoit le choix du modèle Live")
def _attach():
    class J:
        class ui:
            @staticmethod
            def write_log(line):
                pass
    j = J()
    R.attach(j)
    assert j._live_model("models/x") == "models/x" and callable(j._note_live_failure)


def main() -> int:
    ok = sum(1 for _, passed, _ in _results if passed)
    for name, passed, info in _results:
        print(f"  {'OK ' if passed else 'ÉCHEC'} {name}" + (f" — {info}" if info and not passed else ""))
    print(f"resilience : {ok}/{len(_results)}")
    return 0 if ok == len(_results) else 1


if __name__ == "__main__":
    sys.exit(main())
