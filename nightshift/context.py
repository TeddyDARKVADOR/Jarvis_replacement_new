"""
nightshift/context.py — le contexte ciblé d'une tâche, sous un budget de tokens.

    build(task, repo, outputs_dir) -> {"messages", "estimated_tokens", "sections"}

Plan Night Shift, phase 2 : au plus 6 000 tokens par appel, réponse comprise
(Groq gratuit : 8 000 tokens par minute et par modèle). Le prompt vise donc
4 000 tokens et la réponse 1 800 au plus.

Ce qui entre, par ordre de priorité, jusqu'au budget :
  1. la tâche et le symptôme exact (ligne FAIL et son détail, fin de sortie) ;
  2. le contrôle qui échoue, en entier (retrouvé dans le selftest par l'AST) ;
  3. pour une régression, le diff des commits suspects, limité à la zone ;
  4. les fonctions que le contrôle appelle dans le paquet testé, ou la fonction
     qui entoure une remarque ruff ;
  5. le plan du fichier suspect (imports, signatures).
Jamais un fichier entier s'il dépasse le budget ; une section trop longue est
coupée et le dit. Le code du dépôt est montré au modèle, jamais exécuté.
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

from nightshift import zone
from nightshift.run import output_name, redact
from nightshift.suites import SUITES

PROMPT_BUDGET = 4000
RESPONSE_BUDGET = 1800
CHARS_PER_TOKEN = 3.2          # prudent pour du code ; mieux vaut sous-estimer la place

SYSTEM = """You fix ONE problem in the JARVIS repository (Python 3.11+, comments in French).
You get the failing check or the lint finding, the exact symptom and the relevant code.

