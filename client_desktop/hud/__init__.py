"""
The HUD — Matthew on a workstation, as a full holographic interface.

```
python main.py            the HUD, Matthew on Oracle
python main.py --local    the HUD, Matthew on this PC
```

The HUD is the only desktop interface. The controller (`app.MatthewAssistantDesktop`),
the network worker, the store, the wake word, the tray and the debug window
feed it; `HudWindow` renders their snapshot.

The visual is a web page (`web/`) hosted in one `QWebEngineView`: three.js,
shaders and CSS animation are a far richer palette than QPainter. three.js is
vendored in `web/vendor/`.
"""
