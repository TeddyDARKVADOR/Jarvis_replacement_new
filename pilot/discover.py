"""
pilot/discover.py — les raccourcis que cette machine offre, découverts, jamais écrits en dur.

    apps(query)       applications installées qui ressemblent à la demande
    schemes(query)    schémas d'URL enregistrés (msteams:, vscode:, mailto:, zoommtg:…)
    sites(query)      sites déjà visités qui correspondent (historique local des navigateurs)
    open_target(t)    ouvre une URL, un lien profond, un fichier ou une application

Le modèle s'en sert pour choisir le chemin le plus sûr — un lien profond plutôt
que dix clics — mais rien ici ne sait ce qu'est Teams ou draw.io : on lit ce que
le système déclare.

  Windows  menu Démarrer (raccourcis .lnk et AppID des applications du Store),
           HKEY_CLASSES_ROOT (clés portant « URL Protocol »).
  Linux    fichiers .desktop (Name, Exec, MimeType=x-scheme-handler/…).
  macOS    /Applications et ~/Applications ; schémas : `open` les résout.
  Sites    historiques Chromium (Edge, Chrome, Brave, Vivaldi, Opera) et
           Firefox, copiés puis lus en lecture seule. Ne sortent du poste que
           les entrées qui correspondent à la recherche (8 au plus), pour
           répondre à « le site UML que j'utilise d'habitude ».
"""
from __future__ import annotations

import configparser
import difflib
import glob
import html
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unicodedata
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

_cache: dict[str, tuple[float, object]] = {}
TTL = 600.0


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _cached(key: str, build):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    value = build()
    _cache[key] = (time.time(), value)
    return value


def rank(query: str, items: list[dict], key: str = "name", limit: int = 8) -> list[dict]:
    """Les entrées dont le nom contient la demande, puis les plus ressemblantes. Pur."""
    q = _fold(query).strip()
    if not q:
        return items[:limit]
    scored = []
    for it in items:
        name = _fold(it.get(key, ""))
        if not name:
            continue
        score = 2.0 if q in name else max(difflib.SequenceMatcher(None, q, w).ratio() for w in name.split() or [name])
        if score >= 0.8:
            scored.append((score, -len(name), it))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    out, seen = [], set()
    for _, _, it in scored:
        k = _fold(it.get(key, ""))
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out[:limit]


# ── applications ─────────────────────────────────────────────────────────────

