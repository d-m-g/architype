"""Build the benchmark HTML from run logs; the first name=log is our system, the rest are compared against it.

  uv run python eval.py --limit 300 --out runs/bench-pipeline.jsonl
  uv run --env-file .env --extra brain python -m blueprints.brain.baseline --model gemini-4-carbon
  uv run python benchmarks/make_report.py benchmarks/gemini-4-vs-pipeline.html \\
      "Our pipeline=runs/bench-pipeline.jsonl" "gemini-4-carbon=runs/<time>-plain-gemini-4-carbon.jsonl" ...
"""
import html
import json
import statistics
import sys
from collections import defaultdict

out, *specs = sys.argv[1:]
# Report by file type, not dataset name: what the question points at, plus the sibling file the parser reads.
FILETYPE = {"cubicasa5k": "PNG raster plan", "swiss_dwellings": "PDF plan + DXF", "ifc_models": "PDF plan + IFC model",
            "ifc_bench": "IFC model", "opening_design": "PDF schedule sheet"}
runs = {}
for s in specs:
    name, path = s.split("=", 1)
    runs[name] = [json.loads(l) for l in open(path)]
    for r in runs[name]:
        r["source"] = FILETYPE.get(r["source"], r["source"])
names = list(runs)
OURS = names[0]


def stats(rows):
    sc = [r for r in rows if r["correct"] is not None]
    n = len(sc)
    right = sum(r["correct"] for r in sc)
    return dict(n=n, acc=right / n if n else None, right=right,
                usd1k=1000 * sum(r["cost_usd"] for r in rows) / len(rows) if rows else 0,
                ms=statistics.median(r["ms"] for r in rows) if rows else 0,
                err=sum(1 for r in rows if r["error"]),
                answered=sum(1 for r in sc if r["pred"] is not None))


def group(rows, key):
    g = defaultdict(list)
    for r in rows:
        g[key(r)].append(r)
    return g


overall = {m: stats(rows) for m, rows in runs.items()}
sources = sorted({r["source"] for rs in runs.values() for r in rs}, key=lambda s: -sum(r["source"] == s for r in runs[OURS]))
types = sorted({r["type"] for rs in runs.values() for r in rs}, key=lambda t: -sum(r["type"] == t for r in runs[OURS]))
by_src = {m: {k: stats(v) for k, v in group(rows, lambda r: r["source"]).items()} for m, rows in runs.items()}
by_type = {m: {k: stats(v) for k, v in group(rows, lambda r: r["type"]).items()} for m, rows in runs.items()}

# Head to head: same question keys.
key = lambda r: (r["path"], r["question"])
ours = {key(r): r for r in runs[OURS]}
h2h = {}
for m in names[1:]:
    w = l = both = none = 0
    for r in runs[m]:
        o = ours.get(key(r))
        if not o or o["correct"] is None:
            continue
        a, b = bool(o["correct"]), bool(r["correct"])
        w += a and not b; l += b and not a; both += a and b; none += not a and not b
    h2h[m] = (w, l, both, none)

# Calibration of our system.
buckets = [(0, .5), (.5, .8), (.8, .95), (.95, 1.01)]
calib = []
for lo, hi in buckets:
    rs = [r for r in runs[OURS] if r["correct"] is not None and r["pred"] is not None and lo <= r["confidence"] < hi]
    calib.append((lo, min(hi, 1), len(rs), sum(r["correct"] for r in rs) / len(rs) if rs else None))
tiers = defaultdict(int)
for r in runs[OURS]:
    tiers[r["tier"]] += 1

# Examples: ours right, best Gemini wrong, and the reverse.
best = max(names[1:], key=lambda m: overall[m]["acc"])
bestrows = {key(r): r for r in runs[best]}
wins, losses = [], []
for k, o in ours.items():
    g = bestrows.get(k)
    if not g or o["correct"] is None:
        continue
    if o["correct"] and not g["correct"]:
        wins.append((o, g))
    elif g["correct"] and not o["correct"]:
        losses.append((o, g))

