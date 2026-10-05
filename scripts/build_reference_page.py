"""Construit la page de référence HTML depuis docs/REFERENCE.md :
  docs/reference.html            document complet, hors ligne (dans l'archive)
  dist/reference_artifact.html   contenu seul (titre + style + corps), pour publication

    pip install markdown && python scripts/build_reference_page.py
"""

from __future__ import annotations

import html
import re
import sys
import time
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memoryaicm import __version__, model as M, tools  # noqa: E402

FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600'
         '&family=IBM+Plex+Sans+Condensed:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap">')

CSS = """
:root {
  --bg: #F5F7F4; --surface: #FFFFFF; --ink: #1B1F1D; --muted: #5E6B65; --line: #D2D9D4; --line-soft: #E4E9E5;
  --accent: #1F6F5E; --accent-ink: #145041; --accent-tint: rgba(31, 111, 94, 0.08);
  --journal: #A8690A; --journal-tint: rgba(168, 105, 10, 0.08);
  --index: #2C6FB5; --index-tint: rgba(44, 111, 181, 0.08);
  --adapter: #5B4FCF; --adapter-tint: rgba(91, 79, 207, 0.08);
  --ok: #1F7A4D; --no: #B42318; --code-bg: #EEF2EF;
  --cond: "IBM Plex Sans Condensed", "Arial Narrow", "Helvetica Neue", Arial, sans-serif;
  --sans: "IBM Plex Sans", "Helvetica Neue", Arial, sans-serif;
  --mono: "IBM Plex Mono", "SFMono-Regular", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0F1412; --surface: #161C19; --ink: #E3E9E5; --muted: #97A49D; --line: #2A3430; --line-soft: #202924;
    --accent: #3FCDB0; --accent-ink: #7FE0CC; --accent-tint: rgba(63, 205, 176, 0.10);
    --journal: #E3B44A; --journal-tint: rgba(227, 180, 74, 0.10);
    --index: #7FB4F2; --index-tint: rgba(127, 180, 242, 0.10);
    --adapter: #A79BFF; --adapter-tint: rgba(167, 155, 255, 0.10);
    --ok: #5CC48A; --no: #F97066; --code-bg: #1C2420;
  }
}
:root[data-theme="dark"] {
  --bg: #0F1412; --surface: #161C19; --ink: #E3E9E5; --muted: #97A49D; --line: #2A3430; --line-soft: #202924;
  --accent: #3FCDB0; --accent-ink: #7FE0CC; --accent-tint: rgba(63, 205, 176, 0.10);
  --journal: #E3B44A; --journal-tint: rgba(227, 180, 74, 0.10);
  --index: #7FB4F2; --index-tint: rgba(127, 180, 242, 0.10);
  --adapter: #A79BFF; --adapter-tint: rgba(167, 155, 255, 0.10);
  --ok: #5CC48A; --no: #F97066; --code-bg: #1C2420;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font-family: var(--sans); font-size: 14px; line-height: 1.55; -webkit-font-smoothing: antialiased; }
a { color: var(--accent-ink); text-decoration: none; }
a:hover, a:focus-visible { text-decoration: underline; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.shell { display: grid; grid-template-columns: 250px minmax(0, 1fr); gap: 0; max-width: 1360px; margin: 0 auto; }
@media (max-width: 900px) { .shell { grid-template-columns: minmax(0, 1fr); } .rail { display: none; } }

/* ---- rail ---- */
.rail { position: sticky; top: 0; align-self: start; height: 100vh; overflow-y: auto; padding: 22px 14px 22px 22px; border-right: 1px solid var(--line-soft); }
.rail .brand { font-family: var(--cond); font-weight: 700; font-size: 17px; letter-spacing: 0.02em; }
.rail .ver { font-family: var(--mono); font-size: 11px; color: var(--muted); margin-top: 2px; }
.rail nav { margin-top: 18px; }
.rail nav a { display: block; padding: 4px 8px; font-size: 12.5px; color: var(--ink); border-left: 2px solid transparent; }
.rail nav a.h3 { padding-left: 20px; font-size: 12px; color: var(--muted); }
.rail nav a:hover { background: var(--accent-tint); text-decoration: none; }
.rail nav a.active { border-left-color: var(--accent); color: var(--accent-ink); }
.rail nav a .n { font-family: var(--mono); font-size: 11px; color: var(--muted); margin-right: 6px; }

/* ---- header ---- */
.head { padding: 30px 40px 22px; border-bottom: 1px solid var(--line); }
.eyebrow { font-family: var(--cond); font-size: 11px; font-weight: 600; letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); }
h1 { margin: 6px 0 0; font-family: var(--cond); font-weight: 700; font-size: 38px; line-height: 1.05; letter-spacing: -0.01em; text-wrap: balance; }
.thesis { margin: 10px 0 0; font-family: var(--mono); font-size: 13px; color: var(--accent-ink); }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(128px, 1fr)); gap: 1px; margin-top: 20px; background: var(--line); border: 1px solid var(--line); }
.fact { background: var(--surface); padding: 10px 12px; }
.fact b { display: block; font-family: var(--cond); font-weight: 700; font-size: 24px; line-height: 1; font-variant-numeric: tabular-nums; }
.fact span { display: block; margin-top: 4px; font-size: 11px; color: var(--muted); letter-spacing: 0.04em; text-transform: uppercase; font-family: var(--cond); font-weight: 600; }
.fact.j b { color: var(--journal); } .fact.i b { color: var(--index); } .fact.a b { color: var(--adapter); } .fact.c b { color: var(--accent); }

/* ---- figure ---- */
figure.arch { margin: 0; padding: 18px 40px 10px; border-bottom: 1px solid var(--line); background: var(--surface); overflow-x: auto; }
.diag { display: block; width: 100%; min-width: 900px; height: auto; font-family: var(--mono); font-size: 11px; color: var(--ink); }
.diag text { fill: currentColor; }
.diag .band.j { fill: var(--journal-tint); stroke: var(--journal); } .diag .band.i { fill: var(--index-tint); stroke: var(--index); }
.diag .band.a { fill: var(--adapter-tint); stroke: var(--adapter); } .diag .band.c { fill: var(--accent-tint); stroke: var(--accent); }
.diag .band.n { fill: var(--surface); stroke: var(--line); }
.diag .bt { font-family: var(--cond); font-weight: 700; font-size: 12px; letter-spacing: 0.08em; }
.diag .bt.j { fill: var(--journal); } .diag .bt.i { fill: var(--index); } .diag .bt.a { fill: var(--adapter); } .diag .bt.c { fill: var(--accent); }
.diag .cm { fill: var(--muted); } .diag .sm { font-size: 10px; }
.diag .flow { fill: none; stroke: currentColor; stroke-width: 1.2; } .diag .flow.c { stroke: var(--accent); stroke-width: 1.5; }
.diag .box { fill: var(--surface); stroke: var(--line); }
figcaption { margin-top: 8px; font-size: 12px; color: var(--muted); min-width: 900px; }

/* ---- document ---- */
.doc { padding: 10px 40px 60px; max-width: 1000px; }
.doc > h1 { display: none; }
.doc > p:first-of-type { color: var(--muted); font-size: 13px; }
.doc h2 { margin: 44px 0 12px; padding-top: 18px; border-top: 1px solid var(--line); font-family: var(--cond); font-weight: 700; font-size: 24px; letter-spacing: -0.005em; text-wrap: balance; }
.doc h3 { margin: 26px 0 8px; font-family: var(--cond); font-weight: 600; font-size: 16px; letter-spacing: 0.02em; }
.doc h4 { margin: 18px 0 6px; font-family: var(--mono); font-weight: 600; font-size: 13px; color: var(--accent-ink); }
.doc p, .doc li { max-width: 78ch; }
.doc ul, .doc ol { padding-left: 22px; }
.doc li { margin: 3px 0; }
.doc hr { border: 0; height: 0; margin: 0; }
.doc code { font-family: var(--mono); font-size: 0.92em; background: var(--code-bg); padding: 1px 4px; border-radius: 2px; }
.doc pre { margin: 10px 0; padding: 12px 14px; background: var(--code-bg); border-left: 3px solid var(--line); overflow-x: auto; font-family: var(--mono); font-size: 12px; line-height: 1.5; }
.doc pre code { background: transparent; padding: 0; font-size: inherit; }
.doc blockquote { margin: 12px 0; padding: 8px 14px; border-left: 3px solid var(--accent); background: var(--accent-tint); }
.doc blockquote p { margin: 4px 0; }
.tbl { overflow-x: auto; margin: 10px 0 14px; border: 1px solid var(--line); background: var(--surface); }
.doc table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.doc th { text-align: left; padding: 7px 10px; font-family: var(--cond); font-weight: 600; font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); border-bottom: 1px solid var(--line); white-space: nowrap; background: var(--surface); }
.doc td { padding: 5px 10px; border-bottom: 1px solid var(--line-soft); vertical-align: top; }
.doc tr:last-child td { border-bottom: 0; }
.doc td code { white-space: nowrap; }
.doc strong { font-weight: 600; }
.foot { padding: 20px 40px 40px; border-top: 1px solid var(--line); font-size: 12px; color: var(--muted); font-family: var(--mono); }
@media (max-width: 640px) { .head, .doc, .foot, figure.arch { padding-left: 18px; padding-right: 18px; } h1 { font-size: 30px; } }
@media (prefers-reduced-motion: no-preference) { html { scroll-behavior: smooth; } }
"""

