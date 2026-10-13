# Constitution de JARVIS — historique

Chaque version modifie `version` dans `jarvis.toml` et ajoute une entrée ici, avec
la raison. Le hash SHA-256 du fichier est enregistré dans `memory/cognition.db`
(table `constitution_versions`) au premier chargement de chaque version : une
modification sans changement de version est détectée et signalée.

## 0.1.0 — 2026-10-13

Premier jet, rédigé par Claude à la demande de Teddy, à amender.

- Ordre de priorité : sécurité/contrôle > honnêteté > utilité > style
  (inspiré de l'ordonnancement explicite de la constitution de Claude, 2026, et de
  la chaîne de commandement du Model Spec d'OpenAI, 2025).
- Règle d'honnêteté sur la mémoire : la provenance décide du statut d'un souvenir
  (appliquée dans `cognition/policy.py`, pas seulement promise ici).
- Bloc de prompt volontairement court (moins de 450 caractères), à cause du coût
  en tokens à chaque connexion.
