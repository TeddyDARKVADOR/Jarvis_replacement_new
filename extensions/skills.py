"""
extensions/skills.py — des savoir-faire en fichiers, lus seulement quand il faut.

Un skill est un dossier qui contient SKILL.md : un en-tête YAML (`name`,
`description`) puis des instructions, et d'éventuels fichiers de référence.
C'est le format ouvert d'Agent Skills (Anthropic, OpenAI) : un skill écrit
pour Claude ou Codex fonctionne ici tel quel.

Dossiers lus : `skills/` du dépôt, puis ~/.jarvis/skills (le vôtre gagne en
cas de nom identique). Seuls les noms et les descriptions partent à Gemini, dans
la description de l'outil `skills` ; les instructions entières ne sont lues que
par `use`. Les scripts d'un skill ne sont jamais exécutés : un skill est du
texte, et les actions passent par les outils de JARVIS, avec leurs garde-fous.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
SKILL_DIRS = [REPO / "skills", Path.home() / ".jarvis" / "skills"]
MAX_SKILL_CHARS = 20_000
MAX_INDEX_DESC = 200
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path               # le dossier du skill

    @property
    def instructions(self) -> str:
        return parse(self.path / "SKILL.md")[2]


def parse(path: Path) -> tuple[str, str, str]:
    """(nom, description, corps) d'un SKILL.md ; ValueError s'il est mal formé."""
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER.match(text.replace("\r\n", "\n"))
    if not m:
        raise ValueError(f"{path} : en-tête YAML manquant")
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError(f"{path} : en-tête YAML invalide")
    name = str(meta.get("name") or path.parent.name).strip()
    desc = " ".join(str(meta.get("description", "")).split())
    if not desc:
        raise ValueError(f"{path} : l'en-tête doit avoir une description")
    return name, desc, m.group(2).strip()


_cache: tuple[tuple, dict[str, Skill]] | None = None


def _fingerprint(dirs) -> tuple:
    stamps = []
    for base in dirs:
        if Path(base).is_dir():
            for md in sorted(Path(base).glob("*/SKILL.md")):
                stamps.append((str(md), md.stat().st_mtime_ns))
    return tuple(stamps)


def discover(dirs=None) -> dict[str, Skill]:
    """Les skills valides par nom ; relus seulement quand un SKILL.md change."""
    global _cache
    dirs = SKILL_DIRS if dirs is None else dirs
    fp = _fingerprint(dirs)
    if dirs is SKILL_DIRS and _cache is not None and _cache[0] == fp:
        return _cache[1]
    found: dict[str, Skill] = {}
    for md, _ in fp:                 # l'ordre des dossiers : le dernier gagne
        try:
            name, desc, _body = parse(Path(md))
        except (OSError, ValueError, yaml.YAMLError) as e:
            print(f"[Skills] ignoré : {e}")
            continue
        found[name] = Skill(name, desc, Path(md).parent)
    if dirs is SKILL_DIRS:
        _cache = (fp, found)
    return found


def index(dirs=None) -> str:
    """Une ligne par skill, pour la description de l'outil."""
    skills = discover(dirs)
    return "\n".join(f"- {s.name} : {s.description[:MAX_INDEX_DESC]}"
                     for s in sorted(skills.values(), key=lambda s: s.name))


def use(name: str, dirs=None) -> str:
    skills = discover(dirs)
    skill = skills.get(name)
    if skill is None:
        return f"Aucun skill « {name} ». Disponibles : {', '.join(sorted(skills)) or 'aucun'}."
    files = sorted(str(p.relative_to(skill.path)).replace("\\", "/")
                   for p in skill.path.rglob("*") if p.is_file() and p.name != "SKILL.md")
    listing = (f"\n\nFichiers de référence (action read_file) : {', '.join(files)}") if files else ""
    return (f"# Skill : {skill.name}\n\n{skill.instructions[:MAX_SKILL_CHARS]}{listing}\n\n"
            "(Suis ces instructions avec tes outils habituels ; elles ne lèvent aucune confirmation.)")


def read_file(name: str, rel: str, dirs=None) -> str:
    """Un fichier du skill, sans jamais sortir de son dossier."""
    skill = discover(dirs).get(name)
    if skill is None:
        return f"Aucun skill « {name} »."
    root = skill.path.resolve()
    target = (root / str(rel or "")).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return f"Pas de fichier « {rel} » dans le skill « {name} »."
    return target.read_text(encoding="utf-8", errors="replace")[:MAX_SKILL_CHARS]
