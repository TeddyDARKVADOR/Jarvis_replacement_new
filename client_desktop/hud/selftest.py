"""
`python -m client_desktop.hud --selftest` — offline, no window, no network.

What it proves, in the order things would break:

  1  the HUD modules import
  2  `HudWindow` has every signal `JarvisPanel` has
  3  `HudWindow` has every `self.panel.<name>` the controller uses
  4  the scheme serves the page and three.js, and refuses traversal
  5  every file the page references exists
  6  preferences survive a round trip, and junk keys are dropped
  7  the face driver stays silent until enabled, then performs
  8  the machine metrics produce numbers
  9  the existing client is untouched by this package
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_HERE = Path(__file__).resolve().parent


class _Report:
    def __init__(self) -> None:
        self.passed = self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.passed += 1
            print(f"[  ok  ] {name}")
        else:
            self.failed += 1
            print(f"[ FAIL ] {name}" + (f" - {detail}" if detail else ""))


def _signals(cls) -> set[str]:  # noqa: ANN001
    from PyQt6.QtCore import pyqtSignal
    return {n for n in dir(cls) if isinstance(getattr(cls, n, None), type(pyqtSignal()))}


def run() -> bool:  # noqa: C901
    r = _Report()
    print("JARVIS HUD - selftest\n")

    # 1
    try:
        from client_desktop.hud import face, metrics, prefs, scheme, window  # noqa: F401
        from client_desktop.ui.panel import JarvisPanel
        r.check("1  the HUD modules import", True)
    except Exception as exc:
        r.check("1  the HUD modules import", False, repr(exc))
        return False
    HudWindow = window.HudWindow

    # 2
    missing = {n for n in _signals(JarvisPanel) if not hasattr(HudWindow, n)}
    r.check("2  every panel signal exists on the HUD", not missing, f"missing {sorted(missing)}")

    # 3
    app_src = (_REPO / "client_desktop" / "app.py").read_text(encoding="utf-8")
    used = set(re.findall(r"self\.panel\.([A-Za-z_]\w*)", app_src))
    absent = {n for n in used if not hasattr(HudWindow, n) and n != "_collapsed"}
    has_collapsed = "_collapsed" in (_HERE / "window.py").read_text(encoding="utf-8")
    r.check(f"3  the controller's {len(used)} panel members exist on the HUD",
            not absent and has_collapsed, f"missing {sorted(absent)}")

    # 4
    ok_index = scheme.resolve("/index.html") is not None
    ok_three = scheme.resolve("/vendor/three.module.min.js") is not None
    bad = ["/../ui/panel.py", "/vendor/../../client_desktop/config.py",
           "/..%2f..%2fmain.py", "/css/../../window.py"]
    refused = all(scheme.resolve(p) is None for p in bad)
    r.check("4  the scheme serves the page and three.js, refuses traversal",
            ok_index and ok_three and refused,
            f"index={ok_index} three={ok_three} refused={refused}")

    # 5
    web = _HERE / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:href|src)="((?:css|js)/[^"]+)"', html)
    import posixpath
    js_refs = []
    for js in (web / "js").glob("*.js"):
        for m in re.findall(r"""(?:from|import\()\s*['"](\.{1,2}/[^'"]+)['"]""", js.read_text(encoding="utf-8")):
            js_refs.append(posixpath.normpath("/js/" + m))
    missing_files = [p for p in refs if scheme.resolve("/" + p) is None]
    missing_files += [p for p in js_refs if scheme.resolve(p) is None]
    r.check(f"5  all {len(refs) + len(js_refs)} page references resolve", not missing_files,
            f"missing {missing_files}")

    # 6
    old = os.environ.get("LOCALAPPDATA")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LOCALAPPDATA"] = tmp
        try:
            prefs.save({"accent": "#123456", "face": True, "evil": 1})
            back = prefs.load()
            ok = back["accent"] == "#123456" and back["face"] is True and "evil" not in back
        finally:
            if old is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old
    r.check("6  preferences round-trip, unknown keys dropped", ok)

    # 7
    try:
        from client_desktop.state import AssistantState, LinkState, Snapshot
        posted: list[dict] = []
        d = face.FaceDriver(posted.append)
        snap = Snapshot(link=LinkState.CONNECTED, assistant=AssistantState.SPEAKING, speaker_level=.6)
        d.set_snapshot(snap)
        silent = not posted
        d.enable(True)
        d.frame_ready()
        d.set_snapshot(snap)
        kinds = {m["type"] for m in posted}
        r.check("7  the face is silent until enabled, then performs and speaks",
                silent and {"performance", "speak"} <= kinds, f"silent={silent} kinds={kinds}")
    except Exception as exc:
        r.check("7  the face is silent until enabled, then performs and speaks", False, repr(exc))

    # 8
    m = metrics.Metrics()
    sample = m.sample()
    r.check("8  machine metrics produce numbers",
            not sample.get("available") or isinstance(sample.get("cpu"), (int, float)), str(sample)[:80])

    # 9
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--", "client_desktop", ":!client_desktop/hud"],
            cwd=_REPO, capture_output=True, text=True, timeout=15,
        )
        changed = [ln for ln in out.stdout.splitlines() if ln.strip()]
        r.check("9  the existing client is untouched", out.returncode == 0 and not changed,
                f"changed: {changed}")
    except Exception as exc:
        r.check("9  the existing client is untouched", False, repr(exc))

    # 10
    from client_desktop.hud import settings as hs
    try:
        from client_desktop.config import Settings
        st = Settings()
        changed = hs.apply(st, {"host": " https://jarvis-vps.tailnet.ts.net:8000/ ", "port": 0,
                                "device_token": "  abc  ", "wake_word_threshold": 7,
                                "gate_seconds": 1, "device_name": "", "evil": "x"})
        ok = (st.host == "jarvis-vps.tailnet.ts.net" and st.port == 8000 and st.device_token == "abc"
              and st.wake_word_threshold == 0.95 and st.gate_seconds == 5.0
              and "device_name" not in changed and not hasattr(st, "evil"))
        r.check("10 the settings page cleans what is pasted and clamps the rest", ok,
                f"host={st.host!r} port={st.port} th={st.wake_word_threshold} gate={st.gate_seconds}")
    except Exception as exc:
        r.check("10 the settings page cleans what is pasted and clamps the rest", False, repr(exc))

    # 11
    launcher = _HERE / "launch_hud.pyw"
    r.check("11 the Windows start-up entry points at the HUD launcher",
            launcher.is_file() and str(launcher) in hs._command() and hs.VALUE_NAME != "JarvisDesktop")

    # 12
    head = (_REPO / "main.py").read_text(encoding="utf-8").split("import platform", 1)[0]
    r.check("12 python main.py opens the HUD (online, --local), --classic keeps the old window",
            "client_desktop.hud.__main__" in head and "local_hud" in head and "--classic" in head
            and 'if __name__ == "__main__":' in head)

    print(f"\n{r.passed}/{r.passed + r.failed}")
    return r.failed == 0
