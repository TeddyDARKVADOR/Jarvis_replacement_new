"""
server/assist.py — ce qu'un appareil demande au cerveau d'Oracle.

Le PC relié n'a aucune clé d'API (client_desktop : « la clé ne quitte jamais
le serveur »). Quand il a fait sa part — écouter une réunion, regarder son
écran —, il envoie le résultat ici, et c'est Oracle qui appelle le modèle.

    POST /api/meeting/summary   {transcript, started, duration_s, where}
         → {result, delivered}  compte rendu (meeting/summarize.py), puis
           livraison par Oracle : HUD, rappels, objectifs proposés, voix.

Autorité : le même jeton porteur que /api/command, qui exécute déjà n'importe
quelle commande ; résumer un texte est strictement moins. Le texte est borné
(MAX_TRANSCRIPT) et le travail part dans un fil : la boucle du serveur ne
s'arrête pas pendant que le modèle répond.
"""
# Pas de « from __future__ import annotations » ici : FastAPI lit l'annotation
# Request des routes, importée dans attach() ; en chaîne, elle ne se résout pas
# et la requête devient un paramètre obligatoire (422, mesuré).
import asyncio
from datetime import datetime
from typing import Callable

MAX_TRANSCRIPT = 2_000_000


def attach(dashboard, get_jarvis: Callable[[], object], log=print) -> None:
    from fastapi import Request
    from fastapi.responses import JSONResponse

    app = dashboard.app

    def _authorised(req: Request) -> bool:
        tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
        return bool(tok) and tok in dashboard._tokens

    async def meeting_summary(req: Request) -> JSONResponse:
        if not _authorised(req):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            body = await req.json()
        except Exception:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("transcript"), str):
            return JSONResponse({"error": "transcript manquant"}, status_code=400)
        transcript = body["transcript"]
        if len(transcript) > MAX_TRANSCRIPT:
            return JSONResponse({"error": "transcription trop longue"}, status_code=413)
        try:
            started = datetime.fromisoformat(str(body.get("started")))
        except ValueError:
            started = datetime.now()
        try:
            duration = float(body.get("duration_s") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        where = str(body.get("where") or "")[:300]

        def work() -> dict:
            import meeting
            from meeting.brain import Brain
            from meeting.summarize import summarize
            try:
                from memory.config_manager import load_api_keys
                cfg = load_api_keys() or {}
            except Exception:
                cfg = {}
            names = meeting.user_names(cfg)
            result = summarize(transcript, Brain(cfg), started, duration, names)
            jarvis = get_jarvis()
            delivered = meeting.deliver(jarvis, result, where, names) if jarvis is not None else {}
            return {"result": result, "delivered": delivered}

        try:
            out = await asyncio.to_thread(work)
        except Exception as e:
            log(f"[Assist] compte rendu impossible : {type(e).__name__}: {e}")
            return JSONResponse({"error": f"{type(e).__name__}: {e}"[:300]}, status_code=502)
        log(f"[Assist] compte rendu « {out['result'].get('title')} » ({out['result'].get('model')})")
        return JSONResponse(out)

    app.add_api_route("/api/meeting/summary", meeting_summary, methods=["POST"])
