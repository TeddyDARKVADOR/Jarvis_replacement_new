# ⚙️ Teddy-foundation (53)

### The Ultimate Cross-Platform Personal AI Assistant — By FatihMakes

> 📺 **[Watch the full setup video on YouTube](https://www.youtube.com/@FatihMakes)**

A real-time voice AI that can hear, see, understand, and control your computer — on any OS. Supports Windows, macOS, and Linux. Built on the Gemini Live API for native audio streaming, delivering zero subscriptions and total digital autonomy.

---

## 🖥️ Sur ce PC : lancer JARVIS

Le cerveau (MARK LIII) tourne sur **Oracle**. Sur l'ordinateur, `main.py` ouvre le
**HUD** qui s'y connecte :

```
python main.py            le HUD plein écran, relié à JARVIS sur Oracle
python main.py --mini     démarre en orbe dans le coin de l'écran
python main.py --local    JARVIS entièrement sur ce PC (clé Gemini locale), même HUD
python main.py --classic  l'ancienne fenêtre, inchangée
```

Premier lancement : la page **Paramètres** (⚙, ou Ctrl+,) s'ouvre seule. Saisissez
l'adresse Tailscale du serveur et le **jeton d'accès** (généré sur Oracle avec
`python -m server.run_headless --pairing`), puis **Tester** et **Enregistrer**.
Détails : [client_desktop/hud/README.md](client_desktop/hud/README.md).

### 🛠️ Jarvis Code Studio — son atelier de développement

En mode `--local`, JARVIS dispose d'un **espace de travail dédié** (par défaut
`C:\Jarvis_Workspace`, modifiable uniquement depuis le HUD). Dites par exemple :
« Crée-moi une application de gestion de budget… mets tout dans ton espace de travail
et montre-moi ce que tu fais. » Il planifie, écrit les fichiers, **lance les vrais
tests**, corrige (nombre de tentatives limité, il s'arrête s'il tourne en rond) puis
présente un bilan. Le panneau **STUDIO** (bouton `</>` du HUD) s'ouvre tout seul et
affiche en direct les tâches, les fichiers (diff avant/après), le terminal, les points
de sauvegarde (restauration en un clic) et un bouton **ARRÊTER**. Cliquer un fichier
affiche son **code complet** (numéros de ligne, couleurs, recherche) ou ses différences.

- **Un verdict mérité, jamais supposé** : le plan contient des *critères d'acceptation*
  (AC1, AC2…). Un critère n'est « prouvé » que si un test qui porte son nom
  (`test_ac1_…`) ou un parcours navigateur passe réellement. Le bilan dit **VALIDÉ**,
  **PARTIELLEMENT VÉRIFIÉ** (un critère sans preuve, ou le programme lancé plante),
  **NON VÉRIFIÉ** (aucun contrôle fonctionnel n'a tourné : la syntaxe seule ne prouve
  rien) ou **PROBLÈME RESTANT**. S'il manque des tests, une passe les ajoute (dans
  `tests/` uniquement, jamais en modifiant le code).
- **Pages web testées dans un vrai navigateur** (Playwright, Chromium headless) : erreurs
  JavaScript et console, fichiers manquants, parcours scriptés (remplir, cliquer, vérifier),
  capture d'écran. Internet y est bloqué : seul le serveur local du projet répond.
- **Aperçu automatique** à la fin : une page web validée s'ouvre dans une **fenêtre
  d'aperçu isolée** (profil privé, aucun lien avec JARVIS, requêtes externes bloquées et
  comptées) ; un script est lancé une fois et sa sortie s'affiche dans le bilan ; une
  application graphique n'est jamais lancée seule (bouton dédié). Réglable dans le HUD.
- **Analyses Ruff et Pyright** (noms indéfinis, imports inutiles, erreurs de type)
  affichées à titre informatif : elles n'entrent pas dans le verdict ; chaque remarque
  ouvre le fichier à la bonne ligne.
- **Retouches depuis le HUD** : ÉDITER un fichier (Ctrl+S, enregistré par le Studio,
  annulable), puis **TESTS** pour relancer les tests du projet.
