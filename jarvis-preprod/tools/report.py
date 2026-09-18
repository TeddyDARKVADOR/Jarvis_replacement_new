"""
tools/report.py — transforme results.jsonl en report.json + report.html.

UNE DECISION QUI COMPTE PLUS QUE LA MISE EN PAGE
    PHYSICAL_ONLY n'est pas compte comme un SKIP et n'est jamais melange aux
    autres. Un SKIP dit « pas execute cette fois » ; PHYSICAL_ONLY dit « cet
    emulateur ne pourra jamais le prouver ». Les confondre rendrait possible
    une campagne toute verte qu'on lirait comme « JARVIS est valide », alors
    que la batterie, le Bluetooth, le micro et HyperOS n'ont pas ete touches.

    Le rapport affiche donc deux totaux distincts et le HTML met la liste
    physique dans son propre bloc, avec son propre avertissement.
"""
from __future__ import annotations

import html
import json
import sys
import time
from pathlib import Path

LATEST = Path(__file__).resolve().parent.parent / "reports" / "latest"

_ORDER = {"FAIL": 0, "PASS": 1, "SKIP": 2, "PHYSICAL_ONLY": 3}
_COLOUR = {
    "PASS": "#3FB950", "FAIL": "#F85149",
    "SKIP": "#8B949E", "PHYSICAL_ONLY": "#D29922",
}


def load() -> list[dict]:
    path = LATEST / "results.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def build(results: list[dict], server_mode: str = "?") -> dict:
    suites: dict[str, list[dict]] = {}
    for r in results:
        suites.setdefault(r.get("suite", "?"), []).append(r)

    counts = {k: 0 for k in _ORDER}
    for r in results:
        counts[r.get("status", "SKIP")] = counts.get(r.get("status", "SKIP"), 0) + 1

    suite_rows = []
    for name, checks in sorted(suites.items()):
        failed = sum(1 for c in checks if c["status"] == "FAIL")
        physical = sum(1 for c in checks if c["status"] == "PHYSICAL_ONLY")
        real = [c for c in checks if c["status"] != "PHYSICAL_ONLY"]
        if failed:
            verdict = "FAIL"
        elif not real:
            verdict = "PHYSICAL_ONLY"
        elif all(c["status"] == "SKIP" for c in real):
            verdict = "SKIP"
        else:
            verdict = "PASS"
        suite_rows.append({
            "suite": name, "verdict": verdict, "checks": checks,
            "physical": physical,
        })

    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "server_mode": server_mode,
        "counts": counts,
        # Une campagne est verte quand rien n'a echoue ET qu'au moins une chose
        # a reellement ete verifiee. Zero test execute n'est pas un succes.
        "green": counts["FAIL"] == 0 and counts["PASS"] > 0,
        "suites": suite_rows,
    }


def to_html(report: dict) -> str:
    rows = []
    for s in report["suites"]:
        colour = _COLOUR.get(s["verdict"], "#8B949E")
        rows.append(
            f'<tr><td class="s">{html.escape(s["suite"])}</td>'
            f'<td><span class="b" style="background:{colour}22;color:{colour}">'
            f'{s["verdict"]}</span></td>'
            f'<td class="n">{len(s["checks"])}</td></tr>'
        )
        for c in sorted(s["checks"], key=lambda c: _ORDER.get(c["status"], 9)):
            cc = _COLOUR.get(c["status"], "#8B949E")
            rows.append(
                f'<tr class="d"><td class="c">{html.escape(c["check"])}</td>'
                f'<td><span style="color:{cc}">{c["status"]}</span></td>'
                f'<td class="det">{html.escape(c.get("detail", ""))}</td></tr>'
            )

    phys = [c for s in report["suites"] for c in s["checks"]
            if c["status"] == "PHYSICAL_ONLY"]
    phys_html = ""
    if phys:
        items = "".join(
            f'<li><b>{html.escape(c["check"])}</b> — {html.escape(c.get("detail", ""))}</li>'
            for c in phys)
        phys_html = f"""
  <div class="warn">
    <h2>Non validé — nécessite le matériel réel</h2>
    <p>Ces points ne sont pas « à faire » : un émulateur ne peut pas les prouver.
       Aucune campagne verte ne les couvre.</p>
    <ul>{items}</ul>
  </div>"""

    c = report["counts"]
    banner = "VERT" if report["green"] else "ROUGE"
    banner_colour = "#3FB950" if report["green"] else "#F85149"

    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>JARVIS préprod</title>
<style>
 :root {{ color-scheme: dark; }}
 body {{ background:#0D1117; color:#C9D1D9; font:14px/1.6 ui-monospace,Menlo,Consolas,monospace;
        margin:0; padding:32px 16px; }}
 .wrap {{ max-width:960px; margin:0 auto; }}
 h1 {{ font-size:20px; margin:0 0 4px; }}
 .meta {{ color:#8B949E; font-size:12px; margin-bottom:24px; }}
 .banner {{ display:inline-block; padding:6px 14px; border-radius:6px; font-weight:700;
           background:{banner_colour}22; color:{banner_colour}; margin-bottom:20px; }}
 table {{ width:100%; border-collapse:collapse; }}
 td {{ padding:6px 10px; border-bottom:1px solid #21262D; vertical-align:top; }}
 .s {{ font-weight:700; }}
 .d td {{ border-bottom:1px solid #161B22; }}
 .c {{ padding-left:28px; color:#8B949E; }}
 .det {{ color:#6E7681; font-size:12px; }}
 .n {{ text-align:right; color:#6E7681; }}
 .b {{ padding:2px 8px; border-radius:4px; font-size:12px; font-weight:700; }}
 .warn {{ margin-top:32px; padding:16px 20px; border:1px solid #D2992244;
         background:#D2992211; border-radius:8px; }}
 .warn h2 {{ font-size:15px; margin:0 0 6px; color:#D29922; }}
 .warn p {{ color:#8B949E; font-size:12px; margin:0 0 10px; }}
 .warn li {{ font-size:12px; color:#8B949E; }}
 @media (max-width:640px) {{ body {{ padding:20px 16px; }} .det {{ display:none; }} }}
</style></head>
<body><div class="wrap">
  <h1>JARVIS — préproduction Android</h1>
  <div class="meta">{report["generated"]} &middot; serveur en mode
      <b>{html.escape(report["server_mode"])}</b> &middot; émulateur, pas un Redmi</div>
  <div class="banner">{banner}</div>
  <div class="meta">{c["PASS"]} PASS &middot; {c["FAIL"]} FAIL &middot; {c["SKIP"]} SKIP
      &middot; {c["PHYSICAL_ONLY"]} physiques non couverts</div>
  <table>{"".join(rows)}</table>
  {phys_html}
</div></body></html>"""


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "?"
    results = load()
    report = build(results, mode)
    LATEST.mkdir(parents=True, exist_ok=True)
    (LATEST / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (LATEST / "report.html").write_text(to_html(report), encoding="utf-8")

    c = report["counts"]
    print()
    print(f"  {c['PASS']} PASS   {c['FAIL']} FAIL   {c['SKIP']} SKIP   "
          f"{c['PHYSICAL_ONLY']} physiques non couverts")
    print(f"  {LATEST / 'report.html'}")
    return 0 if report["green"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
