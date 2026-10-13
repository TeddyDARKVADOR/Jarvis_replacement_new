"""
The HUD — JARVIS on a workstation, as a full holographic interface.

```
python -m client_desktop.hud              the HUD
python -m client_desktop.hud --selftest   offline checks, no window
```

Built **beside** the panel, not over it. Nothing in `client_desktop/` was edited
to make this exist: the controller (`app.JarvisDesktop`), the network worker,
the store, the wake word, the tray and the debug window are the same objects
running the same code. The only difference is the class that renders the
snapshot — `HudWindow` instead of `JarvisPanel` — and it is a drop-in: same
signals, same methods, so the controller cannot tell them apart.

The visual is a web page (`web/`) hosted in one `QWebEngineView`: three.js,
shaders and CSS animation are a far richer palette than QPainter. three.js is
vendored in `web/vendor/`.
"""
