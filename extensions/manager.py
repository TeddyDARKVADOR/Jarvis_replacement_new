"""
extensions/manager.py — installer, examiner, désactiver, restaurer des plugins, en sachant ce qu'ils font.

    report = inspect(source)          ce que le fichier déclare ET ce que son code fait, sans l'exécuter
    install(jarvis, source, sha256)   après CONFIRMER sur le HUD ; la version d'avant est gardée
    restore(jarvis, name) · remove(jarvis, name) · installed()

LE MANIFESTE — facultatif, dans le fichier du plugin, une constante littérale :

    PLUGIN_MANIFEST = {
        "version": "1.2.0", "author": "…", "source": "https://…",
        "permissions": ["network", "calendar.read"],          # ce qu'il dit faire
        "requires": ["requests>=2.28"],                        # versions épinglées
        "actions": {"list": "read", "create": "act"},          # lire ou agir, action par action
        "default": "act",                                      # une action non listée
    }

CE QUI PROTÈGE VRAIMENT (et ce qui ne protège pas)
  Un plugin Python tourne dans le processus de JARVIS : une liste de
  permissions ne l'EMPÊCHE de rien. Ce module ne prétend donc pas le confiner.
  Il fait trois choses vérifiables :
  1. la PROVENANCE — empreinte SHA-256 montrée, épinglée si l'utilisateur la
     donne (un fichier modifié est refusé), installation seulement après
     CONFIRMER (core/confirm.py, que le modèle ne peut pas fabriquer) ;
  2. la TRANSPARENCE — une analyse statique du code (sans l'exécuter) liste ce
     qu'il fait vraiment (réseau, commandes système, écriture de fichiers, code
     dynamique, clavier, écran, micro) et signale l'écart avec ce qu'il déclare ;
  3. LIRE OU AGIR, appliqué par le code — chaque action « act » d'un plugin
     manifesté passe par CONFIRMER avant de s'exécuter ; « read » passe.
  Plus : la version précédente est gardée (restore), et désactiver réutilise
  le réglage existant des plugins.

Les plugins installés vivent dans user_plugins/ (ignoré par Git), hors de
plugins/ : ceux du dépôt restent ceux du dépôt. Pour MCP, les annotations
d'un serveur (readOnlyHint…) restent ignorées — elles viennent du serveur
lui-même ; l'utilisateur décide quels outils passent sans confirmation
(mcp_auto_approve).
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
USER_DIR = REPO / "user_plugins"
VERSIONS = ".versions"
LEVELS = ("read", "act")
PERMISSIONS = ("network", "files.read", "files.write", "shell", "system", "dynamic_code", "input", "screen",
               "microphone", "camera", "messages.send", "calendar.read", "calendar.write")
MAX_BYTES = 400_000

#: ce que le code importe ou appelle → la permission que cela suppose.
_IMPORTS = {"requests": "network", "urllib": "network", "httpx": "network", "aiohttp": "network",
            "socket": "network", "websockets": "network", "http": "network", "ftplib": "network", "smtplib": "network",
            "subprocess": "shell", "winreg": "system", "ctypes": "system", "pyautogui": "input", "pynput": "input",
            "keyboard": "input", "mss": "screen", "sounddevice": "microphone", "soundcard": "microphone",
            "pyaudio": "microphone", "cv2": "camera", "shutil": "files.write", "importlib": "dynamic_code"}
_CALLS = {"eval": "dynamic_code", "exec": "dynamic_code", "compile": "dynamic_code", "__import__": "dynamic_code",
          "system": "shell", "popen": "shell", "remove": "files.write", "unlink": "files.write",
          "rmtree": "files.write", "write_text": "files.write", "write_bytes": "files.write", "rename": "files.write",
          "ImageGrab": "screen"}


@dataclass
class Report:
    ok: bool
    name: str = ""
    sha256: str = ""
    size: int = 0
    manifest: dict = field(default_factory=dict)
    declared: list[str] = field(default_factory=list)
    detected: list[str] = field(default_factory=list)
    undeclared: list[str] = field(default_factory=list)
    actions: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    text: str = field(default="", repr=False)

    def render(self) -> str:
        if not self.ok:
            return "Refusé : " + " ; ".join(self.problems)
        m = self.manifest
        lines = [f"Extension « {self.name} » {m.get('version', '(sans version)')}" + (f" — {m['author']}" if m.get("author") else ""),
                 f"Empreinte SHA-256 : {self.sha256} ({self.size} octets)",
                 "Déclare : " + (", ".join(self.declared) or "rien (pas de manifeste)"),
                 "Son code fait : " + (", ".join(self.detected) or "rien de sensible détecté")]
        if self.undeclared:
            lines.append("⚠️ Fait sans le déclarer : " + ", ".join(self.undeclared))
        if self.actions:
            lines.append("Actions : " + ", ".join(f"{a} ({lvl})" for a, lvl in self.actions.items()))
        if self.missing:
            lines.append("Dépendances manquantes : " + ", ".join(self.missing))
        lines += [f"⚠️ {p}" for p in self.problems]
        return "\n".join(lines)


def _literal(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == name:
            try:
                return ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                return None
    return None


def detect(tree: ast.Module) -> list[str]:
    """Ce que le code fait, d'après ses imports et ses appels — sans l'exécuter. Pur."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {_IMPORTS[a.name.split(".")[0]] for a in node.names if a.name.split(".")[0] in _IMPORTS}
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top in _IMPORTS:
                found.add(_IMPORTS[top])
            if node.module.startswith("PIL") and any(a.name == "ImageGrab" for a in node.names):
                found.add("screen")
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            if name in _CALLS:
                found.add(_CALLS[name])
            if name == "open" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant) \
                    and any(c in str(node.args[1].value) for c in "wax+"):
                found.add("files.write")
    return sorted(found)


