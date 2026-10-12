# config/__init__.py
import json, os, platform
from pathlib import Path

_CONFIG_DIR = Path(__file__).parent
_CONFIG_PATH = _CONFIG_DIR / "api_keys.json"  # legacy spelling, kept for callers

def api_key_path(base_dir: Path | None = None) -> Path:
    """Return the active credentials file, preferring the singular current name.

    Older installs use ``api_keys.json``. Keep reading those until the new
    ``api_key.json`` exists, so upgrading does not disconnect existing users.
    """
    directory = (Path(base_dir) / "config") if base_dir is not None else _CONFIG_DIR
    current = directory / "api_key.json"
    legacy = directory / "api_keys.json"
    return current if current.exists() or not legacy.exists() else legacy

def _platform_os() -> str:
    """Auto-detect OS when config file is absent."""
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        platform.system(), "linux"
    )

def get_config() -> dict:
    try:
        with open(api_key_path(), "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}

def get_os() -> str:
    """Returns: 'windows' | 'mac' | 'linux'"""
    return get_config().get("os_system", _platform_os()).lower()

def is_windows() -> bool: return get_os() == "windows"
def is_mac()     -> bool: return get_os() == "mac"
def is_linux()   -> bool: return get_os() == "linux"
