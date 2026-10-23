# Constitution de Matthew — historique

Chaque version modifie `version` dans `matthew_assistant.toml` et ajoute une entrée ici, avec
la raison. Le hash SHA-256 du fichier est enregistré dans `memory/cognition.db`
(table `constitution_versions`) au premier chargement de chaque version : une
modification sans changement de version est détectée et signalée.

2026-10-23 : le fichier `jarvis.toml` devient `matthew_assistant.toml`, contenu
inchangé (même version, même hash).

## 0.2.0 — 2026-10-22

L'assistant s'appelle désormais Matthew (droits d'auteur : « JARVIS » est un
personnage de Marvel). Demandé par Teddy.

- `identite.nom` : « JARVIS » → « Matthew ». Le nom vient du modèle ouvert
  entraîné chaque nuit (`matthew/`) : l'assistant et son futur cerveau portent
  le même nom.
- `priorites.Style` : la référence à « Jarvis d'Iron Man » est retirée ; le
  style (professionnel, efficace, une pointe d'esprit) reste, avec une
  personnalité propre.
- `identite.histoire` : la date du changement de nom.
- Inchangé : le nom du fichier (`jarvis.toml`) et les identifiants internes,
  renommés plus tard, pas à pas.

## 0.1.0 — 2026-10-13

Premier jet, rédigé par Claude à la demande de Teddy, à amender.

- Ordre de priorité : sécurité/contrôle > honnêteté > utilité > style
  (inspiré de l'ordonnancement explicite de la constitution de Claude, 2026, et de
  la chaîne de commandement du Model Spec d'OpenAI, 2025).
- Règle d'honnêteté sur la mémoire : la provenance décide du statut d'un souvenir
  (appliquée dans `cognition/policy.py`, pas seulement promise ici).
- Bloc de prompt volontairement court (moins de 450 caractères), à cause du coût
  en tokens à chaque connexion.
