"""
nightshift/local_worker.py — le PC réveillé la nuit pour les modèles locaux (phase 5, facultative).

    python -m nightshift.local_worker [--deadline 05:30] [--model gemma4:12b]
                                      [--sleep-after] [--no-push] [--queue FICHIER]
    python -m nightshift.local_worker --install-help     comment le planifier, pour ce système

Ce que fait une nuit :
  1. lit la file queue.json de la branche nightshift-data (sans jeton : dépôt
     public), écrite par le workflow : des tâches d'AVIS, jamais d'action —
     « résumé » (les échecs de la nuit en quelques lignes) et « tri » (les
     remarques ruff : vrai bug probable, bruit, à voir) ;
  2. vérifie Ollama (http://127.0.0.1:11434) et le modèle (NIGHTSHIFT_LOCAL_MODEL,
     défaut gemma4:12b, qui tient dans les 12 Go de la RTX 3060). Absent : chaque
     tâche est « skipped », avec l'erreur — jamais « en attente » ;
  3. un résultat structuré par tâche (schema_version, task_id, status parmi
     succeeded | failed | timed_out | skipped, started_at, finished_at, model,
     output, error), au plus 2 tentatives, écrit de façon atomique ;
  4. pousse les résultats sur nightshift-data (local/latest.json et local/runs/),
     avec les identifiants git de ce PC — en enregistrant l'erreur s'il échoue ;
  5. avec --sleep-after, remet le PC en veille dès qu'il a fini, ou à l'heure
     limite.

Rien de ce que dit le modèle n'est exécuté ; ses avis sont affichés comme des
hypothèses dans le rapport suivant et l'onglet NUIT, jamais lus par le briefing.
Le job GitHub ne compte jamais sur ce PC : sans résultat, le rapport dit
« indisponible ».
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nightshift import DATA_BRANCH, fetch, state
from nightshift.run import redact

SCHEMA = "jarvis.nightshift-local/1"
QUEUE_SCHEMA = "jarvis.nightshift-queue/1"
OLLAMA = os.environ.get("NIGHTSHIFT_OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("NIGHTSHIFT_LOCAL_MODEL", "gemma4:12b")
TASK_TIMEOUT_S = 600
MAX_ATTEMPTS = 2

PROMPTS = {
    "resume": ("Tu résumes pour un développeur, en français et en 5 lignes au plus, ce qui a échoué cette nuit "
               "dans les tests de son projet. Des faits seulement, aucune supposition présentée comme un fait."),
    "triage": ("Pour chaque remarque d'analyse statique ci-dessous, réponds par une ligne « chemin:ligne — "
               "vrai bug probable | bruit | à voir — raison courte ». En français, sans rien inventer."),
}


def _now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


def _http_json(url: str, body: dict | None, timeout: float, opener=None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def model_ready(model: str, opener=None) -> tuple[bool, str]:
    """Ollama répond-il, et le modèle est-il là ? (ok, raison)."""
    try:
        tags = _http_json(f"{OLLAMA}/api/tags", None, 3, opener)
    except Exception as e:
        return False, f"Ollama injoignable ({type(e).__name__}) — modèle local indisponible"
    names = {m.get("name") for m in tags.get("models") or []}
    if model not in names and f"{model}:latest" not in names:
        return False, f"modèle {model} absent d'Ollama (ollama pull {model})"
    return True, ""


def _ask(model: str, kind: str, payload: dict, timeout: float, opener=None) -> str:
    body = {"model": model, "stream": False,
            "messages": [{"role": "system", "content": PROMPTS[kind]},
                         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)[:12000]}]}
    reply = _http_json(f"{OLLAMA}/api/chat", body, timeout, opener)
    return str((reply.get("message") or {}).get("content") or "").strip()


def run_task(task: dict, model: str, ready: tuple[bool, str], deadline: datetime, opener=None,
             clock=_now) -> dict:
    """Un résultat structuré, quoi qu'il arrive. Au plus MAX_ATTEMPTS appels."""
    start = clock()
    out = {"schema_version": SCHEMA, "task_id": str(task.get("id")), "kind": task.get("kind"), "status": "skipped",
           "started_at": start.isoformat(), "finished_at": None, "model": model, "attempts": 0, "output": "",
           "error": None}
    if task.get("kind") not in PROMPTS:
        out.update(status="skipped", error=f"type de tâche inconnu : {task.get('kind')}")
    elif not ready[0]:
        out.update(status="skipped", error=ready[1])
    elif start >= deadline:
        out.update(status="skipped", error="heure limite atteinte avant la tâche")
    else:
        for n in range(1, MAX_ATTEMPTS + 1):
            out["attempts"] = n
            left = (deadline - clock()).total_seconds()
            if left <= 5:
                out.update(status="timed_out", error="heure limite atteinte pendant la tâche")
                break
            try:
                text = _ask(model, task["kind"], task.get("input") or {}, min(TASK_TIMEOUT_S, left), opener)
                if text:
                    out.update(status="succeeded", output=redact(text)[:4000], error=None)
                    break
                out.update(status="failed", error="réponse vide")
            except TimeoutError:
                out.update(status="timed_out", error="le modèle n'a pas répondu à temps")
            except Exception as e:
                out.update(status="failed", error=f"{type(e).__name__}: {redact(str(e))}"[:300])
    out["finished_at"] = clock().isoformat()
    return out


def overall(results: list[dict]) -> str:
    ok = sum(r["status"] == "succeeded" for r in results)
    if results and ok == len(results):
        return "success"
    return "partial_success" if ok else "failed"


