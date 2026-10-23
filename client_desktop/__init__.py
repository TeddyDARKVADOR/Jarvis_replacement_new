"""
Matthew Desktop — a permanent MARK LIII terminal for a Windows workstation.

This package is a **client**. It holds no model, opens no Gemini session and
owns no memory. The brain is MARK LIII on the Oracle VPS; this is a second
window onto it, beside the Android one.

```
                 ☁️ ORACLE
              MARK LIII + Gemini
                     │
                  Tailscale
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
       📱 Android           💻 client_desktop
```

**The rule this package is built on — the same one `server/` was built on:**

```
client_desktop/  ──────►  core.wake_word   (import, read-only)
client_desktop/  ◄──X──   (nothing existing imports this package)
```

Nothing in `main.py`, `dashboard/`, `server/`, `actions/`, `plugins/` or
`memory/` imports this package. The one thing it reaches into is
`core.wake_word` (and `core.action_loader` to prove its capabilities), which
it only ever imports. `local_hud.py`, beside main.py, reuses its HUD to show
the brain running on this PC.

**Why it does not reuse `MatthewAssistantLive`.** `MatthewAssistantLive` (`main.py`) *is* the brain:
constructing one opens a Gemini Live session. Oracle already runs exactly one.
A desktop client that instantiated it would be a second assistant wearing the
same name — two sessions, two conversation histories, two bills. So this package
speaks the wire protocol instead, and could not open a Gemini session if it
tried: the API key never leaves the server.

Run it with:

```
python -m client_desktop              # the panel
python -m client_desktop --selftest   # no window, no network, no microphone
python -m client_desktop --debug      # panel + the developer window open
```
"""

__all__ = ["__version__"]

__version__ = "1.0.0"

# ctranslate2 (le moteur de faster-whisper : relais hors-ligne, copilote de
# réunion) AVANT Qt. PyQt6 embarque ses propres msvcp140.dll, plus anciens ;
# chargés en premier, ctranslate2 plante le processus (segfault) à son premier
# usage — mesuré le 2026-10-16 : Qt puis ctranslate2 → code 139 ; l'inverse
# → aucun souci. Les deux points d'entrée Qt (python -m client_desktop et
# local_hud.py) passent par ce paquet avant d'importer PyQt6.
try:
    import ctranslate2  # noqa: F401
except Exception:
    pass
