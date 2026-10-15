"""
The HUD — JARVIS on a workstation, as a full holographic interface.

```
python main.py            the HUD, JARVIS on Oracle
python main.py --local    the HUD, JARVIS on this PC
```

The HUD is the only desktop interface. The controller (`app.JarvisDesktop`),
the network worker, the store, the wake word, the tray and the debug window
feed it; `HudWindow` renders their snapshot.

The visual is a web page (`web/`) hosted in one `QWebEngineView`: three.js,
shaders and CSS animation are a far richer palette than QPainter. three.js is
vendored in `web/vendor/`.
"""
