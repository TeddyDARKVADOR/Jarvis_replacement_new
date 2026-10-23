"""
server/ — the 24/7 layer built AROUND MARK LIII, never inside it.

Nothing in this package is imported by main.py, dashboard/server.py or any
action or plugin. The dependency arrow points one way only:

    server/  ──────►  main.py, dashboard/, core/, memory/
    server/  ◄──X──   (nothing existing imports server/)

That is the whole design constraint. (`local_hud.py`, the desktop's local
mode, borrows `headless_ui` and `assistant_control` from here — the arrow still
points away from main.py.)

How the same MatthewAssistantLive runs without a screen and without a sound card:

    run_headless.py
        │  installs a stand-in in sys.modules BEFORE importing main
        └── "sounddevice" → audio_bridge stub      (null mic, phone speaker)
              │
              ▼
        import main
        main.MatthewAssistantLive(HeadlessUI(...))   ← same class, same run(), same tools
"""
