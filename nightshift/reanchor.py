"""
nightshift/reanchor.py — refaire les en-têtes @@ d'un diff écrit par un modèle.

    repair(diff, repo) -> (diff réparé ou None, notes)

Les modèles écrivent souvent « @@ ... @@ » ou des numéros de ligne faux. Sans
numéros, git apply refuse tout : « No valid patches in input » — c'est ce qui a
fait échouer les deux tentatives de gpt-oss-120b au premier vrai run de la
phase 2 (2026-10-14, branche test/bug-plante).

Ce module ne fait confiance qu'au CONTENU des blocs : les lignes d'avant
(contexte et lignes retirées) sont retrouvées dans le vrai fichier, exactement
ou aux espaces de fin près ; le bloc est appliqué en gardant les lignes de
contexte du fichier, pas celles du modèle ; puis difflib régénère un diff
propre, aux bons numéros. Il ne devine rien : un bloc introuvable ou ambigu
est une erreur, renvoyée au modèle pour sa seconde tentative.

Ne lit que des fichiers déjà acceptés par zone.py ; n'exécute rien ; ne
modifie rien sur le disque.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path

from nightshift import zone

_HEADER = re.compile(r"^@@(?: -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@)?")
_META = ("diff --git", "index ", "new file mode", "deleted file mode", "similarity", "rename ", "old mode", "new mode")


def _path(text: str) -> str | None:
    p = text.split("\t", 1)[0].strip()
    if p == "/dev/null":
        return None
    return p[2:] if p.startswith(("a/", "b/")) else p


def _file_patches(diff: str) -> list[dict]:
    """Un dict par fichier : {"old", "new", "hunks": [(ligne annoncée ou None, [(op, texte)])]}."""
    lines = (diff or "").replace("\r\n", "\n").split("\n")
    files: list[dict] = []
    hunk = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            files.append({"old": _path(ln[4:]), "new": _path(lines[i + 1][4:]), "hunks": []})
            hunk = None
            i += 2
            continue
        if files and ln.startswith("@@"):
            m = _HEADER.match(ln)
            hunk = (int(m.group(1)) if m and m.group(1) else None, [])
            files[-1]["hunks"].append(hunk)
        elif hunk is not None and not ln.startswith(_META):
            if ln.startswith("\\"):
                pass                                  # « \ No newline at end of file »
            elif ln[:1] in ("+", "-", " "):
                hunk[1].append((ln[0], ln[1:]))
            else:
                hunk[1].append((" ", ln))             # contexte dont le modèle a mangé l'espace
        i += 1
    for f in files:                                   # le saut de ligne final n'est pas une ligne de contexte
        for _, ops in f["hunks"]:
            while ops and ops[-1] == (" ", ""):
                ops.pop()
    return files


def _locate(text_lines: list[str], ops: list[tuple[str, str]], hint: int | None) -> tuple[int | None, str]:
    old = [t for op, t in ops if op in (" ", "-")]
    if not old:
        return None, "bloc sans ligne de contexte : impossible à situer"
    n = len(old)
    # Exact, puis aux espaces de fin près, puis — dernier recours, pour un modèle
    # qui a mangé l'espace de tête des lignes de contexte — aux espaces près. Ce
    # n'est qu'un moyen de SITUER le bloc, et seulement s'il est unique : les
    # lignes de contexte reconstruites viennent toujours du fichier.
    for strict, norm in ((True, lambda s: s), (True, lambda s: s.rstrip()), (False, lambda s: s.strip())):
        target = [norm(x) for x in old]
        found = [i for i in range(len(text_lines) - n + 1)
                 if [norm(x) for x in text_lines[i:i + n]] == target]
        if len(found) == 1:
            return found[0], ""
        if len(found) > 1:
            if hint is not None and strict:
                return min(found, key=lambda i: abs(i - (hint - 1))), ""
            return None, f"bloc ambigu : {len(found)} endroits possibles"
    return None, f"bloc introuvable : « {old[0].strip()[:60]} »"


def _apply(text_lines: list[str], hunks: list) -> tuple[list[str] | None, list[str]]:
    placed, notes = [], []
    for n, (hint, ops) in enumerate(hunks, 1):
        at, why = _locate(text_lines, ops, hint)
        if at is None:
            return None, [f"bloc {n} : {why}"]
        placed.append((at, ops))
        if hint != at + 1:
            notes.append(f"bloc {n} réancré ligne {at + 1}" + (" (en-tête sans numéros)" if hint is None else ""))
    placed.sort(key=lambda p: p[0])
    for (a, ops_a), (b, _) in zip(placed, placed[1:]):
        if a + sum(op != "+" for op, _ in ops_a) > b:
            return None, ["deux blocs se chevauchent"]
    out = list(text_lines)
    for at, ops in reversed(placed):
        new, j = [], 0
        for op, t in ops:
            if op == " ":
                new.append(out[at + j])               # la ligne du fichier, pas la copie du modèle
                j += 1
            elif op == "-":
                j += 1
            else:
                new.append(t)
        out[at:at + j] = new
    return out, notes


def repair(diff: str, repo: Path) -> tuple[str | None, list[str]]:
    """Le diff régénéré aux bons numéros, ou (None, [la raison])."""
    files = _file_patches(diff)
    if not files:
        return None, ["aucun fichier dans le diff"]
    parts, notes = [], []
    for f in files:
        path = f["new"] or f["old"]
        if not path or not zone.allowed(path)[0]:
            return None, [f"chemin refusé : {path}"]
        if f["new"] is None:
            return None, [f"suppression de fichier non prise en charge : {path}"]
        if f["old"] is None:
            added = [t for _, ops in f["hunks"] for op, t in ops if op == "+"]
            text = "".join(x + "\n" for x in added)
            parts.append("".join(difflib.unified_diff([], text.splitlines(True), "/dev/null", f"b/{path}")))
            continue
        target = repo / path
        try:
            original = target.read_text(encoding="utf-8")
        except OSError:
            return None, [f"fichier introuvable : {path}"]
        if original and not original.endswith("\n"):
            return None, [f"{path} ne finit pas par un saut de ligne : réancrage refusé"]
        lines = original.split("\n")[:-1] if original else []
        new, why = _apply(lines, f["hunks"])
        if new is None:
            return None, [f"{path} : {w}" for w in why]
        notes += [f"{path} : {w}" for w in why]
        before = [x + "\n" for x in lines]
        after = [x + "\n" for x in new]
        parts.append("".join(difflib.unified_diff(before, after, f"a/{path}", f"b/{path}")))
    text = "".join(parts)
    if not text.strip():
        return None, ["le diff ne change rien"]
    return text, notes
