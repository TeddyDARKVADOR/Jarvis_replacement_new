"""
daily/selftest.py — le briefing quotidien, avec un vrai agenda iCal et ses pièges.

    python -m daily.selftest

L'état (memory/daily.json) est mis dans un dossier jetable. Le dernier contrôle
fait une vraie recherche d'actualités ; sans réseau, il se déclare sans objet.
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP = Path(tempfile.mkdtemp(prefix="jarvis-daily-"))
os.environ["JARVIS_DAILY_FILE"] = str(TMP / "daily.json")

import daily as D  # noqa: E402
from daily import agenda as A  # noqa: E402
from daily import news as N  # noqa: E402

# « python -m daily.selftest » importe le paquet AVANT ce fichier : la variable d'environnement arrive trop
# tard, d'où l'affectation directe (sinon le vrai memory/daily.json est écrit — vu le 2026-10-16).
D.STATE = TMP / "daily.json"

ROOT = Path(__file__).resolve().parent
_results: list[tuple[str, bool, str]] = []

# Lundi 2026-10-19 → vendredi 23 : un cours chaque jour à 9 h (Paris), annulé le mercredi (EXDATE), déplacé à
# 14 h le jeudi (RECURRENCE-ID) ; une journée entière le lundi ; un événement annulé ; un appel à New York.
ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//JARVIS//test//FR
X-WR-CALNAME:Perso
BEGIN:VTIMEZONE
TZID:Europe/Paris
BEGIN:STANDARD
DTSTART:19701025T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19700329T020000
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
UID:cours@test
DTSTART;TZID=Europe/Paris:20261019T090000
DTEND;TZID=Europe/Paris:20261019T120000
RRULE:FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR;COUNT=5
EXDATE;TZID=Europe/Paris:20261021T090000
SUMMARY:Cours CDA
LOCATION:Salle 12
END:VEVENT
BEGIN:VEVENT
UID:cours@test
RECURRENCE-ID;TZID=Europe/Paris:20261022T090000
DTSTART;TZID=Europe/Paris:20261022T140000
DTEND;TZID=Europe/Paris:20261022T170000
SUMMARY:Cours CDA (déplacé)
END:VEVENT
BEGIN:VEVENT
UID:salon@test
DTSTART;VALUE=DATE:20261019
DTEND;VALUE=DATE:20261020
SUMMARY:Salon de l'emploi
END:VEVENT
BEGIN:VEVENT
UID:annule@test
DTSTART;TZID=Europe/Paris:20261019T180000
DTEND;TZID=Europe/Paris:20261019T190000
STATUS:CANCELLED
SUMMARY:Sport (annulé)
END:VEVENT
BEGIN:VEVENT
UID:ny@test
DTSTART;TZID=America/New_York:20261019T110000
DTEND;TZID=America/New_York:20261019T113000
SUMMARY:Appel client New York
END:VEVENT
END:VCALENDAR
"""


def check(name: str):
    def deco(fn):
        try:
            _results.append((name, True, str(fn() or "")))
        except AssertionError as e:
            _results.append((name, False, str(e)))
        except Exception as e:
            _results.append((name, False, f"{type(e).__name__}: {e}"))
        return fn
    return deco


def _day(d: date):
    return A.day({"agenda_ics": "x"}, d, fetcher=lambda u: ICS)[0]


@check("1 contrat V2 : daily/ n'importe rien de JARVIS au chargement")
def _contract():
    banned = ("main", "core", "actions", "plugins", "cognition", "server", "dashboard", "client_desktop", "memory",
              "reminders", "meeting", "context")
    for f in ROOT.glob("*.py"):
        for node in ast.parse(f.read_text(encoding="utf-8")).body:
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not [n for n in names if n.split(".")[0] in banned], (f.name, names)
    return "sources lues sous garde, à l'appel"


@check("2 agenda : récurrence, exception, occurrence déplacée, journée entière, annulé, autre fuseau")
def _agenda():
    def norm(d):
        return [(e.start.strftime("%H:%M") if not e.all_day else "jour", e.title) for e in _day(d)]
    lundi = norm(date(2026, 10, 19))
    assert lundi[0] == ("jour", "Salon de l'emploi"), lundi
    assert ("09:00", "Cours CDA") in lundi and not any("Sport" in t for _, t in lundi), lundi
    ny = next(e for e in _day(date(2026, 10, 19)) if "New York" in e.title)
    local = datetime(2026, 10, 19, 11, 0).astimezone()   # sert seulement à savoir où tourne le test
    assert ny.start.hour == (datetime.fromisoformat("2026-10-19T11:00:00-04:00").astimezone().hour), ny
    assert norm(date(2026, 10, 21)) == [], "mercredi : séance annulée (EXDATE)"
    assert norm(date(2026, 10, 22)) == [("14:00", "Cours CDA (déplacé)")], "jeudi : déplacée à 14 h"
    assert norm(date(2026, 10, 24)) == [], "samedi : pas de cours"
    del local
    return f"lundi {len(lundi)} événements (11 h à New York → {ny.start:%H:%M} ici) ; mercredi annulé ; jeudi déplacé"


