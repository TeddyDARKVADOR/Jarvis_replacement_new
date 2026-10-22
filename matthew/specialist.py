"""
matthew/specialist.py — Direction 3 : Matthew spécialiste autonome, mais évalué.

Matthew ne remplace pas les grands modèles d'un bloc. Chaque type de tâche est une
SPÉCIALITÉ, avec son épreuve, son seuil et sa RÉFÉRENCE : le modèle qu'il remplacerait,
noté sur la même épreuve. Matthew ne prend une tâche en charge que si

        score ≥ seuil   ET   score ≥ référence − MARGIN

et la perd dès qu'une nouvelle mesure ne le prouve plus. Les spécialités :

    outil       choisir le bon outil       l'examen de la nuit (champion.json → score.tool_name)
    intention   classer une demande        INTENT_CASES : action, question, conversation,
                                           mémoire, arrêt — un mot attendu
    extraction  sortir un JSON structuré   EXTRACT_CASES : un rappel (quand, quoi), un message
                                           (à qui, quoi) ; champ par champ, via norm.same
    juge        juge de mémoire            cogbench (vrais prompts de cognition/policy.py)
    planif      planificateur autonome     cogbench (vrais prompts de cognition/autonomy.py)

Tout est noté par règles : aucun modèle ne juge (ni Gemini ni Claude ne notent Matthew,
et la référence n'est qu'un concurrent, jamais une cible d'entraînement). La référence
coûte quelques appels gratuits : sa mesure est gardée REF_TTL_DAYS jours.

    python -m matthew.specialist run [--model matthew] [--no-ref]
    python -m matthew.specialist status

Résultat : memory/matthew/specialties.json. allowed(tâche) le lit (cognition/providers.py
écarte Matthew d'une tâche qu'il n'a pas gagnée) ; public() en tire la part publiable
(la vitrine affiche chaque spécialité, son score, son seuil, sa référence).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matthew
from matthew import _api_keys, config
from matthew.client import Client, ClientError
from matthew.norm import same

BENCH_VERSION = 1
MARGIN = 0.02
REF_TTL_DAYS = 7

#: id → (libellé, libellé anglais, seuil). L'ordre est celui de la vitrine.
SPECIALTIES = {
    "outil":      ("Choisir le bon outil", "Picking the right tool", 0.80),
    "intention":  ("Comprendre l'intention", "Understanding intent", 0.85),
    "extraction": ("Extraire l'essentiel", "Structured extraction", 0.80),
    "juge":       ("Juger un souvenir", "Judging a memory", 0.80),
    "planif":     ("Planifier seul", "Planning alone", 0.75),
}

INTENTS = ("action", "question", "conversation", "memoire", "arret")
INTENT_PROMPT = """Classe la demande de l'utilisateur dans UNE catégorie :
action        il veut que l'assistant fasse quelque chose sur l'ordinateur ou le téléphone
question      il veut une information (météo, actualité, fait, calcul)
conversation  il bavarde, salue, remercie, donne son avis
memoire       il demande de retenir ou de rappeler un fait personnel
arret         il veut annuler, arrêter, défaire ce qui vient d'être fait
Réponds par le seul mot de la catégorie, sans rien d'autre.

Demande : {text}"""
INTENT_CASES = [
    ("Ouvre Spotify", "action"), ("Monte le volume à fond", "action"),
    ("Envoie un message à Léa pour lui dire que j'arrive", "action"),
    ("Mets l'ordinateur en veille", "action"), ("Range mes téléchargements par type", "action"),
    ("Quel temps fera-t-il demain à Lille ?", "question"), ("Combien font 17 fois 23 ?", "question"),
    ("C'est qui le président du Brésil ?", "question"), ("Quelles sont les nouvelles ce matin ?", "question"),
    ("Salut, ça va ?", "conversation"), ("Merci beaucoup, t'es génial", "conversation"),
    ("J'ai passé une sale journée", "conversation"), ("Bonne nuit", "conversation"),
    ("Retiens que mon code de vestiaire est le 412", "memoire"),
    ("Tu te souviens du prénom de ma nièce ?", "memoire"),
    ("Note que je suis allergique aux arachides", "memoire"),
    ("Annule ça", "arret"), ("Non, remets le fichier où il était", "arret"),
    ("Arrête, laisse tomber", "arret"), ("Stop, c'est pas ce que je voulais", "arret"),
]

EXTRACT_PROMPT = """Extrais les informations de la demande en JSON, sans aucun texte autour.
Si c'est un rappel : {{"type": "rappel", "quand": "<date et/ou heure telles que dites>", "quoi": "<l'objet du rappel>"}}
Si c'est un message : {{"type": "message", "destinataire": "<prénom ou nom>", "texte": "<le message>"}}