def _apps_windows() -> list[dict]:
    out = []
    roots = [os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
             os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs")]
    for root in roots:
        for lnk in glob.glob(os.path.join(root, "**", "*.lnk"), recursive=True):
            name = Path(lnk).stem
            if not re.search(r"uninstall|désinstall|readme|help|aide", name, re.I):
                out.append({"name": name, "launch": lnk})
    try:   # applications du Store (le nouveau Teams, la Calculatrice…) : leur AppID
        res = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-StartApps | ForEach-Object { $_.Name + '|' + $_.AppID }"],
                             capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace")
        known = {_fold(a["name"]) for a in out}
        for line in res.stdout.splitlines():
            name, _, appid = line.partition("|")
            if name.strip() and appid.strip() and _fold(name) not in known:
                out.append({"name": name.strip(), "launch": f"shell:AppsFolder\\{appid.strip()}"})
    except Exception:
        pass
    return out


def _desktop_dirs() -> list[str]:
    data = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    home = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return [os.path.join(d, "applications") for d in [home] + data] + \
        ["/var/lib/flatpak/exports/share/applications", os.path.expanduser("~/.local/share/flatpak/exports/share/applications")]


def parse_desktop(text: str, desktop_id: str) -> dict | None:
    """Une entrée .desktop → {name, launch, schemes} ; None si cachée. Pur."""
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read_string(text)
    except configparser.Error:
        return None
    if "Desktop Entry" not in cp:
        return None
    e = cp["Desktop Entry"]
    if e.get("NoDisplay", "").lower() == "true" or e.get("Type", "Application") != "Application":
        return None
    schemes = [m.split("/", 1)[1] for m in e.get("MimeType", "").split(";") if m.startswith("x-scheme-handler/")]
    return {"name": e.get("Name", desktop_id), "launch": desktop_id, "schemes": schemes}


def _desktop_entries() -> list[dict]:
    out, seen = [], set()
    for d in _desktop_dirs():
        for f in glob.glob(os.path.join(d, "*.desktop")):
            did = os.path.basename(f)
            if did in seen:
                continue
            seen.add(did)
            try:
                entry = parse_desktop(Path(f).read_text(encoding="utf-8", errors="replace"), did)
            except OSError:
                entry = None
            if entry:
                out.append(entry)
    return out


def _apps_macos() -> list[dict]:
    out = []
    for root in ("/Applications", os.path.expanduser("~/Applications"), "/System/Applications"):
        for app in glob.glob(os.path.join(root, "*.app")) + glob.glob(os.path.join(root, "*", "*.app")):
            out.append({"name": Path(app).stem, "launch": app})
    return out


def all_apps() -> list[dict]:
    def build():
        if sys.platform == "win32":
            return _apps_windows()
        if sys.platform == "darwin":
            return _apps_macos()
        return _desktop_entries()
    return _cached("apps", build)


def apps(query: str) -> list[dict]:
    return [{"name": a["name"]} for a in rank(query, all_apps())]


# ── schémas d'URL ────────────────────────────────────────────────────────────

def _schemes_windows() -> list[str]:
    import winreg
    out = []
    i = 0
    while True:
        try:
            name = winreg.EnumKey(winreg.HKEY_CLASSES_ROOT, i)
        except OSError:
            break
        i += 1
        if name.startswith(".") or "." in name and len(name) > 30:
            continue
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, name) as k:
                winreg.QueryValueEx(k, "URL Protocol")
            out.append(name.lower())
        except OSError:
            continue
    return out


def all_schemes() -> list[str]:
    def build():
        if sys.platform == "win32":
            return _schemes_windows()
        if sys.platform.startswith("linux"):
            return sorted({s for e in _desktop_entries() for s in e.get("schemes") or []})
        return []
    return _cached("schemes", build)


def schemes(query: str) -> list[str]:
    q = _fold(query)
    found = [s for s in all_schemes() if q and (q in s or difflib.SequenceMatcher(None, q, s).ratio() >= 0.7)]
    return sorted(found, key=len)[:12]


# ── sites déjà visités ───────────────────────────────────────────────────────

def _history_files() -> list[tuple[str, str]]:
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        chromium = [base / p for p in ("Microsoft/Edge/User Data", "Google/Chrome/User Data",
                                       "BraveSoftware/Brave-Browser/User Data", "Vivaldi/User Data")]
        chromium.append(Path(os.environ.get("APPDATA", "")) / "Opera Software/Opera Stable")
        firefox = Path(os.environ.get("APPDATA", "")) / "Mozilla/Firefox/Profiles"
    elif sys.platform == "darwin":
        base = home / "Library/Application Support"
        chromium = [base / p for p in ("Microsoft Edge", "Google/Chrome", "BraveSoftware/Brave-Browser", "Vivaldi")]
        firefox = base / "Firefox/Profiles"
    else:
        base = home / ".config"
        chromium = [base / p for p in ("microsoft-edge", "google-chrome", "chromium", "BraveSoftware/Brave-Browser", "vivaldi")]
        firefox = home / ".mozilla/firefox"
    files = []
    for root in chromium:
        for hist in glob.glob(str(root / "*" / "History")) + glob.glob(str(root / "History")):
            files.append(("chromium", hist))
    for places in glob.glob(str(firefox / "*" / "places.sqlite")):
        files.append(("firefox", places))
    return files


