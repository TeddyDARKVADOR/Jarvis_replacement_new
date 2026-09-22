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

# Au niveau module, et pas dans main() comme le reste des imports lourds.
#
# `from __future__ import annotations` transforme toute annotation en chaine.
# FastAPI resout ensuite « Request » avec get_type_hints(), qui ne regarde que
# les globales du MODULE — jamais les locales de la fonction qui englobe le
# handler. Importe dans main(), le nom est invisible a la resolution, FastAPI
# ne reconnait plus le parametre comme la requete et le traite en parametre de
# requete obligatoire. Le symptome est un 422 « Field required / loc: query,
# req » sur un POST qui a pourtant un corps parfaitement valide.
#
# server/api.py fait la meme chose sans probleme parce qu'il n'a pas le future
# import : ses annotations sont de vraies classes.
from fastapi import Request                      # noqa: E402
from fastapi.responses import JSONResponse       # noqa: E402


def main() -> int:
    import uvicorn

    from dashboard.server import DashboardServer
    from server import api, auth, device_api
    from server.audio_bridge import AudioHub
    from server.device_api import DeviceHub
    from server.runtime_state import RuntimeState

    dash = DashboardServer()
    token = auth.seed_dashboard(dash)
    state = RuntimeState(label="MARK LIII (laboratoire, sans Gemini)")
    hub = AudioHub()
    api.attach(dash, state=state, hub=hub, sample_rate=24000, log=lambda *_: None)

    # Les routes d'appareil, assemblees comme run_headless le fait. Sans elles
    # le laboratoire ne voit aucun client : /api/device-register repond 404, le
    # canal ne s'ouvre jamais, et une suite qui teste le routage ne teste rien.
    device_hub = DeviceHub()
    # Le journal des appareils va dans reports/server.log, pas au silence : quand
    # un canal est refuse, cette ligne est la seule qui dit lequel des deux
    # verrous a claque, et le telephone ne voit qu'un "Connection reset".
    device_api.attach(dash, device_hub, log=lambda m: print(m, flush=True))

    # /status expose le registre, comme en mode full. Aucun token n'y passe :
    # DeviceInfo.public() ne porte que l'identite et les capacites.
    state.bind_extra_probe(lambda: {"devices": device_hub.registry.public()})

    async def lab_device_command(req: Request) -> JSONResponse:
        """POST /lab/device-command — la commande que Gemini aurait routee.

        CE QUE CETTE ROUTE N'EST PAS
            Une route de production. Elle n'existe que dans ce fichier, qui ne
            tourne que sans cle Gemini, et rien dans server/ ne l'importe. En
            mode `full` elle n'existe pas du tout.

        POURQUOI ELLE EXISTE
            Le chemin complet est : Gemini appelle un outil -> ActionRouter
            choisit l'appareil -> DeviceChannel.request(). Les deux premiers
            maillons demandent le modele. Le troisieme est celui que le
            telephone doit honorer, et c'est celui-la que le laboratoire doit
            pouvoir exercer sans acheter un tour de conversation.

            targeting.py reste teste par server/routing_selftest.py, ou il est
            deterministe et gratuit. Ici on verifie ce qu'aucun test Python ne
            peut voir : qu'un vrai telephone, au bout d'un vrai WebSocket,
            ouvre vraiment l'application.

        L'authentification est celle de tout le reste : le bearer du dashboard,
        obtenu par `tools/server.sh bearer`.
        """
        header = req.headers.get("authorization", "")
        bearer = header.removeprefix("Bearer ").strip()
        if not bearer or bearer not in dash._tokens:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        try:
            body = await req.json()
        except Exception:
            body = {}

        device_id = str(body.get("device_id") or "").strip()
        action = str(body.get("action") or "").strip()
        parameters = body.get("parameters") or {}
        if not device_id or not action:
            return JSONResponse(
                {"error": "device_id et action sont requis"}, status_code=400
            )

        channel = device_hub.channel(device_id)
        if channel is None:
            known = [d["device_id"] for d in device_hub.registry.public()]
            return JSONResponse(
                {"error": f"aucun canal ouvert pour {device_id}", "connus": known},
                status_code=404,
            )

        result = await channel.request(action, dict(parameters))
        return JSONResponse({"device_id": device_id, "action": action,
                             "result": result})

    dash.app.add_api_route(
        "/lab/device-command", lab_device_command, methods=["POST"]
    )

    print("  MARK LIII — mode laboratoire (transport seul, pas de Gemini)")
    print("  Routes appareil actives : /api/device-register, /ws/device")
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
