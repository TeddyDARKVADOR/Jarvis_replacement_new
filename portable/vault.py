"""
Coffre du mode portable — chiffre les fichiers sensibles de la clé (clés API et,
en option, la mémoire) avec une phrase de passe, pour que la perte de la clé ne
livre rien.

On ne chiffre PAS tout le disque : la clé est un disque Ventoy bootable, un
BitLocker sur la partition entière casserait le boot des ISOs. On chiffre fichier
par fichier (AES via Fernet, clé dérivée de la phrase par scrypt). Au repos (PC
éteint, clé perdue), seuls les .enc existent.

Quoi protéger : un manifeste <root>/config/vault.json, p.ex.
    {"protect": ["config/api_keys.json", "memory/long_term.json", "memory/incidents.json"]}
Sans manifeste : config/api_keys.json seul (comportement d'origine).

Flux, piloté par Lancer-JARVIS.bat :
    python portable/vault.py lock     --root <app>   chiffre les cibles, efface le clair
    python portable/vault.py unlock   --root <app>   demande la phrase, déchiffre
    python portable/vault.py relock   --root <app>   efface le clair (les .enc restent)
    python portable/vault.py status   --root <app>

Compatibilité : --dir <configdir> protège le seul api_keys.json de ce dossier.
Phrase au clavier (getpass) ou, en script/test, via l'env JARVIS_VAULT_PASS.
"""
from __future__ import annotations

import base64
import getpass
import json
import os
import sys
from pathlib import Path

MAGIC = "jarvis.vault/1"
DEFAULT_PROTECT = ["config/api_keys.json"]


def _kdf(passphrase: str, salt: bytes):
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    key = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(key)


def _fernet(passphrase: str, salt: bytes):
    from cryptography.fernet import Fernet
    return Fernet(_kdf(passphrase, salt))


def _ask(confirm: bool) -> str:
    env = os.environ.get("JARVIS_VAULT_PASS")
    if env:
        return env
    p = getpass.getpass("Phrase de passe du coffre JARVIS : ")
    if confirm:
        if p != getpass.getpass("Confirme la phrase : "):
            raise SystemExit("Les phrases ne correspondent pas — annulé.")
    if not p:
        raise SystemExit("Phrase vide — annulé.")
    return p


def _targets(root: Path) -> list[Path]:
    """Chemins (en clair) à protéger, lus dans config/vault.json ou le défaut."""
    manifest = root / "config" / "vault.json"
    protect = DEFAULT_PROTECT
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            protect = [str(p) for p in data.get("protect", DEFAULT_PROTECT) if str(p).strip()]
        except Exception:
            protect = DEFAULT_PROTECT
    return [root / rel for rel in protect]


def _checkpoint_sqlite(db: Path) -> None:
    """Replie le WAL dans le fichier .db (checkpoint TRUNCATE) pour pouvoir le
    chiffrer sans perdre les écritures en attente. Puis supprime les -wal/-shm
    (SQLite les recrée à la prochaine ouverture)."""
    import sqlite3
    try:
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # pas une base, ou verrouillée : on chiffrera le fichier tel quel
    for sfx in ("-wal", "-shm"):
        sib = db.parent / (db.name + sfx)
        try:
            if sib.exists():
                sib.unlink()
        except Exception:
            pass


def _encrypt(plain: Path, passphrase: str) -> None:
    if plain.suffix == ".db":
        _checkpoint_sqlite(plain)       # SQLite : WAL replié avant chiffrement
    salt = os.urandom(16)
    token = _fernet(passphrase, salt).encrypt(plain.read_bytes())
    (plain.parent / (plain.name + ".enc")).write_text(
        json.dumps({"magic": MAGIC, "salt": base64.b64encode(salt).decode(),
                    "token": base64.b64encode(token).decode()}), encoding="utf-8")
    plain.unlink()


def _decrypt(enc: Path, passphrase: str) -> None:
    from cryptography.fernet import InvalidToken
    blob = json.loads(enc.read_text(encoding="utf-8"))
    salt = base64.b64decode(blob["salt"])
    token = base64.b64decode(blob["token"])
    try:
        data = _fernet(passphrase, salt).decrypt(token)
    except InvalidToken:
        raise SystemExit("Phrase incorrecte — le coffre reste fermé.")
    enc.with_name(enc.name[:-4]).write_bytes(data)  # retire « .enc »


def lock(root: Path) -> None:
    plains = [t for t in _targets(root) if t.exists()]
    if not plains:
        raise SystemExit("Rien à chiffrer (aucune cible présente en clair).")
    phrase = _ask(confirm=True)
    for plain in plains:
        _encrypt(plain, phrase)
    print(f"Coffre fermé : {len(plains)} fichier(s) chiffré(s) — " +
          ", ".join(p.name for p in plains))


def unlock(root: Path) -> None:
    encs = [Path(str(t) + ".enc") for t in _targets(root)]
    encs = [e for e in encs if e.exists()]
    if not encs:
        return  # pas de coffre : rien à faire (clé en clair, comportement normal)
    phrase = _ask(confirm=False)
    for enc in encs:
        _decrypt(enc, phrase)
    print(f"Coffre ouvert : {len(encs)} fichier(s) déchiffré(s) pour cette session.")


def relock(root: Path) -> None:
    """Après la session : efface le clair quand un .enc existe (le .enc demeure)."""
    n = 0
    for t in _targets(root):
        enc = Path(str(t) + ".enc")
        if enc.exists() and t.exists():
            t.unlink()
            n += 1
    if n:
        print(f"Session finie : {n} clair(s) effacé(s) (les coffres .enc demeurent).")


def status(root: Path) -> None:
    for t in _targets(root):
        enc = Path(str(t) + ".enc")
        rel = t.name
        print(f"  {rel:<24} coffre:{'oui' if enc.exists() else 'non':<4} "
              f"clair:{'présent' if t.exists() else 'absent'}")
    print(f"racine : {root}")


def _resolve_root(argv: list[str]) -> Path:
    if "--root" in argv:
        return Path(argv[argv.index("--root") + 1])
    if "--dir" in argv:                       # compat : --dir <config> -> racine = parent
        return Path(argv[argv.index("--dir") + 1]).parent
    env = os.environ.get("JARVIS_APP_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    action = argv[0] if argv and not argv[0].startswith("-") else "status"
    root = _resolve_root(argv)
    try:
        {"lock": lock, "unlock": unlock, "relock": relock, "status": status}[action](root)
    except KeyError:
        print("Usage : python portable/vault.py lock|unlock|relock|status [--root <app>|--dir <config>]")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
