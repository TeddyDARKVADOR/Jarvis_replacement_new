"""
matthew/flywheel.py — la boucle d'amélioration, de bout en bout, sans humain.

    python -m matthew.flywheel              un cycle « à sec » : école, données, plan
    python -m matthew.flywheel --execute    idem + entraîne, importe, évalue, promeut
    python -m matthew.flywheel --status     où en est Matthew
    python -m matthew.flywheel --schedule   la commande qui pose la tâche de chaque nuit (affichée, pas lancée)
    python -m matthew.flywheel --install    la pose (Planificateur de tâches, minuterie systemd ou crontab)

Un cycle :
  1. école      le professeur ouvert réécrit les nouveaux épisodes (budget borné)
  2. données    train / eval / dpo reconstruits depuis tout le journal
  3. seuil      moins de min_new_examples nouveaux exemples → on attend
  4. entraîner  depuis le modèle de base (jamais depuis le précédent candidat)
  5. importer   ollama create matthew-candidate
  6. évaluer    candidat ET champion sur le même hold-out, plusieurs passes
  7. promouvoir ollama cp matthew-candidate matthew, seulement si le gate passe
Chaque étape est gardée : un échec s'écrit dans le rapport, le cycle s'arrête
proprement, rien n'est promu. Un verrou empêche deux cycles en même temps.
Rapports : reports/<date>.json ; état : state.json ; champion : champion.json.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matthew import NAME, OPEN_LICENSES, REPO, config, data_dir
from matthew import dataset as D
from matthew import evaluate as E
from matthew import train as T
from matthew.client import Client, usage_summary
from matthew.journal import Journal
from matthew.teacher import LicenseError, School

LOCK_STALE_S = 12 * 3600
#: Matthew est entraîné sans réflexion (enable_thinking=False) : on le sert pareil.
#: Mesuré le 2026-10-20 : 0,3 s par réponse au lieu de 1,5 à 34 s avec réflexion.
SERVE_EXTRA = {"reasoning_effort": "none"}


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class Flywheel:
    def __init__(self, root: Path | None = None, cfg: dict | None = None, *, school=None,
                 client_factory=None, runner=subprocess.run, trainer=None, clock=time.time,
                 bootstrap=None, reference=None, packs=None, bench=None):
        self._bootstrap = bootstrap        # fabrique (selftest) ; None = la vraie
        # La référence des spécialités (un autre modèle, noté sur les mêmes épreuves) : la vraie
        # seulement avec le vrai client de Matthew — un selftest n'appelle jamais Groq.
        self._reference = reference or ((lambda: None) if client_factory else None)
        # Paquets de données (matthew/pack.py) et banc scellé (matthew/bench.py) : les vrais
        # seulement avec le vrai client ; un selftest les fournit ou s'en passe.
        self._packs = packs if packs is not None else (None if client_factory else "repo")
        self._bench = bench if bench is not None else ((lambda: []) if client_factory else None)
        self.root = Path(root) if root else data_dir()
        self.cfg = cfg or config()
        self.journal = Journal(self.root, clock=clock)
        self._school = school
        self._client_factory = client_factory or (
            lambda model: Client(self.cfg["endpoint"], model, purpose="matthew", usage_root=self.root,
                                 extra=SERVE_EXTRA))
        self._runner = runner
        self._trainer = trainer or (lambda run: T.execute(run, cfg=self.cfg, on_progress=self._live))
        self._clock = clock

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    def school(self) -> School | None:
        if self._school is None:
            self._school = School(self.journal, cfg=self.cfg, clock=self._clock)
        return self._school

    def _lock(self) -> bool:
        lock = self.root / "flywheel.lock"
        if lock.exists() and self._clock() - lock.stat().st_mtime < LOCK_STALE_S:
            return False
        lock.write_text(str(os.getpid()), encoding="utf-8")
        return True

    def _unlock(self) -> None:
        try:
            (self.root / "flywheel.lock").unlink()
        except OSError:
            pass

    def cycle(self, execute: bool = False, budget: int | None = None) -> dict:
        stamp = datetime.fromtimestamp(self._clock()).strftime("%Y%m%d-%H%M%S")
        rep = {"schema": "matthew.cycle/1", "at": stamp, "execute": execute, "steps": []}

        def step(name, **kw):
            rep["steps"].append({"step": name, **kw})

        if not self._lock():
            step("verrou", ok=False, reason="un cycle tourne déjà")
            return self._finish(rep, "occupé")
        try:
            self._phase("prep")
            # 0a. paquets : la donnée vérifiée fabriquée sur un autre PC (matthew/pack.py)
            if self._packs:
                try:
                    from matthew import pack
                    step("paquets", ok=True, **pack.import_all(self.root, None if self._packs == "repo" else self._packs))
                except Exception as e:
                    step("paquets", ok=False, reason=f"{type(e).__name__}: {e}"[:200])
            # 0b. santé du champion servi ; retour arrière automatique s'il a cédé (matthew/rollback.py)
            self._health(step)
            # 0. amorçage : complète les outils pas encore amorcés (budget borné, reprise)
            if self.cfg.get("bootstrap_budget", 0):
                try:
                    bs = self._bootstrap() if self._bootstrap else None
                    if bs is None:
                        from matthew.bootstrap import Bootstrap
                        bs = Bootstrap(self.journal, cfg=self.cfg, max_calls=self.cfg["bootstrap_budget"])
                    st = bs.run()
                    step("amorçage", ok="stopped" not in st or "budget" in st.get("stopped", ""),
                         written=st.get("ok", 0), rejected=st.get("rejected", 0),
                         **({"reason": st["stopped"]} if "stopped" in st else {}))
                except Exception as e:
                    step("amorçage", ok=False, reason=f"{type(e).__name__}: {e}")
            # 1. école
            try:
                st = self.school().relabel(budget)
                step("école", ok="stopped" not in st, written=st["ok"], rejected=st["rejected"],
                     errors=st["errors"], **({"reason": st["stopped"]} if "stopped" in st else {}))
            except LicenseError as e:
                step("école", ok=False, reason=str(e))
            except Exception as e:
                step("école", ok=False, reason=f"{type(e).__name__}: {e}")
            # 2. données
            try:
                man = D.build(self.journal, self._school, cfg=self.cfg, clock=self._clock)
            except Exception as e:
                step("données", ok=False, reason=f"{type(e).__name__}: {e}")
                return self._finish(rep, "échec données")
            step("données", ok=True, **{k: man[k] for k in ("train", "eval", "dpo", "origins", "with_tools")})
            # 3. seuil
            st = _read(self.state_path, {})
            total = man.get("base", man["train"] + man["eval"])     # avant augmentations
            new = total - st.get("trained_examples", 0)
            base, why_base = self.pick_base(st, total)
            if new < self.cfg["min_new_examples"] and why_base != "challenger":
                step("seuil", ok=True, reason=f"{new} nouveaux exemples, {self.cfg['min_new_examples']} requis")
                return self._finish(rep, "en attente de données")
            # 4. entraîner (depuis la base choisie : champion, réglage, ou challenger)
            run = T.plan(Path(man["dir"]), {**self.cfg, "base_model": base["model"],
                                            "base_license": base["license"]},
                         root=self.root, clock=self._clock)
            step("plan", ok=True, run=str(run), base=base["model"], why=why_base)
            if not execute:
                return self._finish(rep, "plan prêt (à sec)")
            res = self._trainer(run)
            step("entraînement", **res)
            if not res.get("ok"):
                return self._finish(rep, "échec entraînement")
            if why_base == "challenger":
                st.setdefault("challenged", {})[base["model"]] = total
            else:
                st["trained_examples"] = total
            st["last_run"] = str(run)
            _write(self.state_path, st)
            # 5. importer
            cand = f"{self.cfg['serve_model']}-candidate"
            self._phase("import")
            res = T.ollama_create(run, cand, runner=self._runner)
            step("import", **res)
            if not res["ok"]:
                return self._finish(rep, "échec import Ollama")
            # 6. évaluer
            rows = E.load(Path(man["dir"]) / "eval.jsonl")
            reps = self.cfg["eval_repeats"]
            cand_score = E.score(self._client_factory(cand), rows, reps, progress=self._exam("exam"))
            champ = _read(self.root / "champion.json", None)
            champ_score = E.score(self._client_factory(self.cfg["serve_model"]), rows, reps,
                                  progress=self._exam("duel")) if champ else None
            ok, why = E.gate(cand_score, champ_score, self.cfg["promote_margin"],
                             min_eval=self.cfg.get("min_eval", 30))
            # Banc scellé (jamais appris) : aucune régression critique ni recul essentiel permis.
            cases, decls = self._bench_cases(), self.journal.tools()
            bench_c = bench_h = None
            if cases:
                from matthew import bench as B
                bench_c = B.run(self._client_factory(cand), cases, decls)
                bench_h = B.run(self._client_factory(self.cfg["serve_model"]), cases, decls) if champ else None
                ok_b, why_b = B.gate(bench_c, bench_h)
                if ok and not ok_b:
                    ok, why = False, why_b
            # Banc de cognition (juge de mémoire, planificateur) : décide de sa place dans la cognition.
            try:
                from matthew import cogbench
                cog = cogbench.run(self._client_factory(cand))
            except Exception as e:
                cog = {"score": None, "error": f"{type(e).__name__}: {e}"}
            # Les points faibles du candidat orientent le prochain amorçage (remédiation).
            _write(self.root / "last_eval.json", {"at": stamp, "model": cand, **cand_score})
            brief = lambda b: {k: b.get(k) for k in ("composite", "per", "critical", "n")} if b else None
            step("évaluation", ok=True, candidate=cand_score, champion=champ_score, promote=ok, why=why,
                 cognition={k: cog.get(k) for k in ("score", "judge", "planner")},
                 bench={"candidate": brief(bench_c), "champion": brief(bench_h)})
            if not ok:
                return self._finish(rep, "candidat non promu")
            # 7. promouvoir — l'ancien champion d'abord archivé sous une étiquette immuable
            from matthew import rollback as R
            champions = R.Champions(self.root, self.cfg, runner=self._runner, clock=self._clock)
            if champ and not champions.history():
                try:
                    champions.archive(champ)
                except Exception as e:
                    step("archive", ok=False, reason=f"{type(e).__name__}: {e}"[:200])
            cp = self._runner(["ollama", "cp", cand, self.cfg["serve_model"]],
                              capture_output=True, text=True, timeout=600)
            if cp.returncode != 0:
                step("promotion", ok=False, reason=(cp.stderr or "")[-200:])
                return self._finish(rep, "échec promotion")
            _write(self.root / "champion.json", {"model": self.cfg["serve_model"], "since": stamp,
                                                 "run": str(run), "score": cand_score,
                                                 "cognition": {k: cog.get(k) for k in ("score", "judge", "planner", "n")},
                                                 "bench": brief(bench_c),
                                                 "base": base["model"], "base_license": base["license"]})
            step("promotion", ok=True)
            # La copie servie se comporte-t-elle comme le candidat évalué ? Sinon, retour arrière.
            try:
                h = R.health(self._client_factory(self.cfg["serve_model"]), cases, decls) if cases else None
                h = h if h and h.get("status") == "mesuré" else None
                entry = champions.archive(_read(self.root / "champion.json", {}), health=h)
                step("archive", ok=True, tag=entry["tag"])
                if h and bench_c and h["critical"] > bench_c["critical"]:
                    rb = champions.rollback("la copie servie ne se comporte pas comme le candidat évalué")
                    step("retour arrière", **rb)
                    return self._finish(rep, "retour arrière")
            except Exception as e:
                step("archive", ok=False, reason=f"{type(e).__name__}: {e}"[:200])
            # Direction 3 : le nouveau champion repasse chaque épreuve de spécialité, contre la
            # référence ; il ne garde que les tâches qu'il mérite (matthew/specialist.py).
            try:
                from matthew import specialist
                ref = self._reference() if self._reference else specialist.reference_client(self.cfg)
                sp = specialist.run(self._client_factory(self.cfg["serve_model"]), ref,
                                    champion=_read(self.root / "champion.json", {}), root=self.root)
                step("spécialités", ok=True, **{k: v["delegated"] for k, v in sp["results"].items()})
            except Exception as e:
                step("spécialités", ok=False, reason=f"{type(e).__name__}: {e}"[:200])
            return self._finish(rep, "nouveau champion")
        finally:
            self._unlock()

    def _bench_cases(self) -> list[dict]:
        if self._bench:
            return self._bench()
        from matthew import bench as B
        return B.load()

    def _health(self, step) -> None:
        """Début de nuit : le champion servi est-il toujours celui qu'on a promu ?"""
        champ = _read(self.root / "champion.json", None)
        try:
            cases = self._bench_cases()
            if not champ or not cases:
                return
            from matthew import rollback as R
            champions = R.Champions(self.root, self.cfg, runner=self._runner, clock=self._clock)
            if not champions.history():
                champions.archive(champ)          # champion d'avant ce module : archivé une fois
            res = R.check(champions, self._client_factory(self.cfg["serve_model"]), cases, self.journal.tools())
            step("santé", ok=res.get("action") != "retour arrière",
                 **{k: res.get(k) for k in ("status", "composite", "critical", "action", "rollback") if k in res})
        except Exception as e:
            step("santé", ok=False, reason=f"{type(e).__name__}: {e}"[:200])

    def pick_base(self, st: dict, total: int) -> tuple[dict, str]:
        """Le modèle de base de ce cycle. Par défaut celui du champion (un challenger
        qui a gagné devient la base), sinon le réglage. Un challenger (base_challengers :
        [{"model", "license"}]) est essayé une fois par palier de données, sur les MÊMES
        données : le juge compare deux bases à armes égales. Licence ouverte exigée.
        Les modèles ouverts suivent la pointe à ~4 mois (Epoch AI, 2026) : changer de
        base dès qu'un meilleur sort est le levier le moins cher qui existe."""
        champ = _read(self.root / "champion.json", None) or {}
        current = {"model": champ.get("base") or self.cfg["base_model"],
                   "license": champ.get("base_license") or self.cfg["base_license"]}
        tried = st.get("challenged", {})
        for c in self.cfg.get("base_challengers") or []:
            if c.get("model") == current["model"] or str(c.get("license", "")).lower() not in OPEN_LICENSES:
                continue
            # Réessayé seulement quand les données ont grandi de moitié depuis son essai.
            if c["model"] not in tried or total >= 1.5 * tried[c["model"]]:
                return {"model": c["model"], "license": c["license"].lower()}, "challenger"
        return current, "champion" if champ.get("base") else "réglage"

    def _phase(self, phase: str, **kw) -> None:
        """Le direct de la vitrine : prep → entraînement → export → import → exam → duel."""
        try:
            self._live({"phase": phase, **kw})
        except Exception:
            pass

    def _exam(self, phase: str):
        """Progression d'un examen pour le direct, écrite au plus toutes les 20 s (et à la fin)."""
        last = [0.0]

        def progress(done: int, total: int, right: int) -> None:
            if done == total or self._clock() - last[0] >= 20:
                last[0] = self._clock()
                self._phase(phase, done=done, total=total, right=right)
        return progress

    def _live(self, progress: dict) -> None:
        """Pendant l'entraînement : live.json pour la vitrine, puis publication en fond
        (sans rejouer l'examen : la carte graphique est prise). Au plus toutes les 5 min."""
        import threading
        live = self.root / "vitrine" / "live.json"
        live.parent.mkdir(parents=True, exist_ok=True)
        cur = _read(live, {})
        _write(live, {**progress, "since": cur.get("since") or datetime.now().strftime("%Y-%m-%d %H:%M"),
                      "at": self._clock()})
        if not self.cfg.get("vitrine_publish") or not self.cfg.get("vitrine_repo"):
            return

        def bg():
            try:
                from matthew import vitrine
                vitrine.flag("IA : entraînement en cours", self.root)
                vitrine.run(self.root, self.cfg, publisher=lambda: vitrine.publish(
                    Path(self.cfg["vitrine_repo"]), self.cfg, runner=self._runner, do_replay=False))
            except Exception:
                pass
        threading.Thread(target=bg, daemon=True).start()

    def _finish(self, rep: dict, outcome: str) -> dict:
        rep["outcome"] = outcome
        d = self.root / "reports"
        d.mkdir(parents=True, exist_ok=True)
        _write(d / f"{rep['at']}.json", rep)
        st = _read(self.state_path, {})
        st["last_cycle"] = {"at": rep["at"], "outcome": outcome}
        _write(self.state_path, st)
        if outcome != "occupé":
            (self.root / "vitrine" / "live.json").unlink(missing_ok=True)     # fin du direct
            self._vitrine(rep)
        return rep

    def _vitrine(self, rep: dict) -> None:
        """8. vitrine : rejouer l'examen, régénérer la page publique, pousser. Un échec
        (Ollama éteint, réseau, garde-fou de vie privée) est noté, jamais bloquant."""
        if not self.cfg.get("vitrine_publish") or not self.cfg.get("vitrine_repo"):
            return
        try:
            from matthew import vitrine
            vitrine.flag(f"IA : {rep.get('outcome', 'cycle')}", self.root)
            res = vitrine.run(self.root, self.cfg, publisher=lambda: vitrine.publish(
                Path(self.cfg["vitrine_repo"]), self.cfg, runner=self._runner))
        except Exception as e:
            res = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
        rep["steps"].append({"step": "vitrine", **{k: res.get(k) for k in ("ok", "pushed", "reason")}})
        _write(self.root / "reports" / f"{rep['at']}.json", rep)

    def status(self) -> dict:
        eps = corr = 0
        for r in self.journal.records():
            eps += r.get("kind") == "episode"
            corr += r.get("kind") == "correction"
        sch = self._school
        if sch is None:
            try:
                sch = self.school()
            except Exception:
                sch = None
        syn = list(D.synthetic_records(self.root))
        return {"name": NAME, "episodes": eps, "corrections": corr,
                "synthetic": len(syn), "synthetic_tools": len({r.get("target") for r in syn} - {"__chat__"}),
                "teacher": sch.stats() if sch else None,
                "state": _read(self.state_path, {}), "champion": _read(self.root / "champion.json", None),
                "tokens_today": usage_summary(self.root, datetime.now().strftime("%Y-%m-%d")),
                "tokens_total": usage_summary(self.root),
                "base_model": self.cfg["base_model"], "min_new_examples": self.cfg["min_new_examples"],
                "paused": (self.root / "PAUSED").exists(), "deps_missing": T.missing_deps(self.cfg)}