e = html.escape
pct = lambda x: "–" if x is None else f"{100 * x:.1f}%"
COLORS = {n: f"var(--s{i})" for i, n in enumerate(names)}


def bars(values, fmt=pct, maxv=1.0):
    """Horizontal bars, one per model."""
    out = []
    for m in names:
        v = values.get(m)
        w = 0 if v is None else 100 * v / maxv
        out.append(f'<div class="bar"><span class="bl">{e(m)}</span><span class="track"><span class="fill" style="width:{w:.1f}%;background:{COLORS[m]}"></span></span><span class="bv">{fmt(v)}</span></div>')
    return "".join(out)


def table(groups, rows_by_model, label):
    head = "".join(f"<th>{e(m)}</th>" for m in names)
    body = ""
    for gname in groups:
        n = rows_by_model[OURS].get(gname, {}).get("n", 0)
        accs = {m: rows_by_model[m].get(gname, {}).get("acc") for m in names}
        top = max((a for a in accs.values() if a is not None), default=None)
        cells = "".join(f'<td class="{"top" if a is not None and a == top else ""}">{pct(a)}</td>' for a in accs.values())
        body += f"<tr><th scope=row>{e(gname)}<small>n={n}</small></th>{cells}</tr>"
    return f'<div class="scroll"><table><thead><tr><th>{label}</th>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def example(o, g):
    return (f'<li><p class="q">{e(o["question"])}</p><p class="meta">{e(o["source"])} · <code>{e(o["path"].split("/")[-1])}</code></p>'
            f'<dl><dt>gold</dt><dd>{e(str(o["gold"]))} {e(o["gold_unit"] or "")}</dd>'
            f'<dt>{e(OURS)}</dt><dd class="{"ok" if o["correct"] else "bad"}">{e(str(o["pred"]))} {e(o["pred_unit"] or "")}</dd>'
            f'<dt>{e(best)}</dt><dd class="{"ok" if g["correct"] else "bad"}">{e(str(g["pred"]))} {e(g["pred_unit"] or "")}</dd></dl></li>')


summary_rows = "".join(
    f'<tr><th scope=row><i class="dot" style="background:{COLORS[m]}"></i>{e(m)}</th><td>{pct(s["acc"])}</td><td>{s["right"]}/{s["n"]}</td>'
    f'<td>${s["usd1k"]:.2f}</td><td>{s["ms"] / 1000:.1f} s</td><td>{s["err"]}</td></tr>' for m, s in overall.items())
h2h_rows = "".join(
    f'<tr><th scope=row>{e(m)}</th><td class="ok">{w}</td><td class="bad">{l}</td><td>{b}</td><td>{n}</td></tr>' for m, (w, l, b, n) in h2h.items())
calib_rows = "".join(f"<tr><th scope=row>{lo:.2f}–{hi:.2f}</th><td>{n}</td><td>{pct(a)}</td></tr>" for lo, hi, n, a in calib)
tier_txt = ", ".join(f"{e(str(t))} {100 * n / len(runs[OURS]):.0f}%" for t, n in sorted(tiers.items(), key=lambda x: -x[1]))
maxcost = max(s["usd1k"] for s in overall.values()) or 1
o_acc, b_acc = overall[OURS]["acc"], overall[best]["acc"]
cost_ratio = overall[best]["usd1k"] / overall[OURS]["usd1k"] if overall[OURS]["usd1k"] else None
lede = (f"On the same {overall[OURS]['n']} held-out test questions, our pipeline scores <b>{pct(o_acc)}</b> against "
        f"<b>{pct(b_acc)}</b> for the best plain Gemini 4 model ({e(best)}), at "
        + (f"<b>{cost_ratio:,.0f}× lower</b> cost per question." if cost_ratio else "<b>zero</b> model cost."))

