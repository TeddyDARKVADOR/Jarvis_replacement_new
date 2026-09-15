"""
server/auth.py — pairing a phone with a server that has no screen.

THE PROBLEM
    dashboard/server.py's pairing is sound but desktop-shaped: the six-character
    key comes from new_key(), which is called when the user presses REMOTE
    CONTROL in the Qt window (main.py:598). On a VPS there is no window and no
    button, so there is no way to obtain a key.

    It also keeps paired devices in RAM (`_device_sessions`, dashboard/server.py
    :382). Fine for a desktop session; on a 24/7 server every restart — every
    deploy, every crash, every `systemctl restart` — would un-pair the phone.

WHAT THIS ADDS, WITHOUT CHANGING ANY OF IT
    One long-lived device credential, generated once, stored on the server, and
    injected into the dashboard's own `_device_sessions` at boot. From then on
    the phone uses the endpoint that already exists:

        POST /api/device-login  {"device_token": "..."}
          → {"ok": true, "token": "<bearer>", "key": "<session key>"}

    which is the same path dashboard/server.py:549 already serves for a browser
    that had paired earlier. No new authentication scheme, no second token
    format, no change to the existing one.

WHERE THE SECRET LIVES
    config/device_credentials.json, 0600.

    The name is not decorative: .gitignore already carries `*credentials*.json`,
    so this file is ignored by the repository as it stands. Picking a name the
    existing rules already cover is what let this land without editing
    .gitignore — and it means the secret cannot be committed by accident.
"""
from __future__ import annotations

import json
import os
import secrets
import stat
from datetime import datetime
from pathlib import Path

BASE_DIR  = Path(__file__).resolve().parent.parent
CRED_PATH = BASE_DIR / "config" / "device_credentials.json"


def load_or_create() -> dict:
    """Return {"device_token", "session_key", "created"}, creating it once.

    The session key is the material dashboard/server.py hashes into the AES key
    for encrypted commands (_derive_key). The browser flow uses a 6-character
    one-time PIN there; a permanent credential has no reason to be that short,
    so this one is 32 URL-safe characters."""
    if CRED_PATH.exists():
        try:
            data = json.loads(CRED_PATH.read_text(encoding="utf-8"))
            if data.get("device_token") and data.get("session_key"):
                return data
        except Exception:
            # Corrupt file: regenerate rather than leave the server unpairable.
            # The cost is re-pairing the phone once, which is a QR scan.
            pass

    data = {
        "device_token": secrets.token_urlsafe(32),
        "session_key":  secrets.token_urlsafe(24),
        "created":      datetime.now().isoformat(timespec="seconds"),
    }
    CRED_PATH.parent.mkdir(parents=True, exist_ok=True)
    CRED_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(CRED_PATH, stat.S_IRUSR | stat.S_IWUSR)   # 0600
    except OSError:
        pass
    return data


def seed_dashboard(dashboard, creds: dict | None = None) -> str:
    """Teach the running DashboardServer about the persistent device.

    Writes into `_device_sessions` — the same dict /api/device-login reads, and
    the same shape /auto-login writes (dashboard/server.py:524). Nothing is
    replaced; a browser that pairs by QR code still gets its own entry."""
    creds = creds or load_or_create()
    token = creds["device_token"]
    key   = creds["session_key"]
    dashboard._device_sessions[token] = {"session_key": key}
    # Pre-derive and cache the AES key so the first encrypted command from the
    # phone does not pay for it.
    try:
        dashboard._aes_key(key)
    except Exception:
        pass
    return token


def mint_pairing_code(dashboard, expiry_secs: int = 600) -> str:
    """A one-time six-character code, for the browser/QR flow.

    Straight through to DashboardServer.new_key(): the button in the desktop UI
    calls exactly this. Here it is reachable from a terminal on the server."""
    return dashboard.new_key(expiry_secs=expiry_secs)


def describe(creds: dict | None = None, host: str = "<server>",
             port: int = 8000) -> str:
    creds = creds or load_or_create()
    return (
        "── MARK LIII device credential ─────────────────────────────────\n"
        f"  file          : {CRED_PATH}\n"
        f"  created       : {creds.get('created', '?')}\n"
        f"  device_token  : {creds['device_token']}\n"
        "\n"
        "  The Android client exchanges it for a bearer token:\n"
        f"    curl -s http://{host}:{port}/api/device-login \\\n"
        "         -H 'Content-Type: application/json' \\\n"
        f"         -d '{{\"device_token\":\"{creds['device_token']}\"}}'\n"
        "\n"
        "  Treat it like a password: it is full control of JARVIS.\n"
        "  Revoke every paired device with POST /api/revoke-devices,\n"
        "  or delete the file above and restart the service.\n"
        "────────────────────────────────────────────────────────────────"
    )


if __name__ == "__main__":
    import socket
    print(describe(host=socket.gethostname()))
