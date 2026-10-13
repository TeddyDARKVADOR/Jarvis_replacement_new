"""
cognition/policy.py — les règles épistémiques, en fonctions pures. Pas d'I/O.

POURQUOI CE FICHIER EXISTE
    Dans la V1, `save_memory` écrit ce que le modèle décide d'écrire. Si Gemini
    comprend de travers, ou invente, l'erreur devient un « fait » qui revient
    dans chaque prompt suivant. Les Generative Agents (Park et al. 2023)
    montrent exactement ce défaut : des souvenirs « embellis ».

    Ici, un souvenir porte sa PROVENANCE, et c'est la provenance — pas
    l'assurance du modèle — qui décide de son statut et de sa confiance.
    Ces règles sont du code, testé par le selftest, et non une consigne dans un
    prompt que le modèle pourrait ignorer.

LA RÈGLE
    user_stated     l'utilisateur l'a dit, et on le retrouve dans ce qu'il a dit
                    (cf. evidence_ratio)                    -> active, 0.85
    tool_observed   un outil l'a constaté                   -> active, 0.9
    legacy_import   déjà dans long_term.json (V1)           -> active, 0.7
    model_summary   résumé de session par le modèle         -> active, 0.6
                    (un récit de ce dont on a parlé, pas un fait sur le monde)
    model_inferred  le modèle l'affirme sans preuve trouvée -> proposed, ≤ 0.5
    reflection      synthèse générée                        -> proposed, ≤ 0.5

    Un souvenir `proposed` peut être rappelé, mais il est toujours marqué « non
    vérifié » et n'entre jamais dans le bloc de prompt comme un fait.
"""
from __future__ import annotations

import re
import unicodedata

SOURCES = ("user_stated", "tool_observed", "legacy_import", "model_summary",
           "model_inferred", "reflection")

KINDS = ("episodic", "semantic", "autobiographical", "procedural",
         "reflective", "prospective")

STATUSES = ("proposed", "active", "validated", "contested", "superseded",
            "retracted")

# Statuts qu'un rappel peut renvoyer. superseded/retracted restent en base pour
# l'historique, mais ne sont plus « ce que JARVIS sait ».
RECALLABLE = ("proposed", "active", "validated", "contested")

_DEFAULTS = {
    "user_stated":    ("active",   0.85),
    "tool_observed":  ("active",   0.90),
    "legacy_import":  ("active",   0.70),
    "model_summary":  ("active",   0.60),
    "model_inferred": ("proposed", 0.50),
    "reflection":     ("proposed", 0.50),
}

# Plafond de confiance tant qu'aucune validation n'a eu lieu. Un modèle très
# sûr de lui ne monte pas plus haut : son assurance n'est pas une preuve.
_CEILING = {"model_inferred": 0.5, "reflection": 0.5, "model_summary": 0.6}

# Part minimale des mots porteurs d'une valeur qu'on doit retrouver dans les
# tours récents de l'utilisateur pour la considérer comme « dite par lui ».
EVIDENCE_THRESHOLD = 0.5


def initial_status(source: str, confidence: float | None = None) -> tuple[str, float]:
    """(statut, confiance) d'un nouveau souvenir, d'après sa provenance seule."""
    if source not in _DEFAULTS:
        raise ValueError(f"source inconnue : {source!r}")
    status, default = _DEFAULTS[source]
    conf = default if confidence is None else float(confidence)
    conf = min(conf, _CEILING.get(source, 1.0))
    return status, max(0.0, min(1.0, conf))


def can_supersede(new_source: str, old_source: str) -> bool:
    """Une nouvelle valeur peut-elle remplacer l'ancienne sur la même clé ?

    Seule une source au moins aussi fiable le peut. Une inférence du modèle ne
    remplace jamais ce que l'utilisateur a dit : elle est enregistrée à côté,
    `proposed`, avec un lien `contradicts`, et l'ancienne passe `contested`.
    """
    rank = {"tool_observed": 4, "user_stated": 4, "legacy_import": 3,
            "model_summary": 2, "model_inferred": 1, "reflection": 1}
    return rank.get(new_source, 0) >= rank.get(old_source, 0)


