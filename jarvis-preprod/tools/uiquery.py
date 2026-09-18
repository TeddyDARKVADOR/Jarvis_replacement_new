"""
tools/uiquery.py — interroge l'arbre UI produit par `uiautomator dump`.

POURQUOI PAS DES COORDONNEES
    Un `input tap 540 1200` marche jusqu'au jour ou une marge change de 8 dp.
    Il ne casse pas bruyamment : il clique a cote, le test echoue plus loin, et
    on cherche le bug au mauvais endroit. Chercher un noeud par son identifiant,
    son texte ou sa description, puis taper au centre de ses bornes reelles,
    echoue la ou il faut — « element introuvable » — et dit lequel.

    Les coordonnees restent possibles (tools/ui.sh tap-xy) pour les cas ou il
    n'y a vraiment rien a quoi s'accrocher, mais c'est le dernier recours et il
    est nomme comme tel.

COMPOSE ET LES IDENTIFIANTS
    L'application est en Jetpack Compose, qui n'expose PAS de resource-id : les
    noeuds sortent avec un resource-id vide. Ce qui reste utilisable, et ce sur
    quoi les tests doivent s'appuyer, c'est `text` et `content-desc`. C'est
    pourquoi --by text est le mode par defaut ici, alors que sur une application
    en Views on aurait prefere --by id.

USAGE
    python uiquery.py find  --by text --value CONNECT      < dump.xml
    python uiquery.py center --by text --value CONNECT     < dump.xml
    python uiquery.py texts                                < dump.xml
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET

_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

_ATTR = {
    "id": "resource-id",
    "text": "text",
    "desc": "content-desc",
    "class": "class",
}


def iter_nodes(root):
    for node in root.iter("node"):
        yield node


def matches(node, attr: str, value: str, exact: bool) -> bool:
    actual = node.get(attr, "") or ""
    if exact:
        return actual == value
    return value.lower() in actual.lower()


def center(node) -> tuple[int, int] | None:
    m = _BOUNDS.match(node.get("bounds", "") or "")
    if not m:
        return None
    x1, y1, x2, y2 = (int(g) for g in m.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["find", "center", "texts", "exists"])
    ap.add_argument("--by", default="text", choices=sorted(_ATTR))
    ap.add_argument("--value", default="")
    ap.add_argument("--exact", action="store_true")
    args = ap.parse_args()

    raw = sys.stdin.buffer.read()
    if not raw.strip():
        print("arbre UI vide (dump absent ou ecran verrouille)", file=sys.stderr)
        return 3
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"arbre UI illisible: {e}", file=sys.stderr)
        return 3

    if args.action == "texts":
        seen = []
        for node in iter_nodes(root):
            t = (node.get("text", "") or "").strip()
            d = (node.get("content-desc", "") or "").strip()
            for v in (t, d):
                if v and v not in seen:
                    seen.append(v)
        print("\n".join(seen))
        return 0

    attr = _ATTR[args.by]

    # L'EXACT AVANT LE PARTIEL — et ce n'est pas un detail de confort.
    #
    # Avec une simple recherche par sous-chaine, taper "CONNECT" sur l'ecran de
    # reglages visait l'en-tete de section "Connection", qui apparait plus haut
    # dans l'arbre et n'est pas cliquable. Le tap partait dans le vide, aucune
    # commande ne signalait d'erreur, et le test echouait soixante secondes plus
    # loin sur « pas de connexion » — a l'oppose de la vraie cause.
    #
    # Donc : une correspondance exacte gagne toujours. Le partiel ne sert que
    # lorsqu'aucun libelle ne correspond mot pour mot, ce qui reste utile pour
    # les textes longs ("Allowed. The ongoing notification...").
    exact_hits = [n for n in iter_nodes(root) if matches(n, attr, args.value, True)]
    loose_hits = [] if args.exact else [
        n for n in iter_nodes(root) if matches(n, attr, args.value, False)
    ]
    hits = exact_hits or loose_hits

    if args.action == "exists":
        return 0 if hits else 1

    if not hits:
        print(f"introuvable: {args.by}='{args.value}'", file=sys.stderr)
        return 1

    # A egalite, le noeud cliquable l'emporte. Dans Compose un libelle visible
    # est souvent un Text imbrique dans le bouton ; taper le centre du Text
    # atteint quand meme le bouton, mais viser directement le cliquable evite
    # les cas ou le libelle deborde de son parent.
    node = next((n for n in hits if n.get("clickable") == "true"), hits[0])

    if args.action == "center":
        c = center(node)
        if c is None:
            print("noeud sans bornes exploitables", file=sys.stderr)
            return 1
        print(f"{c[0]} {c[1]}")
        return 0

    print(f"class={node.get('class','')} text={node.get('text','')!r} "
          f"desc={node.get('content-desc','')!r} bounds={node.get('bounds','')} "
          f"clickable={node.get('clickable','')} enabled={node.get('enabled','')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