TASK = "Matthew flywheel"


def _plan(root: Path | None = None):
    root = root or data_dir()
    return (TASK, ["-m", "matthew.flywheel", "--execute"], root / "flywheel.log", root)


def wrapper(root: Path | None = None) -> Path:
    """Le script que lance le planificateur (run.cmd sous Windows, run.sh sous Linux)."""
    from matthew import sched
    name, argv, log, folder = _plan(root)
    return sched.script(name, argv, log, folder, stem="flywheel")


def next_run(runner=subprocess.run) -> dict | None:
    """La prochaine nuit, lue dans le planificateur du système (Planificateur de tâches, minuterie
    systemd ou crontab) : jamais une heure écrite en dur. {"at", "enabled"}, ou None sans tâche."""
    from matthew import sched
    return sched.next_run(TASK, runner=runner)


def schedule_command(hour: str = "02:30", root: Path | None = None) -> list[str]:
    """La commande qui pose la tâche de chaque nuit (affichée par --schedule, rien n'est posé)."""
    from matthew import sched
    name, argv, log, folder = _plan(root)
    out = sched.daily(name, hour, argv, log, folder, dry=True, stem="flywheel")
    return out["commands"][-1] if out.get("commands") else ["crontab", out["line"]]


def install(hour: str = "02:30", runner=subprocess.run) -> dict:
    from matthew import sched
    name, argv, log, folder = _plan()
    return sched.daily(name, hour, argv, log, folder, runner=runner, stem="flywheel")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="matthew.flywheel")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--schedule", action="store_true", help="affiche la commande qui pose la tâche (schtasks, systemd ou cron)")
    ap.add_argument("--install", action="store_true", help="pose la tâche planifiée (chaque nuit 02 h 30)")
    ap.add_argument("--budget", type=int)
    a = ap.parse_args(argv)
    if a.install:
        out = install()
        print(out.get("out") or out)
        return 0 if out["ok"] else 1
    if a.schedule:
        print(" ".join(schedule_command()))
        return 0
    fw = Flywheel()
    out = fw.status() if a.status else fw.cycle(execute=a.execute, budget=a.budget)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