def load_queue(path: str | None, opener=None) -> dict | None:
    """La file : un fichier local, sinon queue.json sur nightshift-data (sans jeton)."""
    try:
        if path:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        else:
            url = f"https://raw.githubusercontent.com/{fetch.repo_slug()}/{DATA_BRANCH}/queue.json"
            data = _http_json(url, None, 10, opener)
    except Exception:
        return None
    return data if isinstance(data, dict) and data.get("schema") == QUEUE_SCHEMA else None


def write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def push(batch: dict, repo_dir: Path) -> str | None:
    """Pousse les résultats sur nightshift-data avec les identifiants git de ce PC. None, ou l'erreur."""
    origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(repo_dir), capture_output=True,
                            text=True).stdout.strip()
    if not origin:
        return "pas de remote origin"
    tmp = Path(tempfile.mkdtemp(prefix="nightshift-local-"))
    try:
        def git(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(["git", *args], cwd=str(tmp), capture_output=True, text=True, timeout=120)
        if subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", DATA_BRANCH, origin, str(tmp)],
                          capture_output=True, text=True, timeout=180).returncode != 0:
            return "branche nightshift-data inaccessible"
        day = batch["finished_at"][:10]
        write_atomic(tmp / "local" / "latest.json", batch)
        write_atomic(tmp / "local" / "runs" / f"{day}-{platform.node() or 'pc'}.json", batch)
        git("add", "local")
        git("-c", "user.name=jarvis-nightshift-local", "-c", "user.email=nightshift@localhost",
            "commit", "-q", "-m", f"nightshift : avis du modèle local, {day}")
        p = git("push", "-q", "origin", f"HEAD:{DATA_BRANCH}")
        return None if p.returncode == 0 else f"push refusé : {redact(p.stderr.strip())[:200]}"
    except Exception as e:
        return f"{type(e).__name__}: {e}"[:200]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def sleep_command() -> list[str] | None:
    """La commande qui remet ce PC en veille, selon le système."""
    if sys.platform == "win32":
        return ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]
    if sys.platform == "darwin":
        return ["pmset", "sleepnow"]
    if sys.platform.startswith("linux"):
        return ["systemctl", "suspend"]
    return None


def install_help() -> str:
    exe, repo = sys.executable, Path(__file__).resolve().parent.parent
    if sys.platform == "win32":
        return (f"Windows — vérifier d'abord : powercfg /a ; powercfg /waketimers ; puis activer « Autoriser les\n"
                f"minuteurs de réveil » dans les options d'alimentation (PC sur secteur).\n"
                f"Planificateur de tâches → Créer une tâche :\n"
                f"  Général : « Exécuter même si l'utilisateur n'est pas connecté »\n"
                f"  Déclencheur : tous les jours à 03:00\n"
                f"  Action : {exe} -m nightshift.local_worker --deadline 05:30 --sleep-after\n"
                f"           (démarrer dans : {repo})\n"
                f"  Conditions : « Réveiller l'ordinateur pour exécuter cette tâche » ; « seulement sur secteur »\n")
    return (f"Linux — une minuterie systemd qui réveille le PC (droits administrateur) :\n"
            f"  /etc/systemd/system/nightshift-local.service\n"
            f"    [Service]\n    Type=oneshot\n    User={os.environ.get('USER', 'vous')}\n"
            f"    WorkingDirectory={repo}\n"
            f"    ExecStart={exe} -m nightshift.local_worker --deadline 05:30 --sleep-after\n"
            f"  /etc/systemd/system/nightshift-local.timer\n"
            f"    [Timer]\n    OnCalendar=*-*-* 03:00\n    WakeSystem=true\n    Persistent=false\n"
            f"    [Install]\n    WantedBy=timers.target\n"
            f"  puis : sudo systemctl enable --now nightshift-local.timer\n")


def _deadline(text: str | None, now: datetime) -> datetime:
    if not text:
        return now + timedelta(hours=2)
    h, m = (int(x) for x in text.split(":"))
    d = now.replace(hour=h, minute=m, second=0)
    return d if d > now else d + timedelta(days=1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nightshift.local_worker")
    ap.add_argument("--deadline", help="heure limite HH:MM (défaut : dans 2 h)")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--queue", help="un fichier queue.json au lieu de celui de nightshift-data")
    ap.add_argument("--no-push", action="store_true", help="garder les résultats ici (memory/nightshift.json)")
    ap.add_argument("--sleep-after", action="store_true", help="remettre le PC en veille à la fin")
    ap.add_argument("--install-help", action="store_true")
    args = ap.parse_args(argv)
    if args.install_help:
        print(install_help())
        return 0
    started = _now()
    deadline = _deadline(args.deadline, started)
    queue = load_queue(args.queue)
    tasks = (queue or {}).get("tasks") or []
    ready = model_ready(args.model) if tasks else (False, "file vide")
    results = [run_task(t, args.model, ready, deadline) for t in tasks]
    batch = {"schema_version": SCHEMA, "queue_run_id": (queue or {}).get("run_id"), "host": platform.node() or "pc",
             "started_at": started.isoformat(), "finished_at": _now().isoformat(), "model": args.model,
             "status": overall(results) if results else "failed", "tasks": results,
             "error": None if queue else "file de tâches introuvable (queue.json)"}
    state.update("local", {"last": batch})
    if not args.no_push and results:
        batch["push_error"] = push(batch, Path(__file__).resolve().parent.parent)
        state.update("local", {"last": batch})
    for r in results:
        print(f"  {r['status']:10} {r['task_id']}" + (f" — {r['error']}" if r.get("error") else ""))
    print(f"Nuit locale : {batch['status']}" + (f" ({batch['error']})" if batch.get("error") else ""))
    if args.sleep_after and (cmd := sleep_command()):
        subprocess.run(cmd, check=False)        # fini avant l'heure limite : la veille tout de suite
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