Demande : {text}"""
EXTRACT_CASES = [
    ("Rappelle-moi d'appeler le garage demain à 9h", {"type": "rappel", "quand": "demain 9h", "quoi": "appeler le garage"}),
    ("Fais-moi penser à sortir les poubelles ce soir à 20h", {"type": "rappel", "quand": "ce soir 20h", "quoi": "sortir les poubelles"}),
    ("Le 12 juillet, rappelle-moi l'anniversaire de Paul", {"type": "rappel", "quand": "12 juillet", "quoi": "anniversaire de Paul"}),
    ("Rappel à 18h30 : acheter du pain", {"type": "rappel", "quand": "18h30", "quoi": "acheter du pain"}),
    ("Dis à Marc que je serai en retard", {"type": "message", "destinataire": "Marc", "texte": "je serai en retard"}),
    ("Envoie à Julie : on se voit samedi", {"type": "message", "destinataire": "Julie", "texte": "on se voit samedi"}),
    ("Écris à maman que je l'aime", {"type": "message", "destinataire": "maman", "texte": "je l'aime"}),
    ("Préviens Karim que la réunion est annulée", {"type": "message", "destinataire": "Karim", "texte": "la réunion est annulée"}),
]


def _path(root: Path | None = None) -> Path:
    return (root or matthew.data_dir()) / "specialties.json"


def load(root: Path | None = None) -> dict:
    try:
        return json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ── Les épreuves ─────────────────────────────────────────────────────────────

def parse_intent(text: str) -> str | None:
    t = (text or "").strip().lower().replace("é", "e").replace("ê", "e")
    m = re.search(r"\b(" + "|".join(INTENTS) + r")\b", t)
    return m.group(1) if m else None


def parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        obj = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


_FILLER = re.compile(r"\b(à|a|le|la|les|vers|pour|au|du)\b", re.I)


def _when(v) -> str:
    """« demain à 9h » et « demain 9h » disent la même chose."""
    return " ".join(_FILLER.sub(" ", str(v)).split())


def extract_score(want: dict, got: dict | None) -> float:
    """Part des champs attendus retrouvés ; le type faux annule tout."""
    if not got or str(got.get("type", "")).lower() != want["type"]:
        return 0.0
    keys = [k for k in want if k != "type"]
    ok = lambda k: same(_when(want[k]), _when(got.get(k, ""))) if k == "quand" else same(want[k], got.get(k, ""))
    return sum(ok(k) for k in keys) / len(keys)


def _ask(client: Client, prompt: str, max_tokens: int = 120) -> str:
    try:
        return client.chat([{"role": "user", "content": prompt}], max_tokens=max_tokens, temperature=0)["content"]
    except ClientError:
        return ""


def bench_intent(client: Client) -> dict:
    fails = []
    for text, want in INTENT_CASES:
        got = parse_intent(_ask(client, INTENT_PROMPT.format(text=text), 20))
        if got != want:
            fails.append({"text": text, "want": want, "got": got})
    n = len(INTENT_CASES)
    return {"score": round((n - len(fails)) / n, 3), "n": n, "failures": fails[:8]}


def bench_extract(client: Client) -> dict:
    total, fails = 0.0, []
    for text, want in EXTRACT_CASES:
        got = parse_json(_ask(client, EXTRACT_PROMPT.format(text=text), 160))
        s = extract_score(want, got)
        total += s
        if s < 1:
            fails.append({"text": text, "got": got})
    n = len(EXTRACT_CASES)
    return {"score": round(total / n, 3), "n": n, "failures": fails[:8]}


def bench_all(client: Client) -> dict:
    """Les quatre épreuves qui demandent des appels (l'outil vient de l'examen de la nuit)."""
    from matthew import cogbench
    cog = cogbench.run(client)
    return {"intention": bench_intent(client), "extraction": bench_extract(client),
            "juge": {"score": cog["judge"], "n": len(cogbench.JUDGE_CASES)},
            "planif": {"score": cog["planner"], "n": len(cogbench.PLANNER_CASES)}}


# ── La référence : le modèle que Matthew remplacerait ────────────────────────

def reference_client(cfg: dict | None = None, keys: dict | None = None) -> Client | None:
    """Réglage "specialist_reference": {"base_url", "model", "api_key_name"} ; sinon le premier
    générateur de la cognition qu'il remplacerait : gpt-oss-20b chez Groq si la clé existe,
    sinon le modèle local de secours du professeur. "specialist_reference": false la coupe."""
    cfg = cfg or config()
    keys = _api_keys() if keys is None else keys
    ref = cfg.get("specialist_reference")
    if ref is False:
        return None
    if isinstance(ref, dict) and ref.get("model"):
        key = keys.get(ref["api_key_name"]) if ref.get("api_key_name") else None
        return Client(ref.get("base_url", cfg["endpoint"]), ref["model"], key, purpose="teacher",
                      extra=ref.get("extra") or {}, timeout=60)
    if keys.get("groq_api_key"):
        return Client("https://api.groq.com/openai/v1", "openai/gpt-oss-20b", keys["groq_api_key"],
                      purpose="teacher", extra={"reasoning_effort": "low"}, timeout=60)
    fb = cfg.get("teacher_fallback") or {}
    if fb.get("model"):
        return Client(fb.get("base_url", cfg["endpoint"]), fb["model"], purpose="teacher",
                      extra=fb.get("extra") or {}, timeout=120)
    return None


def decide(score: float | None, floor: float, ref: float | None) -> tuple[bool, str]:
    if score is None:
        return False, "pas encore mesuré"
    if score < floor:
        return False, f"sous le seuil ({score:.2f} < {floor:.2f})"
    if ref is not None and score < ref - MARGIN:
        return False, f"moins bon que la référence ({score:.2f} < {ref:.2f})"
    return True, "seuil atteint" + ("" if ref is None else ", référence égalée ou battue")


def run(client: Client, ref_client: Client | None = None, champion: dict | None = None,
        root: Path | None = None, clock=time.time) -> dict:
    """Mesure Matthew (et la référence si sa mesure a plus de REF_TTL_DAYS jours), décide,
    écrit specialties.json. Rend le fichier écrit."""
    from matthew import champion as _champion
    prev = load(root)
    mine = bench_all(client)
    champ = champion if champion is not None else (_champion() or {})
    tool = ((champ.get("score") or {}).get("tool_name"))
    mine["outil"] = {"score": round(float(tool), 3) if isinstance(tool, (int, float)) else None,
                     "n": (champ.get("score") or {}).get("n")}
    ref = prev.get("reference") or {}
    fresh = (ref.get("version") == BENCH_VERSION and ref_client is not None
             and ref.get("model") == ref_client.model and clock() - float(ref.get("at", 0)) < REF_TTL_DAYS * 86400)
    if ref_client is not None and not fresh:
        got = bench_all(ref_client)
        ref = {"model": ref_client.model, "at": clock(), "version": BENCH_VERSION,
               "scores": {k: v["score"] for k, v in got.items()}}
    elif ref_client is None:
        ref = {}
    out = {"version": BENCH_VERSION, "model": client.model,
           "at": datetime.fromtimestamp(clock()).isoformat(timespec="seconds"), "reference": ref, "results": {}}
    for sid, (_, _, floor) in SPECIALTIES.items():
        r = mine.get(sid) or {}
        rs = (ref.get("scores") or {}).get(sid)
        ok, why = decide(r.get("score"), floor, rs)
        out["results"][sid] = {"score": r.get("score"), "n": r.get("n"), "floor": floor, "reference": rs,
                               "delegated": ok, "why": why, "failures": r.get("failures", [])}
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


# ── Ce que lit le reste ──────────────────────────────────────────────────────

def delegated(task: str, root: Path | None = None) -> bool:
    r = ((load(root).get("results") or {}).get(task) or {})
    return bool(r.get("delegated"))


def public(root: Path | None = None) -> list[dict]:
    """Pour la vitrine : seulement des agrégats (aucun cas raté, aucun nom de modèle)."""
    res = load(root).get("results") or {}
    out = []
    for sid, (label, label_en, floor) in SPECIALTIES.items():
        r = res.get(sid)
        if not r:
            continue
        num = lambda v: round(float(v), 3) if isinstance(v, (int, float)) else None
        out.append({"id": sid, "label": label, "label_en": label_en, "score": num(r.get("score")),
                    "floor": floor, "reference": num(r.get("reference")),
                    "n": int(r["n"]) if isinstance(r.get("n"), int) else None, "delegated": bool(r.get("delegated"))})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="matthew.specialist")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="mesurer Matthew (et la référence), décider, écrire")
    r.add_argument("--model", default=None)
    r.add_argument("--no-ref", action="store_true", help="sans référence (seuils seuls)")
    sub.add_parser("status", help="les spécialités et leur décision")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        cfg = config()
        from matthew.flywheel import SERVE_EXTRA
        client = Client(cfg["endpoint"], a.model or cfg["serve_model"], purpose="matthew", extra=SERVE_EXTRA, timeout=120)
        out = run(client, None if a.no_ref else reference_client(cfg))
    else:
        out = load()
    for sid, (label, _, _) in SPECIALTIES.items():
        x = (out.get("results") or {}).get(sid)
        if x:
            ref = "—" if x["reference"] is None else f"{x['reference']:.2f}"
            sc = "—" if x["score"] is None else f"{x['score']:.2f}"
            print(f"  {'✓' if x['delegated'] else '·'} {label:<26} {sc:>5}  seuil {x['floor']:.2f}  réf {ref:>5}  {x['why']}")
    if not out:
        print("aucune mesure : python -m matthew.specialist run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
