"""
daily/ — le briefing quotidien personnalisé.

    b = compose(cfg)          agenda du jour, rappels du jour, nouvelles des sujets suivis (sans doublon,
                              avec leurs sources), dernière réunion
    b.spoken()                deux ou trois phrases, pour la voix
    b.text()                  le détail, pour le panneau BRIEFING du HUD

DEUX MOMENTS
  - Au démarrage : le briefing existant de main.py ajoute `_daily_clause` à son
    accueil (comme le rapport de la nuit). Le briefing est préparé en fond et
    rafraîchi toutes les 30 min : l'accueil n'attend aucun réseau.
  - À l'heure choisie (« briefing_time », ex. 07:30 ; vide = seulement au
    démarrage) : livré une fois par jour, par le canal que la politique de
    contexte choisit — la voix si elle est permise, sinon une notification
    (téléphone) et le HUD, sinon plus tard ; les heures silencieuses (réglages
    du contexte) sont respectées.

RÉGLAGES (config/api_keys.json) : agenda_ics (adresse(s) iCal secrète(s)),
briefing_time, briefing_sections ({"agenda", "reminders", "news", "meeting"}),
timezone (ex. "Europe/Paris" ; sinon le fuseau du système).
Les sujets suivis sont ceux de l'outil manage_monitor. L'outil vocal est
plugins/briefing.py. État (nouvelles déjà annoncées, livraison du jour) :
memory/daily.json, ignoré par Git.

Paquet racine (contrat V2) : la mémoire, les rappels, la recherche et le
contexte sont lus sous garde, à l'appel ; le supprimer rend le briefing de V1.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from daily import agenda as A
from daily import news as N

STATE = Path(os.environ.get("JARVIS_DAILY_FILE") or Path(__file__).resolve().parent.parent / "memory" / "daily.json")
REFRESH_S = 30 * 60
SEEN_DAYS = 14
SECTIONS = ("agenda", "reminders", "news", "meeting")
_attached = None


@dataclass
class Briefing:
    when: datetime
    events: list[A.Event] = field(default_factory=list)
    events_day: date | None = None
    reminders: list[tuple[str, str]] = field(default_factory=list)      # (heure, message)
    news: list[dict] = field(default_factory=list)
    meeting: dict | None = None
    errors: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.events or self.reminders or self.news or self.meeting)

    def spoken(self) -> str:
        """Ce que la voix en dit : court, le détail est à l'écran."""
        bits = []
        if self.events:
            which = "today" if self.events_day == self.when.date() else "tomorrow"
            first = self.events[0]
            bits.append(f"{len(self.events)} event(s) {which}, the first: {first.render()}")
        if self.reminders:
            bits.append(f"{len(self.reminders)} reminder(s) due today ({self.reminders[0][0]} {self.reminders[0][1][:60]})")
        if self.news:
            top = self.news[0]
            src = ", ".join(s for s, _ in top["sources"][:3])
            bits.append(f"{len(self.news)} new item(s) on the topics they follow; the most covered: « {top['title'][:120]} » ({src})")
        if self.meeting:
            bits.append(f"the minutes of « {self.meeting.get('title')} » are ready")
        return "; ".join(bits)

    def text(self) -> str:
        lines = [f"Briefing — {self.when:%A %d %B, %H:%M}"]
        if self.events:
            label = "Aujourd'hui" if self.events_day == self.when.date() else "Demain"
            lines += ["", f"{label} :"] + [f"• {e.render()}" for e in self.events]
        if self.reminders:
            lines += ["", "Rappels du jour :"] + [f"• {h} — {m}" for h, m in self.reminders]
        if self.news:
            lines += ["", "Sujets suivis :"]
            for g in self.news:
                lines.append(f"• {g['title']} [{', '.join(sorted(t for t in g['topics'] if t))}]")
                lines += [f"   {s} — {u}" for s, u in g["sources"][:3]]
        if self.meeting:
            lines += ["", f"Dernière réunion : « {self.meeting.get('title')} » — {self.meeting.get('path', '')}"]
        if self.errors:
            lines += ["", "Non lu : " + " ; ".join(self.errors)]
        return "\n".join(lines) if len(lines) > 1 else "Rien de prévu ni de neuf aujourd'hui."


# ── état ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(st: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_name(f".daily-{os.getpid()}.tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, STATE)
    except OSError:
        pass


def mark_delivered(b: Briefing, st: dict | None = None) -> None:
    st = st if st is not None else load_state()
    today = b.when.date().isoformat()
    cutoff = (b.when.date() - timedelta(days=SEEN_DAYS)).isoformat()
    seen = {k: d for k, d in (st.get("seen") or {}).items() if d >= cutoff}
    seen.update({g["key"]: today for g in b.news})
    st.update(seen=seen, delivered=today)
    save_state(st)


# ── sources (sous garde : chacune peut manquer) ──────────────────────────────

def _topics() -> list[str]:
    try:
        from actions.background_monitor import list_monitors
        return list_monitors()
    except Exception:
        return []


def _search(topic: str, n: int) -> list[dict]:
    from actions.web_search import _ddg_news
    return _ddg_news(topic, max_results=n)


def _reminders(day: date) -> list[tuple[str, str]]:
    try:
        import reminders
        out = []
        for _rid, r in reminders.upcoming():
            due = str(r.get("due") or "")
            if due[:10] == day.isoformat():
                out.append((due[11:16], str(r.get("message") or "")))
        return sorted(out)
    except Exception:
        return []