page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blueprint QA Benchmark</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&family=Archivo:wght@600;800&display=swap">
<style>
/* Layout: single drafting-sheet column, title block up top, numbered sheets below */
:root {{
  --bg:#f3f5f7; --paper:#ffffff; --fg:#17202a; --muted:#5d6b7a; --line:#d5dde5; --accent:#1f5fa8;
  --ok:#1d7a46; --bad:#b3362c;
  --s0:#1f5fa8; --s1:#c27c0e; --s2:#7a5cbf; --s3:#2e8f86;
  --display:"Archivo",system-ui,sans-serif; --body:"IBM Plex Sans",system-ui,sans-serif; --mono:"IBM Plex Mono",ui-monospace,monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg:#0f1419; --paper:#161d24; --fg:#e4e9ee; --muted:#93a1af; --line:#2a3540; --accent:#6aa5ea;
  --ok:#5cc58a; --bad:#ec7a70; --s0:#6aa5ea; --s1:#e0a646; --s2:#a98ce8; --s3:#57c2b7; color-scheme:dark }} }}
:root[data-theme="dark"] {{
  --bg:#0f1419; --paper:#161d24; --fg:#e4e9ee; --muted:#93a1af; --line:#2a3540; --accent:#6aa5ea;
  --ok:#5cc58a; --bad:#ec7a70; --s0:#6aa5ea; --s1:#e0a646; --s2:#a98ce8; --s3:#57c2b7; color-scheme:dark }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 var(--body); padding-left:max(16px,env(safe-area-inset-left,0px)); padding-right:max(16px,env(safe-area-inset-right,0px)) }}
main {{ max-width:940px; margin:0 auto; padding-block:40px 64px; display:grid; gap:28px }}
h1,h2 {{ font-family:var(--display); text-wrap:balance; margin:0 }}
h1 {{ font-size:2.1rem; font-weight:800; letter-spacing:-.01em }}
h2 {{ font-size:1.15rem; font-weight:600; display:flex; gap:10px; align-items:baseline }}
h2 .no {{ font-family:var(--mono); font-size:.75rem; color:var(--muted); font-weight:400 }}
.tb {{ border:1.5px solid var(--fg); display:grid; grid-template-columns:1fr auto; background:var(--paper) }}
.tb > div {{ padding:18px 20px; min-width:0 }}
.tb .meta {{ border-left:1.5px solid var(--fg); font-family:var(--mono); font-size:.78rem; color:var(--muted); display:grid; gap:4px; align-content:center }}
.lede {{ max-width:65ch; margin:10px 0 0; font-size:1.02rem }}
section {{ background:var(--paper); border:1px solid var(--line); padding:20px; display:grid; gap:14px; min-width:0 }}
.bar {{ display:grid; grid-template-columns:minmax(0,190px) 1fr 70px; gap:12px; align-items:center; font-size:.9rem }}
.bl {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-family:var(--mono); font-size:.8rem }}
.track {{ height:14px; background:var(--line); display:block }}
.fill {{ height:100%; display:block }}
.bv {{ text-align:right; font-variant-numeric:tabular-nums; font-family:var(--mono) }}
.scroll {{ overflow-x:auto }}
table {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; font-size:.9rem }}
th,td {{ padding:7px 10px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap }}
thead th {{ font-family:var(--mono); font-size:.72rem; font-weight:500; text-transform:uppercase; letter-spacing:.06em; color:var(--muted) }}
th[scope=row], thead th:first-child {{ text-align:left; font-weight:500 }}
th small {{ color:var(--muted); font-family:var(--mono); font-weight:400; margin-left:8px; font-size:.72rem }}
td.top {{ font-weight:600; color:var(--accent) }}
.ok {{ color:var(--ok) }} .bad {{ color:var(--bad) }}
.dot {{ display:inline-block; width:9px; height:9px; margin-right:8px; vertical-align:middle }}
.two {{ display:grid; grid-template-columns:1fr 1fr; gap:28px }}
.two > * {{ min-width:0 }}
.note {{ color:var(--muted); font-size:.85rem; margin:0; max-width:70ch }}
ul.ex {{ list-style:none; padding:0; margin:0; display:grid; gap:12px }}
ul.ex li {{ border-left:3px solid var(--line); padding:4px 0 4px 12px; min-width:0 }}
.q {{ margin:0; font-weight:500 }}
.meta {{ margin:2px 0 6px; font-size:.78rem; color:var(--muted) }}
.meta code {{ font-family:var(--mono); word-break:break-all }}
dl {{ display:grid; grid-template-columns:auto 1fr; gap:2px 12px; margin:0; font-family:var(--mono); font-size:.82rem }}
dt {{ color:var(--muted) }} dd {{ margin:0; overflow-wrap:anywhere }}
@media (max-width:640px) {{ .tb {{ grid-template-columns:1fr }} .tb .meta {{ border-left:0; border-top:1.5px solid var(--fg) }} .two {{ grid-template-columns:1fr }} .bar {{ grid-template-columns:minmax(0,110px) 1fr 58px }} }}
</style>
<main>
<header class="tb">
  <div><h1>Blueprint QA Benchmark</h1><p class="lede">{lede}</p></div>
  <div class="meta"><span>JUNCTION 2026 · STOA</span><span>SPLIT test (held-out 20%)</span><span>SAMPLE seeded n={overall[OURS]['n']}</span><span>DATE 2026-11-14</span></div>
</header>

<section><h2><span class="no">01</span>Accuracy</h2>{bars({m: s["acc"] for m, s in overall.items()})}
<div class="scroll"><table><thead><tr><th>System</th><th>Accuracy</th><th>Correct</th><th>USD / 1k q</th><th>Median latency</th><th>Errors</th></tr></thead><tbody>{summary_rows}</tbody></table></div>
<p class="note">Scoring follows the dataset README: counts must match exactly, areas, lengths and volumes within 2%. Errors (timeouts, unparsed replies) count as wrong.</p></section>

<section><h2><span class="no">02</span>Cost per 1,000 questions</h2>{bars({m: s["usd1k"] for m, s in overall.items()}, fmt=lambda v: f"${v:.2f}", maxv=maxcost)}
<p class="note">Token prices from <code>blueprints/costs.py</code> (Google AI Studio paid tier, read 2026-11-13). Our cost includes any Gemini escalation calls the pipeline made.</p></section>

<section><h2><span class="no">03</span>Accuracy by file type</h2>{table(sources, by_src, "File type")}</section>
<section><h2><span class="no">04</span>Accuracy by question type</h2>{table(types, by_type, "Type")}</section>

<div class="two">
<section><h2><span class="no">05</span>Head to head</h2>
<div class="scroll"><table><thead><tr><th>vs</th><th>Only ours</th><th>Only theirs</th><th>Both</th><th>Neither</th></tr></thead><tbody>{h2h_rows}</tbody></table></div>
<p class="note">Questions only our pipeline gets right, against questions only the Gemini model gets right.</p></section>
<section><h2><span class="no">06</span>Our confidence, calibrated?</h2>
<div class="scroll"><table><thead><tr><th>Confidence</th><th>n</th><th>Accuracy</th></tr></thead><tbody>{calib_rows}</tbody></table></div>
<p class="note">Answered questions only. Tiers used: {tier_txt}.</p></section>
</div>

<div class="two">
<section><h2><span class="no">07</span>We win, {e(best)} misses</h2><p class="note">{len(wins)} questions; first 6 shown.</p><ul class="ex">{"".join(example(o, g) for o, g in wins[:6])}</ul></section>
<section><h2><span class="no">08</span>{e(best)} wins, we miss</h2><p class="note">{len(losses)} questions; first 6 shown.</p><ul class="ex">{"".join(example(o, g) for o, g in losses[:6])}</ul></section>
</div>
</main>"""
open(out, "w").write(page)
print("wrote", out)
