# Phase 7 — Oracle deployment: where we are

Handover note. Phase 7 is **started but not finished**, and it is blocked on one
thing only: the VM does not exist yet.

Last updated: 15 September 2026, 16:20.

---

## The blocker

Creating an Oracle Cloud VM needs the Oracle web console, your account and
possibly identity verification. Checked on this machine:

```
oci CLI            absent
~/.oci             absent
SSH config         no Oracle host
known_hosts        github.com, git.ecole-89.com, 89.168.63.255 — no VM
tailscale          not installed on this PC
```

So **steps 1 and 6 of `ORACLE.md` are yours**: create the VM, install Tailscale.
Everything after that can be done from here once there is an SSH target.

---

## What was done instead, and what it proved

### 1. Git history audit (`ORACLE.md` step 3) — done

| Searched | Result |
|---|---|
| `config/api_keys.json` in any commit | **never committed** ✓ |
| `config/device_credentials.json` | **never committed** ✓ |
| `memory/long_term.json` | **never committed** ✓ |
| `AIza…` strings in history | only `ui.py`'s placeholder text (`setPlaceholderText("AIza…")`, 4 chars) — **not a key** ✓ |
| `config/certs/jarvis.key` | ⚠️ **committed in `067f528`, deleted in `8a79f40`, still reachable in history** |

**Your Gemini key was never exposed.** That was the thing that mattered and it
is clean.

But a real RSA private key is in the history of a **public** repository
(`https://github.com/TeddyDARKVADOR/Jarvis_replacement_new` answers HTTP 200).
It is the dashboard's self-signed TLS key.

**Decision needed — nothing was done about it:**

* Practical impact today is low: the Tailscale deployment uses plain HTTP inside
  the tunnel and never loads `config/certs/`.
* But that keypair must be treated as compromised. If you ever enable the
  dashboard's HTTPS, **generate a new one** — do not reuse those files.
* Removing it from history means rewriting history (`git filter-repo` or BFG)
  and a force-push. That is destructive and affects the remote, so it was not
  done without your say-so.

### 2. Deployment rehearsed in a clean Ubuntu 24.04 container — done

Since there is no VM, the whole install path was run against a fresh Ubuntu with
nothing preinstalled. This is what a first Oracle deployment would hit.

Validated end to end:

```
Python detection          → found /usr/bin/python3.12
system user `jarvis`      → created
code copied to /opt/jarvis (config/ correctly excluded)
virtualenv + requirements-server.txt → installed
required-imports check    → all present
permissions               → config/ 0700, api_keys.json 0600
systemd unit              → installed to /etc/systemd/system/
run_headless              → started
GET /health               → {"ok":true,"service":"MARK LIII","uptime_s":1.7}
--pairing                 → device credential generated
```

Not validated, and cannot be from a container: `systemctl enable/start`, reboot
survival, Tailscale, the Android release APK against a remote host.

### 3. Two defects found and fixed

**`jarvis.service`** — `StartLimitIntervalSec` and `StartLimitBurst` were in the
`[Service]` section. Modern systemd **silently ignores them there**
(`systemd-analyze verify` says "Unknown key"), so the unit looked rate-limited
and was not. Moved to `[Unit]`. `systemd-analyze verify` is now clean.

**`install.sh`** — died with `set -e` when `systemctl` was missing, throwing away
an otherwise successful install. Now warns and continues.

### 4. A deployment trap worth knowing

Running the self-test from the git clone instead of `/opt/jarvis` tests the
**clone**, not the installed copy: `python -m` puts the current directory first
on `sys.path`. In the rehearsal this produced four confusing failures pointing at
a read-only directory. `ORACLE.md` steps 6, 7 and 8 now all start with
`cd /opt/jarvis`.

---

## State of your machines right now

| | |
|---|---|
| **Repository** | `selftest 12/12`. `git diff -- main.py dashboard core memory actions plugins` empty. New: `server/`, `client-android/`, `deployment/`. Modified: `.gitignore` only (the security fix from Phase 6). |
| **Local MARK LIII server** | **stopped.** `curl 127.0.0.1:8000/health` does not answer. It was running in your terminal; I cannot tell from here whether it exited on its own or was stopped. Restart: `cd "…/Jarvis_replacement_new" && python3.14 -m server.run_headless` |
| **Phone (Redmi)** | JARVIS service **stopped** since the Phase 5 force-stop test. Unlock the phone, open JARVIS, CONNECT + START MIC. Wake word is **on** in its settings. |
| **Background tasks** | all stopped (timeline watcher, logcat capture, containers). |

---

## Next session — in order

1. **Create the Oracle VM.** `ORACLE.md` step 0–1. Always Free Ampere A1, Ubuntu
   22.04+, your SSH key, **do not open any port** in the security list.
2. Give me the SSH target (`ssh ubuntu@<ip>`) and I can drive steps 2–5, 7–10
   and 12 from here.
3. **Tailscale** on the VM and on the phone — `TAILSCALE.md`. You will need to
   approve both machines in the admin console.
4. Build and install the **release** APK, point it at the MagicDNS name.
5. Then the tests: audio both ways, wake word, screen off, `systemctl restart`,
   `reboot`.

### Decide before then

* The leaked TLS key: leave it (and never use that keypair), or rewrite history.
* Whether I get SSH access to the VM, or you run `install.sh` yourself and I
  guide you.

---

## Still deliberately untouched

The 1008 Gemini drop, `/api/wake`, phone-side interrupt, and the headless
confirmation gate are all still open and unmodified — see
[`../server/README.md`](../server/README.md#known-bugs). `main.py`,
`dashboard/`, `core/`, `memory/`, `actions/` and `plugins/` have not been
modified in any phase.
