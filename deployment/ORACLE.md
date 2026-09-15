# Deploying MARK LIII on an Oracle Cloud VPS

Runbook for putting the headless assistant on a Linux VM and connecting the
Android client to it over a private network.

Written for whoever operates the server. It assumes shell access and nothing
else about the machine.

```
📱 Android  ──tailscale──►  Oracle VM  ──►  MARK LIII headless  ──►  Gemini
```

---

## 0. What you need first

| | |
|---|---|
| **VM** | Oracle "Always Free" Ampere A1 (ARM) or VM.Standard.E2.1.Micro (x86). 1 GB RAM is enough: the assistant holds no model, it forwards audio. |
| **OS** | Ubuntu 22.04+ or Oracle Linux 9. Needs **Python 3.11 or newer** — `main.py` uses `asyncio.TaskGroup`. |
| **Disk** | ~500 MB with the recommended dependencies. |
| **Gemini API key** | From aistudio.google.com. It never leaves the server. |
| **Tailscale account** | Free tier. See [TAILSCALE.md](TAILSCALE.md). |

**Do not open port 8000 to the internet.** The audio sockets carry unencrypted
PCM and the dashboard speaks plain HTTP unless you add certificates. Tailscale
is what makes this safe; a public port is what makes it a microphone anyone can
subscribe to.

---

## 1. Create the VM

Oracle Cloud → Compute → Instances → Create.

* Image: Ubuntu 22.04 (or later)
* Shape: VM.Standard.A1.Flex, 1 OCPU / 6 GB is generous and free
* Add your SSH public key
* **Networking: leave the default security list alone.** Nothing needs to be
  opened. Tailscale connects outbound.

```bash
ssh ubuntu@<public-ip>
```

## 2. System packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git rsync
python3 --version          # must be 3.11 or newer
```

If your distribution ships something older, install a newer Python (deadsnakes
PPA on Ubuntu, `dnf module` on Oracle Linux) and pass it to the installer:
`sudo PYTHON=/usr/bin/python3.12 bash deployment/install.sh`.

## 3. Get the repository

```bash
git clone https://github.com/TeddyDARKVADOR/Jarvis_replacement_new.git
cd Jarvis_replacement_new
```

## 4. Install

```bash
sudo bash deployment/install.sh
```

It creates the `jarvis` system user, copies the code to `/opt/jarvis`, builds a
virtualenv, installs `deployment/requirements-server.txt`, sets permissions and
installs the systemd unit. It is idempotent and never overwrites `config/`.

It deliberately does **not** install any secret, and does **not** start the
service.

## 5. The Gemini key

```bash
sudo -u jarvis tee /opt/jarvis/config/api_keys.json >/dev/null <<'EOF'
{"gemini_api_key": "AIza..."}
EOF
sudo chmod 600 /opt/jarvis/config/api_keys.json
```

Optional fields the desktop also understands: `assistant_name`, `user_name`,
`voice`, `wake_word_enabled`. Leave `wake_word_enabled` **false** on a server —
the wake word belongs to the phone, and a server that comes up "asleep" will
ignore audio until something wakes it.

## 6. Self-test before anything else

```bash
cd /opt/jarvis
sudo -u jarvis /opt/jarvis/.venv/bin/python -m server.selftest
```

Expect **12/12**.

**`cd /opt/jarvis` first, and it matters.** `python -m` puts the current
directory at the front of `sys.path`, so running this from your git clone tests
the *clone* rather than the installed copy — with the clone's paths, its
`config/`, and its permissions. The symptom is a handful of failures mentioning
a directory you did not expect.

This runs with no Gemini connection and no socket: if it fails, nothing
downstream is worth trying.

## 7. Run it by hand once

```bash
cd /opt/jarvis
sudo -u jarvis /opt/jarvis/.venv/bin/python -m server.run_headless
```

Expect, within a few seconds:

```
[Headless] MARK LIII starting — main.py, actions, plugins and memory unchanged.
[Actions] Action discovery complete: 11 active.
[JARVIS] SYS: /health, /status and /ws/phone-out are up.
[JARVIS] SYS: paired device …XXXXXX restored from device_credentials.json.
[JARVIS] SYS: JARVIS online.
```

**11 actions, not 16, is correct on a server.** Five desktop-control actions
(`computer_control`, `computer_settings`, `desktop`, `send_message`,
`youtube_video`) import `pyautogui`, which needs an X display. The loader
rejects them and logs a traceback; nothing else is affected. A VPS has no mouse
to move.

From a second shell:

```bash
curl http://127.0.0.1:8000/health
# {"ok":true,"service":"MARK LIII","uptime_s":12.4}
```

Stop it with Ctrl-C.

## 8. Pair the phone

```bash
cd /opt/jarvis
sudo -u jarvis /opt/jarvis/.venv/bin/python -m server.run_headless --pairing
```

Copy the `device_token`. It is printed twice — once on its own line and once
inside the example `curl` command — so redact both if you paste this anywhere.

It is generated once into `config/device_credentials.json` (mode 0600) and
reused at every start, so the phone stays paired across restarts.

Treat it as a password: it is full control of JARVIS.

## 9. Tailscale

Follow [TAILSCALE.md](TAILSCALE.md), then note the server's tailnet name:

```bash
tailscale status
# 100.x.y.z   jarvis-vps   ...
tailscale cert --help      # only if you later want real TLS
```

Verify from the phone (with Tailscale connected), in a browser:
`http://jarvis-vps.tailnet-XXXX.ts.net:8000/health`

