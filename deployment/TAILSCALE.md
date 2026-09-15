# Private network between the phone and the server

Why Tailscale is not optional here, and how to set it up.

---

## The reason

MARK LIII's audio sockets carry **raw PCM with no encryption**:

```
WS /ws/phone-audio   your voice, 16 kHz, in the clear
WS /ws/phone-out     JARVIS's voice, 24 kHz, in the clear
```

The AES-256-CBC layer in `dashboard/server.py` covers **typed commands only**.
It does nothing for audio. And the dashboard serves plain HTTP unless
`config/certs/` is populated.

So something underneath has to encrypt the link. Tailscale is WireGuard: the
traffic is encrypted end to end, the server is reachable only by devices you
have authorised, and **nothing is exposed to the internet** — Tailscale connects
outbound, so no inbound port is opened anywhere.

The alternative — port 8000 forwarded from the internet — means anyone who finds
it can subscribe to a live microphone. Do not do that.

---

## Server

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh --hostname=jarvis-vps
```

It prints a URL. Open it, sign in, approve the machine.

```bash
tailscale status
# 100.101.102.103  jarvis-vps  you@  linux  -
tailscale ip -4
```

Enable **MagicDNS** in the Tailscale admin console (DNS → MagicDNS). The server
then has a stable name:

```
jarvis-vps.tailnet-XXXX.ts.net
```

**Use the name, not the IP.** Two reasons: the Android release build only
permits cleartext to `*.ts.net`, and a name survives the address changing.

### Keep it authenticated

By default a Tailscale node key expires after ~6 months and the server silently
falls off the tailnet. For an always-on machine, disable expiry:

Admin console → Machines → `jarvis-vps` → ⋯ → **Disable key expiry**.

Or use a tagged auth key, which does not expire:

```bash
sudo tailscale up --authkey=tskey-auth-XXXX --advertise-tags=tag:server
```

---

## Phone

1. Install **Tailscale** from Play Store.
2. Sign in with the same account.
3. Leave it connected. Android keeps it up in the background.

Check it reaches the server, in the phone's browser:

```
http://jarvis-vps.tailnet-XXXX.ts.net:8000/health
```

`{"ok":true,...}` means the tunnel and the server are both fine, and the only
thing left to configure is the JARVIS app itself.

---

## What to put in the app

| Field | Value |
|---|---|
| Host | `jarvis-vps.tailnet-XXXX.ts.net` |
| Port | `8000` |
| TLS | off — WireGuard already encrypts. Turn it on only if you populate `config/certs/`. |
| Device token | from `server.run_headless --pairing` |

---

## Ports

| Port | Where | Who reaches it |
|---|---|---|
| 8000/tcp | server | tailnet only — HTTP + all three WebSockets |
| 8001/tcp | server | only if `config/certs/` exists (the dashboard's HTTPS alias) |
| 41641/udp | server | Tailscale itself, outbound; no inbound rule needed |

**Nothing to open in the Oracle security list.** If you have opened 8000 there,
close it.

Port 8000 is hardcoded (`PORT` in `dashboard/server.py`) and is deliberately not
changed — editing that file to move a port would modify MARK LIII's core for no
gain. If you need a different port, put a reverse proxy in front of it rather
than editing the constant.

---

## Access policy

Tailscale's default ACL lets every device on your tailnet reach every other.
That is fine for a personal tailnet of two or three devices. To narrow it, an
ACL that only lets your phone reach the assistant:

```json
{
  "tagOwners": {"tag:server": ["autogroup:admin"]},
  "acls": [
    {
      "action": "accept",
      "src":    ["autogroup:member"],
      "dst":    ["tag:server:8000"]
    }
  ]
}
```

---

## Verifying

```bash
# on the server
tailscale status              # the phone should be listed
sudo journalctl -u jarvis -f  # LOGIN appears when the app authenticates
```

```bash
# from any tailnet device
curl http://jarvis-vps.tailnet-XXXX.ts.net:8000/health
```

If `/health` answers from another tailnet device but not from the phone, the
problem is on the phone: Tailscale disconnected, or the app is pointed at an
address the release build's network policy refuses.
