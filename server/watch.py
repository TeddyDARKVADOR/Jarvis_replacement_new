"""
server/watch.py — a timeline of what the link actually did.

    python3.14 -m server.watch                  # follow until Ctrl-C
    python3.14 -m server.watch --minutes 30 --out run.log

WHY THIS EXISTS
    The 24/7 questions — does the service survive the screen going off, does the
    phone come back after the Wi-Fi drops, how many times did it reconnect in an
    hour — are all questions about *transitions*, and /status only ever reports
    now. Polling it by hand answers none of them.

    So this polls once a second and writes a line only when something changes.
    An hour of a healthy link is a handful of lines; an hour of a flapping one
    is a diagnosis.

WHAT EACH LINE MEANS
    STATE   MARK LIII's own state (LISTENING → THINKING → SPEAKING).
            THINKING followed by SPEAKING after you talk is proof the whole
            round trip worked: phone mic → Gemini → phone speaker.
    PHONE   the /ws/phone-out socket count. 1 → 0 means the app lost the link
            (or was killed); 0 → 1 with a gap is a reconnect, and the gap is
            what the reconnect policy actually cost.
    MIC     whether the phone is streaming audio right now (needs a server
            restart to appear — see phone_mic_streaming in run_headless).
    AUDIO   JARVIS speaking, in seconds of PCM sent to the phone.
    LOGIN   a device-login. Each one is the Android client reconnecting.
    GEMINI  the Live session dropping or coming back.
    ERROR   anything MARK LIII logged as ERR:/NET:.

    One bearer is taken at startup and reused: every device-login shows up in
    the counters, so a monitor that re-authenticated each second would fake the
    very reconnections it is meant to measure.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _post(url: str, payload: dict, bearer: str | None = None, timeout: float = 5.0):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get(url: str, bearer: str, timeout: float = 5.0):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def login(base: str) -> str:
    creds = json.loads((BASE_DIR / "config" / "device_credentials.json")
                       .read_text(encoding="utf-8"))
    return _post(f"{base}/api/device-login",
                 {"device_token": creds["device_token"]})["token"]


def main() -> int:
    ap = argparse.ArgumentParser(prog="server.watch")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--minutes", type=float, default=0, help="0 = until Ctrl-C")
    ap.add_argument("--out", default="")
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()

    out = open(args.out, "a", buffering=1, encoding="utf-8") if args.out else sys.stdout

    def say(kind: str, message: str) -> None:
        print(f"{datetime.now():%H:%M:%S}  {kind:7} {message}", file=out, flush=True)

    try:
        bearer = login(args.base)
    except Exception as e:
        say("FATAL", f"cannot log in to {args.base}: {e}")
        return 1

    prev: dict = {}
    reachable = True          # NOT in `prev`: see the note at the first poll
    last_phone_gone: float | None = None
    started = time.monotonic()
    deadline = started + args.minutes * 60 if args.minutes else None
    relogins = 0

    say("START", f"watching {args.base} every {args.interval}s")

    while deadline is None or time.monotonic() < deadline:
        try:
            s = _get(f"{args.base}/status", bearer)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                # The server restarted: its bearers live in RAM. Re-authenticating
                # is itself the evidence.
                relogins += 1
                say("GEMINI", f"server restarted — bearer invalid, logging in again "
                              f"(#{relogins})")
                try:
                    bearer = login(args.base)
                except Exception as e2:
                    say("ERROR", f"re-login failed: {e2}")
                    time.sleep(args.interval)
                continue
            say("ERROR", f"HTTP {e.code}")
            time.sleep(args.interval)
            continue
        except Exception as e:
            if reachable:
                say("SERVER", f"unreachable ({type(e).__name__}) — is MARK LIII down?")
            reachable = False
            time.sleep(args.interval)
            continue

        # Reachability is tracked in its own variable, not in `prev`: writing it
        # into `prev` made the dict non-empty before the first real poll, so the
        # "first sample" branch below was skipped and the next line read a key
        # that had never been set.
        if not reachable:
            say("SERVER", "reachable again")
        reachable = True

        now = time.monotonic()
        phones = s["phone"]["audio_streams"]
        state = s["state"]
        live = s["session_live"]
        frames = s.get("audio_out", {}).get("bytes", 0)
        rate = s.get("audio_out", {}).get("sample_rate", 24000) or 24000
        logins = s["counts"]["phone_connects"]
        errors = s["counts"]["errors"]
        mic = s.get("phone_mic_streaming")

        if not prev:
            say("START", f"state={state} gemini={'up' if live else 'down'} "
                         f"phone_sockets={phones} logins={logins}")
        else:
            if state != prev["state"]:
                say("STATE", f"{prev['state']} → {state}")
            if live != prev["live"]:
                say("GEMINI", "session up" if live else "session DOWN")
            if phones != prev["phones"]:
                if phones < prev["phones"]:
                    last_phone_gone = now
                    say("PHONE", f"downlink socket closed ({prev['phones']} → {phones})"
                                 " — app backgrounded-killed, or network gone")
                else:
                    gap = f" after {now - last_phone_gone:.0f}s" if last_phone_gone else ""
                    say("PHONE", f"downlink socket back ({prev['phones']} → {phones}){gap}")
            if mic is not None and mic != prev.get("mic"):
                say("MIC", "phone is streaming audio" if mic else "phone stopped streaming")
            if logins != prev["logins"]:
                say("LOGIN", f"android client authenticated (total {logins})")
            if frames > prev["frames"]:
                secs = (frames - prev["frames"]) / 2 / rate
                if secs >= 0.25:
                    say("AUDIO", f"JARVIS spoke {secs:.1f}s to the phone")
            if errors != prev["errors"]:
                say("ERROR", f"{s.get('last_error')}")

        prev = {"state": state, "live": live, "phones": phones, "frames": frames,
                "logins": logins, "errors": errors, "mic": mic}
        time.sleep(args.interval)

    say("END", f"watched {(time.monotonic() - started) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nstopped.")
