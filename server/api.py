"""
server/api.py — three routes grafted onto the server that already exists.

WHY GRAFTED AND NOT A SECOND SERVER
    dashboard/server.py already runs FastAPI on port 8000 with authentication,
    a WebSocket event channel, and the phone-microphone uplink. Standing up a
    second app beside it would mean a second port to open, a second token
    scheme to keep in step, and two things to restart. `DashboardServer.app` is
    a plain FastAPI instance, so the three missing pieces are added to it:

        GET  /health          liveness. No auth — see the note below.
        GET  /status          what JARVIS is doing. Bearer token required.
        WS   /ws/phone-out    JARVIS's voice, 24 kHz PCM, to the phone.

    Nothing existing is replaced or wrapped. If this module is never called,
    dashboard/server.py behaves exactly as it does today.

/ws/phone-out COMPLETES THE LOOP
    The uplink has been there all along (/ws/phone-audio → _relay_phone_audio →
    Gemini). The downlink was the missing half: JARVIS's voice went to the
    desktop speakers and nowhere else. With audio_bridge standing in for the
    sound card, _play_audio's writes arrive here instead — unmodified, still
    batched at ~200 ms, still cut short by interrupt().

WHY /health IS NOT AUTHENTICATED
    It is what systemd, a uptime probe or `curl` asks every few seconds, and
    making those carry a credential is how credentials end up in shell history
    and log files. It answers with liveness and uptime only: no state, no logs,
    no configuration, nothing that is not already implied by the port being
    open. Everything that says something about the user's session is behind
    /status, which requires the same bearer token as /api/command.
"""
import asyncio

# Imported at module level, and the annotations below are NOT strings, on
# purpose. FastAPI resolves a handler's annotations with get_type_hints against
# its module globals: with `from __future__ import annotations` and these names
# imported inside attach(), `req: Request` resolves to nothing and FastAPI
# decides `req` is a missing QUERY parameter — every call answers 422 and the
# auth check never runs. Cost of the mistake: an endpoint that looks wired up,
# returns a plausible error, and is never actually guarded.
from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

# The sample rate main.py opens its output stream at. Passed in by the caller
# from main.RECEIVE_SAMPLE_RATE so the two can never drift; this is only the
# fallback for a caller that does not supply it.
DEFAULT_SAMPLE_RATE = 24000


