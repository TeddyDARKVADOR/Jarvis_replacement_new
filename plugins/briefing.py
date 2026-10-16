"""
plugins/briefing.py — régler et demander le briefing quotidien (paquet daily/).
"""

PLUGIN = {
    "name": "briefing",
    "description": (
        "The user's daily briefing: today's agenda (from their iCal calendar links), reminders due today, new items "
        "on the topics they follow (deduplicated, with sources), the last meeting. Actions: now (« fais-moi le "
        "briefing ») · status · time with value HH:MM or off (« briefing tous les jours à 7 h 30 ») · add_agenda with "
        "value = the secret iCal address · remove_agenda (all) · sections with value like « news off » or "
        "« agenda on » (agenda, reminders, news, meeting) · quiet with value « 23-7 » (quiet hours: no voice). "
        "Topics themselves are managed with manage_monitor."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "now | status | time | add_agenda | remove_agenda | sections | quiet"},
            "value": {"type": "STRING"},
        },
        "required": ["action"],
    },
}


def _cfg() -> dict:
    from memory.config_manager import load_api_keys
    return load_api_keys() or {}


def _save(**fields) -> None:
    from memory.config_manager import _patch_config
    _patch_config(**fields)


def run(parameters: dict, player=None, session_memory=None) -> str:
    import re
    try:
        import daily
    except ImportError:
        return "Le briefing quotidien n'est pas installé (dossier daily/ absent)."
    action = str(parameters.get("action") or "status").strip().lower()
    value = str(parameters.get("value") or "").strip()
    cfg = _cfg()
    try:
        if action == "now":
            d = daily.get()
            if d is None:
                b = daily.compose(cfg)
                if player:
                    player.show_content("BRIEFING", b.text())
                return b.text()
            return f"Briefing livré ({d.deliver(force=True)})."
        if action == "time":
            if value.lower() in ("off", "non", "aucun", ""):
                _save(briefing_time="")
                return "D'accord : le briefing ne viendra plus qu'au démarrage."
            m = re.match(r"^(\d{1,2})[:h ]?(\d{2})?$", value.replace(" ", ""))
            if not m or int(m.group(1)) > 23:
                return "Donne une heure comme 07:30."
            at = f"{int(m.group(1)):02d}:{int(m.group(2) or 0):02d}"
            _save(briefing_time=at)
            return f"D'accord : briefing tous les jours à {at} (par la voix si je peux te parler, sinon à l'écran)."
        if action == "add_agenda":
            if not re.match(r"^(https?|webcal)://\S+$", value):
                return "Il me faut l'adresse iCal secrète (elle commence par https:// ou webcal://)."
            urls = daily.A.urls(cfg)
            if value not in urls:
                urls.append(value)
            _save(agenda_ics=urls)
            events, errors = daily.A.day({"agenda_ics": [value]})
            if errors:
                return "J'ai gardé l'adresse, mais je n'arrive pas à lire cet agenda pour l'instant."
            return f"Agenda ajouté : {len(events)} événement(s) aujourd'hui."
        if action == "remove_agenda":
            _save(agenda_ics=[])
            return "Agendas retirés."
        if action == "sections":
            m = re.match(r"^(agenda|reminders|rappels|news|actualit\w*|meeting|réunion)\s+(on|off|oui|non)$", value.lower())
            if not m:
                return "Exemple : « news off » ou « agenda on » (agenda, reminders, news, meeting)."
            key = {"rappels": "reminders", "réunion": "meeting"}.get(m.group(1), m.group(1))
            key = "news" if key.startswith("actualit") else key
            sections = dict(cfg.get("briefing_sections") or {})
            sections[key] = m.group(2) in ("on", "oui")
            _save(briefing_sections=sections)
            return f"Section {key} : {'oui' if sections[key] else 'non'}."
        if action == "quiet":
            m = re.match(r"^(\d{1,2})\D+(\d{1,2})$", value)
            if not m:
                return "Exemple : « 23-7 » (de 23 h à 7 h, pas de voix)."
            from memory.config_manager import get_plugin_config, save_plugin_config
            ctx = dict(get_plugin_config("context") or {})
            ctx.update(quiet_start_hour=int(m.group(1)) % 24, quiet_end_hour=int(m.group(2)) % 24)
            save_plugin_config("context", ctx)
            try:
                import context
                context.reset()
            except Exception:
                pass
            return f"Heures silencieuses : de {ctx['quiet_start_hour']} h à {ctx['quiet_end_hour']} h."
        at = str(cfg.get("briefing_time") or "") or "au démarrage seulement"
        n = len(daily.A.urls(cfg))
        st = daily.load_state()
        return (f"Briefing : {at} ; {n} agenda(s) ; dernier livré le {st.get('delivered') or 'jamais'} ; "
                f"sections : {', '.join(k for k, v in {**{s: True for s in daily.SECTIONS}, **(cfg.get('briefing_sections') or {})}.items() if v)}.")
    except Exception as e:
        return f"Briefing : {type(e).__name__}: {e}"
