"""
daily/agenda.py — les événements du jour, depuis un ou plusieurs agendas iCal.

Le plus simple et le plus sûr pour commencer : le lien iCal SECRET d'un agenda
(Google Agenda → Paramètres de l'agenda → « Adresse secrète au format iCal »,
Outlook → Publier le calendrier, Apple → partage public). Lecture seule, aucune
connexion OAuth ; le lien est un mot de passe : il vit dans
config/api_keys.json (ignoré par Git), clé « agenda_ics » (une adresse ou une
liste). Les heures sont données dans le fuseau « timezone » (ex.
"Europe/Paris") s'il est réglé, sinon dans celui du système : sur Oracle
(VM en UTC), sans ce réglage, un cours de 9 h à Paris s'annoncerait à 7 h.

Les récurrences sont le piège des agendas (cours hebdomadaires, exceptions,
occurrences déplacées, fuseaux) : recurring-ical-events (sur icalendar) les
déroule correctement ; ce module ne refait pas ce travail.
"""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

USER_AGENT = "jarvis-daily/1"


@dataclass
class Event:
    start: datetime          # heure locale (minuit pour un événement d'une journée)
    end: datetime
    title: str
    location: str = ""
    all_day: bool = False
    calendar: str = ""

    def render(self) -> str:
        when = "toute la journée" if self.all_day else f"{self.start:%H:%M}–{self.end:%H:%M}"
        return f"{when} · {self.title}" + (f" ({self.location})" if self.location else "")


def urls(cfg: dict) -> list[str]:
    raw = cfg.get("agenda_ics") or []
    items = [raw] if isinstance(raw, str) else list(raw)
    return [str(u).strip() for u in items if str(u).strip()]


def fetch(url: str, timeout: float = 15.0) -> str:
    """Le texte iCal d'une adresse https://, webcal://, file:// ou d'un chemin local."""
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if url.startswith(("http://", "https://")):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    path = Path(url[len("file://"):] if url.startswith("file://") else url).expanduser()
    return path.read_text(encoding="utf-8", errors="replace")


def zone(cfg: dict):
    """Le fuseau de l'utilisateur (réglage « timezone »), ou None : celui du système."""
    name = str(cfg.get("timezone") or "").strip()
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None                                    # fuseau inconnu : l'heure du système, plutôt que rien


def now(cfg: dict) -> datetime:
    """Maintenant, heure locale de l'utilisateur, sans fuseau attaché."""
    tz = zone(cfg)
    return datetime.now(tz).replace(tzinfo=None) if tz else datetime.now()


def _local(value, all_day: bool, tz=None) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(tz).replace(tzinfo=None) if value.tzinfo else value
    return datetime.combine(value, time())            # une date seule : événement d'une journée


def between(ics_text: str, start: datetime, end: datetime, calendar: str = "", tz=None) -> list[Event]:
    """Les occurrences qui touchent [start, end[, récurrences comprises, triées. Pur."""
    import icalendar
    import recurring_ical_events
    cal = icalendar.Calendar.from_ical(ics_text)
    name = calendar or str(cal.get("X-WR-CALNAME") or "")
    out = []
    if tz is not None:                                 # la journée de l'utilisateur, pas celle du serveur
        start, end = start.replace(tzinfo=tz), end.replace(tzinfo=tz)
    for ev in recurring_ical_events.of(cal).between(start, end):
        if str(ev.get("STATUS") or "").upper() == "CANCELLED":
            continue
        s, e = ev.get("DTSTART"), ev.get("DTEND")
        s = s.dt if s is not None else None
        if s is None:
            continue
        all_day = not isinstance(s, datetime)
        e = e.dt if e is not None else (s + timedelta(days=1) if all_day else s + timedelta(hours=1))
        out.append(Event(_local(s, all_day, tz), _local(e, all_day, tz), str(ev.get("SUMMARY") or "(sans titre)").strip(),
                         str(ev.get("LOCATION") or "").strip(), all_day, name))
    return sorted(out, key=lambda x: (not x.all_day, x.start))


def day(cfg: dict, when: date | None = None, fetcher=fetch) -> tuple[list[Event], list[str]]:
    """(les événements de la journée, les erreurs par agenda). Un agenda en panne n'empêche pas les autres."""
    tz = zone(cfg)
    when = when or now(cfg).date()
    start = datetime.combine(when, time())
    events, errors = [], []
    for i, url in enumerate(urls(cfg), 1):
        try:
            events += between(fetcher(url), start, start + timedelta(days=1), tz=tz)
        except Exception as e:
            errors.append(f"agenda {i} : {type(e).__name__}")   # jamais l'adresse : c'est un secret
    return sorted(events, key=lambda x: (not x.all_day, x.start)), errors
