"""
The HUD's settings page — what it shows, what it accepts, and how it is tested.

Everything here works on the existing `config.Settings` object and saves it
with the existing `config.save` — the same file the debug window writes, so the
two screens can never disagree about how this PC is paired. Nothing is invented
here: no second settings file for the connection, no second token.

Kept free of Qt so the selftest can exercise validation without a window.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..config import Settings, settings_path

#: Fields the page may change, with how to coerce them. Anything else in the
#: payload is ignored — the page is a form, not a way to write arbitrary keys.
_FIELDS = {
    "host": str,
    "port": int,
    "use_tls": bool,
    "device_token": str,
    "device_name": str,
    "input_device": "device",
    "output_device": "device",
    "wake_word_enabled": bool,
    "wake_word_threshold": float,
    "gate_seconds": float,
    "mic_open_at_start": bool,
    "start_collapsed": bool,
    "notifications": bool,
}

#: Changing any of these means the sockets must be rebuilt.
RECONNECT_KEYS = {"host", "port", "use_tls", "device_token", "device_name", "output_device"}


def _clean_host(value: str) -> str:
    """Accept what people paste: a URL, a trailing slash, a port, spaces."""
    v = value.strip()
    for prefix in ("https://", "http://", "wss://", "ws://"):
        if v.lower().startswith(prefix):
            v = v[len(prefix):]
    v = v.split("/")[0]
    if v.count(":") == 1:          # host:port pasted into the host field
        v = v.split(":")[0]
    return v


def snapshot(settings: Settings) -> dict:
    """Everything the page needs to draw itself."""
    from ..audio import list_devices
    from ..wake import engine_available, engine_installed

    inputs, outputs = list_devices()
    return {
        "host": settings.host,
        "port": settings.port,
        "use_tls": settings.use_tls,
        "device_token": settings.device_token,
        "device_name": settings.device_name,
        "device_id": settings.device_id,
        "input_device": settings.input_device,
        "output_device": settings.output_device,
        "inputs": inputs,
        "outputs": outputs,
        "wake_word_enabled": settings.wake_word_enabled,
        "wake_word_threshold": settings.wake_word_threshold,
        "wake_available": engine_available(),
        "wake_installed": engine_installed(),
        "gate_seconds": settings.gate_seconds,
        "mic_open_at_start": settings.mic_open_at_start,
        "start_collapsed": settings.start_collapsed,
        "notifications": settings.notifications,
        "autostart": autostart_enabled(),
        "paired": settings.is_configured,
        "settings_path": str(settings_path()),
    }


def apply(settings: Settings, data: dict) -> list[str]:
    """Copy the page's values onto `settings`. Returns the keys that changed.

    Validation is clamping, not refusal: a port of 0 becomes 8000, a threshold
    of 7 becomes 0.95. The page should never be able to save a value that
    leaves the client unable to start.
    """
    changed: list[str] = []
    for key, kind in _FIELDS.items():
        if key not in data:
            continue
        raw = data[key]
        try:
            if kind == "device":
                value = (str(raw).strip() or None) if raw is not None else None
            elif kind is bool:
                value = bool(raw)
            elif kind is int:
                value = int(raw)
            elif kind is float:
                value = float(raw)
            else:
                value = str(raw)
        except (TypeError, ValueError):
            continue

        if key == "host":
            value = _clean_host(value)
        elif key == "port":
            value = value if 1 <= value <= 65535 else 8000
        elif key in ("device_token", "device_name"):
            value = value.strip()
            if key == "device_name" and not value:
                continue        # an empty name would register a nameless device
        elif key == "wake_word_threshold":
            value = round(min(0.95, max(0.2, value)), 2)
        elif key == "gate_seconds":
            value = float(min(300.0, max(5.0, value)))

        if getattr(settings, key) != value:
            setattr(settings, key, value)
            changed.append(key)
    return changed


def test_connection(host: str, port: int, use_tls: bool, token: str) -> dict:
    """Two questions, answered separately because they fail for different reasons.

    1. Is MARK LIII reachable?        GET /health  — needs no credential.
    2. Is this token accepted?        POST /api/device-login — the same call the
                                      client makes when it connects.

    "Unreachable" sends you to Tailscale; "refused" sends you to `--pairing`.
    Collapsing them into one red light would send you to the wrong place.
    """
    import requests

    from ..protocol import ServerEndpoint

    host = _clean_host(host or "")
    if not host:
        return {"ok": False, "step": "host", "detail": "Renseignez l'adresse du serveur."}
    endpoint = ServerEndpoint(host=host, port=int(port or 8000), use_tls=bool(use_tls))
    try:
        r = requests.get(f"{endpoint.http_base}/health", timeout=6)
        info = r.json() if r.ok else {}
    except Exception as exc:
        if host.endswith(".ts.net") and not _tailscale_app_running():
            # The trap documented in client_desktop/README.md: the service runs
            # but sits in NoState until the tray app activates the profile.
            return {"ok": False, "step": "reach",
                    "detail": "Tailscale n'est pas lancé sur ce PC (tailscale-ipn.exe). "
                              "Ouvrez Tailscale depuis le menu Démarrer, puis retestez."}
        return {"ok": False, "step": "reach",
                "detail": f"Serveur injoignable ({type(exc).__name__}). Tailscale est-il connecté ?"}
    if not info.get("ok"):
        return {"ok": False, "step": "reach", "detail": f"Réponse inattendue du serveur (HTTP {r.status_code})."}

    service = info.get("service", "MARK")
    uptime = int(info.get("uptime_s") or 0)
    up = f"{uptime // 3600} h {uptime % 3600 // 60:02d}"
    if not token.strip():
        return {"ok": False, "step": "token", "service": service,
                "detail": f"{service} répond (en ligne depuis {up}), mais aucun jeton n'est saisi."}
    try:
        r = requests.post(endpoint.device_login, json={"device_token": token.strip()}, timeout=8)
    except Exception as exc:
        return {"ok": False, "step": "token", "detail": f"Connexion coupée pendant l'authentification ({type(exc).__name__})."}
    if r.status_code == 401:
        return {"ok": False, "step": "token", "service": service,
                "detail": "Jeton refusé. Générez-en un sur Oracle avec --pairing."}
    if not r.ok:
        return {"ok": False, "step": "token", "detail": f"Authentification impossible (HTTP {r.status_code})."}
    return {"ok": True, "step": "done", "service": service,
            "detail": f"{service} en ligne depuis {up} — jeton accepté."}


def _tailscale_app_running() -> bool:
    """True if the Tailscale tray app is running — or if we cannot tell."""
    if sys.platform != "win32":
        return True
    try:
        import psutil
        return any((p.info.get("name") or "").lower() == "tailscale-ipn.exe"
                   for p in psutil.process_iter(["name"]))
    except Exception:
        return True


# ── starting with Windows ────────────────────────────────────────────────────
#
# Its own Run value, beside the panel's (`autostart.VALUE_NAME`) rather than
# rewriting it: the panel's entry belongs to the panel. Enabling the HUD
# removes the panel's entry, so that Windows does not start both and leave the
# single-instance mutex to pick one at random.

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "JarvisHUD"


def _command() -> str:
    from ..autostart import pythonw

    launcher = Path(__file__).resolve().parent / "launch_hud.pyw"
    return f'"{pythonw()}" "{launcher}"'


def autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
        return bool(value)
    except OSError:
        return False


def set_autostart(enabled: bool) -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "uniquement sous Windows"
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        return False, str(exc)
    if enabled:
        from .. import autostart
        if autostart.is_enabled():
            autostart.disable()
            return True, "HUD au démarrage (l'ancien panneau n'y est plus)"
        return True, "HUD au démarrage"
    return True, "démarrage automatique désactivé"
