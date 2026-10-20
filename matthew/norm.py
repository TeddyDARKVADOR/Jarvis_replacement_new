"""
matthew/norm.py — comparer deux valeurs d'argument comme un humain le ferait.

Le juge comparait les textes à l'identique : « 12 juillet » et « 2027-07-12 », « C:\\x »
et « c:/x/ », « 15h » et « 15:00 » comptaient comme des erreurs (score « bons
arguments » 79 % au premier champion, en partie à tort). Ici, chaque valeur est ramenée
à une forme canonique avant comparaison :

    dates      « 12 juillet », « 12/07 », « 2027-07-12 », « 12 juil. 2027 »  → 07-12
    heures     « 15h », « 15 h 00 », « 15:00 », « 3 pm »                     → 15:00
    chemins    barres obliques unifiées, pas de barre finale
    texte      casse, accents, espaces, guillemets et ponctuation de bord
    nombres    « 8081 » et 8081, « 1,5 » et 1.5
    booléens   oui / yes / vrai / true → true

same() accepte aussi l'inclusion d'un texte dans l'autre quand le plus court fait au
moins 4 caractères (« tour eiffel » ⊂ « page wikipédia de la tour eiffel »).
"""
from __future__ import annotations

import re
import unicodedata

MONTHS = {"janvier": 1, "janv": 1, "fevrier": 2, "fevr": 2, "fev": 2, "mars": 3, "avril": 4, "avr": 4,
          "mai": 5, "juin": 6, "juillet": 7, "juil": 7, "aout": 8, "septembre": 9, "sept": 9,
          "octobre": 10, "oct": 10, "novembre": 11, "nov": 11, "decembre": 12, "dec": 12,
          "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
          "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}
TRUE = {"oui", "yes", "vrai", "true", "1", "on"}
FALSE = {"non", "no", "faux", "false", "0", "off"}


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in s if not unicodedata.combining(c))


def _date(s: str) -> str | None:
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[t ].*)?", s)
    if m:
        return f"{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.fullmatch(r"(?:le |lundi |mardi |mercredi |jeudi |vendredi |samedi |dimanche )*(\d{1,2})(?:er)? ([a-z]+)\.?(?: (\d{4}))?", s)
    if m and m.group(2) in MONTHS:
        return f"{MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"
    return None


def _time(s: str) -> str | None:
    m = re.fullmatch(r"(\d{1,2}) ?(?:h|:|heures?) ?(\d{2})?(?: ?min)?", s)
    if m and int(m.group(1)) < 24:
        return f"{int(m.group(1)):02d}:{int(m.group(2) or 0):02d}"
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))? ?(am|pm)", s)
    if m:
        h = int(m.group(1)) % 12 + (12 if m.group(3) == "pm" else 0)
        return f"{h:02d}:{int(m.group(2) or 0):02d}"
    return None


def canon(v) -> str:
    """La forme canonique d'une valeur d'argument."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return f"{v:g}"
    if isinstance(v, (list, tuple)):
        return "|".join(sorted(canon(x) for x in v))
    if isinstance(v, dict):
        return "|".join(f"{k}={canon(x)}" for k, x in sorted(v.items()))
    s = _fold(str(v)).strip().strip("\"'«»“”‘’ .,;!?")
    s = re.sub(r"\s+", " ", s)
    if s in TRUE:
        return "true"
    if s in FALSE:
        return "false"
    if re.fullmatch(r"-?\d+(?:[.,]\d+)?", s):
        return f"{float(s.replace(',', '.')):g}"
    for f in (_date, _time):
        r = f(s)
        if r:
            return r
    if "\\" in s or "/" in s:
        s = s.replace("\\", "/").rstrip("/")
        s = re.sub(r"/+", "/", s)
    return s


def same(a, b) -> bool:
    ca, cb = canon(a), canon(b)
    if ca == cb:
        return True
    short, long_ = sorted((ca, cb), key=len)
    return len(short) >= 4 and short in long_
