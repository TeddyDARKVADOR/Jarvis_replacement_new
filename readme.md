<p align="center">
  <img src="docs/banner.svg" alt="J.A.R.V.I.S — MARK LIII" width="100%">
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%20%7C%203.12-0B1A2A?style=for-the-badge&logo=python&logoColor=7DD3FC">
  <img alt="Gemini Live" src="https://img.shields.io/badge/Gemini-3.1%20Flash%20Live-0B1A2A?style=for-the-badge&logo=googlegemini&logoColor=7DD3FC">
  <img alt="Plateformes" src="https://img.shields.io/badge/PC%20%C2%B7%20Android%20%C2%B7%20Oracle-0B1A2A?style=for-the-badge&logo=android&logoColor=6EE7B7">
  <a href="https://github.com/TeddyDARKVADOR/Jarvis_replacement_new/actions/workflows/nightly.yml"><img alt="Night Shift" src="https://img.shields.io/github/actions/workflow/status/TeddyDARKVADOR/Jarvis_replacement_new/nightly.yml?branch=main&style=for-the-badge&label=Night%20Shift&logo=githubactions&logoColor=white&color=0B1A2A"></a>
</p>

<p align="center"><b>Un assistant vocal temps réel qui entend, voit, se souvient et agit</b><br>
le cerveau tourne 24 h/24 sur un serveur, le PC et le téléphone sont ses terminaux.</p>

---

## 🛰️ Comment c'est fait

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontFamily": "Segoe UI, Helvetica, Arial", "fontSize": "16px", "primaryColor": "#0B1A2A", "primaryTextColor": "#E0F4FF", "primaryBorderColor": "#7DD3FC", "lineColor": "#7DD3FC", "edgeLabelBackground": "#0B1A2A"}}}%%
flowchart TB
    you(("🎙️ Vous"))

    hud["🖥️ <b>HUD holographique</b><br/>noyau 3D · ESPRIT · STUDIO"]
    android["📱 <b>Android</b><br/>micro · notifications"]
    web["🌐 <b>Tableau de bord</b><br/>appairage QR · fichiers"]

    live{{"⚡ <b>JARVIS · MARK LIII</b><br/>Gemini 3.1 Flash Live<br/>Oracle 24/7 ou ce PC"}}
    night["🌙 <b>Night Shift</b><br/>3 passes par nuit"]

    tools["🧰 <b>35 outils</b><br/>plugins · MCP · skills"]
    ctx["🎚️ <b>Contexte</b><br/>quand et comment parler"]
    mind["🧠 <b>Cognition</b><br/>mémoire avec provenance"]
    offline["🔌 <b>Hors-ligne</b><br/>Whisper + Ollama"]

    you <==> hud & android & web
    hud & android & web <==> live
    night -.-> live
    live ==> tools & ctx & mind
    live -.-> offline

    classDef core fill:#0E2A3F,stroke:#7DD3FC,stroke-width:3px,color:#FFFFFF
    classDef ui fill:#0A101A,stroke:#6EE7B7,stroke-width:1.5px,color:#E0F4FF
    classDef brain fill:#0A101A,stroke:#7DD3FC,stroke-width:1.5px,color:#E0F4FF
    classDef side fill:#0A101A,stroke:#C4B5FD,stroke-width:1.5px,color:#E0F4FF,stroke-dasharray:5 4
    classDef user fill:#7DD3FC,stroke:#E0F4FF,stroke-width:2px,color:#05070C
    class live core
    class hud,android,web ui
    class tools,ctx,mind brain
    class offline,night side
    class you user
```

## ✨ Ce qu'il sait faire

```mermaid
%%{init: {"theme": "dark", "themeVariables": {"fontFamily": "Segoe UI, Helvetica, Arial", "fontSize": "14px"}}}%%
mindmap
  root((JARVIS))
    🎙️ Voix
      Gemini Live, 5 voix
      Mot d'éveil Hey Jarvis
      Reprise sans perte
      Briefing du matin
      Relais hors-ligne
    🖥️ Le PC
      Applis et réglages
      Souris et clavier
      Fichiers et bureau
      Navigateur Playwright
      WhatsApp et Telegram
      Steam et Epic
      Rappels
    🔎 Information
      Recherche web
      Météo et vols
      Écran et webcam
      Fichiers envoyés
      Revue de dépôt
    🛠️ Code Studio
      Planifie et code
      Lance les vrais tests
      Corrige et présente
    🧠 Mémoire
      Faits avec provenance
      Archive des conversations
      Oubli sur demande
      Objectifs approuvés
    📡 Proactif
      Situation du téléphone
      Quand et comment parler
      Veille de sujets
      Alertes matériel
    🛡️ Sécurité
      Confirmation infalsifiable
      Annuler la dernière action
      Espace de travail isolé
    🧩 Extensions
      Plugins en un fichier
      Serveurs MCP
      Skills SKILL.md
    🧪 Qualité
      Selftests par paquet
      EVAL LAB
      Night Shift
```

> JARVIS connaît son propre graphe : demandez-lui **« montre-moi ton graphe »**, il l'ouvre dans le HUD.
> Le détail de chaque fonctionnalité, avec son état vérifié, est dans [`capabilities/graphs/`](capabilities/graphs/).

## ⚡ Démarrer

```bash
git clone https://github.com/TeddyDARKVADOR/Jarvis_replacement_new.git
cd Jarvis_replacement_new
python setup.py            # les dépendances de votre OS, rien de plus
```

| Commande | Ce qui se lance |
|---|---|
| `python main.py` | le HUD, relié à JARVIS sur le serveur |
| `python main.py --local` | JARVIS entièrement sur ce PC, même HUD |
| `python main.py --mini` | le HUD réduit en orbe dans un coin |

**Premier lancement :** les Paramètres (⚙ ou `Ctrl+,`) s'ouvrent seuls. Entrez l'adresse du serveur et le jeton (`python -m server.run_headless --pairing` sur le serveur), ou, en `--local`, votre clé [Gemini](https://aistudio.google.com/apikey) gratuite.

**Il faut :** Python 3.11 ou 3.12, un micro, des haut-parleurs. Le mot d'éveil, Ollama et le serveur sont facultatifs.

## 🧪 Vérifier

Chaque paquet se prouve seul, sans clé ni réseau : `python -m capabilities.selftest`, `python -m context.selftest`, `python -m server.selftest`, …
La liste complète est dans [`CLAUDE.md`](CLAUDE.md). Trois fois par nuit, **Night Shift** relance les 15 suites sous Linux, mesure si JARVIS s'améliore et s'attaque à chaque passe à un problème différent : un correctif proposé, à relire, jamais fusionné par une machine.

## 📜 Licence

Basé sur **Mark LIII** de [FatihMakes](https://www.youtube.com/@FatihMakes).
Usage personnel et non commercial : [CC BY-NC 4.0](LICENSE).
