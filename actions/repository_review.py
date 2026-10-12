"""Bounded, read-only repository inspection with a compact model handoff."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

MAX_DISCOVERED = 4000
MAX_SCAN_FILES = 140
MAX_RETURN_FILES = 12
MAX_FILE_CHARS = 2800
MAX_CONTEXT_CHARS = 22000
TEXT_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".go",
    ".rs", ".c", ".h", ".cpp", ".cs", ".rb", ".php", ".swift", ".kt",
    ".sh", ".ps1", ".html", ".css", ".scss", ".vue", ".svelte", ".json",
    ".yaml", ".yml", ".toml", ".ini", ".md", ".rst", ".sql", ".xml",
}
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "vendor",
    "dist", "build", "coverage", "__pycache__", ".next", ".cache", "target",
}
SKIP_NAMES = {
    ".env", ".env.local", ".env.production", "api_key.json", "api_keys.json",
    "credentials.json", "secrets.json", "package-lock.json", "pnpm-lock.yaml",
    "yarn.lock", "poetry.lock", "uv.lock", "Cargo.lock",
}


def _is_sensitive(path: Path) -> bool:
    name = path.name.lower()
    parts = {p.lower() for p in path.parts}
    return (
        name in SKIP_NAMES
        or name.startswith(".env")
        or name in {"long_term.json", "device_credentials.json"}
        or any(word in name for word in ("secret", "credential", "password", "private_key"))
        or name.endswith("_token.json")
        or name.endswith((".pem", ".p12", ".pfx", ".key", ".secret"))
        or name.endswith(("_credentials.json", "_secrets.json"))
        or bool(parts & {".aws", ".ssh", "secrets", "credentials"})
    )


def _repo_files(root: Path) -> list[Path]:
    """List tracked and non-ignored files, with a bounded filesystem fallback."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
            capture_output=True, timeout=8, check=False,
        )
        if result.returncode == 0:
            rels = result.stdout.decode("utf-8", "replace").split("\0")
            return [root / rel for rel in rels if rel]
    except (OSError, subprocess.TimeoutExpired):
        pass

    found: list[Path] = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for filename in files:
            found.append(Path(base) / filename)
            if len(found) >= MAX_DISCOVERED:
                return found
    return found


def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[a-zA-Z0-9_]{3,}", text)}


def _path_score(path: Path, terms: set[str], overview: bool) -> int:
    rel = path.as_posix().lower()
    name_terms = _words(rel.replace("/", " ").replace(".", " "))
    score = 4 * len(name_terms & terms)
    if path.name.lower() in {"readme.md", "pyproject.toml", "package.json", "cargo.toml"}:
        score += 10 if overview else 2
    if path.name.lower() in {"main.py", "app.py", "index.ts", "index.js", "src.rs"}:
        score += 7 if overview else 1
    if path.suffix.lower() in {".lock", ".map", ".min.js"}:
        score -= 20
    return score


def _ranked_sources(root: Path, question: str, overview: bool) -> list[tuple[Path, str, int]]:
    terms = _words(question)
    candidates = []
    for path in _repo_files(root)[:MAX_DISCOVERED]:
        try:
            rel = path.relative_to(root)
            if (any(part in SKIP_DIRS or part.lower() == "memory" for part in rel.parts)
                    or any(part.startswith(".") and part.lower() != ".github" for part in rel.parts[:-1])
                    or _is_sensitive(rel)):
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file() or path.is_symlink():
                continue
            if path.stat().st_size > 180_000:
                continue
        except OSError:
            continue
        candidates.append((_path_score(rel, terms, overview), rel))

    candidates.sort(key=lambda item: (-item[0], len(item[1].parts), item[1].as_posix().lower()))
    scanned = []
    for path_score, rel in candidates[:MAX_SCAN_FILES]:
        full = root / rel
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\ufffd" in content[:1000] and "\x00" in content[:1000]:
            continue
        content = content[:MAX_FILE_CHARS]
        lower = content.lower()
        body_score = sum(lower.count(term) for term in terms if len(term) > 3)
        score = path_score + min(30, body_score)
        if overview and not terms:
            score = path_score
        scanned.append((rel, content, score))
    scanned.sort(key=lambda row: (-row[2], len(row[0].parts), row[0].as_posix().lower()))
    if overview:
        # An overview always carries the project entry points and README if they
        # exist, even when their prose does not match the natural-language ask.
        for preferred in ("README.md", "readme.md", "pyproject.toml", "package.json", "main.py", "app.py"):
            match = next((row for row in scanned if row[0].as_posix().lower() == preferred.lower()), None)
            if match and match not in scanned[:MAX_RETURN_FILES]:
                scanned.insert(0, match)
    selected = []
    used = 0
    for row in scanned:
        if row in selected:
            continue
        if used + len(row[1]) > MAX_CONTEXT_CHARS:
            continue
        selected.append(row)
        used += len(row[1])
        if len(selected) >= MAX_RETURN_FILES:
            break
    return selected


