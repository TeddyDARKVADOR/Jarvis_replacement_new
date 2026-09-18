"""
JARVIS Desktop — a permanent MARK LIII terminal for a Windows workstation.

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

Nothing here is imported by `main.py`, `ui.py`, `dashboard/`, `server/`,
`actions/`, `plugins/` or `memory/`. `server/selftest.py` fails if any of those
report a git modification, so this package must remain strictly additive. The
one thing it reaches into is `core.wake_word`, which it only ever imports.

**Why it does not reuse `JarvisLive`.** `JarvisLive` (`main.py`) *is* the brain:
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