SVG = """
<svg class="diag" viewBox="0 0 1200 516" role="img" aria-label="Les clients (Claude par MCP, un LLM par la boucle d'outils, le hook Claude Code, le service HTTP, ACA) passent par les mêmes outils et la même politique d'écriture ; tout entre par le journal append-only ; l'index en est une projection ; l'adaptateur est compilé depuis l'index ; le sommeil consolide, teste, promeut ou annule ; le validateur vérifie les sorties.">
  <defs>
    <marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="currentColor"></path></marker>
    <marker id="arc" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill: var(--accent)"></path></marker>
  </defs>
  <!-- clients -->
  <rect class="band n" x="20" y="20" width="1160" height="86"></rect>
  <text class="bt" x="36" y="44">CLIENTS</text>
  <text class="sm cm" x="36" y="60">tout LLM</text>
  <text class="sm cm" x="36" y="74">propose</text>
  <g>
    <rect class="box" x="150" y="34" width="160" height="56"></rect><text x="230" y="56" text-anchor="middle">Claude Desktop · Code</text><text x="230" y="72" text-anchor="middle" class="sm cm">MCP stdio · hook</text>
    <rect class="box" x="322" y="34" width="190" height="56"></rect><text x="417" y="56" text-anchor="middle">OpenAI-compat. · Anthropic</text><text x="417" y="72" text-anchor="middle" class="sm cm">boucle d'outils (toolloop)</text>
    <rect class="box" x="524" y="34" width="160" height="56"></rect><text x="604" y="56" text-anchor="middle">modèle sans outils</text><text x="604" y="72" text-anchor="middle" class="sm cm">protocole texte</text>
    <rect class="box" x="696" y="34" width="130" height="56"></rect><text x="761" y="56" text-anchor="middle">service HTTP</text><text x="761" y="72" text-anchor="middle" class="sm cm">127.0.0.1:8765</text>
    <rect class="box" x="838" y="34" width="150" height="56"></rect><text x="913" y="56" text-anchor="middle">ACA / Echo-Core</text><text x="913" y="72" text-anchor="middle" class="sm cm">LongTermMemoryPort</text>
    <rect class="box" x="1000" y="34" width="166" height="56"></rect><text x="1083" y="56" text-anchor="middle">chat · say · agent</text><text x="1083" y="72" text-anchor="middle" class="sm cm">autonome, hors ligne</text>
  </g>
  <!-- router -->
  <line class="flow" x1="600" y1="106" x2="600" y2="132" marker-end="url(#ar)"></line>
  <rect class="band c" x="200" y="134" width="800" height="44"></rect>
  <text class="bt c" x="216" y="152">TOOLROUTER · 11 OUTILS · UNE SOURCE (tools.py) · MÊME RENDU POUR TOUS</text>
  <text class="sm" x="216" y="168">memory_recall · remember · ingest_external · forget · search · status · sleep · review · reactivate · history · timeline</text>
  <!-- policy -->
  <line class="flow" x1="400" y1="178" x2="400" y2="210" marker-end="url(#ar)"></line>
  <text class="sm" x="410" y="200">write</text>
  <rect class="box" x="200" y="212" width="400" height="90"></rect>
  <text class="bt" x="216" y="232">POLITIQUE D'ÉCRITURE</text>
  <text class="sm" x="216" y="250">porte src=user · INFER ⇒ skip · éphémère ⇒ skip</text>
  <text class="sm" x="216" y="264">ancrage ≥ 50 % dans le message verbatim</text>
  <text class="sm" x="216" y="278">conflit ⇒ merge ver++ · redit ⇒ renforcé · sensible ⇒ revue</text>
  <text class="sm" x="216" y="292">importance : explicite · ≥ 3 sessions · surprise · utilité</text>
  <!-- read -->
  <line class="flow" x1="800" y1="178" x2="800" y2="210" marker-end="url(#ar)"></line>
  <text class="sm" x="810" y="200">read</text>
  <rect class="box" x="620" y="212" width="380" height="90"></rect>
  <text class="bt" x="636" y="232">CONTEXTE · CALIBRAGE · VALIDATEUR</text>
  <text class="sm" x="636" y="250">Ctx = [ sys+profil · notes+étiquettes · vue rédigée ·</text>
  <text class="sm" x="636" y="264">DONNEE délimitée · prefs @fin · user ]</text>
  <text class="sm" x="636" y="278">calibrage : route note | accord échantillons | abstention</text>
  <text class="sm" x="636" y="292">validateur : secrets · instruction externe · valeur périmée</text>
  <!-- journal -->
  <line class="flow" x1="400" y1="302" x2="400" y2="326" marker-end="url(#ar)"></line>
  <text class="sm" x="410" y="318">append(Log)</text>
  <rect class="band j" x="20" y="328" width="540" height="150"></rect>
  <text class="bt j" x="36" y="348">JOURNAL · journal.sqlite · SOURCE DE VÉRITÉ</text>
  <text class="sm" x="36" y="368">append-only · triggers no UPDATE/DELETE · chaîne SHA-256 · FTS5 · WAL</text>
  <text class="sm" x="36" y="384">seq · id · ts · actor · type · payload · prev_hash · hash</text>
  <text class="sm" x="36" y="400">22 types : turn.user/assistant/redact · external.ingest · quarantine</text>
  <text class="sm" x="36" y="414">fact.write/merge/forget/off/on/use · review.queue/decide · sleep.run</text>
  <text class="sm" x="36" y="428">adapter.compile/promote/rollback · guard.block · selfcheck · model.check · policy.refuse</text>
  <text class="sm cm" x="36" y="450">verify() : chaîne intacte · search() : plein texte, y compris le désindexé</text>
  <text class="sm cm" x="36" y="466">« oublie x » = un événement de plus · rien n'est jamais détruit</text>
  <!-- index -->
  <line class="flow" x1="560" y1="400" x2="618" y2="400" marker-end="url(#ar)"></line>
  <text class="sm" x="589" y="392" text-anchor="middle">apply</text>
  <rect class="band i" x="620" y="328" width="280" height="150"></rect>
  <text class="bt i" x="636" y="348">INDEX · index.sqlite · PROJECTION</text>
  <text class="sm" x="636" y="368">{ f ∈ Log : f.on ∧ act(f) &gt; θ }</text>
  <text class="sm" x="636" y="384">act = ln Σ tᵢ^(−d) · rel = max(lex, sém)</text>
  <text class="sm" x="636" y="400">score = 1·rel + 0.35·act + 0.5·imp · top-8</text>
  <text class="sm" x="636" y="416">history(sujet) · timeline(jours) · prefs()</text>
  <text class="sm cm" x="636" y="450">rebuild() : identique depuis le journal</text>
  <text class="sm cm" x="636" y="466">vecteurs : n-grammes hachés | Ollama</text>
  <!-- adapter -->
  <line class="flow" x1="900" y1="400" x2="958" y2="400" marker-end="url(#ar)"></line>
  <text class="sm" x="929" y="392" text-anchor="middle">compile</text>
  <rect class="band a" x="960" y="328" width="220" height="150"></rect>
  <text class="bt a" x="976" y="348">ADAPTATEUR · vN</text>
  <text class="sm" x="976" y="368">profile.md (profil compilé)</text>
  <text class="sm" x="976" y="384">train.jsonl (+ générique)</text>
  <text class="sm" x="976" y="400">manifest.json · CURRENT</text>
  <text class="sm cm" x="976" y="434">promote ⇔ tests verts</text>
  <text class="sm cm" x="976" y="450">rollback sinon · versionné</text>
  <text class="sm cm" x="976" y="466">fait oublié ∉ W</text>
  <!-- sleep loop -->
  <path class="flow c" d="M 1070 478 V 494 H 290 V 480" marker-end="url(#arc)"></path>
  <text class="bt c" x="600" y="510" text-anchor="middle">SOMMEIL · sessions → dedup → conflits → abstraction → élagage → compile → tests → promote | rollback · auto : 25 tours / 6 h / 15 min d'inactivité</text>
</svg>
"""


