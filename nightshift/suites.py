"""
nightshift/suites.py — les suites que le veilleur lance, et ce qu'on sait d'elles.

Une suite est une commande `python …` du dépôt, sans clé ni réseau. Pour
chacune : les plateformes où elle a un sens, son délai, ce qu'il lui faut pour
démarrer, et les contrôles connus pour échouer sur une plateforme. Un échec
connu n'est jamais compté comme une régression, mais reste affiché.

ÉTAT SOUS LINUX — le premier livrable de la phase 1
    Mesuré le 2026-10-14 dans un conteneur Ubuntu 24.04.5 / Python 3.12.3 (la
    même image que le runner `ubuntu-24.04` du workflow), avec les dépendances
    du workflow : deployment/requirements-server.txt + pyyaml + ruff.

    13 suites sur 14 passent ; code_workspace passe à son échec connu près
    (corrigé depuis : 34/34, plus aucun échec connu ; l'e2e est la 16e suite).
    Confirmé le même jour par le premier run GitHub (image ubuntu24
    20261004.327.1, Python 3.12.15) : mêmes scores, 54 s.

    client_desktop demande PyQt6-WebEngine et PortAudio. Sur le runner, il
    manquait libEGL.so.1 ; ldd sur PyQt6-WebEngine 6.11 liste les 16 autres
    bibliothèques dont l'import a besoin en mode offscreen, et le workflow les
    installe toutes. Quand l'une manque, la suite est « ignorée » (dépendance
    absente), pas « en erreur ». Avec elles, elle tourne : 27/28 au run
    38535001267 (le n°25 cassé par un commentaire de local_hud.py), puis
    28/28 au run 38560540611 une fois le commentaire corrigé.

    Le champ `linux` de chaque suite garde ce qui a été mesuré ; le workflow le
    revérifie chaque nuit.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

#: Toutes les plateformes où le code de JARVIS tourne.
ALL = ("linux", "win32", "darwin")


@dataclass(frozen=True)
class Suite:
    name: str                          # nom affiché, et clé de comparaison d'une nuit à l'autre
    argv: tuple[str, ...]              # ce qui suit « python »
    timeout_s: int = 300
    platforms: tuple[str, ...] = ALL
    #: (plateforme, contrôle, raison) — le contrôle est le numéro affiché par la suite
    known_failures: tuple[tuple[str, str, str], ...] = ()
    #: modules importés avant de lancer la suite ; un import qui échoue → « ignorée »
    requires: tuple[str, ...] = ()
    #: variable d'environnement qui désigne un autre interpréteur (venv à part)
    python_env: str = ""
    #: ce qui a été mesuré sous Linux, et quand
    linux: str = ""

    def known(self, platform: str) -> dict[str, str]:
        """Les contrôles connus pour échouer sur `platform` : {numéro: raison}."""
        return {check: why for plat, check, why in self.known_failures if plat == platform}


def platform_name() -> str:
    """linux, win32 ou darwin — les valeurs de sys.platform qui comptent ici."""
    p = sys.platform
    return "linux" if p.startswith("linux") else p


def short(name: str) -> str:
    """Le nom qu'on dit à voix haute : « cognition.selftest » → « cognition »,
    « core.code_workspace.selftest » → « code_workspace »."""
    n = str(name).replace(" --selftest", "")
    for suffix in (".selftest", "_selftest"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    for prefix in ("core.", "actions."):
        if n.startswith(prefix):
            n = n[len(prefix):]
    return n


_MEASURED = "ok le 2026-10-14 (Ubuntu 24.04 : conteneur, Python 3.12.3, puis runner GitHub, Python 3.12.15)"

SUITES: tuple[Suite, ...] = (
    Suite("capabilities.selftest", ("-m", "capabilities.selftest"), linux=f"{_MEASURED} : 11/11"),
    Suite("context.selftest", ("-m", "context.selftest"), linux=f"{_MEASURED} : 33/33"),
    Suite("server.selftest", ("-m", "server.selftest"), linux=f"{_MEASURED} : 35/35"),
    Suite("server.routing_selftest", ("-m", "server.routing_selftest"), linux=f"{_MEASURED} : 35/35"),
    Suite("client_desktop --selftest", ("-m", "client_desktop", "--selftest"),
          requires=("PyQt6.QtWebEngineWidgets", "sounddevice"),
          python_env="NIGHTSHIFT_PYTHON_HUD",
          linux="28/28 le 2026-10-14 sur GitHub (run 38560540611), avec les bibliothèques de Qt "
                "mesurées par ldd ; le n°25 avait été cassé par un commentaire de local_hud.py, corrigé"),
    Suite("cognition.selftest", ("-m", "cognition.selftest"), linux=f"{_MEASURED} : 64/64"),
    Suite("eval_lab.selftest", ("-m", "eval_lab.selftest"), linux=f"{_MEASURED} : 28/28"),
    Suite("core.code_workspace.selftest", ("-m", "core.code_workspace.selftest"), timeout_s=600,
          linux="34/34 le 2026-10-14 (Ubuntu 24.04, Python 3.12.3), Pyright installé compris : le "
                "nettoyage du n°2 (os.rmdir sur un lien symbolique) est corrigé, il n'y a plus "
                "d'échec connu"),
    Suite("reminders.selftest", ("-m", "reminders.selftest"), linux=f"{_MEASURED} : 4/4"),
    Suite("resilience.selftest", ("-m", "resilience.selftest"), linux=f"{_MEASURED} : 13/13"),
    Suite("extensions.selftest", ("-m", "extensions.selftest"), linux=f"{_MEASURED} : 11/11"),
    Suite("offline.selftest", ("-m", "offline.selftest"), linux=f"{_MEASURED} : 10/10"),
    Suite("core.token_usage_selftest", ("-m", "core.token_usage_selftest"), linux=f"{_MEASURED} : PASS"),
    Suite("actions.repository_review_selftest", ("-m", "actions.repository_review_selftest"),
          linux=f"{_MEASURED} : PASS"),
    Suite("nightshift.selftest", ("-m", "nightshift.selftest"), linux="ok le 2026-10-14 (Ubuntu 24.04)"),
    # JARVIS de bout en bout (python -m e2e) : le vrai serveur contre un faux Gemini,
    # la vraie page du HUD dans Chromium. Hors de la zone de la nuit : un e2e rouge
    # n'est jamais confié au modèle, il se diagnostique à la main.
    Suite("e2e", ("-m", "e2e"), timeout_s=900, requires=("uvicorn", "starlette", "websockets", "google.genai"),
          linux="serveur 18/18 le 2026-10-14 (conteneur Ubuntu 24.04, Python 3.12.3) ; HUD 7/7 le même jour "
                "dans Chromium (hôte Fedora, Python 3.14) — sans Playwright, la famille HUD est « ignorée »"),
)


def by_name(names: list[str] | None = None) -> list[Suite]:
    """Les suites demandées (toutes si `names` est vide), dans l'ordre de SUITES.
    Un nom inconnu lève ValueError : une faute de frappe ne doit pas passer pour
    « 0 suite, tout va bien »."""
    if not names:
        return list(SUITES)
    known = {s.name: s for s in SUITES}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValueError(f"suites inconnues : {unknown}")
    return [s for s in SUITES if s.name in names]
