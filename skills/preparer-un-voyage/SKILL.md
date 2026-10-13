---
name: preparer-un-voyage
description: Préparer un déplacement de bout en bout — météo sur place, vols, itinéraire, rappels de départ. À utiliser quand l'utilisateur dit qu'il part quelque part, prépare un voyage ou demande « organise mon déplacement à … ».
---

# Préparer un voyage

## Ce qu'il faut savoir avant d'agir
Destination, date de départ, date de retour, ville de départ. Demande seulement ce qui manque, en une seule question. N'invente jamais une date.

## Dans l'ordre
1. `web_search` en mode `search` : la météo prévue à destination pour ces dates. Une phrase.
2. Si le trajet est long (plus de 500 km ou un autre pays) : `flight_finder` pour l'aller. Annonce les deux meilleures options, prix et horaires, sans en choisir une à la place de l'utilisateur.
3. Si le trajet se fait par la route : `open_itinerary` de la ville de départ à la destination.
4. Propose deux rappels et crée-les seulement après un « oui » : la veille au soir (« préparer la valise ») et trois heures avant le départ.
5. Si l'utilisateur a des objectifs actifs qui tombent pendant le voyage, signale-le en une phrase.

## Style
À l'oral : pas de liste, pas de titres. Moins de 80 mots pour le bilan. Les prix et les heures arrondis comme on les dit.