def _meeting(now: datetime) -> dict | None:
    try:
        import meeting
        cop = meeting.get()
        last = cop.last if cop else {}
        if last and last.get("started", "")[:10] >= (now.date() - timedelta(days=1)).isoformat():
            return {"title": last["result"].get("title"), "path": last.get("path", "")}
    except Exception:
        pass
    return None


def compose(cfg: dict, now: datetime | None = None, fetcher=A.fetch, search=_search, topics=_topics,
            reminders_of=_reminders, meeting_of=_meeting, state: dict | None = None) -> Briefing:
    now = now or A.now(cfg)
    on = {**{s: True for s in SECTIONS}, **(cfg.get("briefing_sections") or {})}
    b = Briefing(now)
    if on["agenda"] and A.urls(cfg):
        events, errs = A.day(cfg, now.date(), fetcher)
        events = [e for e in events if e.all_day or e.end >= now]       # ce qui est passé n'intéresse plus
        b.events, b.events_day, b.errors = events, now.date(), errs
        if not events and now.hour >= 18:                              # le soir : demain
            b.events, errs2 = A.day(cfg, now.date() + timedelta(days=1), fetcher)
            b.events_day, b.errors = now.date() + timedelta(days=1), errs + errs2
    if on["reminders"]:
        b.reminders = reminders_of(now.date())
    if on["news"]:
        items, errs = N.gather(topics(), search)
        st = state if state is not None else load_state()
        b.news = N.fresh(N.cluster(items), st.get("seen") or {})[:6]
        b.errors += errs
    if on["meeting"]:
        b.meeting = meeting_of(now)
    return b


# ── livraison ────────────────────────────────────────────────────────────────

def _policy_channel() -> str:
    """« voice », « notify » ou « defer » selon la politique de contexte ; « voice » sans contexte (V1)."""
    try:
        from context import Priority, get_policy, get_store
        d = get_policy().decide(Priority.USEFUL, get_store().snapshot())
        if d.speaks:
            return "voice"
        return "defer" if "DEFER" in str(d.channel) else "notify"
    except ImportError:
        return "voice"
    except Exception:
        return "notify"


def _phone(jarvis, title: str, text: str) -> bool:
    try:
        from server.notify import get_hub
        hub = get_hub()
        return getattr(hub, "_dashboard", None) is not None and hub.notify("USEFUL", title, text[:400]) is not None
    except Exception:
        return False


class Daily:
    def __init__(self, jarvis, settings: Callable[[], dict], log: Callable[[str], None] = print):
        self.jarvis, self.settings, self.log = jarvis, settings, log
        self.cached: Briefing | None = None
        self._lock = threading.Lock()

    def refresh(self) -> Briefing:
        b = compose(self.settings())
        with self._lock:
            self.cached = b
        return b

    def clause(self) -> str:
        """Pour l'accueil de main.py : instantané (le briefing est déjà prêt), et une seule fois par jour."""
        st = load_state()
        b = self.cached
        if b is None or st.get("delivered") == A.now(self.settings()).date().isoformat() or b.empty():
            return ""
        mark_delivered(b, st)
        # Le panneau des actualités du briefing de main.py s'affiche quelques secondes après l'accueil :
        # le nôtre vient après lui, pour ne pas être remplacé.
        threading.Timer(15.0, lambda: self._show(b)).start()
        return (f" Then, in one or two short sentences, give the user's day: {b.spoken()}. "
                "Say the details are on screen (panel BRIEFING).")

    def _show(self, b: Briefing) -> None:
        try:
            self.jarvis.ui.show_content("BRIEFING", b.text())
        except Exception:
            pass

    def deliver(self, force: bool = False) -> str:
        """La livraison à l'heure choisie (ou « briefing maintenant »)."""
        b = self.refresh()
        channel = "voice" if force else _policy_channel()
        if channel == "defer":
            return "plus tard (la situation ne permet pas de te déranger)"
        self._show(b)
        if channel == "voice" and getattr(self.jarvis, "session", None) and not b.empty():
            self.jarvis.plugin_say(f"[BRIEFING] Give the user their day in three or four short sentences, in their "
                                   f"language: {b.spoken()}. The details are on screen. Do not call any tool.")
            done = "voix et HUD"
        else:
            done = "HUD" + (" et téléphone" if _phone(self.jarvis, "Briefing du jour", b.text()) else "")
        mark_delivered(b)
        return done

    def loop(self) -> None:
        last_refresh = 0.0
        while True:
            try:
                if time.time() - last_refresh > REFRESH_S:
                    self.refresh()
                    last_refresh = time.time()
                cfg = self.settings()
                at, here = str(cfg.get("briefing_time") or "").strip(), A.now(cfg)
                st = load_state()
                if at and st.get("delivered") != here.date().isoformat() and here.strftime("%H:%M") >= at:
                    out = self.deliver()
                    self.log(f"SYS: Briefing du jour : {out}")
            except Exception as e:
                self.log(f"SYS: briefing — {type(e).__name__}: {e}")
            time.sleep(30)


def get() -> "Daily | None":
    return _attached


def attach(jarvis) -> None:
    """Hook de main.py (local et Oracle) : prépare le briefing en fond et le livre à l'heure choisie."""
    global _attached
    if _attached is not None:
        return

    def settings() -> dict:
        try:
            from memory.config_manager import load_api_keys
            return load_api_keys() or {}
        except Exception:
            return {}

    def log(m: str) -> None:
        try:
            jarvis.ui.write_log(m)
        except Exception:
            print(m)

    _attached = Daily(jarvis, settings, log)
    jarvis._daily_clause = _attached.clause
    threading.Thread(target=_attached.loop, name="daily-briefing", daemon=True).start()