## 10. Start the service

```bash
sudo systemctl enable --now jarvis
systemctl status jarvis
journalctl -u jarvis -f
```

## 11. Connect the Android client

Install the **release** APK (the debug build allows cleartext to any host and
must not be used outside your LAN):

```bash
cd client-android
ANDROID_HOME=$HOME/Android/Sdk ./gradlew :app:assembleRelease
```

In the app: **Host** = the MagicDNS name (`jarvis-vps.tailnet-XXXX.ts.net`),
**Port** = 8000, **Device token** = from step 8. Then GRANT → CONNECT →
START MIC.

The release build only permits cleartext to `*.ts.net` and loopback. A raw
`100.x.y.z` address will be refused — use the MagicDNS name, or add that exact
address to `app/src/main/res/xml/network_security_config.xml`.

## 12. Verify the whole chain

```bash
TOKEN=$(sudo -u jarvis python3 -c \
  "import json;print(json.load(open('/opt/jarvis/config/device_credentials.json'))['device_token'])")
BEARER=$(curl -s -X POST http://127.0.0.1:8000/api/device-login \
  -H 'Content-Type: application/json' -d "{\"device_token\":\"$TOKEN\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s http://127.0.0.1:8000/status -H "Authorization: Bearer $BEARER" | python3 -m json.tool
```

Healthy output, with the phone connected and talking:

| Field | Expected |
|---|---|
| `state` | `LISTENING`, or `THINKING`/`SPEAKING` mid-turn |
| `session_live` | `true` |
| `phone.audio_streams` | `1` |
| `phone_mic_streaming` | `true` while you are speaking |
| `counts.reconnects` | low and stable |
| `audio_out.frames` | climbing when JARVIS answers |

Watch it live over time:

```bash
sudo -u jarvis /opt/jarvis/.venv/bin/python -m server.watch
```

One line per transition, nothing while it is healthy.

## 13. Restart and reboot

```bash
sudo systemctl restart jarvis        # the phone reconnects on its own
sudo reboot                          # the service comes back with the machine
```

After the reboot, `systemctl status jarvis` should be `active (running)` with no
manual step. The phone's bearer token is invalidated by a restart (they live in
the server's RAM); the client logs in again automatically and you should see a
single `LOGIN` line in `server.watch`.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `No Gemini API key found` at startup | step 5 not done, or the file is not readable by `jarvis` |
| App fails to start, error mentions **Form data / python-multipart** | `python-multipart` missing. FastAPI needs it to *build* the upload route, so the whole app refuses to start and the message names an upload nobody attempted. |
| `Action rejected: … 'DISPLAY'` | Expected on a server. See step 7. |
| Phone shows `RECONNECTING` forever | Tailscale down on one side, or the release APK pointed at a non-`ts.net` host (cleartext refused, silently) |
| Phone shows `ERROR: device token refused` | `config/device_credentials.json` was regenerated. Re-pair (step 8). |
| `/health` works locally, not from the phone | the server is bound on `0.0.0.0:8000`; check `tailscale status` on both ends before suspecting the app |
| Gemini session drops every 2–3 min in `server.watch` | **Known, not yet fixed.** See [../server/README.md](../server/README.md#known-bugs). It recovers automatically and is inaudible. |

## Notes on what the server writes

| Path | Contents |
|---|---|
| `config/api_keys.json` | Gemini key. 0600. Never committed (`.gitignore`). |
| `config/device_credentials.json` | The phone's credential. 0600. Generated on first start. |
| `memory/long_term.json` | Everything JARVIS remembers about you. |
| `uploads/` | Files sent from the phone. |

`journalctl -u jarvis` contains no conversation as shipped — the unit passes
`--quiet`. Drop that flag to log every line the desktop HUD would have shown,
including what you say. That is a deliberate opt-in.

`GET /status` **does** include recent conversation lines in its `log` array. It
requires the bearer token, so it is the owner reading their own transcript, but
do not paste a `/status` dump into a bug report without reading it first.
