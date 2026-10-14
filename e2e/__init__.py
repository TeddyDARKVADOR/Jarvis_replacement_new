"""
e2e/ — JARVIS de bout en bout, sans clé, sans réseau, sans toucher à rien.

    python -m e2e                  tous les scénarios (serveur, puis HUD si Playwright est là)
    python -m e2e --only serveur   une famille : serveur · hud
    python -m e2e --keep           garde le bac à sable et les journaux (chemin affiché)

CE QUE « BOUT EN BOUT » VEUT DIRE ICI
    Les selftests prouvent chaque paquet seul. Ici, c'est le vrai JARVIS qui
    tourne, comme sur Oracle : `python -m server.run_headless`, dans son propre
    processus, avec ses vrais outils, sa vraie mémoire, son vrai tableau de bord
    sur de vraies sockets. Le test lui parle comme le téléphone et le HUD lui
    parlent (/health, /status, /ws, /ws/phone-out, /api/device-register,
    /ws/device), et il joue Gemini.

LE FAUX GEMINI (e2e/gemini.py)
    Il parle le vrai protocole de l'API Live : websocket TLS, `setup` puis
    `setupComplete`, puis clientContent / toolResponse contre serverContent /
    toolCall / goAway / sessionResumptionUpdate. Le vrai SDK google-genai y
    arrive sans qu'une ligne de main.py change : GOOGLE_GEMINI_BASE_URL (lue par
    le SDK) désigne le faux, SSL_CERT_FILE l'autorité de test que le SDK
    reconnaît pour httpx, aiohttp et le websocket. Aucun appel ne peut partir
    chez Google : la clé est fausse, et un proxy injoignable coupe tout réseau
    sortant du processus de JARVIS (127.0.0.1 excepté).

LE BAC À SABLE (e2e/sandbox.py)
    JARVIS tourne dans une copie temporaire des fichiers suivis par Git : jamais
    la vraie clé de config/, jamais la vraie mémoire de memory/, un HOME à part.
    Un e2e lancé sur le PC de tous les jours ne lit ni n'écrit rien de réel.

CE QUI N'EST PAS TESTÉ, ET POURQUOI
    Le vrai modèle (sa voix, ses choix d'outils) : il faudrait une vraie clé, et
    la CI exécute le code de la PR — une clé y serait exposée. Le micro et le
    haut-parleur réels, les actions Windows réelles (souris, volume), le
    téléphone. Une CI verte dit : tout ce qui se teste sans clé ni matériel marche.

CONTRAT V2
    Paquet racine, retirable : rien de JARVIS ne l'importe, et lui n'importe le
    code de JARVIS que dans le processus du bac à sable (e2e/launch.py). Le
    retirer ne change rien au JARVIS qui tourne. Sortie au format des selftests
    (« [PASS] n libellé ») : nightshift.run et la CI la lisent comme les autres.
"""