- **Windows Sandbox** (bouton SANDBOX, sur demande uniquement) : le projet tourne dans
  une machine jetable, **réseau coupé**, projet et Python montés **en lecture seule**,
  presse-papiers/micro/caméra coupés ; fermer la fenêtre efface tout. Prérequis, une fois :
  « Activer ou désactiver des fonctionnalités Windows » → « Bac à sable Windows » →
  redémarrer (Windows Pro/Entreprise). Seuls la bibliothèque standard et `_deps/` du
  projet y sont disponibles ; pas de Node.js.
- `dev_agent` et la création via `code_helper` passent désormais par le Studio sur ce PC.
- Le code est écrit par **Groq** dès que `"groq_api_key"` est présent dans
  `config/api_keys.json` (meilleur modèle disponible pour la clé, `openai/gpt-oss-120b` en
  priorité ; `"groq_model"` dans `plugin_config.code_studio` pour en imposer un). Sans clé
  Groq, le Studio utilise Gemini. La conversation, elle, reste sur Gemini Live.
- Chaque ligne du suivi est écrite par les outils qui ont agi, jamais par le modèle.
- Fichiers : chemins vérifiés par le code (`..`, lecteurs, UNC, flux `:`, noms réservés,
  jonctions, liens physiques refusés) ; le dépôt JARVIS ne peut jamais être la racine.
