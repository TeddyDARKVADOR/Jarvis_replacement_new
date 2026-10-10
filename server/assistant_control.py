"""
server/assistant_control.py — what the HUD can change about the assistant itself.

    memory.list · memory.forget          what JARVIS knows about you
    profile.get · profile.set            his name, yours, his voice, the briefing
    plugins.list · plugins.toggle        drop-in skills, on and off
    plugins.save · plugins.test          their settings forms, and the test button

ONE IMPLEMENTATION, TWO TRANSPORTS
    Local (`python main.py --local`), `local_hud.py` calls `AssistantControl`
    in-process. Online, `attach()` puts the very same object behind
    `POST /api/assistant` on Oracle, and the desktop client calls it over
    Tailscale with the bearer it already holds. The HUD cannot tell the two
    apart — which is the point: one interface, whichever brain is answering.

WHAT IT TOUCHES, AND WHAT IT DOES NOT
    Only what the original desktop window already let a user change, through the
    functions that window used (`memory.memory_manager`, `memory.config_manager`)
    and the callbacks `main.JarvisLive` puts on its UI object (`on_voice_change`,
    `get_plugins`, `get_plugin_settings`). No new capability, no second way of
    writing the config.

    A voice or a name is baked into the Gemini session when it is opened, so a
    change to either asks for the same session rebuild the old window asked for
    (`ui.on_voice_change`): the conversation is kept, the new voice speaks.
"""

import asyncio
import json
from typing import Any, Callable, Optional

# Module level, and no `from __future__ import annotations`: FastAPI resolves a
# handler's annotations against its module globals, and a `req: Request` it
# cannot resolve becomes a missing query parameter — every call answers 422 and
# the auth check never runs. server/api.py documents the same trap.
from fastapi import Request
from fastapi.responses import JSONResponse

# A single-op request may not grow without bound: the largest legitimate payload
# is a plugin form.
MAX_BODY_BYTES = 64 * 1024


