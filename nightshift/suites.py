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

    13 suites sur 14 passent ; code_workspace passe à son échec connu près.
    Confirmé le même jour par le premier run GitHub (image ubuntu24
    20261004.327.1, Python 3.12.15) : mêmes scores, 54 s.

    client_desktop demande PyQt6-WebEngine et PortAudio. Sur le runner, il
    manquait libEGL.so.1 ; ldd sur PyQt6-WebEngine 6.11 liste les 16 autres
    bibliothèques dont l'import a besoin en mode offscreen, et le workflow les
    installe toutes. Quand l'une manque, la suite est « ignorée » (dépendance
    absente), pas « en erreur ».

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
          linux="ignorée le 2026-10-14 sur GitHub : libEGL.so.1 absente du runner. Le workflow "
                "installe désormais les bibliothèques de Qt mesurées avec ldd : à confirmer"),
    Suite("cognition.selftest", ("-m", "cognition.selftest"), linux=f"{_MEASURED} : 64/64"),
    Suite("eval_lab.selftest", ("-m", "eval_lab.selftest"), linux=f"{_MEASURED} : 28/28"),
    Suite("core.code_workspace.selftest", ("-m", "core.code_workspace.selftest"), timeout_s=600,
          known_failures=(
              ("linux", "2", "le nettoyage du test appelle os.rmdir() sur un lien symbolique "
                             "(NotADirectoryError sous Linux) : l'échec masque le vrai verdict "
                             "de la sandbox — correctif d'une ligne dans core/, humain seulement"),
              ("linux", "33", "Pyright, seulement s'il est installé : sans lui le contrôle "
                              "passe (« non lancé »), et le workflow ne l'installe pas"),
          ),
          linux="33/34 le 2026-10-14 (Ubuntu 24.04, Python 3.12.3) : seul le n°2 échoue (connu)"),
    Suite("reminders.selftest", ("-m", "reminders.selftest"), linux=f"{_MEASURED} : 4/4"),
    Suite("resilience.selftest", ("-m", "resilience.selftest"), linux=f"{_MEASURED} : 13/13"),
    Suite("extensions.selftest", ("-m", "extensions.selftest"), linux=f"{_MEASURED} : 11/11"),
    Suite("offline.selftest", ("-m", "offline.selftest"), linux=f"{_MEASURED} : 10/10"),
    Suite("core.token_usage_selftest", ("-m", "core.token_usage_selftest"), linux=f"{_MEASURED} : PASS"),
    Suite("actions.repository_review_selftest", ("-m", "actions.repository_review_selftest"),
          linux=f"{_MEASURED} : PASS"),
    Suite("nightshift.selftest", ("-m", "nightshift.selftest"), linux="ok le 2026-10-14 (Ubuntu 24.04)"),
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