Rules:
- Answer with ONE unified diff in a ```diff fenced block: paths relative to the repository
  root with a/ and b/ prefixes, context lines copied verbatim from the code shown.
- Make the smallest change that fixes the root cause in the product code. No refactoring,
  no reformatting, keep the surrounding style.
- Never edit tests or selftests, CI, dependencies or configuration: such a patch is rejected.
- You may only edit files under: context/ cognition/ eval_lab/ (not eval_lab/corpus/)
  capabilities/ reminders/ resilience/ offline/ extensions/ skills/.
- If the code shown is not enough to fix it safely, answer exactly: NO_PATCH: <one-line reason>.
"""


def estimate(text: str) -> int:
    return int(len(text or "") / CHARS_PER_TOKEN) + 1


def _cut(text: str, tokens: int) -> str:
    limit = int(tokens * CHARS_PER_TOKEN)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit("\n", 1)[0] + "\n… (coupé : budget de tokens)\n"


def selftest_file(suite_name: str, repo: Path) -> Path | None:
    for s in SUITES:
        if s.name == suite_name and s.argv[:1] == ("-m",):
            mod = s.argv[1].replace(".", "/")
            for cand in (repo / f"{mod}.py", repo / mod / "selftest.py"):
                if cand.is_file():
                    return cand
    return None


def package_dir(suite_name: str, repo: Path) -> str | None:
    """Le dossier testé par une suite, relatif au dépôt (« reminders/ »)."""
    f = selftest_file(suite_name, repo)
    if not f:
        return None
    parts = f.relative_to(repo).parts
    return f"{parts[0]}/" if len(parts) > 1 else None


# ── le contrôle qui échoue ───────────────────────────────────────────────────

def _check_id(label: str) -> str:
    m = re.match(r"^(\d+[a-z]?)\s", label)
    return m.group(1) if m else label.strip()


def _literal_start(node) -> str:
    """Le texte d'un argument str ou du début d'une f-string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
            else:
                break
        return "".join(out)
    return ""


def find_check(source: str, check_id: str) -> tuple[int, int] | None:
    """(première ligne, dernière ligne) du contrôle `check_id`, 1-indexées.

    @check("21 …") sur une fonction (la plupart des suites), ou un appel
    report.check("25 …") (client_desktop, routage) avec les lignes qui le
    préparent depuis le commentaire « # -- 25. »."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and dec.args and getattr(dec.func, "id", getattr(dec.func, "attr", "")) == "check":
                    if _check_id(_literal_start(dec.args[0])) == check_id:
                        return dec.lineno, node.end_lineno
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "check" and node.args:
            text = _literal_start(node.args[0])
            if text and _check_id(text) == check_id:
                start = node.lineno
                for i in range(node.lineno - 1, max(0, node.lineno - 40), -1):
                    if re.match(rf"^\s*# -- {re.escape(check_id)}\.", lines[i - 1]):
                        start = i
                        break
                else:
                    start = max(1, node.lineno - 12)
                return start, node.end_lineno
    return None


def called_names(snippet: str) -> set[str]:
    """Les noms appelés dans un extrait : f(…) et module.f(…)."""
    try:
        tree = ast.parse(_dedent(snippet))
    except SyntaxError:
        return set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", snippet))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def _dedent(text: str) -> str:
    lines = text.splitlines()
    indents = [len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()]
    cut = min(indents) if indents else 0
    return "\n".join(ln[cut:] for ln in lines)


# ── le code du paquet ────────────────────────────────────────────────────────

def _segment(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1:end])


def definitions(repo: Path, pkg: str, names: set[str]) -> list[tuple[str, int, int, str]]:
    """(chemin, début, fin, source) des fonctions et classes nommées dans `names`,
    dans les fichiers modifiables du paquet `pkg` (les tests sont exclus)."""
    out = []
    for f in sorted((repo / pkg).rglob("*.py")):
        rel = f.relative_to(repo).as_posix()
        if not zone.allowed(rel)[0]:
            continue
        try:
            src = f.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        lines = src.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names:
                start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
                out.append((rel, start, node.end_lineno, _segment(lines, start, node.end_lineno)))
    return out


def enclosing(repo: Path, rel: str, line: int) -> tuple[int, int, str] | None:
    """La fonction la plus interne qui contient `line`, sinon 25 lignes autour."""
    try:
        src = (repo / rel).read_text(encoding="utf-8")
    except OSError:
        return None
    lines = src.splitlines()
    best = None
    try:
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= line <= node.end_lineno:
                if best is None or node.lineno >= best.lineno:
                    best = node
    except SyntaxError:
        pass
    if best is not None:
        return best.lineno, best.end_lineno, _segment(lines, best.lineno, best.end_lineno)
    start, end = max(1, line - 12), min(len(lines), line + 12)
    return start, end, _segment(lines, start, end)


def outline(repo: Path, rel: str) -> str:
    """Imports et signatures d'un fichier, une ligne chacun."""
    try:
        src = (repo / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ""
    lines = src.splitlines()
    out = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out.append(lines[node.lineno - 1].strip())
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(f"{node.lineno}: {lines[node.lineno - 1].strip()}")
            if isinstance(node, ast.ClassDef):
                out += [f"{n.lineno}:     {lines[n.lineno - 1].strip()}" for n in node.body
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return "\n".join(out)


def suspect_diff(repo: Path, commits: list[str], limit_tokens: int) -> str:
    """Le diff des commits suspects, restreint aux fichiers modifiables."""
    shas = [c.split()[0] for c in commits or [] if re.fullmatch(r"[0-9a-f]{7,40}", c.split()[0] if c.split() else "")]
    if not shas:
        return ""
    try:
        files = subprocess.run(["git", "show", "--name-only", "--format=", *shas], cwd=str(repo),
                               capture_output=True, text=True, timeout=30).stdout.split()
        keep = sorted({f for f in files if zone.allowed(f)[0]})
        if not keep:
            return ""
        text = subprocess.run(["git", "show", "--format=commit %h %s", "--unified=3", *shas, "--", *keep],
                              cwd=str(repo), capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ""
    return _cut(redact(text), limit_tokens)


_RESULT_LINE = re.compile(r"^\s*\[\s*(PASS|FAIL|SKIP|ok)\s*\]|^ {2}(OK {2}|ÉCHEC )|^\s*\d+\s*/\s*\d+")


def symptom(task: dict, outputs_dir: Path | None) -> str:
    """La ligne FAIL exacte et ce qui la suit dans la sortie brute, sinon le détail du rapport."""
    if task.get("source") == "ruff":
        return f"{task['path']}:{task['line']}: {task['code']} {task['message']}"
    lines = [f"[FAIL] {task.get('check')} {task.get('label')}  {task.get('detail', '')}".rstrip()]
    log = (outputs_dir / output_name(task.get("suite", ""))) if outputs_dir else None
    if log and log.is_file():
        raw = log.read_text(encoding="utf-8", errors="replace").splitlines()
        cid = re.escape(str(task.get("check")))
        for i, ln in enumerate(raw):
            if re.search(rf"(FAIL\s*\]|ÉCHEC)\s+{cid}\b", ln) or (task.get("check") == "*" and "Traceback" in ln):
                lines = [ln.strip()]
                for x in raw[i + 1:i + 15]:          # la suite du message, jusqu'au résultat suivant
                    if _RESULT_LINE.match(x):
                        break
                    if x.strip():
                        lines.append(x.rstrip())
                break
    return redact("\n".join(lines))


# ── assemblage ───────────────────────────────────────────────────────────────

def _block(title: str, body: str, lang: str = "") -> str:
    return f"## {title}\n```{lang}\n{body.rstrip()}\n```\n"


def build(task: dict, repo: Path, outputs_dir: Path | None = None, budget: int = PROMPT_BUDGET) -> dict:
    """Le prompt d'une tâche (messages pour une API compatible OpenAI) et sa taille estimée."""
    left = budget - estimate(SYSTEM)
    sections: list[tuple[str, str]] = []

    def add(name: str, text: str, cap: int) -> None:
        nonlocal left
        if not text.strip() or left <= 50:
            return
        text = _cut(text, min(cap, left))
        left -= estimate(text)
        sections.append((name, text))

    if task.get("source") == "ruff":
        head = (f"Lint finding to fix (ruff, a likely real bug): {task['code']} in {task['path']} "
                f"line {task['line']}.\nSymptom: {symptom(task, outputs_dir)}\n")
        add("tâche", head, 600)
        enc = enclosing(repo, task["path"], int(task.get("line") or 1))
        if enc:
            add("fonction", _block(f"{task['path']}, lines {enc[0]}-{enc[1]}", enc[2], "python"), 1800)
        add("plan", _block(f"Outline of {task['path']}", outline(repo, task["path"])), 500)
    else:
        why = {"regression": "it passed on the previous night and fails now (a regression)",
               "failing_check": "it fails (already red, or no earlier reference to compare with)"}
        head = (f"Failing check to fix: suite `{task.get('suite')}`, check {task.get('check')} "
                f"« {task.get('label')} » — {why.get(task.get('source'), 'it fails')}.\n"
                f"Exact symptom:\n{symptom(task, outputs_dir)}\n")
        add("tâche", head, 700)
        tf = selftest_file(task.get("suite", ""), repo)
        snippet = ""
        if tf:
            src = tf.read_text(encoding="utf-8", errors="replace")
            span = find_check(src, str(task.get("check")))
            if span:
                snippet = _segment(src.splitlines(), *span)
                add("contrôle", _block(f"The failing check: {tf.relative_to(repo).as_posix()}, lines "
                                       f"{span[0]}-{span[1]} (read-only)", snippet, "python"), 1200)
        if task.get("source") == "regression" and task.get("suspects"):
            add("suspects", _block("Diff of the suspect commits since the last green night (editable files only)",
                                   suspect_diff(repo, task["suspects"], 1200), "diff"), 1200)
        pkg = package_dir(task.get("suite", ""), repo)
        if pkg and snippet:
            defs = definitions(repo, pkg, called_names(snippet))
            for rel, a, b, text in defs[:6]:
                add(f"def:{rel}:{a}", _block(f"{rel}, lines {a}-{b}", text, "python"), 900)
            if defs:
                add("plan", _block(f"Outline of {defs[0][0]}", outline(repo, defs[0][0])), 400)

    user = "\n".join(text for _, text in sections)
    user += "\nAnswer with the ```diff block only (or NO_PATCH: reason)."
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    return {"messages": messages, "estimated_tokens": estimate(SYSTEM) + estimate(user),
            "sections": [name for name, _ in sections], "response_budget": RESPONSE_BUDGET}