# ── Preuve dans la transcription ─────────────────────────────────────────────

_STOP = {
    # fr
    "les", "des", "une", "est", "son", "ses", "sur", "dans", "pour", "par", "avec",
    "que", "qui", "pas", "plus", "mon", "mes", "ton", "tes", "elle", "il", "aux",
    "du", "de", "la", "le", "et", "au", "ce", "cette", "suis", "ai", "a",
    # en
    "the", "and", "for", "with", "his", "her", "their", "has", "have", "was",
    "are", "is", "user", "users", "likes", "named", "name",
}


def _fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def content_words(text: str) -> list[str]:
    """Mots porteurs, sans accents ni casse : « Ayşe » == « ayse »."""
    words = re.split(r"[^\w]+", _fold(text))
    return [w for w in words if len(w) >= 3 and w not in _STOP and not w.isdigit()] \
        + [w for w in words if w.isdigit()]


def specific_words(text: str) -> list[str]:
    """Noms propres et nombres : ce qu'un modèle invente, et ce qui ne se traduit pas.

    Une majuscule en tête de phrase ne compte pas (« Likes jazz » n'a pas de nom
    propre)."""
    out = []
    for sentence in re.split(r"[.!?:;\n]+", text or ""):
        tokens = re.findall(r"\w+", sentence)
        for i, tok in enumerate(tokens):
            if tok.isdigit() or (i > 0 and tok[:1].isupper()):
                out.append(_fold(tok))
    return out


def _heard(word: str, heard: set[str]) -> bool:
    """Égalité, ou même radical (≥ 5 lettres communes en tête) : lasagna/lasagnes,
    marathon/marathons. Ne rapproche pas des mots courts différents."""
    if word in heard:
        return True
    if len(word) < 5:
        return False
    stem = word[:5]
    return any(h.startswith(stem) and abs(len(h) - len(word)) <= 3 for h in heard)


def evidence_ratio(value: str, user_turns: list[str]) -> float:
    """Part de `value` qu'on retrouve dans ce que l'utilisateur a dit.

    Volontairement simple et lexical : c'est un filtre contre l'invention, pas
    une compréhension. Si la valeur contient des noms propres ou des nombres,
    seuls eux comptent : Gemini écrit souvent en anglais ce qui a été dit en
    français, et « sister Ayse, 31 years old » est soutenu par « ma sœur
    s'appelle Ayşe, 31 ans » alors que « sister » et « years » n'y sont pas.
    Sinon, tous les mots porteurs comptent.

    Les faux négatifs (paraphrase, traduction d'un mot commun) ne perdent rien :
    le souvenir est gardé, seulement marqué non vérifié.
    """
    words = set(specific_words(value)) or set(content_words(value))
    if not words:
        return 0.0
    heard = set(content_words(" ".join(user_turns)))
    return sum(_heard(w, heard) for w in words) / len(words)


def source_for_model_save(value: str, user_turns: list[str]) -> tuple[str, float]:
    """Provenance d'un `save_memory` appelé par le modèle : (source, ratio).

    Seule la valeur est confrontée à la transcription. La clé est une étiquette
    choisie par le modèle (« sister », « favorite_food ») : la compter comme
    preuve reviendrait à laisser le modèle se citer lui-même.
    """
    ratio = evidence_ratio(value, user_turns)
    # Un seul nom propre ou nombre jamais entendu suffit à fabriquer un faux
    # souvenir (« Ayse lives in Paris », « 34 ans » au lieu de 31) : quand la
    # valeur en contient, ils doivent TOUS avoir été dits.
    needed = 1.0 if specific_words(value) else EVIDENCE_THRESHOLD
    return ("user_stated" if ratio >= needed else "model_inferred"), ratio
