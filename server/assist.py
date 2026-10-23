"""
server/assist.py — ce qu'un appareil demande au cerveau d'Oracle.

Le PC relié n'a aucune clé d'API (client_desktop : « la clé ne quitte jamais
le serveur »). Quand il a fait sa part — écouter une réunion, regarder son
écran —, il envoie le résultat ici, et c'est Oracle qui appelle le modèle.

    POST /api/meeting/summary   {transcript, started, duration_s, where}
         → {result, delivered}  compte rendu (meeting/summarize.py), puis
           livraison par Oracle : HUD, rappels, objectifs proposés, voix.
    POST /api/pilot/step        {body}  → la réponse du modèle (pilot/brain.py)
    POST /api/pilot/confirm     {title} → {confirmed} : le bandeau CONFIRMER de
           core/confirm.py sur le HUD et le téléphone, attendu jusqu'à 95 s
    POST /api/pilot/report      {outcome, message} → la voix d'Oracle l'annonce

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
MAX_PILOT_BODY = 8_000_000          # une capture JPEG en base64 et le texte de l'écran


def attach(dashboard, get_matthew_assistant: Callable[[], object], log=print) -> None:
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
            matthew_assistant = get_matthew_assistant()
            delivered = meeting.deliver(matthew_assistant, result, where, names) if matthew_assistant is not None else {}
            return {"result": result, "delivered": delivered}

        try:
            out = await asyncio.to_thread(work)
        except Exception as e:
            log(f"[Assist] compte rendu impossible : {type(e).__name__}: {e}")
            return JSONResponse({"error": f"{type(e).__name__}: {e}"[:300]}, status_code=502)
        log(f"[Assist] compte rendu « {out['result'].get('title')} » ({out['result'].get('model')})")
        return JSONResponse(out)

    app.add_api_route("/api/meeting/summary", meeting_summary, methods=["POST"])

    async def meeting_assist(req: Request) -> JSONResponse:
        """L'aide en direct d'un PC relié : une suggestion en une seconde (Groq, Ollama Cloud, Gemini)."""
        if not _authorised(req):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            body = await req.json()
        except Exception:
            body = None
        if not isinstance(body, dict) or not body.get("system") or not body.get("user"):
            return JSONResponse({"error": "system et user attendus"}, status_code=400)
        system, user = str(body["system"])[:4000], str(body["user"])[:20000]

        def work() -> str:
            import meeting
            try:
                from memory.config_manager import load_api_keys
                cfg = load_api_keys() or {}
            except Exception:
                cfg = {}
            name = str(cfg.get("user_name") or "").strip()
            prefix = f"The user is called {name}.\n" if name and "The user is called" not in user else ""
            return meeting.fast_answer(cfg, system, prefix + user)

        try:
            return JSONResponse({"text": await asyncio.to_thread(work)})
        except Exception as e:
            return JSONResponse({"error": f"{type(e).__name__}: {e}"[:300]}, status_code=502)

    app.add_api_route("/api/meeting/assist", meeting_assist, methods=["POST"])

    # ── le pilote (pilot/) : le PC regarde et agit, Oracle choisit, confirme, annonce ──

    def _keys() -> dict:
        try:
            from memory.config_manager import load_api_keys
            return load_api_keys() or {}
        except Exception:
            return {}

    async def _body(req: Request):
        if not _authorised(req):
            return None, JSONResponse({"error": "Unauthorized"}, status_code=401)
        raw = await req.body()
        if len(raw) > MAX_PILOT_BODY:
            return None, JSONResponse({"error": "requête trop grande"}, status_code=413)
        try:
            body = await req.json()
        except Exception:
            body = None
        if not isinstance(body, dict):
            return None, JSONResponse({"error": "JSON attendu"}, status_code=400)
        return body, None

    async def pilot_step(req: Request) -> JSONResponse:
        body, err = await _body(req)
        if err:
            return err
        if not isinstance(body.get("body"), dict):
            return JSONResponse({"error": "body manquant"}, status_code=400)

        def work() -> dict:
            from pilot import brain
            cfg = _keys()
            first = str(cfg.get("pilot_model") or "").strip()
            models = (first,) + tuple(m for m in brain.MODELS if m != first) if first else brain.MODELS
            return brain.step(body["body"], str(cfg.get("gemini_api_key") or ""),
                              str(cfg.get("groq_api_key") or ""), models)

        try:
            return JSONResponse(await asyncio.to_thread(work))
        except Exception as e:
            return JSONResponse({"error": f"{type(e).__name__}: {e}"[:300]}, status_code=502)

    async def pilot_confirm(req: Request) -> JSONResponse:
        body, err = await _body(req)
        if err:
            return err
        title = str(body.get("title") or "")[:400]
        if not title:
            return JSONResponse({"error": "title manquant"}, status_code=400)
        matthew_assistant = get_matthew_assistant()

        def say(text: str) -> None:
            if matthew_assistant is not None and getattr(matthew_assistant, "session", None):
                matthew_assistant.plugin_say(text)

        def work() -> bool:
            import pilot
            return pilot.confirm_on_hud(title, say=say)

        confirmed = await asyncio.to_thread(work)
        log(f"[Assist] pilote : « {title[:80]} » → {'confirmé' if confirmed else 'refusé'}")
        return JSONResponse({"confirmed": bool(confirmed)})

    async def pilot_report(req: Request) -> JSONResponse:
        body, err = await _body(req)
        if err:
            return err
        import pilot
        outcome, message = str(body.get("outcome") or "done")[:20], str(body.get("message") or "")[:1500]
        matthew_assistant = get_matthew_assistant()
        if matthew_assistant is not None:
            try:
                matthew_assistant.ui.write_log(f"SYS: Pilote (PC) — {outcome} : {message[:200]}")
                if getattr(matthew_assistant, "session", None):
                    matthew_assistant.plugin_say(pilot.spoken(outcome, message))
            except Exception:
                pass
        return JSONResponse({"ok": True})

    app.add_api_route("/api/pilot/step", pilot_step, methods=["POST"])
    app.add_api_route("/api/pilot/confirm", pilot_confirm, methods=["POST"])
    app.add_api_route("/api/pilot/report", pilot_report, methods=["POST"])