def _numbered_context(rows: list[tuple[Path, str, int]]) -> str:
    pieces = []
    for path, content, _score in rows:
        lines = content.splitlines()
        numbered = "\n".join(f"{i + 1}: {line}" for i, line in enumerate(lines))
        pieces.append(f"--- {path.as_posix()} ---\n{numbered}")
    return "\n\n".join(pieces)


def inspect_repository(parameters: dict, player=None) -> str:
    params = parameters or {}
    root_arg = str(params.get("repo_path") or "").strip()
    root = Path(root_arg).expanduser() if root_arg else Path(__file__).resolve().parent.parent
    try:
        root = root.resolve(strict=True)
    except OSError:
        return f"Repository path not found: {root_arg or root}"
    if not root.is_dir():
        return f"Repository path is not a folder: {root}"

    mode = str(params.get("mode") or "overview").strip().lower()
    if mode not in {"overview", "inspect"}:
        mode = "overview"
    question = str(params.get("question") or "").strip()
    overview = mode == "overview" or not question
    if overview and not question:
        question = "Give a concise architecture overview: project purpose, entry points, main modules, and likely extension points."
    rows = _ranked_sources(root, question, overview)
    if not rows:
        return f"I found no safe, readable source files in {root}."

    context = _numbered_context(rows)
    listing = "\n".join(f"- {p.as_posix()}" for p, _content, _score in rows)
    prompt = (
        "You analyze source repositories. Use only the supplied excerpts; do not claim you inspected other files. "
        "Be concise and answer in the user's language. Cite each finding as `path:line`. "
        "Start with a compact overview; for a specific question, answer it directly and mention uncertainty. "
        "Keep the answer under 320 words.\n\n"
        f"Repository: {root.name}\nSelected files ({len(rows)} of a bounded local scan):\n{listing}\n\n"
        f"Source excerpts:\n{context}\n\nQuestion: {question}"
    )
    try:
        from config import api_key_path
        import json
        from google import genai
        key_file = api_key_path(Path(__file__).resolve().parent.parent)
        key = json.loads(key_file.read_text(encoding="utf-8-sig")).get("gemini_api_key", "")
        if not key:
            return "No Gemini API key is configured for repository analysis."
        response = genai.Client(api_key=key).models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=prompt,
            config={"max_output_tokens": 900},
        )
        from core.token_usage import record_response
        record_response(response, "gemini-3.1-flash-lite", "repository_review")
        text = (response.text or "").strip()
        return text or f"I scanned {len(rows)} files but the model returned no summary. Key files: {listing}"
    except Exception as exc:
        return f"Repository scan prepared {len(rows)} relevant files, but Gemini analysis failed: {exc}. Key files: {listing}"


TOOL = {
    "name": "repository_review",
    "description": (
        "Inspect a local source repository without uploading the whole tree. Use mode=overview for a compact first pass, "
        "then mode=inspect with a focused question. It reads a bounded, ranked set of text files, excludes common "
        "dependencies/build folders and credential files, and cites findings by path and line."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "repo_path": {"type": "STRING", "description": "Repository folder. Defaults to JARVIS's own source tree."},
            "mode": {"type": "STRING", "enum": ["overview", "inspect"], "description": "overview for the initial compact map; inspect for a focused follow-up."},
            "question": {"type": "STRING", "description": "What to understand, trace, or review. Leave empty for a repository overview."},
        },
        "required": [],
    },
    "handler": inspect_repository,
}
