# `jarvis/male/` — le visage masculin

**Installé le 23/09/2026** : Microsoft Rocketbox `Male_Adult_01` (MIT, voir
`LICENSE.md` et `male.model.json`), converti depuis `Male_Adult_01_facial.fbx`.
Le binaire n'est pas versionné ; son profil (empreinte, provenance,
calibration) l'est. L'activer : `python -m presence.install_model --use jarvis/male`.

Un autre visage s'installe ici en une commande :

```bash
python -m presence.install_model <fichier.glb|.vrm> --slot jarvis/male \
    --label "JARVIS (visage masculin)" --license "…" --source "…"
```

Ce qu'il faut vérifier dans le fichier avant de le choisir, par ordre d'importance :

1. **52 blendshapes ARKit**, sous n'importe quel nom (`browDownLeft`,
   `browDown_L`, `brow_down_l`, `Wolf3D_Head.browDownLeft` sont tous reconnus) ;
2. **les yeux** — des formes `eyeLook*` ou des os `LeftEye`/`RightEye` ;
3. **un os de tête** (`Head` ou `Neck`) — sans lui, aucun hochement ne tourne rien ;
4. les **visèmes Oculus** — la bouche parle mieux avec ;
5. un squelette humanoïde, si l'on veut un jour `"motion": "full"`.

Le labo (`avatar/lab.html`) accepte le fichier par glisser-déposer **avant**
l'installation : son profil mesuré dit tout de suite ce qu'il sait faire.

Revenir au visage féminin : `python -m presence.install_model --use jarvis/female`.
