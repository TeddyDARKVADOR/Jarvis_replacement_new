"""
JARVIS portable — l'assistant vit sur une clé USB et n'écrit rien sur le PC hôte.

Cet outil ne déploie rien lui-même : le déploiement se fait par le lanceur
`portable/Lancer-JARVIS.bat` posé sur la clé. L'outil répond à la question que
l'utilisateur pose à JARVIS une fois qu'il tourne — « es-tu en mode portable ? »,
« où sont mes données ? », « comment retirer la clé sans rien casser ? ».

Le mode portable est activé par le lanceur, qui exporte avant de démarrer Python :
  JARVIS_PORTABLE=1          marqueur
  JARVIS_DATA_DIR=<clé>/data racine de données
  USERPROFILE / LOCALAPPDATA / APPDATA / TEMP / TMP / HF_HOME / OLLAMA_MODELS
                             redirigés vers des dossiers de la clé
Ainsi tout ce que le code écrit « chez l'utilisateur » atterrit sur la clé, sans
qu'aucun fichier de core/ ne change (contrat V2).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

PLUGIN = {
    "name": "portable",
    "description": (
        "État du mode portable (JARVIS sur clé USB). À appeler quand l'utilisateur "
        "demande s'il tourne depuis une clé, où sont stockées ses données en mode "
        "portable, combien d'espace reste sur la clé, ou comment retirer la clé "
        "proprement. N'installe ni ne supprime rien : lecture seule. Pour déployer "
        "JARVIS sur une clé, c'est le lanceur Lancer-JARVIS.bat de la clé, pas cet outil."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "status (défaut) : où vivent les données, espace libre. "
                               "eject : comment retirer la clé sans rien casser. "
                               "doctor : bilan du dispositif portable (Python, dépendances, "
                               "Ollama, coffre, modèle conseillé, vitesse de la clé, connexion). "
                               "pair : comment appairer le téléphone (adresse locale, QR du HUD).",
            },
        },
        "required": [],
    },
}


def _is_portable() -> bool:
    return os.environ.get("JARVIS_PORTABLE", "") not in ("", "0", "false", "False")


def _data_dir() -> Path | None:
    d = os.environ.get("JARVIS_DATA_DIR", "").strip()
    return Path(d) if d else None


def _free_gb(path: Path) -> float | None:
    try:
        return round(shutil.disk_usage(path).free / (1024 ** 3), 1)
    except Exception:
        return None


def _key_root() -> Path | None:
    """Racine de la clé : le dossier qui contient data/, app/, runtime/.
    Le lanceur pose JARVIS_DATA_DIR = <racine>\\data, donc la racine est son parent."""
    d = _data_dir()
    return d.parent if d is not None else None


def _status() -> str:
    if not _is_portable():
        return ("JARVIS ne tourne pas en mode portable, Sir : il est installé sur ce PC. "
                "Pour le rendre portable, lancez-le par Lancer-JARVIS.bat depuis la clé.")
    data = _data_dir()
    lines = ["Mode portable actif — je vis sur la clé, rien n'est écrit sur ce PC."]
    if data is not None:
        lines.append(f"Données : {data}")
        free = _free_gb(data)
        if free is not None:
            lines.append(f"Espace libre sur la clé : {free} Go.")
    # Montrer que les écritures « chez l'utilisateur » sont bien redirigées sur la clé.
    redirs = {k: os.environ.get(k, "") for k in ("USERPROFILE", "LOCALAPPDATA", "TEMP", "OLLAMA_MODELS")}
    on_key = [k for k, v in redirs.items() if v and (data is None or str(data) in v or str(_key_root() or "") in v)]
    if on_key:
        lines.append("Redirigé vers la clé : " + ", ".join(on_key) + ".")
    return " ".join(lines) if len(lines) == 1 else "\n".join(lines)


def _eject() -> str:
    if not _is_portable():
        return "Pas en mode portable : rien de spécial à faire, la clé ne me fait pas tourner."
    return ("Pour retirer la clé sans rien casser, Sir : fermez d'abord JARVIS "
            "(dites « arrête-toi » ou fermez le HUD), attendez 2 secondes que les "
            "fichiers soient écrits, puis éjectez la clé normalement (icône « Retirer "
            "le périphérique » de Windows). Un gardien surveille la clé : si elle "
            "disparaît pendant que je tourne, je m'arrête aussitôt pour ne rien corrompre, "
            "mais une fermeture propre reste préférable.")


def _doctor() -> str:
    if not _is_portable():
        return ("Pas en mode portable : le bilan du dispositif ne s'applique qu'à JARVIS lancé "
                "depuis la clé (Lancer-JARVIS.bat).")
    root = _key_root()
    lines = ["Bilan du dispositif portable :"]
    def ok(b):  # petite coche parlée
        return "✓" if b else "✗"
    try:
        py = (root / "runtime" / "python" / "python.exe") if root else None
        deps = (root / "runtime" / "python" / ".deps-ok") if root else None
        oll = (root / "runtime" / "ollama" / "ollama.exe") if root else None
        models = (root / "data" / "ollama-models") if root else None
        enc = (root / "app" / "config" / "api_keys.enc") if root else None
        has_models = bool(models and models.exists() and any(models.iterdir()))
        lines.append(f"  {ok(py and py.exists())} Python portable")
        lines.append(f"  {ok(deps and deps.exists())} dépendances installées")
        lines.append(f"  {ok(oll and oll.exists())} Ollama (cerveau hors-ligne){' — modèle présent' if has_models else ' — pas de modèle'}")
        lines.append(f"  {ok(enc and enc.exists())} coffre chiffré des clés" + ("" if (enc and enc.exists()) else " (clés en clair)"))
        data = _data_dir()
        free = _free_gb(data) if data else None
        if free is not None:
            lines.append(f"  espace libre : {free} Go")
    except Exception as e:
        lines.append(f"  (checks partiels : {e})")
    # profil de ce PC : matériel, vitesse de la clé, connexion
    try:
        from portable import host_profile
        p = host_profile.profile()
        gpu = f"GPU NVIDIA {p['vram_gb']} Go" if p["gpu_nvidia"] else "sans GPU dédié"
        lines.append(f"  ce PC : {gpu}, {p['ram_gb']} Go RAM → modèle conseillé {p['recommended_model']}")
        net = "en ligne (Gemini dispo)" if p.get("online") else "hors-ligne (JARVIS passe sur Ollama local)"
        lines.append(f"  connexion : {net}")
        if p.get("key_write_mbps"):
            warn = " — clé lente (USB 2.0), c'est normal que ce soit plus posé" if p.get("key_slow") else ""
            lines.append(f"  vitesse d'écriture de la clé : {p['key_write_mbps']} Mo/s{warn}")
    except Exception:
        pass
    return "\n".join(lines)


def _lan_ips() -> list[str]:
    import socket
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(ips)


def _pair() -> str:
    ips = _lan_ips()
    where = (", ".join(ips)) if ips else "l'adresse locale de ce PC"
    return ("Pour appairer ton téléphone, Sir : le HUD affiche un QR d'appairage "
            "(tableau de bord local). Sur le même réseau Wi-Fi, ouvre depuis le "
            f"téléphone http://{ips[0] if ips else '<ip>'}:8000 ou scanne le QR du HUD. "
            f"Adresses de ce PC sur le réseau : {where}. Le téléphone te joint tant que "
            "la clé tourne ici (pour un accès de partout, il faut Oracle + Tailscale).")


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str((parameters or {}).get("action", "status")).strip().lower()
    try:
        if action in ("eject", "ejection", "éjection", "retirer"):
            result = _eject()
        elif action in ("doctor", "bilan", "diagnostic", "sante", "santé"):
            result = _doctor()
        elif action in ("pair", "appairer", "appairage", "telephone", "téléphone", "qr"):
            result = _pair()
        else:
            result = _status()
    except Exception as e:  # ne jamais lever : renvoyer une phrase d'erreur parlée
        result = f"Sir, l'outil portable a échoué : {e}"
    if player:
        try:
            player.write_log(f"JARVIS: {result.splitlines()[0]}")
        except Exception:
            pass
    return result