class AssistantControl:
    """`call(op, data) -> dict`. Never raises: errors come back as {"error": …}."""

    def __init__(self, ui_getter: Callable[[], Any]) -> None:
        #: The UI object main.JarvisLive was built with (LocalUI or HeadlessUI).
        #: A getter, because on the server the assistant exists only once its
        #: session starts.
        self._ui = ui_getter

    def call(self, op: str, data: Optional[dict] = None) -> dict:
        data = data if isinstance(data, dict) else {}
        handler = getattr(self, "_op_" + str(op).replace(".", "_"), None)
        if handler is None:
            return {"error": f"opération inconnue : {op}"}
        try:
            return handler(data)
        except Exception as exc:  # a broken op must never take the host down
            return {"error": f"{type(exc).__name__}: {exc}"}

    # ── memory ───────────────────────────────────────────────────────────────

    def _op_memory_list(self, _data: dict) -> dict:
        from memory.memory_manager import all_entries_for_ui
        return {"entries": all_entries_for_ui()}

    def _op_memory_forget(self, data: dict) -> dict:
        from memory.memory_manager import forget
        category, key = str(data.get("category", "")), str(data.get("key", ""))
        if not category or not key:
            return {"error": "catégorie et clé requises"}
        result = forget(key, category)
        return {"ok": result.startswith("Forgotten"), "detail": result}

    # ── profile ──────────────────────────────────────────────────────────────

    def _op_profile_get(self, _data: dict) -> dict:
        from memory import config_manager as cm
        return {
            "assistant_name": cm.get_assistant_name(),
            "user_name": cm.get_user_name(),
            "voice": cm.get_voice(),
            "voices": list(cm.AVAILABLE_VOICES),
            "brief": bool(cm.get_brief_enabled()),
        }

    def _op_profile_set(self, data: dict) -> dict:
        from memory import config_manager as cm
        before = self._op_profile_get({})
        name = str(data.get("assistant_name", before["assistant_name"])).strip()[:32] or "JARVIS"
        user = str(data.get("user_name", before["user_name"])).strip()[:48]
        voice = str(data.get("voice", before["voice"]))
        if voice not in cm.AVAILABLE_VOICES:
            voice = before["voice"]

        if (name, user) != (before["assistant_name"], before["user_name"]):
            cm.save_assistant_config(name, user)
        if voice != before["voice"]:
            cm.save_voice(voice)
        if "brief" in data and bool(data["brief"]) != before["brief"]:
            cm.save_brief_enabled(bool(data["brief"]))

        # Name and voice are part of the session's configuration.
        rebuild = voice != before["voice"] or name != before["assistant_name"] or user != before["user_name"]
        if rebuild:
            ui = self._ui()
            callback = getattr(ui, "on_voice_change", None) if ui is not None else None
            if callable(callback):
                callback()
        return {"ok": True, "reconnecting": rebuild, **self._op_profile_get({})}

    # ── plugins ──────────────────────────────────────────────────────────────

    def _registry(self, name: str):  # noqa: ANN202
        ui = self._ui()
        fn = getattr(ui, name, None) if ui is not None else None
        return fn if callable(fn) else None

    def _sections(self) -> list[dict]:
        schemas = self._registry("get_plugin_settings")
        return list(schemas() if schemas else [])

    def _op_plugins_list(self, _data: dict) -> dict:
        plugins = self._registry("get_plugins")
        sections = []
        for sec in self._sections():
            action = sec.get("action") or {}
            sections.append({
                "plugin": sec.get("plugin"), "namespace": sec.get("namespace"),
                "title": sec.get("title"), "fields": sec.get("fields", []),
                "values": sec.get("values", {}),
                # the callable stays here; the page only learns there is a button
                "action": {"label": action.get("label") or "Tester"} if callable(action.get("run")) else None,
            })
        return {"plugins": list(plugins() if plugins else []), "settings": sections,
                "ready": plugins is not None}

    def _op_plugins_toggle(self, data: dict) -> dict:
        from memory.config_manager import save_plugin_enabled
        name = str(data.get("name", ""))
        if not name:
            return {"error": "nom requis"}
        save_plugin_enabled(name, bool(data.get("enabled")))
        return {"ok": True, "detail": "Pris en compte au prochain démarrage de JARVIS."}

    def _op_plugins_save(self, data: dict) -> dict:
        from memory.config_manager import save_plugin_config
        ns = str(data.get("namespace", ""))
        values = data.get("values") if isinstance(data.get("values"), dict) else {}
        if not ns:
            return {"error": "namespace requis"}
        save_plugin_config(ns, values)
        return {"ok": True}

    def _op_plugins_test(self, data: dict) -> dict:
        ns = str(data.get("namespace", ""))
        sec = next((s for s in self._sections() if (s.get("namespace") or s.get("plugin")) == ns), None)
        run = ((sec or {}).get("action") or {}).get("run")
        if not callable(run):
            return {"error": "ce plugin n'a pas de test"}
        values = data.get("values") if isinstance(data.get("values"), dict) else {}
        self._op_plugins_save({"namespace": ns, "values": values})
        result = run(values)
        ok, msg = (bool(result[0]), str(result[1])) if isinstance(result, tuple) and len(result) == 2 \
            else (bool(result), str(result))
        return {"ok": ok, "detail": msg}


def attach(dashboard, control: AssistantControl, log=print) -> None:  # noqa: ANN001
    """`POST /api/assistant {op, data}` on the dashboard's app, bearer required —
    the same token check as /api/command and /status."""
    app = dashboard.app

    async def assistant(req: Request) -> JSONResponse:
        tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not tok or tok not in dashboard._tokens:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        raw = await req.body()
        if len(raw) > MAX_BODY_BYTES:
            return JSONResponse({"error": "requête trop grande"}, status_code=413)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return JSONResponse({"error": "JSON invalide"}, status_code=400)
        op = str(body.get("op", ""))
        # Plugin tests and memory reads touch the disk or the network: never on
        # the event loop that also carries JARVIS's voice.
        result = await asyncio.to_thread(control.call, op, body.get("data"))
        return JSONResponse(result, status_code=400 if "error" in result else 200)

    app.add_api_route("/api/assistant", assistant, methods=["POST"])
    log("[Assistant] POST /api/assistant prêt")
