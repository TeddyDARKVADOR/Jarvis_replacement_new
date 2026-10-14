"""
nightshift/zone.py — ce qu'un patch de la nuit a le droit de toucher.

    allowed(path)       (True, "") ou (False, raison) pour un chemin du dépôt
    check_patch(diff)   PatchCheck(ok, files, reasons) pour un diff unifié entier

LISTE BLANCHE (plan Night Shift, phase 2)
    context/  cognition/  eval_lab/  capabilities/  reminders/  resilience/
    offline/  extensions/  skills/
    et seulement des fichiers texte (.py .json .md .txt .yaml .yml).

REFUSÉ D'OFFICE, même dans la liste blanche
  - les tests (selftest.py, *_selftest.py, tests/, test_*.py) et le corpus
    d'eval_lab : un correctif qui réécrit sa propre preuve ne prouve rien ;
  - nightshift/ en entier : chacun de ses fichiers fixe une règle (cette zone,
    les budgets), l'applique (validation, comparaison, lecture des suites) ou
    filtre ce qui parvient à Gemini (briefing). Le plan l'ouvrait « sauf ses
    règles de sécurité » : en pratique, c'est tout le paquet ;
  - main.py, core/, actions/, dashboard/, memory/, plugins/, server/,
    client_desktop/, .github/, deployment/, config/, requirements*,
    pyproject.toml, tout ce qui ressemble à un secret ;
  - binaires, liens symboliques, changements de droits.

Un seul chemin hors zone, et le patch est rejeté entier, sans exception. Le
diff est lu comme une donnée : aucune ligne n'en est exécutée ici.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

ALLOWED_DIRS = ("context/", "cognition/", "eval_lab/", "capabilities/", "reminders/", "resilience/",
                "offline/", "extensions/", "skills/")
TEXT_SUFFIXES = (".py", ".json", ".md", ".txt", ".yaml", ".yml")
REFUSED_DIRS = ("nightshift/", "eval_lab/corpus/", "main.py", "core/", "actions/", "dashboard/", "memory/",
                "plugins/", "server/", "client_desktop/", ".github/", "deployment/", "config/")
_TEST = re.compile(r"(^|/)(selftest\.py|[^/]*_selftest\.py|test_[^/]*\.py|tests?/)")
_SECRET = re.compile(r"(api_?keys?|secret|token|credential|password|\.env\b|\.pem$|\.key$|id_rsa)",
                     re.IGNORECASE)


def allowed(path: str) -> tuple[bool, str]:
    """Un chemin relatif au dépôt peut-il être modifié par un patch de la nuit ?"""
    p = str(path or "")
    if not p or "\\" in p or "\x00" in p or p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        return False, f"chemin invalide : {p!r}"
    parts = PurePosixPath(p).parts
    if ".." in parts or "." in parts or any(part.startswith(".git") for part in parts):
        return False, f"chemin interdit : {p}"
    if PurePosixPath(p).name.startswith(("requirements", "pyproject")):
        return False, f"dépendances ou configuration : {p}"
    for refused in REFUSED_DIRS:
        if p == refused.rstrip("/") or p.startswith(refused):
            return False, f"zone refusée ({refused}) : {p}"
    if not p.startswith(ALLOWED_DIRS):
        return False, f"hors de la liste blanche : {p}"
    if _TEST.search(p):
        return False, f"fichier de test (la preuve ne se réécrit pas) : {p}"
    if _SECRET.search(p):
        return False, f"ressemble à un secret : {p}"
    if not p.endswith(TEXT_SUFFIXES):
        return False, f"type de fichier non autorisé : {p}"
    return True, ""


@dataclass
class PatchCheck:
    ok: bool
    files: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


_GIT_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
_FILE_LINE = re.compile(r"^(---|\+\+\+) (\S+)")
_RENAME = re.compile(r"^(rename|copy) (from|to) (.+)$")


def _strip_prefix(path: str) -> str | None:
    if path == "/dev/null":
        return None
    path = path.split("\t", 1)[0]
    return path[2:] if path.startswith(("a/", "b/")) else path


def normalize(diff: str) -> str:
    """Ajoute les préfixes a/ et b/ quand un modèle les oublie (« --- reminders/x.py »),
    pour que `git apply` lise les mêmes chemins que ce filtre. Rien d'autre ne change."""
    out = []
    for line in (diff or "").replace("\r\n", "\n").split("\n"):
        m = _FILE_LINE.match(line)
        if m and m.group(2) != "/dev/null" and not m.group(2).startswith(("a/", "b/")):
            line = f"{m.group(1)} {'a' if m.group(1) == '---' else 'b'}/{m.group(2)}"
        out.append(line)
    text = "\n".join(out)
    return text if text.endswith("\n") else text + "\n"


_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def touched(diff: str) -> tuple[list[str], list[str]]:
    """(chemins touchés, problèmes de forme) d'un diff unifié.

    Les en-têtes ne sont lus qu'entre les blocs @@ : une ligne retirée qui
    commence par « -- » ne passe pas pour un nom de fichier."""
    files: list[str] = []
    problems: list[str] = []
    old_left = new_left = 0

    def add(path: str | None) -> None:
        if path and path not in files:
            files.append(path)

    for line in (diff or "").split("\n"):
        if old_left > 0 or new_left > 0:
            if line.startswith(" ") or line == "":
                old_left, new_left = old_left - 1, new_left - 1
            elif line.startswith("-"):
                old_left -= 1
            elif line.startswith("+"):
                new_left -= 1
            elif line.startswith("\\"):
                pass
            else:
                problems.append("bloc @@ incomplet")
                old_left = new_left = 0
            continue
        if m := _HUNK.match(line):
            old_left = int(m.group(1)) if m.group(1) is not None else 1
            new_left = int(m.group(2)) if m.group(2) is not None else 1
        elif m := _GIT_HEADER.match(line):
            add(m.group(1))
            add(m.group(2))
        elif m := _FILE_LINE.match(line):
            add(_strip_prefix(m.group(2)))
        elif m := _RENAME.match(line):
            add(m.group(3).strip())
        elif line.startswith(("GIT binary patch", "Binary files ")):
            problems.append("binaire")
        elif re.match(r"^(new file|deleted file|old|new) mode \d+", line):
            mode = line.rsplit(" ", 1)[-1]
            if mode == "120000":
                problems.append("lien symbolique")
            elif line.startswith(("old mode", "new mode")):
                problems.append("changement de droits")
            elif mode != "100644":
                problems.append(f"mode {mode}")
    return files, problems


def check_patch(diff: str) -> PatchCheck:
    """Le patch entier est-il dans la zone ? Le moindre écart le rejette."""
    if not (diff or "").strip():
        return PatchCheck(False, [], ["diff vide"])
    files, problems = touched(diff)
    reasons = list(dict.fromkeys(problems))
    if not files:
        reasons.append("aucun fichier reconnu dans le diff")
    for f in files:
        ok, why = allowed(f)
        if not ok:
            reasons.append(why)
    return PatchCheck(not reasons, files, reasons)