def attach(dashboard, *, state, hub, sample_rate: int = DEFAULT_SAMPLE_RATE,
           label: str = "MARK LIII", log=print) -> None:
    """Add the routes to `dashboard.app`. Call once, before serve().

    Raises if called twice on the same app, and that is not pedantry. FastAPI
    matches the FIRST route registered for a path, so a second attach registers
    routes that are never reached — /status then reports the *first* caller's
    RuntimeState while the second caller believes it is being served. The
    symptom is counters frozen at zero with no error anywhere, which is exactly
    how this was found.
    """
    app = dashboard.app
    if getattr(app.state, "jarvis_attached", False):
        raise RuntimeError(
            "server.api.attach() was already called on this dashboard. The "
            "routes are bound to the first state/hub and a second set would be "
            "shadowed. Reuse app.state.jarvis_state / app.state.jarvis_hub."
        )
    app.state.jarvis_attached = True
    app.state.jarvis_state = state
    app.state.jarvis_hub = hub

    # The notification hub needs the dashboard to broadcast through, and this is
    # the one function that already holds it. Binding here rather than in
    # run_headless keeps the transport working for any host that attaches these
    # routes, including the selftest's.
    from server.notify import get_hub as _get_notify_hub
    _get_notify_hub().bind_dashboard(dashboard)

    def _authorised(req: Request) -> bool:
        """Same check dashboard/server.py performs on /api/command: a bearer
        token that the dashboard itself has issued. Reading its token set is
        not a second auth scheme — it is the same one, asked the same question."""
        tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
        return bool(tok) and tok in dashboard._tokens

    # ── GET /health ──────────────────────────────────────────────────────────

    async def health() -> JSONResponse:
        snap = state.snapshot(log_lines=0)
        return JSONResponse({
            "ok":       True,
            "service":  label,
            "uptime_s": snap["uptime_s"],
        })

    # ── GET /status ──────────────────────────────────────────────────────────

    async def status(req: Request) -> JSONResponse:
        if not _authorised(req):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        snap = state.snapshot()
        # Same name /health reports, so a monitoring script does not have to
        # know that one endpoint calls it `service` and the other `label`.
        snap["service"] = label
        snap["audio_out"] = {
            "listeners":   hub.listeners,
            "sample_rate": sample_rate,
            "channels":    1,
            "encoding":    "pcm_s16le",
            "frames":      hub.frames_published,
            "bytes":       hub.bytes_published,
        }
        # Counts only. A notification's title and text are the user's content
        # and have no business in an endpoint a monitoring script polls.
        snap["notifications"] = _get_notify_hub().stats()
        return JSONResponse(snap)

    # ── WS /ws/phone-out ─────────────────────────────────────────────────────

    async def phone_out(websocket: WebSocket, token: str = "") -> None:
        # Query-parameter token, matching /ws/phone-audio (dashboard/server.py
        # :610). A WebSocket handshake from a browser cannot carry a custom
        # header, and the Android client already speaks this form for the
        # uplink — one convention, not two.
        tok = token.strip()
        if not tok or tok not in dashboard._tokens:
            await websocket.close(code=4001)
            return

        await websocket.accept()

        # Tell the client what the bytes are before sending any, so the phone
        # never has to hardcode a rate that main.py could change.
        await websocket.send_json({
            "type":        "audio_format",
            "sample_rate": sample_rate,
            "channels":    1,
            "encoding":    "pcm_s16le",
        })

        sub = hub.subscribe()
        state.note_phone_stream(+1)
        log("[PhoneOut] listener connected")

        async def _pump() -> None:
            while True:
                data = await sub.queue.get()
                await websocket.send_bytes(data)

        async def _watch_close() -> None:
            # Without this, a phone that goes away silently would not be
            # noticed until the next time JARVIS happened to speak.
            while True:
                await websocket.receive()

        pump  = asyncio.ensure_future(_pump())
        watch = asyncio.ensure_future(_watch_close())
        try:
            await asyncio.wait({pump, watch}, return_when=asyncio.FIRST_COMPLETED)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log(f"[PhoneOut] {type(e).__name__}: {e}")
        finally:
            for task in (pump, watch):
                task.cancel()
            hub.unsubscribe(sub)
            state.note_phone_stream(-1)
            if sub.dropped:
                log(f"[PhoneOut] listener gone — {sub.dropped} frame(s) dropped "
                    "while it was behind")
            else:
                log("[PhoneOut] listener gone")

    # ── POST /api/notify ─────────────────────────────────────────────────────

    async def notify_ep(req: Request) -> JSONResponse:
        """Produce a notification. The producer-facing half of server/notify.py,
        for anything that is not already inside this process.

        Authority: the same bearer that /api/command accepts, which already runs
        arbitrary commands on this machine. Being able to raise a notification
        is strictly less than that, so this adds no new privilege — it reuses
        the one the device already holds.

        It is also how the Android side is tested end to end without waiting for
        a real producer: curl a CRITICAL one and watch the phone.
        """
        if not _authorised(req):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            body = await req.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        note = _get_notify_hub().notify(
            priority = body.get("priority") or "USEFUL",
            title    = body.get("title") or "JARVIS",
            text     = body.get("text") or "",
        )
        if note is None:
            # Empty body, or a level the table never delivers. Refusing loudly
            # beats a 200 for something that was silently dropped.
            return JSONResponse(
                {"ok": False, "reason": "refused (empty text, or TRIVIAL)"},
                status_code=400,
            )
        return JSONResponse({"ok": True, "id": note.id, "priority": note.priority})

    app.add_api_route("/health", health, methods=["GET"])
    app.add_api_route("/status", status, methods=["GET"])
    app.add_api_route("/api/notify", notify_ep, methods=["POST"])
    app.add_api_websocket_route("/ws/phone-out", phone_out)
