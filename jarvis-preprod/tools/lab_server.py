"""
tools/lab_server.py — le serveur du laboratoire, sans clé Gemini.

CE QUE C'EST, ET CE QUE CE N'EST PAS
    Ce n'est PAS un fork de MARK LIII. Aucune ligne de dashboard/server.py,
    server/api.py, server/notify.py ou server/auth.py n'est recopiée ici : ce
    fichier les importe et les assemble, exactement comme run_headless le fait.
    Un bug corrigé dans le serveur réel l'est ici le jour même.

    Ce qu'il ne construit pas, c'est le JarvisLive : pas de session Gemini, pas
    de micro, pas de boucle de reconnexion.

POURQUOI IL EXISTE
    `python -m server.run_headless` s'arrête sur `ui.wait_for_api_key()`. C'est
    juste : un assistant sans modèle n'est pas un assistant. Mais les chemins
    que ce laboratoire teste — notifications, état du lien, appairage,
    confirmation, contexte — ne touchent jamais la session Live. Exiger une clé
    Gemini payante pour vérifier qu'une notification s'affiche reviendrait à
    facturer chaque exécution de la suite de tests.

    Donc : si config/api_keys.json existe, tools/server.sh lance le vrai
    run_headless et ce fichier ne sert pas. Sinon il prend le relais, et le
    rapport dit lequel des deux tournait.

CE QUI N'EST PAS TESTABLE AVEC CELUI-CI
    Tout ce qui demande une réponse du modèle : transcription, appel d'outil,
    interruption d'une phrase réellement prononcée, audio descendant. Les tests
    concernés doivent vérifier `mode` dans /status et se déclarer SKIP plutôt
    que d'échouer.
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

PORT = int(os.environ.get("LAB_SERVER_PORT", "8000"))


def main() -> int:
    import uvicorn

    from dashboard.server import DashboardServer
    from server import api, auth
    from server.audio_bridge import AudioHub
    from server.runtime_state import RuntimeState

    dash = DashboardServer()
    token = auth.seed_dashboard(dash)
    state = RuntimeState(label="MARK LIII (laboratoire, sans Gemini)")
    hub = AudioHub()
    api.attach(dash, state=state, hub=hub, sample_rate=24000, log=lambda *_: None)

    print("  MARK LIII — mode laboratoire (transport seul, pas de Gemini)")
    print(f"  Depuis l'emulateur : 10.0.2.2:{PORT}")
    print(f"  Depuis ce PC       : 127.0.0.1:{PORT}")
    # Quatre caracteres : assez pour verifier qu'on parle du bon credential,
    # inutilisable pour s'authentifier.
    print(f"  Token appaire      : ...{token[-4:]}")
    sys.stdout.flush()

    # uvicorn.run sur l'app, jamais dash.serve() : serve() appelle
    # _ensure_network_access, qui reconfigure le pare-feu de la machine. Un
    # laboratoire n'a rien a reconfigurer sur son hote.
    uvicorn.run(dash.app, host="0.0.0.0", port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
