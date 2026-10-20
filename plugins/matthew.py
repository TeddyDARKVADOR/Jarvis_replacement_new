"""
plugins/matthew.py — parler à JARVIS de Matthew, le modèle à nous qui apprend à côté.

Le paquet matthew/ fait le travail ; ce plugin n'est que la porte vocale.
Le retirer (avec matthew/) rend JARVIS identique à avant.
"""

PLUGIN = {
    "name": "matthew",
    "description": (
        "Matthew is the user's own open-weight AI model that learns from their conversations, to take over "
        "from Gemini step by step. Actions: 'status' (how much it has learned, its own token usage, its "
        "evaluation score), 'correct' (the user says the right answer to the LAST reply: « non, la bonne "
        "réponse c'était… », « retiens plutôt que… » — pass that answer in 'text'; this is the best training "
        "data), 'forget_last' (« n'apprends pas ça », keep the last exchange out of training), 'pause' / "
        "'resume' (stop or restart collecting), 'cycle' (run one learning cycle in the background now). "
        "Do NOT use for ordinary memory (save_memory) — only for training Matthew."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "enum": ["status", "correct", "forget_last", "pause", "resume", "cycle"],
                       "description": "What to do"},
            "text": {"type": "STRING", "description": "For 'correct': the right answer, in the user's words"},
        },
        "required": ["action"],
    },
}


def _status_text(s: dict) -> str:
    t = s.get("teacher") or {}
    champ = s.get("champion")
    mine = (s.get("tokens_total") or {}).get("matthew", {})
    school = (s.get("tokens_total") or {}).get("teacher", {})
    bits = [f"{s['episodes']} échanges observés, {s['corrections']} corrections de votre part",
            f"{s.get('synthetic', 0)} exemples amorcés et vérifiés sur {s.get('synthetic_tools', 0)} outils",
            f"{t.get('ok', 0)} échanges réels réécrits par le professeur ouvert, {t.get('rejected', 0)} écartés"]
    if champ:
        sc = champ.get("score") or {}
        bits.append(f"champion en service depuis {champ['since']}, score {sc.get('composite')}")
    else:
        bits.append(f"pas encore entraîné : il faut {s['min_new_examples']} exemples")
    bits.append(f"tokens de Matthew : {mine.get('in', 0) + mine.get('out', 0):,} "
                f"(école : {school.get('in', 0) + school.get('out', 0):,})")
    if s.get("paused"):
        bits.append("collecte en pause")
    if s.get("deps_missing"):
        bits.append("entraînement local pas encore installé")
    last = (s.get("state") or {}).get("last_cycle")
    if last:
        bits.append(f"dernier cycle {last['at']} : {last['outcome']}")
    return "Matthew — " + " ; ".join(bits) + "."


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        import matthew
        from matthew.journal import Journal
    except ImportError:
        return "Matthew n'est pas installé (dossier matthew/ absent)."
    action = (parameters.get("action") or "status").strip()
    try:
        if action == "correct":
            text = (parameters.get("text") or "").strip()
            if not text:
                return "Dites-moi la bonne réponse, et je la donnerai à Matthew."
            rec = Journal().correction(text)
            return ("Noté : Matthew apprendra cette réponse-là." if rec
                    else "Je n'ai pas d'échange récent à corriger (ou la collecte est en pause).")
        if action == "forget_last":
            return ("Ce dernier échange ne sera pas appris." if Journal().forget_last()
                    else "Aucun échange récent à retirer.")
        if action in ("pause", "resume"):
            flag = matthew.data_dir() / "PAUSED"
            if action == "pause":
                flag.write_text("1", encoding="utf-8")
                return "Matthew n'observe plus nos échanges."
            if flag.exists():
                flag.unlink()
            return "Matthew observe de nouveau nos échanges."
        if action == "cycle":
            import subprocess
            import sys
            subprocess.Popen([sys.executable, "-m", "matthew.flywheel"], cwd=str(matthew.REPO),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return "Cycle d'apprentissage de Matthew lancé en arrière-plan (à sec : école et données)."
        from matthew.flywheel import Flywheel
        text = _status_text(Flywheel().status())
    except Exception as e:
        return f"Matthew : erreur {type(e).__name__}: {e}"
    if player:
        try:
            player.show_content("MATTHEW", text)
        except Exception:
            pass
    return text
