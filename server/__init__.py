"""
server/ — the 24/7 layer built AROUND MARK LIII, never inside it.

Nothing in this package is imported by main.py, ui.py, dashboard/server.py or
any action or plugin. The dependency arrow points one way only:

    server/  ──────►  main.py, dashboard/, core/, memory/
    server/  ◄──X──   (nothing existing imports server/)

That is the whole design constraint, and it is what makes `python main.py` on a
desktop byte-for-byte the program it was before this package existed.

How the same JarvisLive runs without a screen and without a sound card:

    run_headless.py
        │  installs two stand-ins in sys.modules BEFORE importing main
        ├── "ui"          → headless_ui.JarvisUI   (the 26-attribute façade)
        └── "sounddevice" → audio_bridge stub      (null mic, phone speaker)
              │
              ▼
        import main            ← unmodified
        main.JarvisLive(ui)    ← same class, same run(), same tools
"""