- Commandes : sans shell, liste blanche (python, pytest, node, npm), délai max, Job
  Object Windows (mémoire, nombre de processus, arrêt de tout l'arbre), secrets retirés
  de l'environnement, refus si JARVIS tourne en administrateur. Installer un paquet,
  `python -c` ou un exécutable inconnu demandent votre accord sur le HUD.
- Limite assumée : un programme exécuté par le Studio (tests, aperçu) tourne avec les
  droits de votre compte. Pour une isolation forte, utilisez le bouton SANDBOX.

Code : [core/code_workspace/](core/code_workspace/__init__.py) · outil
[actions/code_workspace.py](actions/code_workspace.py) · tests :
`python -m core.code_workspace.selftest`.

---

## ✨ Overview

**Teddy-foundation is the hands-free & scalable release.** Say **"Hey Jarvis"** and it wakes; stay quiet and it slips back to sleep on its own — while asleep, your microphone never leaves the machine, so an off-hand _"I'll be right there"_ to someone in the room no longer sets it off. Under the hood it now runs on the faster **Gemini 3.1 Flash Live** engine, and the moment you ask for something that takes a beat — analysing a file, searching the web — it answers instantly _("On it — going through that now…")_ so you never wonder whether it heard you.

It's also built to grow: every skill — bundled or drop-in — now **describes itself in its own file**, so adding a tool is a one-file operation and the core stays lean.

It's not just an assistant — it's an extension of your digital life.

---

## 🚀 Capabilities

### Core Features

| Feature                    | Description                                                                                                                                                                                          |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 🎙️ Wake Word               | Local **"Hey Jarvis"** detection — sleeps until called, auto-sleeps after 2 min of silence, and never streams audio while asleep. Opt-in, one-click download, toggle & manual sleep/wake from the UI |
| ⚡ Instant Acknowledgment  | Speaks a short, context-aware reply in **your language** the instant a longer task starts — no more silent waiting                                                                                   |
| 🚀 Faster Live Engine      | Runs on **Gemini 3.1 Flash Live** — roughly 2× faster time-to-first-word than the previous model                                                                                                     |
| 🧩 Self-Describing Skills  | Actions and plugins share one shape (`TOOL` / `PLUGIN` dict + `run()`), auto-discovered at launch — adding or moving a skill is a single file, no core edits                                         |
| 🧠 Recallable Memory       | No size limit and nothing silently forgotten — the prompt carries what fits, the rest is looked up on demand from a local search                                                                     |
| 👁️ Memory Panel            | See every fact JARVIS has stored about you, when it learned it, and delete any of it in one click                                                                                                    |
| ↩️ Undo                    | Take back what the assistant did — files it moved, renamed, created or wrote, and settings it changed                                                                                                |
| ⚠️ Real Confirmation       | Shutdown, restart and WiFi wait for a button **you** press — the model cannot confirm its own irreversible actions                                                                                   |
| 🎧 Audio Device Picker     | Choose the microphone and speakers by name, filtered to the short list your OS shows — and measured, so every entry actually works                                                                   |
| 🔗 Session Continuity      | A dropped connection, a voice change or a device change no longer wipes the conversation                                                                                                             |
| 🧩 Plugin System           | Drop a single `.py` file into `plugins/` — JARVIS learns a new skill on next launch                                                                                                                  |
| 🎙️ Real-time Voice         | Ultra-low latency conversation in any language via Gemini Live API                                                                                                                                   |
| 🎨 Live Theming            | Recolour the entire HUD from a hue wheel or hex — applied instantly across every panel                                                                                                               |
| 〰️ Reactive HUD            | Waveform and reactor core pulse to real audio — your mic while listening, JARVIS while speaking                                                                                                      |
| 🎙️ Voice Picker            | Choose from 5 native Gemini voices and switch live from the UI — no restart                                                                                                                          |
| ♾️ Unlimited Sessions      | Sliding-window context compression — one conversation can last for hours                                                                                                                             |
| 🖥️ System Control          | Launch apps, adjust volume/brightness, WiFi, shortcuts, power — all by voice                                                                                                                         |
| 🧩 Autonomous Tasks        | High-level planning for complex multi-step goals via agent mode                                                                                                                                      |
| 👁️ Visual Awareness        | Real-time screen capture and webcam vision piped into your main Gemini session                                                                                                                       |
| 🧠 Persistent Memory       | Deeply remembers projects, preferences, and personal context across sessions                                                                                                                         |
| ⌨️ Hybrid Input            | Seamlessly switch between keyboard typing and voice commands                                                                                                                                         |
| 🌅 Morning Briefing        | On first boot: greets you, reads the time, recaps yesterday, and fetches live news                                                                                                                   |
| 🔔 Proactive 2.0           | Time-aware, context-aware check-ins — knows the time of day, your projects, and what you've been discussing                                                                                          |
| 🗓️ Session Memory          | Summarises each conversation and mentions it naturally next morning — consumed after use, never repeats                                                                                              |
| 👁️‍🗨️ Background Monitoring   | User-configured topic watching — checks for new headlines once a day and alerts naturally                                                                                                            |
| 📊 Hardware Monitoring     | Continuous CPU, RAM, GPU and temperature telemetry with localized voice alerts                                                                                                                       |
| 🌤️ Weather Report          | Live weather data for your city, personalized from memory                                                                                                                                            |
| 🗺️ Dynamic Content Panel   | Scrollable display layer beneath the HUD that renders web results, news, and search data                                                                                                             |
| 🔍 Multi-Mode Web Search   | `news` / `research` / `price` / `compare` / `search` — Gemini Grounded first, DDG fallback                                                                                                           |
| ⏰ Smart Reminders         | OS-native scheduled notifications (Windows Task Scheduler / macOS LaunchAgent / Linux systemd)                                                                                                       |
| ✈️ Flight Finder           | Live flight price and availability lookup                                                                                                                                                            |
| 🎮 Game Updater            | Checks and triggers game updates on Steam and Epic Games on demand                                                                                                                                   |
| 📂 File Processor          | Read, summarize, and answer questions about local files                                                                                                                                              |
| 💻 Code Helper             | Inline code review, debugging, and generation                                                                                                                                                        |
| 🌐 Browser Control         | Open URLs, navigate tabs, and interact with the browser by voice                                                                                                                                     |
| 📨 Send Message            | Compose and send messages through WhatsApp, Telegram, and more                                                                                                                                       |
| 🎬 YouTube Control         | Search, play, and control YouTube playback by voice                                                                                                                                                  |
| 🖱️ Desktop Control         | Taskbar, window management, and desktop-level operations                                                                                                                                             |
| 🧑‍💻 Silent Language Memory  | Detects spoken language on first use — all future sessions adapt automatically                                                                                                                       |
| 📱 Remote Dashboard        | Control the assistant from your phone via QR code pairing                                                                                                                                            |
| ⚡ Auto-Start on Boot      | Registers with the OS startup system (registry / LaunchAgent / .desktop)                                                                                                                             |
| 📋 Clipboard Intelligence  | Copy any text → floating panel with Translate / Summarise / Explain / Fix                                                                                                                            |
| 🪪 Assistant Customization | Change the assistant name, your name, voice, and colour from the UI — takes effect immediately                                                                                                       |

---

## 🆕 What's New in Teddy-foundation

Teddy-foundation is about making JARVIS **hands-free, faster, and easy to extend** — all universal: no hardcoded language, no bundled asset files, works the same on Windows, macOS and Linux.

### 🎙️ Wake Word — "Hey Jarvis"

JARVIS can now sit quietly until you call it. Turn on **⚙ → WAKE WORD** (a one-click, opt-in download of a tiny local model) and it goes to sleep: the microphone is processed **only on your machine** by a local detector, and nothing is sent to the cloud until it hears **"Hey Jarvis."** Once awake it listens normally, then **auto-sleeps after 2 minutes** of silence. You can also **sleep/wake it by clicking** in the settings. Because it's a _local_ gate, background chatter — _"I'm coming!"_ to someone at home — never wakes it. It costs **zero** when off (the model isn't even loaded), and the detection runs in its own thread, so nothing else in the app slows down.

### ⚡ Instant Acknowledgment

No more silent gaps. When you ask for something that takes a moment — reading an uploaded file, a web/research search, building code — JARVIS **immediately** says one short, natural sentence _in your language_ (_"Right away — going through that file now."_) and _then_ runs the tool. Instant actions (opening an app, volume) stay snappy with no chatter.

### 🚀 Faster Live Engine — Gemini 3.1 Flash Live

The live session moved to **`gemini-3.1-flash-live-preview`**, cutting the time-to-first-word roughly in half while keeping tools, all five voices, transcription, session resumption and sliding-window compression intact.

### 🧩 Self-Describing Skills — a Scalable Core

Every bundled **action** now carries its own `TOOL` declaration in its own file (exactly like a drop-in **plugin's** `PLUGIN` dict), and the core auto-discovers them at launch. `main.py` no longer holds a giant list of tool definitions and dispatch branches — it shrank by hundreds of lines. Adding a new built-in skill, or promoting an `actions/*.py` file into a shareable plugin, is now just… moving a file.

> Built on the Mark LI/LII foundation: the **🧩 Plugin System**, **♾️ Unlimited Sessions**, **🎨 Live Theming**, **〰️ Reactive HUD** and **🎙️ Voice Picker** are all still here.

---

## 🔄 The Foundation Update — in every Mark from LII

These four landed across **Mark LII, LIII, LIV and LV at the same time**, after each of those releases had already shipped. They are not what any one of those versions originally introduced; they are the floor all of them now stand on, so moving up a Mark never costs you something the one below it had.

No new dependencies. No bundled asset files. No hardcoded language, and nothing that assumes one operating system.

### 🧠 A memory that actually remembers

The store was capped at **2,200 characters — the whole memory, not per entry** — because all of it was pasted into the system prompt on every connect, so growing the memory grew every request. When it filled, the oldest entries were deleted and one line was printed to a console nobody reads. An assistant advertised as remembering "projects, preferences and personal context" was in practice a two-page notepad that quietly forgot your sister's name after a few weeks.

Storage and prompt budget are now separate problems:

- **Nothing is deleted.** The cap is a runaway guard normal use never approaches, and if it is ever hit it says so in the activity log instead of on stdout.
- **The prompt carries a core, not a dump.** Identity in full, then the most recently updated facts, budgeted — measured at **971 characters on a memory holding 62 stored facts.** That is _smaller_ than the old whole-store cap, so sessions now connect with fewer tokens than before.
- **The rest is fetched on demand.** A `recall_memory` tool searches the full store locally — no network, no second model, well under a millisecond.

The part that is easy to get wrong: **a model cannot look something up if it doesn't know the thing exists.** So the prompt also carries an **index of the keys** it had no room for. Without it, "who is Ayşe?" gets "I don't know" while `ayse_sister` sits on disk unread. That index interleaves categories rather than sorting by recency — sorted like the core, a memory with forty preferences pushed the one entry the index existed for off the end.

⚙ → **🧠 MEMORY** shows every stored fact, when it was learned, and a ✕ to forget it. Everything stays in `memory/long_term.json` on your machine.

### ↩️ Undo — it can take back what it did

JARVIS moves files, renames them, writes to them and changes your settings. None of that had a way back; if it misheard you, the only remedy was to fix it by hand.

Say **"undo"** — in any language — and it reverses its own last action:

|              |                                                                   |
| ------------ | ----------------------------------------------------------------- |
| **Files**    | move · rename · create · copy · write · delete · organize desktop |
| **Settings** | volume · brightness · dark mode                                   |

Three things it deliberately does _not_ do:

- **It does not guess.** Settings undo reads the current value _before_ changing it. Where a platform won't report that value, nothing is registered — an undo that restores a guess is worse than no undo.
- **It does not hoard.** Undoing a write means keeping the old contents in memory, so files over 1 MB are excluded and it says so rather than holding a 200 MB log for the session.
- **It does not delete your files to undo a copy.** The reverse of a copy is removing the copy; the reverse of "create a folder" is removing it _only while it's still empty_.

`organize_desktop` gets special treatment — one command that moves dozens of files, which made it the least reversible thing the assistant could do. It journals every move and puts all of them back in one go, cleaning up the folders it created if they're still empty.

**Undo costs nothing at runtime.** It appends a closure to a list; nothing in it runs unless you ask.

### ⚠️ A confirmation the model can't forge

The old gate read like this:

```python
if action in _DANGEROUS_ACTIONS:            # {"restart", "shutdown"}
    confirmed = str(params.get("confirmed", "")).lower()
```

`confirmed` is a **tool parameter, which means the model fills it in.** Nothing stopped it sending `confirmed=yes` on the first call and nothing checked that a human was ever involved. It was a convention, not a gate. And its coverage was two actions — so `toggle_wifi`, which cuts the assistant's own connection to the Live API and therefore _cannot be asked to undo itself_, went through with no gate at all.

The token is now issued by the interface. Shutdown, restart and WiFi put a banner on the HUD and **return immediately**; the action runs only if you press CONFIRM. Nothing blocks — JARVIS keeps talking while the banner is up — so this is **cheaper than the old gate**, which burned two tool round trips on every power command.

> The split between the two mechanisms is about reversibility, not about how alarming a word sounds. Anything undoable is done at once; only the genuinely irreversible asks. An assistant that checks with you before turning the volume down is one you stop talking to.

### 🎧 It finally asks which microphone

Both audio streams opened with no device argument at all, so they always took whatever the OS called "default" — and on Windows that _moves on its own_ the moment you plug a headset in. "JARVIS can't hear me" almost always meant "JARVIS is listening to the webcam".

⚙ → **🎧 AUDIO DEVICES** lets you pick the microphone and the speakers by name. Two things matter more than the dropdown:

**The list is short.** `query_devices()` returns one entry per _device × host API_, not per device — measured on an ordinary Windows machine, **41 entries for what the sound settings show as 4 microphones and 4 speakers.** The same microphone appears four times, under MME, DirectSound, WASAPI and WDM-KS, with nothing to say which is which. That is not a choice, it's a quiz. The picker takes one host API per direction, drops the "Sound Mapper" and "Primary Sound Driver" pseudo-devices that just mean "default", and deduplicates. **41 → 8.**

**Every entry has been measured, not assumed.** The obvious approach is to pick the host API with the nicest names — WASAPI on Windows, which in shared mode **doesn't resample**, so with 16 kHz in and 24 kHz out against 48 kHz hardware every open failed. Adding a rate check and moving to DirectSound passes that test on both sides, and PortAudio's DirectSound **output is a silent sink**: the stream opens, every write returns success in ~0 ms, and not one sample reaches the speakers.

|             | write(2.0 s) took |                       |
| ----------- | ----------------- | --------------------- |
| MME         | **2.02 s**        | consumed in real time |
| DirectSound | **0.00 s**        | swallowed instantly   |

No capability flag reports that. So the app measures it — once per host API per direction, on a background thread at startup, using silence. Two consequences worth stating plainly:

- **Each direction picks its own host API.** On Windows this lands on DirectSound for the microphone and MME for the speakers — a split no amount of reasoning would have produced.
- **The probe runs in the mode the app actually ships.** DirectSound input passes a callback stream and fails a blocking read; probing the wrong mode rejected a microphone that works perfectly.

Your choice is stored **by name, not by index** — indices shift whenever something is plugged in. If the saved device is gone, it falls back to the system default and says so in the log rather than failing to start.

### 🔗 It stops forgetting the conversation when the connection drops

`session_resumption` was switched on in the config and the handle the server sent back was **never read** — so every reconnect started an empty session. A dropped packet, or simply changing the voice, wiped the conversation. "Unlimited sessions" leaked through exactly this hole.

The handle is captured and replayed now. A network blip, or switching your microphone, keeps the conversation intact.

It is held in memory only, deliberately: writing it to disk would make a fresh launch continue yesterday's chat, which sounds appealing but breaks the session-summary flow — a conversation that never ends never produces a summary, and the "yesterday we talked about…" line in the morning briefing silently disappears. Changing the **voice** also starts clean on purpose, since resuming restores the server's session state and would likely bring the old voice back with it.

### 🩹 Fixes that came with it

- **The assistant could die on a log line.** Status lines carry emoji and arrows (`📤 file_controller → Moved: a.txt → Documents/`). On a non-UTF-8 console — cp1254 on a Turkish Windows, cp1251 on a Russian one, cp932 on a Japanese one — printing one raises `UnicodeEncodeError`, and because that print sits _after_ the tool's own `try/except`, it escaped into the receive loop and took the session down.
- **Every computer command paid for two model round trips.** `computer_settings` made an _entire second Gemini call, inside the tool_, purely to translate the request into one of its own action names — because the declaration only said "The action to perform", so the model rarely filled it in. When that second call failed, the fallback was `description.lower().replace(" ", "_")`, which turns the Turkish for "turn it down" into `sesi_kis` and straight into "Unknown action". The declaration now names all 56 actions and the rest is spelling tolerance handled locally by `difflib` in microseconds. When nothing matches it suggests real action names instead of dead-ending.
- An unresolvable saved audio device, or one the driver refuses to open, falls back to the system default and says so — on both the microphone and the speakers.
- A rejected session-resumption handle is dropped after one attempt, so an expired handle can never be replayed on every retry and prevent the reconnect it exists to protect.

---

## 🗺️ Mark Roadmap

| Mark     | Focus                                                                                                                                |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| **XLIX** | Auto-start · clipboard intelligence · assistant customization                                                                        |
| **L**    | Session memory · background monitoring · proactive 2.0 · instant vision                                                              |
| **LI**   | Plugin system · affective dialog · proactive audio · unlimited sessions                                                              |
| **LII**  | Voice picker · live theming · reactive HUD · recallable memory · undo · real confirmation · audio device picker · session continuity |
| **LIII** | Wake word · Gemini 3.1 Flash Live · instant acknowledgment · self-describing action/plugin architecture                              |
| _shared_ | The last five above also shipped to LIII, LIV and LV at the same time — moving up a Mark never loses them                            |
| **LIV+** | Plugin files: email · quiz mode · calendar · home assistant · 3D-printer · and more                                                  |

---

## ⚡ Quick Start

```bash
git clone https://github.com/FatihMakes/Mark-LIII.git
cd Mark-LIII
python setup.py        # installs deps for YOUR OS + the browser automation engine
python main.py
```

`setup.py` only ever installs what your operating system needs — the Windows-only libraries are skipped automatically on macOS and Linux (and vice-versa). Prefer to do it by hand? `pip install -r requirements.txt` works too.

> ⚠️ **Installation Note:** If you hit a `ModuleNotFoundError` for an OS-specific package, install it with `pip install <module_name>`. The optional **wake word** engine is _not_ installed here — grab it in one click from **⚙ → WAKE WORD** inside the app.

---

## 📋 Requirements

| Requirement                | Details                                                                       |
| -------------------------- | ----------------------------------------------------------------------------- |
| **OS**                     | Windows 10/11, macOS, or Linux                                                |
| **Python**                 | 3.11 or 3.12                                                                  |
| **Microphone**             | Required for voice interaction (and for the "Hey Jarvis" wake word)           |
| **Speakers**               | Required for voice replies                                                    |
| **API Key**                | Free Gemini API key (entered on first launch → `config/api_keys.json`)        |
| **Wake word** _(optional)_ | One-click download from ⚙ → WAKE WORD (`openwakeword`, a few MB, fully local) |

---

## 🗂️ Project Structure

```
Teddy-foundation/
├── main.py                   # Core loop — Gemini Live session, audio I/O, wake/sleep state, tool dispatch
├── ui.py                     # PyQt6 HUD — reactive waveform, log panel, settings drawer, plugin manager, camera feed
├── setup.py                  # OS-aware installer (skips wrong-OS dependencies)
├── plugins/
│   ├── _template.py          # Copy this to write a new plugin — one file, drop in, done
│   └── ...                   # Drop-in skills (each self-describes via a PLUGIN dict + run())
├── actions/                  # Bundled skills — each self-describes via a TOOL dict + handler
│   ├── web_search.py         # Gemini + DDG parallel search (news, research, price, compare)
│   ├── screen_processor.py   # Screen & webcam capture for vision
│   ├── background_monitor.py # User-configured topic watching — daily DDG check, no crypto
│   ├── proactive.py          # Proactive 2.0 — time/context/rotation-aware check-ins
│   ├── reminder.py           # OS-native scheduled notifications
│   ├── system_monitor.py     # CPU / RAM / GPU / temperature telemetry
│   ├── computer_settings.py  # Volume, brightness, WiFi, power (per-OS)
│   ├── computer_control.py   # Keyboard shortcuts, mouse, window management
│   ├── open_app.py           # Application launcher (per-OS name map)
│   ├── browser_control.py    # Web browser control
│   ├── file_controller.py    # File system operations
│   ├── file_processor.py     # Document reading and summarization
│   ├── send_message.py       # Messaging integration
│   ├── weather_report.py     # Live weather data
│   ├── flight_finder.py      # Flight search
│   ├── youtube_video.py      # YouTube playback control
│   ├── game_updater.py       # Game update management (Steam / Epic)
│   ├── code_helper.py        # Code review and generation
│   ├── dev_agent.py          # Developer task agent
│   └── desktop.py            # Desktop and taskbar control
├── memory/
│   ├── memory_manager.py     # Load/save long_term.json — sessions, monitors, identity
│   ├── config_manager.py     # api_keys.json access — key, OS, name, voice, colour, toggles
│   └── long_term.json        # Persistent store: identity, preferences, projects, sessions, monitors
├── core/
│   ├── prompt.txt            # Assistant personality and tool-routing rules
│   ├── undo.py               # One shared undo stack — actions register how to reverse themselves
│   ├── confirm.py            # Irreversible-action gate — the token is issued by the UI, not the model
│   ├── audio_devices.py      # Microphone / speaker list — filtered, measured, resolved by name
│   ├── plugin_loader.py      # Plugin engine — discovery, validation, crash isolation
│   ├── action_loader.py      # Bundled-action engine — the built-in twin of plugin_loader
│   └── wake_word.py          # Local "Hey Jarvis" detector — own thread, offline, opt-in
└── config/
    └── api_keys.json         # API key, OS setting, assistant name, user name, voice, UI colour, toggles
```

---

## ⚠️ License

Personal and non-commercial use only.
Licensed under **[Creative Commons BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)**.

---

## 👤 Connect with the Creator

Engineered by a developer building a real-world JARVIS-style assistant.
⭐ **Star the repository to support the journey to Mark 100.**

| Platform  | Link                                                |
| --------- | --------------------------------------------------- |
| YouTube   | [@FatihMakes](https://www.youtube.com/@FatihMakes)  |
| Instagram | [@fatihmakes](https://www.instagram.com/fatihmakes) |
