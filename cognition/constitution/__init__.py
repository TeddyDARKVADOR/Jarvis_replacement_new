"""
cognition/constitution — charge jarvis.toml, en garde la trace versionnée.

    load()                  -> Constitution (données + sha256)
    register(store, c)      -> enregistre la version dans cognition.db ;
                               lève ConstitutionDrift si le fichier a changé
                               sans changement de numéro de version.
"""
from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PATH = HERE / "jarvis.toml"


class ConstitutionDrift(RuntimeError):
    """Le texte a changé, le numéro de version non : modification non tracée."""


@dataclass(frozen=True)
class Constitution:
    data: dict
    sha256: str
    path: Path

    @property
    def version(self) -> str:
        return str(self.data.get("version", "0"))

    def priorities(self) -> list[str]:
        return [p["nom"] for p in self.data.get("priorites", [])]

    def prompt_core(self) -> str:
        core = str(self.data.get("prompt", {}).get("core", "")).strip()
        return core.replace("{version}", self.version)


def load(path: Path | str = DEFAULT_PATH) -> Constitution:
    raw = Path(path).read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    for field in ("version", "identite", "priorites", "prompt"):
        if field not in data:
            raise ValueError(f"constitution incomplète : `{field}` manquant")
    return Constitution(data=data, sha256=hashlib.sha256(raw).hexdigest(), path=Path(path))


def register(store, c: Constitution, rationale: str = "") -> bool:
    """True si cette version est nouvelle. Ne réécrit jamais une version connue."""
    rows = store.query("SELECT sha256 FROM constitution_versions WHERE version=?", (c.version,))
    if rows:
        if rows[0]["sha256"] != c.sha256:
            raise ConstitutionDrift(
                f"jarvis.toml a changé sans nouveau numéro de version ({c.version}). "
                f"Incrémentez `version` et décrivez la raison dans CHANGELOG.md.")
        return False
    from ..store import now_iso
    store.execute("INSERT INTO constitution_versions(version, ts, sha256, rationale) "
                  "VALUES (?,?,?,?)", (c.version, now_iso(), c.sha256, rationale))
    return True