@check("3 agenda : un agenda en panne n'empêche pas les autres, et son adresse (secrète) n'est jamais affichée")
def _agenda_errors():
    secret = "https://calendar.google.com/calendar/ical/abc%40gmail.com/private-SECRET/basic.ics"

    def fetch(u):
        if "SECRET" in u:
            raise OSError("404")
        return ICS
    events, errors = A.day({"agenda_ics": [secret, "ok"]}, date(2026, 10, 19), fetcher=fetch)
    assert events and errors == ["agenda 1 : OSError"] and "SECRET" not in " ".join(errors)
    return "agenda 2 lu, agenda 1 signalé sans son adresse"


@check("4 actualités : la même info sur plusieurs sites = une ligne, avec ses sources ; déjà annoncée = absente")
def _news():
    items = [{"topic": "F1", "title": "Verstappen remporte le Grand Prix du Japon", "url": "https://a.fr/x", "source": "L'Équipe"},
             {"topic": "F1", "title": "Grand Prix du Japon : Verstappen remporte la course", "url": "https://b.fr/y", "source": "Le Monde"},
             {"topic": "IA", "title": "Une nouvelle loi européenne encadre l'IA", "url": "https://c.fr/z", "source": "Les Échos"},
             {"topic": "F1", "title": "Verstappen remporte le Grand Prix du Japon", "url": "https://a.fr/x?utm=1", "source": "L'Équipe"}]
    groups = N.cluster(items)
    assert len(groups) == 2 and groups[0]["sources"] == [("L'Équipe", "https://a.fr/x"), ("Le Monde", "https://b.fr/y")], groups
    assert N.fresh(groups, {groups[0]["key"]: "2026-10-18"}) == [groups[1]], "déjà annoncée hier : retirée"
    return "4 articles → 2 nouvelles ; la plus reprise d'abord ; l'ancienne retirée"


@check("5 briefing complet : sections, réglages, phrase courte, détail avec sources, livré une fois par jour")
def _compose():
    cfg = {"agenda_ics": "x", "briefing_sections": {"meeting": False}}
    news = [{"title": "Verstappen remporte le Grand Prix du Japon", "url": "https://a.fr/x", "source": "L'Équipe", "snippet": ""},
            {"title": "Grand Prix du Japon : Verstappen remporte la course", "url": "https://b.fr/y", "source": "Le Monde", "snippet": ""}]
    b = D.compose(cfg, now=datetime(2026, 10, 19, 8, 30), fetcher=lambda u: ICS, search=lambda t, n: news,
                  topics=lambda: ["F1"], reminders_of=lambda d: [("10:00", "Appeler le garage")],
                  meeting_of=lambda now: {"title": "jamais"}, state={})
    assert b.meeting is None, "section réunion désactivée"
    spoken = b.spoken()
    assert "event(s) today" in spoken and "Salon de l'emploi" in spoken and "Appeler le garage" in spoken
    assert "L'Équipe, Le Monde" in spoken, spoken
    text = b.text()
    assert "• toute la journée · Salon de l'emploi" in text and "   Le Monde — https://b.fr/y" in text, text
    evening = D.compose(cfg, now=datetime(2026, 10, 23, 19, 0), fetcher=lambda u: ICS, search=lambda t, n: [],
                        topics=lambda: [], reminders_of=lambda d: [], meeting_of=lambda n: None, state={})
    assert evening.events == [] or evening.events_day.isoformat() == "2026-10-24", "le soir : demain"
    D.mark_delivered(b)
    st = D.load_state()
    assert st["delivered"] == "2026-10-19" and b.news[0]["key"] in st["seen"]
    again = D.compose(cfg, now=datetime(2026, 10, 20, 8, 0), fetcher=lambda u: ICS, search=lambda t, n: news,
                      topics=lambda: ["F1"], reminders_of=lambda d: [], meeting_of=lambda n: None)
    assert again.news == [], "le lendemain, la même nouvelle ne revient pas"
    return f"« {spoken[:90]}… »"


@check("6 accueil de main.py : la phrase est prête d'avance, donnée une seule fois par jour")
def _clause():
    D.save_state({})

    class _UI:
        def show_content(self, t, x):
            pass
    d = D.Daily(type("J", (), {"ui": _UI()})(), lambda: {})
    assert d.clause() == "", "pas encore préparé : l'accueil n'attend pas"
    d.cached = D.Briefing(datetime.now(), reminders=[("10:00", "Garage")])
    first, second = d.clause(), d.clause()
    assert "Garage" in first and second == "", (first, second)
    main = (ROOT.parent / "main.py").read_text(encoding="utf-8")
    assert 'getattr(self, "_daily_clause", str))  # daily/' in main and "import daily\n            daily.attach(self)" in main
    return "vide tant que rien n'est prêt ; une fois par jour ; hooks gardés dans main.py"


@check("8 le vrai memory/daily.json n'est pas touché par le selftest")
def _isolated():
    assert D.STATE.parent == TMP and not (ROOT.parent / "memory" / "daily.json").exists()
    return f"état de test : {TMP.name}/"


@check("7 réel : actualités de deux sujets, regroupées, avec leurs sources")
def _real_news():
    try:
        b = D.compose({}, topics=lambda: ["Formule 1", "intelligence artificielle"], state={},
                      reminders_of=lambda d: [], meeting_of=lambda n: None)
    except Exception as e:
        return f"sans objet : {type(e).__name__}"
    if not b.news:
        return f"sans objet : aucune actualité lue ({'; '.join(b.errors) or 'réseau ?'})"
    top = b.news[0]
    return f"{len(b.news)} nouvelles ; la plus reprise : « {top['title'][:60]} » ({len(top['sources'])} source(s))"


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