def counts() -> dict:
    py = list((ROOT / "memoryaicm").rglob("*.py"))
    lines = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in py)
    tests = sum(len(re.findall(r"^def test_", p.read_text(encoding="utf-8"), re.M)) for p in (ROOT / "tests").glob("test_*.py"))
    events = len([n for n in dir(M) if n.startswith("EV_")])
    return {"tools": len(tools.TOOL_SPECS), "events": events, "tests": tests, "lines": lines, "files": len(py)}


def render_toc(tokens: list) -> str:
    out = []
    for t in tokens:
        name = html.escape(t["name"])
        m = re.match(r"^(\d+(?:\.\d+)?)\.?\s+(.*)$", t["name"])
        if m:
            name = f'<span class="n">{m.group(1)}</span>{html.escape(m.group(2))}'
        out.append(f'<a href="#{t["id"]}" class="h{t["level"]}">{name}</a>')
        for c in t.get("children", []):
            if c["level"] <= 3:
                cname = html.escape(c["name"])
                m = re.match(r"^(\d+(?:\.\d+)?)\.?\s+(.*)$", c["name"])
                if m:
                    cname = f'<span class="n">{m.group(1)}</span>{html.escape(m.group(2))}'
                out.append(f'<a href="#{c["id"]}" class="h3">{cname}</a>')
    return "\n".join(out)


