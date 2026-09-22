# `jarvis/male/` — la place du prochain visage

Vide pour l'instant, et prêt. Le visage masculin s'installe ici en une commande :

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