def _missing(requires: list) -> list[str]:
    from importlib.metadata import PackageNotFoundError, version
    try:
        from packaging.requirements import Requirement
    except ImportError:              # sans packaging (runner Linux, vu le 2026-10-21) : présence seule, pas les versions
        Requirement = None
    out = []
    for spec in requires or []:
        try:
            if Requirement is None:
                name = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", str(spec))
                if not name:
                    raise ValueError(spec)
                version(name.group(1))
                continue
            req = Requirement(str(spec))
            have = version(req.name)
            if req.specifier and have not in req.specifier:
                out.append(f"{spec} (installé : {have})")
        except PackageNotFoundError:
            out.append(str(spec))
        except Exception:
            out.append(f"{spec} (illisible)")
    return out


def read_source(source: str) -> str:
    if source.startswith("https://"):
        req = urllib.request.Request(source, headers={"User-Agent": "jarvis-extensions/1"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(MAX_BYTES + 1)
    elif source.startswith("http://"):
        raise ValueError("http:// refusé : une extension se télécharge en https://")
    else:
        raw = Path(source).expanduser().read_bytes()[: MAX_BYTES + 1]
    if len(raw) > MAX_BYTES:
        raise ValueError("fichier trop gros pour un plugin")
    return raw.decode("utf-8")


def inspect(source: str, text: str | None = None, taken: set[str] | None = None) -> Report:
    """Examine sans exécuter : déclaration, manifeste, ce que fait le code, dépendances, conflits de nom."""
    try:
        text = text if text is not None else read_source(source)
    except Exception as e:
        return Report(False, problems=[f"lecture impossible : {type(e).__name__}: {e}"[:200]])
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return Report(False, sha256=sha, problems=[f"Python invalide : {e.msg} (ligne {e.lineno})"])
    plugin = _literal(tree, "PLUGIN")
    if not isinstance(plugin, dict) or not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$", str(plugin.get("name") or "")):
        return Report(False, sha256=sha, problems=["pas de PLUGIN littéral avec un nom valide"])
    manifest = _literal(tree, "PLUGIN_MANIFEST")
    manifest = manifest if isinstance(manifest, dict) else {}
    declared = sorted({str(p) for p in manifest.get("permissions") or []})
    detected = detect(tree)
    rep = Report(True, str(plugin["name"]), sha, len(text.encode("utf-8")), manifest, declared, detected,
                 [p for p in detected if p not in declared and not (p == "files.read")],
                 {str(a): str(lvl) for a, lvl in (manifest.get("actions") or {}).items()},
                 _missing(manifest.get("requires") or []), text=text)
    if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
        rep.ok, rep.problems = False, rep.problems + ["pas de fonction run()"]
    bad = [lvl for lvl in rep.actions.values() if lvl not in LEVELS]
    if bad:
        rep.problems.append(f"niveaux d'action inconnus : {', '.join(bad)} (read ou act)")
    if "dynamic_code" in detected:
        rep.problems.append("exécute du code construit à la volée (eval/exec/import dynamique) : à lire de près")
    if taken and rep.name in taken:
        rep.ok, rep.problems = False, rep.problems + [f"le nom « {rep.name} » est déjà pris par un outil de JARVIS"]
    return rep


# ── installation ─────────────────────────────────────────────────────────────

def _registry(jarvis):
    return getattr(jarvis, "_plugin_registry", None)


def _taken(jarvis, except_user: str = "") -> set[str]:
    names = set()
    reg = _registry(jarvis)
    if reg is not None:
        names |= {n for n in getattr(reg, "_plugins", {}) if n != except_user}
    ar = getattr(jarvis, "_action_registry", None)
    if ar is not None:
        try:
            names |= set(ar.names())
        except Exception:
            pass
    return names


def write(rep: Report, user_dir: Path = USER_DIR) -> Path:
    """Écrit le plugin ; l'ancienne version part dans .versions/<nom>/."""
    user_dir.mkdir(parents=True, exist_ok=True)
    dest = user_dir / f"{rep.name}.py"
    if dest.exists():
        old = dest.read_text(encoding="utf-8")
        tag = f"{datetime.now():%Y%m%d-%H%M%S}-{hashlib.sha256(old.encode()).hexdigest()[:8]}"
        keep = user_dir / VERSIONS / rep.name
        keep.mkdir(parents=True, exist_ok=True)
        (keep / f"{tag}.py").write_text(old, encoding="utf-8")
    dest.write_text(rep.text, encoding="utf-8")
    return dest


def load_into(jarvis, path: Path) -> str:
    """Importe un plugin de user_plugins/ et le range dans le registre de JARVIS, avec la garde lire/agir."""
    from core.plugin_loader import _validate
    module_name = f"user_plugins.{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)                      # type: ignore[union-attr]
    rec = _validate(module, path.name)
    if not rec.valid:
        raise ValueError(rec.error)
    manifest = getattr(module, "PLUGIN_MANIFEST", None)
    if isinstance(manifest, dict):
        rec.run = gate(rec.name, rec.run, manifest)
    reg = _registry(jarvis)
    if reg is not None:
        reg._plugins[rec.name] = rec
    return rec.name


def level_of(manifest: dict, parameters: dict) -> str:
    action = str((parameters or {}).get("action") or "")
    actions = manifest.get("actions") or {}
    lvl = actions.get(action) or manifest.get("default") or "act"
    return lvl if lvl in LEVELS else "act"


def gate(name: str, run, manifest: dict):
    """Une action « act » attend CONFIRMER (core/confirm.py) ; une action « read » passe tout de suite."""
    def gated(parameters, player=None, session_memory=None):
        if level_of(manifest, parameters) == "read":
            return run(parameters, player=player, session_memory=session_memory)
        from core import confirm
        if confirm.pending_title():
            return "Une autre confirmation attend déjà sur le HUD ; réessaie quand elle est réglée."
        say = getattr(player, "request_say", None)

        def _do():
            out = str(run(parameters, player=player, session_memory=session_memory) or "Fait.")
            if say:
                say(f"[{name} — résultat après confirmation de l'utilisateur]\n{out[:1500]}\n"
                    "Dis-le à l'utilisateur en une phrase, dans sa langue.")
            return out[:200]
        detail = f"{name}({json.dumps(parameters, ensure_ascii=False)[:300]})"
        return confirm.request(f"ext:{name}", f"Extension {name} : {parameters.get('action') or 'agir'}", detail, _do)
    gated.__wrapped__ = run
    return gated


def install(jarvis, source: str, expected_sha: str = "", user_dir: Path = USER_DIR, confirmer=None) -> str:
    """Examine, puis installe après CONFIRMER. `expected_sha` (si donné) doit correspondre exactement."""
    existing = ""
    rep = inspect(source, taken=None)
    if rep.ok:
        existing = rep.name if (user_dir / f"{rep.name}.py").exists() else ""
        clash = _taken(jarvis, except_user=existing)
        if rep.name in clash and not existing:
            rep.ok, rep.problems = False, rep.problems + [f"le nom « {rep.name} » est déjà pris par un outil de JARVIS"]
    if not rep.ok:
        return rep.render()
    if expected_sha and expected_sha.lower() != rep.sha256:
        return f"Refusé : l'empreinte ne correspond pas (attendue {expected_sha[:16]}…, reçue {rep.sha256[:16]}…)."

    def _do() -> str:
        path = write(rep, user_dir)
        load_into(jarvis, path)
        return f"Extension {rep.name} installée."

    summary = rep.render()
    if confirmer is not None:                                # tests et appelants qui confirment eux-mêmes
        return _do() if confirmer(summary) else "Installation annulée."
    from core import confirm
    if confirm.pending_title():
        return "Une autre confirmation attend déjà sur le HUD."
    verb = "Mettre à jour" if existing else "Installer"
    sentence = confirm.request(f"ext-install:{rep.name}", f"{verb} l'extension {rep.name}", summary, _do)
    return summary + "\n\n" + sentence


def installed(user_dir: Path = USER_DIR) -> list[dict]:
    out = []
    for f in sorted(user_dir.glob("*.py")) if user_dir.exists() else []:
        rep = inspect(str(f))
        versions = sorted((user_dir / VERSIONS / f.stem).glob("*.py")) if (user_dir / VERSIONS / f.stem).exists() else []
        out.append({"name": rep.name or f.stem, "version": rep.manifest.get("version", "?"), "sha256": rep.sha256[:16],
                    "permissions": rep.declared, "undeclared": rep.undeclared, "previous": len(versions)})
    return out


def restore(jarvis, name: str, user_dir: Path = USER_DIR) -> str:
    keep = user_dir / VERSIONS / name
    versions = sorted(keep.glob("*.py")) if keep.exists() else []
    if not versions:
        return f"Aucune version précédente de {name}."
    current = user_dir / f"{name}.py"
    shutil.copy2(versions[-1], current)
    versions[-1].unlink()
    load_into(jarvis, current)
    return f"{name} : version précédente restaurée ({versions[-1].stem})."


def remove(jarvis, name: str, user_dir: Path = USER_DIR) -> str:
    path = user_dir / f"{name}.py"
    if not path.exists():
        return f"{name} n'est pas une extension installée (les plugins du dépôt ne se retirent pas ici)."
    keep = user_dir / VERSIONS / name
    keep.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(keep / f"{datetime.now():%Y%m%d-%H%M%S}-retiree.py"))
    reg = _registry(jarvis)
    if reg is not None:
        reg._plugins.pop(name, None)
    return f"{name} retirée (gardée dans .versions : « restaure {name} » la remet)."


def attach(jarvis, user_dir: Path = USER_DIR, log=print) -> list[str]:
    """Hook de main.py : charge user_plugins/ dans le registre (après les plugins du dépôt), avec la garde."""
    loaded = []
    taken = _taken(jarvis)
    for f in sorted(user_dir.glob("*.py")) if user_dir.exists() else []:
        try:
            rep = inspect(str(f))
            if not rep.ok or rep.name in taken:
                log(f"SYS: Extension {f.name} écartée : {'; '.join(rep.problems) or 'nom déjà pris'}")
                continue
            loaded.append(load_into(jarvis, f))
        except Exception as e:
            log(f"SYS: Extension {f.name} écartée : {type(e).__name__}: {e}")
    return loaded