def build() -> tuple[str, str]:
    md_text = (ROOT / "docs" / "REFERENCE.md").read_text(encoding="utf-8")
    md = markdown.Markdown(extensions=["tables", "fenced_code", "toc"], extension_configs={"toc": {"toc_depth": "2-3"}})
    body = md.convert(md_text)
    body = body.replace("<table>", '<div class="tbl"><table>').replace("</table>", "</table></div>")
    toc = render_toc(md.toc_tokens)
    c = counts()
    lines_fr = format(c["lines"], ",").replace(",", "\u202f")  # séparateur de milliers français (espace fine)
    date = time.strftime("%Y-%m-%d")
    inner_head = f"<title>memoryaicm Référence</title>\n{FONTS}\n<style>{CSS}</style>"
    inner_body = f"""
<div class="shell">
  <aside class="rail">
    <div class="brand">memoryaicm</div>
    <div class="ver">référence · v{__version__} · {date}</div>
    <nav id="toc">{toc}</nav>
  </aside>
  <div>
    <header class="head">
      <div class="eyebrow">Mémoire tri-couche · neutre LLM · référence complète · v{__version__}</div>
      <h1>memoryaicm — la mémoire qui n'efface jamais, pour n'importe quel modèle</h1>
      <p class="thesis">Log ⊇ Index ⊇ Adapter · oubli = désindexer · un LLM propose, la politique décide · adhésion 100 % ∈ gardes</p>
      <div class="facts">
        <div class="fact c"><b>{c['tools']}</b><span>outils, une source</span></div>
        <div class="fact j"><b>{c['events']}</b><span>types d'événements</span></div>
        <div class="fact i"><b>{c['tests']}</b><span>tests verts</span></div>
        <div class="fact a"><b>{lines_fr}</b><span>lignes · {c['files']} modules</span></div>
        <div class="fact"><b>0</b><span>dépendance</span></div>
        <div class="fact"><b>≥ 3.10</b><span>python</span></div>
      </div>
    </header>
    <figure class="arch">
      {SVG}
      <figcaption>Tout client passe par les mêmes outils et la même politique ; tout entre par le journal ; l'index et l'adaptateur en sont des projections ; le sommeil consolide et se teste ; le validateur vérifie chaque sortie.</figcaption>
    </figure>
    <article class="doc">
      {body}
    </article>
    <footer class="foot">memoryaicm v{__version__} · docs/REFERENCE.md généré par scripts/gen_docs.py · page construite par scripts/build_reference_page.py · {date}</footer>
  </div>
</div>
<script>
(function () {{
  var links = Array.prototype.slice.call(document.querySelectorAll('#toc a'));
  var targets = links.map(function (a) {{ return document.getElementById(a.getAttribute('href').slice(1)); }});
  function update() {{
    var y = window.scrollY + 90, best = 0;
    for (var i = 0; i < targets.length; i++) {{ if (targets[i] && targets[i].offsetTop <= y) best = i; }}
    links.forEach(function (a, i) {{ a.classList.toggle('active', i === best); }});
  }}
  window.addEventListener('scroll', update, {{ passive: true }});
  update();
}})();
</script>
"""
    artifact = inner_head + "\n" + inner_body
    full = ('<!doctype html>\n<html lang="fr">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            + inner_head + "\n</head>\n<body>" + inner_body + "</body>\n</html>\n")
    return artifact, full


def main() -> None:
    artifact, full = build()
    (ROOT / "docs" / "reference.html").write_text(full, encoding="utf-8")
    (ROOT / "dist").mkdir(exist_ok=True)
    (ROOT / "dist" / "reference_artifact.html").write_text(artifact, encoding="utf-8")
    print(f"docs/reference.html : {len(full)} caractères · dist/reference_artifact.html : {len(artifact)} caractères")


if __name__ == "__main__":
    main()
