"""
capabilities/selftest.py — le graphe dit-il vrai sur le code ?

    python -m capabilities.selftest

Sans clé, sans réseau, sans fenêtre. Ce qui est vérifié :

  1  chaque graphe se lit, porte le bon schéma, son id, un titre et une date
  2  dans chaque graphe : ids uniques, genres, états et relations connus, une
     seule racine, arêtes vers des nœuds existants, aucun nœud isolé
  3  chaque fichier cité existe
  4  chaque lien vers un sous-graphe mène quelque part, et chaque sous-graphe
     est atteignable depuis le graphe général
  5  LES OUTILS : l'ensemble des outils du graphe général est exactement celui
     que le code déclare (main.py, actions/, plugins/), lu dans le source sans
     rien importer. Un outil ajouté sans son nœud fait échouer ce contrôle.
  6  chaque README remplacé par un graphe a bien disparu, et aucun README de
     dossier n'est revenu (seul readme.md, la page du dépôt, reste)
  7  le paquet n'importe rien de main.py, du cœur, du serveur ni des clients
  8  la recherche trouve ce qu'on attend, et l'outil répond sans HUD comme avec
  9  le chemin jusqu'au HUD existe des deux côtés (Oracle et ce PC)
 10  CLAUDE.md porte la règle de mise à jour
 11  chaque outil a une sonde de vérification ou une suite qui le prouve
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import capabilities as cap

REPO = cap.REPO
_results: list[tuple[str, bool, str]] = []


def check(name: str):
    def deco(fn):
        try:
            detail = fn() or ""
            _results.append((name, True, str(detail)))
        except AssertionError as e:
            _results.append((name, False, str(e)))
        except Exception as e:  # un contrôle qui plante est un échec, pas un crash du banc
            _results.append((name, False, f"{type(e).__name__}: {e}"))
        return fn
    return deco


def _graphs() -> dict[str, dict]:
    return cap.load_all()


# ── 1-2. forme ───────────────────────────────────────────────────────────────

@check("1  chaque graphe se lit : schéma, id, titre, date")
def _shape():
    gs = _graphs()
    assert cap.GENERAL in gs, "pas de graphe général"
    today = date.today().isoformat()
    for gid, g in gs.items():
        assert g.get("schema") == cap.SCHEMA, f"{gid}: schéma {g.get('schema')!r}"
        assert g.get("id") == gid, f"{gid}: id interne {g.get('id')!r}"
        assert g.get("title") and g.get("summary"), f"{gid}: titre ou résumé manquant"
        upd = str(g.get("updated", ""))
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", upd), f"{gid}: date {upd!r}"
        assert upd <= today, f"{gid}: daté du futur ({upd})"
        assert isinstance(g.get("replaces", []), list), f"{gid}: replaces doit être une liste"
    return f"{len(gs)} graphes"


@check("2  nœuds et arêtes cohérents dans chaque graphe")
def _coherence():
    total_n = total_e = 0
    for gid, g in _graphs().items():
        ids = [n.get("id") for n in g["nodes"]]
        dup = {i for i in ids if ids.count(i) > 1}
        assert not dup, f"{gid}: ids en double {dup}"
        roots = [n["id"] for n in g["nodes"] if n.get("kind") == "racine"]
        assert len(roots) == 1, f"{gid}: {len(roots)} racines"
        for n in g["nodes"]:
            assert n.get("label") and n.get("summary"), f"{gid}/{n.get('id')}: libellé ou résumé manquant"
            assert n.get("kind") in cap.KINDS, f"{gid}/{n['id']}: genre {n.get('kind')!r}"
            assert n.get("status") in (None, *cap.STATUSES), f"{gid}/{n['id']}: état {n.get('status')!r}"
            assert isinstance(n.get("details", []), list), f"{gid}/{n['id']}: details doit être une liste"
        linked = set()
        for e in g.get("edges", []):
            assert isinstance(e, list) and len(e) == 3, f"{gid}: arête mal formée {e}"
            a, b, rel = e
            assert a in ids and b in ids, f"{gid}: arête vers un nœud inconnu {e}"
            assert rel in cap.RELATIONS, f"{gid}: relation {rel!r}"
            linked |= {a, b}
        alone = set(ids) - linked
        assert not alone, f"{gid}: nœuds isolés {sorted(alone)}"
        total_n += len(ids)
        total_e += len(g.get("edges", []))
    return f"{total_n} nœuds, {total_e} arêtes"


# ── 3-4. fichiers et sous-graphes ────────────────────────────────────────────

@check("3  chaque fichier cité existe")
def _files():
    missing, n = [], 0
    for gid, g in _graphs().items():
        for node in g["nodes"]:
            for f in node.get("files", []):
                n += 1
                if not (REPO / f).exists():
                    missing.append(f"{gid}/{node['id']}: {f}")
    assert not missing, "; ".join(missing[:8])
    return f"{n} références"


@check("4  liens vers les sous-graphes, et chaque sous-graphe atteignable")
def _subgraphs():
    gs = _graphs()
    for gid, g in gs.items():
        for n in g["nodes"]:
            if n.get("graph"):
                assert n["graph"] in gs, f"{gid}/{n['id']}: graphe {n['graph']!r} inconnu"
    seen, todo = {cap.GENERAL}, [cap.GENERAL]
    while todo:
        for n in gs[todo.pop()]["nodes"]:
            if n.get("graph") and n["graph"] not in seen:
                seen.add(n["graph"])
                todo.append(n["graph"])
    orphans = set(gs) - seen
    assert not orphans, f"sous-graphes inaccessibles depuis le général : {sorted(orphans)}"
    return f"{len(gs) - 1} sous-graphes atteignables"


# ── 5. les outils ────────────────────────────────────────────────────────────

def _literal_name(tree: ast.Module, var: str) -> str | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == var for t in node.targets):
            if isinstance(node.value, ast.Dict):
                for k, v in zip(node.value.keys, node.value.values):
                    if isinstance(k, ast.Constant) and k.value == "name" and isinstance(v, ast.Constant):
                        return v.value
    return None


def declared_tools() -> dict[str, str]:
    """Nom d'outil → fichier qui le déclare, lu dans le source sans importer."""
    out: dict[str, str] = {}
    tree = ast.parse((REPO / "main.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "TOOL_DECLARATIONS" for t in node.targets):
            for d in ast.literal_eval(node.value):
                out[d["name"]] = "main.py"
    for folder, var in (("actions", "TOOL"), ("plugins", "PLUGIN")):
        for f in sorted((REPO / folder).glob("*.py")):
            if f.name.startswith("_") or f.name.endswith("_selftest.py"):
                continue
            name = _literal_name(ast.parse(f.read_text(encoding="utf-8")), var)
            if name:
                out[name] = f"{folder}/{f.name}"
    return out


@check("5  le graphe général connaît exactement les outils du code")
def _tools():
    code = declared_tools()
    nodes = [n for n in cap.load(cap.GENERAL)["nodes"] if n.get("tool")]
    graph = {n["tool"] for n in nodes}
    assert all(n["kind"] == "outil" for n in nodes), "un nœud avec « tool » n'est pas de genre outil"
    missing = sorted(set(code) - graph)
    ghost = sorted(graph - set(code))
    assert not missing, ("outils sans nœud dans graphs/general.json : "
                         + ", ".join(f"{t} ({code[t]})" for t in missing))
    assert not ghost, f"outils du graphe absents du code : {ghost}"
    for n in nodes:
        assert code[n["tool"]] in n.get("files", []), (
            f"{n['id']}: devrait citer {code[n['tool']]}, le fichier qui le déclare")
    return f"{len(code)} outils, tous dans le graphe"


# ── 6. les README remplacés ──────────────────────────────────────────────────

@check("6  les README remplacés ont disparu, aucun README de dossier n'est revenu")
def _readmes():
    replaced = [r for g in _graphs().values() for r in g.get("replaces", [])]
    back = [r for r in replaced if (REPO / r).exists()]
    assert not back, f"remplacés par un graphe mais toujours là : {back}"
    try:
        tracked = subprocess.run(["git", "ls-files", "*README.md", "*readme.md"], cwd=REPO,
                                 capture_output=True, text=True, timeout=20).stdout.split()
    except Exception:
        tracked = [str(p.relative_to(REPO)).replace("\\", "/") for p in REPO.rglob("README.md")]
    extra = [t for t in tracked if t.lower() != "readme.md"]
    assert not extra, f"README de dossier : {extra} — en faire un graphe dans capabilities/graphs/"
    return f"{len(replaced)} documents remplacés"


# ── 7. le contrat V2 ─────────────────────────────────────────────────────────

@check("7  capabilities/ n'importe ni le cœur, ni le serveur, ni les clients")
def _imports():
    forbidden = ("main", "core", "server", "dashboard", "actions", "plugins", "memory",
                 "client_desktop", "local_hud", "cognition", "context")
    bad = []
    for f in (REPO / "capabilities").glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                mods = [node.module]
            bad += [f"{f.name}: {m}" for m in mods if m.split(".")[0] in forbidden]
    assert not bad, f"imports interdits : {bad}"
    return "rien de JARVIS n'est importé"


# ── 8. recherche et outil ────────────────────────────────────────────────────

@check("8  la recherche trouve, et l'outil répond avec ou sans HUD")
def _tool_behaviour():
    hits = cap.find("recherche web")
    assert hits and any(h.get("tool") == "web_search" for h in hits[:3]), [h["id"] for h in hits[:3]]
    assert cap.find("Oracle"), "Oracle introuvable"
    assert cap.find("xqzv wjyk") == [], "une requête absurde a trouvé quelque chose"

    import importlib.util
    spec = importlib.util.spec_from_file_location("plugins.capabilities", REPO / "plugins" / "capabilities.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    found = mod.run({"action": "find", "query": "mot d'éveil"})
    assert "Hey Jarvis" in found or "éveil" in found, found[:120]
    assert "web_search" in mod.run({"query": "chercher sur internet"}), "find par défaut"
    assert "Voix et session" in mod.run({"action": "list"}), "list"

    class _UI:
        shown: list = []

        def show_graph(self, graph, focus=""):
            self.shown.append((graph, focus))

    ui = _UI()
    said = mod.run({"action": "show", "graph": "server"}, player=ui)
    assert ui.shown == [("server", "")], ui.shown
    assert "HUD" in said, said
    mod.run({"action": "show", "focus": "Code Studio"}, player=ui)
    assert ui.shown[-1][1], "focus non résolu en nœud"
    assert "No graph named" in mod.run({"action": "show", "graph": "inexistant"}, player=ui)
    assert "cannot display" in mod.run({"action": "show"}, player=object())
    return "find, list, show (avec et sans HUD)"


# ── 9. jusqu'au HUD ──────────────────────────────────────────────────────────

@check("9  show_graph va jusqu'au HUD, en ligne comme en local")
def _hud_path():
    src = {p: (REPO / p).read_text(encoding="utf-8") for p in (
        "server/headless_ui.py", "local_hud.py", "client_desktop/net.py", "client_desktop/state.py",
        "client_desktop/app.py", "client_desktop/protocol.py", "client_desktop/hud/window.py",
        "client_desktop/hud/web/js/main.js", "client_desktop/hud/web/index.html")}
    assert "def show_graph" in src["server/headless_ui.py"], "HeadlessUI.show_graph"
    assert '"show_graph"' in src["server/headless_ui.py"], "événement show_graph"
    assert "def show_graph" in src["local_hud.py"] and 'kind == "graph"' in src["local_hud.py"], "local_hud"
    assert 'EV_SHOW_GRAPH = "show_graph"' in src["client_desktop/protocol.py"], "protocole"
    assert "P.EV_SHOW_GRAPH" in src["client_desktop/net.py"], "réception en ligne"
    assert "def request_graph" in src["client_desktop/state.py"], "store"
    assert 'kind == "graph"' in src["client_desktop/app.py"], "contrôleur en ligne"
    assert "def show_graph" in src["client_desktop/hud/window.py"], "HudWindow.show_graph"
    assert "showGraph" in src["client_desktop/hud/web/js/main.js"], "HUD.showGraph"
    assert 'id="graph"' in src["client_desktop/hud/web/index.html"], "panneau"
    assert 'id="btn-graph"' not in src["client_desktop/hud/web/index.html"], "le panneau ne doit pas avoir de bouton"
    return "Oracle → /ws → HUD, et ce PC → store → HUD"


# ── 11. chaque outil a sa vérification ───────────────────────────────────────

@check("11 chaque outil a une sonde (capabilities.verify) ou une suite qui le prouve")
def _probes():
    from capabilities import verify
    covered = set(verify.PROBES) | {t for tools in verify.SUITES.values() for t in tools}
    missing = sorted(set(declared_tools()) - covered)
    ghost = sorted(covered - set(declared_tools()))
    assert not missing, f"outils sans vérification : {missing} — ajouter une sonde dans verify.PROBES"
    assert not ghost, f"sondes d'outils qui n'existent plus : {ghost}"
    real = sum(1 for kind, _ in verify.PROBES.values() if kind == "reel")
    return f"{len(covered)} outils couverts, dont {real} par une sonde réelle"


# ── 10. la règle ─────────────────────────────────────────────────────────────

@check("10 CLAUDE.md demande la mise à jour du graphe en fin de session")
def _claude_md():
    text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    assert "capabilities/graphs" in text and "fin de session" in text.lower(), "règle absente"
    return "règle présente"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    width = max(len(n) for n, _, _ in _results)
    failed = 0
    print()
    for name, ok, detail in _results:
        failed += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<{width}}  {detail}")
    print(f"\n  {len(_results) - failed}/{len(_results)} passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