def _query_history(kind: str, path: str, words: list[str]) -> list[dict]:
    tmp = os.path.join(tempfile.mkdtemp(), "h.sqlite")
    shutil.copy2(path, tmp)                   # le navigateur garde le fichier verrouillé : une copie
    try:
        con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        like = " AND ".join(["(lower(url) LIKE ? OR lower(title) LIKE ?)"] * len(words)) or "1"
        args = [a for w in words for a in (f"%{w}%", f"%{w}%")]
        if kind == "chromium":
            rows = con.execute(f"SELECT url, title, visit_count, last_visit_time FROM urls WHERE {like} "
                               "ORDER BY visit_count DESC LIMIT 200", args).fetchall()
            when = lambda t: datetime(1601, 1, 1) + timedelta(microseconds=t or 0)   # noqa: E731
        else:
            rows = con.execute(f"SELECT url, title, visit_count, last_visit_date FROM moz_places WHERE {like} "
                               "ORDER BY visit_count DESC LIMIT 200", args).fetchall()
            when = lambda t: datetime(1970, 1, 1) + timedelta(microseconds=t or 0)   # noqa: E731
        con.close()
    finally:
        shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)
    return [{"url": u, "title": t or "", "visits": int(v or 0), "last": when(lv).strftime("%Y-%m-%d")} for u, t, v, lv in rows]


def by_site(rows: list[dict], limit: int = 8) -> list[dict]:
    """Regroupe par site : visites cumulées, dernière visite, une adresse et un titre représentatifs. Pur."""
    sites: dict[str, dict] = {}
    for r in rows:
        m = re.match(r"https?://([^/]+)", r["url"])
        if not m:
            continue
        host = m.group(1).lower().removeprefix("www.")
        # Le chemin sans paramètres ni fragment, un titre court : une URL complète peut porter un jeton de
        # partage, un titre peut être une conversation privée (mesuré : liens OneDrive, titres ChatGPT).
        path = re.split(r"[?#]", r["url"], maxsplit=1)[0]
        seg = re.match(r"(https?://[^/]+/[^/]{0,40})", path)
        url = seg.group(1) if seg else path[:80]          # le site et le premier segment, pas plus
        title = html.unescape(r["title"])[:40]
        s = sites.setdefault(host, {"site": host, "visits": 0, "last": "", "example": url, "title": title})
        s["visits"] += r["visits"]
        if r["last"] > s["last"]:
            s["last"], s["example"], s["title"] = r["last"], url, title
    return sorted(sites.values(), key=lambda s: (s["visits"], s["last"]), reverse=True)[:limit]


def sites(query: str) -> list[dict]:
    words = [w for w in re.findall(r"[\w.-]{2,}", _fold(query))][:4]
    rows: list[dict] = []
    for kind, path in _history_files():
        try:
            rows += _query_history(kind, path, words)
        except Exception:
            continue
    return by_site(rows)


# ── ouvrir ───────────────────────────────────────────────────────────────────

_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]{1,30}:")


def _os_open(target: str) -> None:
    if sys.platform == "win32":
        os.startfile(target)                  # noqa: S606 — l'ouvreur du système, comme un double-clic
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_target(target: str) -> str:
    target = (target or "").strip()
    if not target:
        return "rien à ouvrir"
    if re.match(r"^https?://", target, re.I):
        webbrowser.open(target)
        return f"ouvert dans le navigateur : {target[:120]}"
    if _SCHEME.match(target) and not re.match(r"^[a-zA-Z]:\\", target):
        scheme = target.split(":", 1)[0].lower()
        known = all_schemes()
        if known and scheme not in known and scheme not in ("mailto", "tel", "file"):
            return f"le schéma « {scheme}: » n'est enregistré par aucune application ici"
        _os_open(target)
        return f"lien ouvert par l'application de « {scheme}: »"
    if os.path.exists(os.path.expanduser(target)):
        _os_open(os.path.expanduser(target))
        return f"ouvert : {target}"
    hits = rank(target, all_apps(), limit=1)
    if not hits:
        return f"aucune application installée ne ressemble à « {target} »"
    app = hits[0]
    launch = app["launch"]
    if sys.platform == "win32":
        if launch.startswith("shell:AppsFolder"):
            subprocess.Popen(["explorer.exe", launch])
        else:
            os.startfile(launch)              # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", launch])
    else:
        did = launch.removesuffix(".desktop")
        if shutil.which("gtk-launch"):
            subprocess.Popen(["gtk-launch", did], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", launch], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"application lancée : {app['name']}"
