"""
tools/notifdump.py — ce qu'Android affiche vraiment, une notification par ligne.

    adb shell dumpsys notification --noredact | python notifdump.py [--package com.jarvis]

Sortie :  <id>|<canal>|<importance>|<titre>|<texte>

POURQUOI PASSER PAR DUMPSYS
    C'est le seul endroit ou l'on lit ce qu'Android a REELLEMENT retenu, apres
    ses propres arbitrages : le canal effectivement utilise, l'importance
    reellement appliquee (qui peut differer de celle demandee si l'utilisateur
    a retouche le canal), et le texte apres troncature eventuelle. Verifier
    cote application prouverait seulement qu'on a demande quelque chose.

IMPORTANCE
    0 NONE · 1 MIN · 2 LOW · 3 DEFAULT · 4 HIGH · 5 MAX
    Le laboratoire attend 4 pour CRITICAL, 3 pour IMPORTANT, 2 pour USEFUL.

LA NOTIFICATION DU SERVICE
    id 5301, canal jarvis_link : c'est la notification permanente du service de
    premier plan, pas un message. --exclude-service la retire, ce que fait tout
    test qui compte des notifications recues.
"""
from __future__ import annotations

import argparse
import re
import sys

_REC = re.compile(
    r"NotificationRecord\([^)]*?pkg=(?P<pkg>[\w.]+).*?"
    r"\sid=(?P<id>\d+).*?"
    r"importance=(?P<imp>\d+).*?"
    r"channel=(?P<chan>[\w_]+)",
    re.S,
)
_TITLE = re.compile(r"android\.title=String \((?P<v>.*?)\)\n")
_TEXT = re.compile(r"android\.text=String \((?P<v>.*?)\)\n")

SERVICE_ID = "5301"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", default="com.jarvis")
    ap.add_argument("--exclude-service", action="store_true")
    ap.add_argument("--count", action="store_true")
    args = ap.parse_args()

    raw = sys.stdin.read()

    # Decouper au debut de chaque enregistrement : les champs android.title /
    # android.text vivent dans les lignes qui SUIVENT l'en-tete, pas dedans.
    chunks = raw.split("NotificationRecord(")
    rows = []
    for chunk in chunks[1:]:
        head = "NotificationRecord(" + chunk
        m = _REC.search(head)
        if not m or m.group("pkg") != args.package:
            continue
        if args.exclude_service and m.group("id") == SERVICE_ID:
            continue
        # Fenetre bornee : au-dela, on lirait les champs de l'enregistrement
        # suivant et un titre se retrouverait attribue a la mauvaise notification.
        window = head[:4000]
        title = _TITLE.search(window)
        text = _TEXT.search(window)
        rows.append("|".join([
            m.group("id"), m.group("chan"), m.group("imp"),
            title.group("v") if title else "",
            text.group("v") if text else "",
        ]))

    # Une meme notification apparait plusieurs fois dans dumpsys : une section
    # compacte, ou android.title/android.text suivent immediatement, et une
    # section verbeuse (uid, opPkg, flags, key...) ou ils sont bien plus bas,
    # parfois hors de la fenetre de lecture.
    #
    # On dedoublonne donc sur l'id, en gardant la version RENSEIGNEE. Prendre
    # betement la premiere rencontree donnait un titre vide une fois sur deux,
    # selon l'ordre dans lequel Android avait imprime ses sections — un test qui
    # echoue par intermittence sans que rien n'ait change.
    best: dict[str, str] = {}
    order: list[str] = []
    for row in rows:
        nid, _, rest = row.partition("|")
        if nid not in best:
            order.append(nid)
            best[nid] = row
        else:
            filled = lambda r: bool(r.split("|")[3] or r.split("|")[4])  # noqa: E731
            if filled(row) and not filled(best[nid]):
                best[nid] = row
    unique = [best[nid] for nid in order]

    if args.count:
        print(len(unique))
    else:
        print("\n".join(unique))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
