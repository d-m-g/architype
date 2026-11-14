"""Build the editable pipeline poster and its high-resolution PNG preview.

Run with: .venv/bin/python artifacts/pipeline/build_pipeline.py
Only the PNG export needs PyMuPDF (already in the project environment).
"""
from html import escape
from pathlib import Path
import re

OUT = Path(__file__).resolve().parent
W, H = 2400, 1640
P = {
    "paper": "#F5F3ED", "ink": "#242B29", "muted": "#66706A",
    "rule": "#D7DBD3", "line": "#929C94", "white": "#FEFDF9",
    "rust": "#AE5035", "rustwash": "#F0E2D7", "sage": "#DDE5DB",
    "green": "#324C41", "light": "#EDF1E9", "dim": "#BBC7BD",
}
parts = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">
<title id="title">Blueprints Talk — from drawings to verifiable answers</title>
<desc id="desc">Drawing readers produce a cached Scene. A question planner produces a cached QueryPlan. An exact executor combines them. Answers with confidence at least 0.50 proceed to the evidence viewer; missing or low-confidence answers escalate to a tool-using Nemotron agent. Gemini reads drawings without a working builder and provides the agent fallback. Human corrections rerun the same executor without a model call. This is a logical architecture overview of the current repository.</desc>
<defs>
 <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M1 1 8 5 1 9" fill="none" stroke="{P['line']}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>
 <marker id="rustarrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M1 1 8 5 1 9" fill="none" stroke="{P['rust']}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect width="2400" height="1640" fill="{P['paper']}"/>
''']
parts.append('<g fill="#D5D9D1" opacity="0.48">')
for dot_y in range(378, 1423, 28):
    for dot_x in range(80, 2321, 28):
        parts.append(f'<circle cx="{dot_x}" cy="{dot_y}" r="0.7"/>')
parts.append('</g>')


def rect(x, y, w, h, fill, stroke=None, radius=14):
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}"' +
                 (f' stroke="{stroke}" stroke-width="1.5"' if stroke else '') + '/>')


def text(x, y, value, size=22, color=None, weight=400, mono=False, anchor="start", tracking=None):
    font = "Courier New, monospace" if mono else "Helvetica Neue, Helvetica, Arial, sans-serif"
    parts.append(f'<text x="{x}" y="{y}" fill="{color or P["ink"]}" font-family="{font}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}"' +
                 (f' letter-spacing="{tracking}"' if tracking else '') + f'>{escape(value)}</text>')


def lines(x, y, values, size=22, color=None, gap=32, **kwargs):
    for i, value in enumerate(values):
        text(x, y + i * gap, value, size, color, **kwargs)


def path(d, color=None, arrow=True, dash=False, width=2):
    color = color or P["line"]
    x = y = old_x = old_y = 0
    points = []
    for command, raw in re.findall(r'([MHVQ])([^MHVQ]+)', d):
        values = [float(v) for v in re.findall(r'-?\d+(?:\.\d+)?', raw)]
        old_x, old_y = x, y
        if command == 'Q':
            cx, cy, ex, ey = values
            for step in range(1, 13):
                t = step / 12
                points.append(((1-t)**2*x + 2*(1-t)*t*cx + t*t*ex,
                               (1-t)**2*y + 2*(1-t)*t*cy + t*t*ey))
            x, y = ex, ey
        else:
            if command == 'M':
                x, y = values
            elif command == 'H':
                x = values[-1]
            elif command == 'V':
                y = values[-1]
            points.append((x, y))
    draw_d = d
    if dash:
        # Write the dash geometry explicitly for SVG renderers with partial
        # stroke-dasharray support, including PyMuPDF's SVG importer.
        commands, distance = [], 0.0
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            length = ((bx-ax)**2 + (by-ay)**2)**0.5
            cursor = 0.0
            while cursor < length - 1e-8:
                phase = distance % 15
                on = phase < 7
                amount = min((7 if on else 15) - phase, length-cursor)
                if amount < 1e-8:
                    amount = min(0.000001, length-cursor)
                if on:
                    a, b = cursor/length, (cursor+amount)/length
                    commands.append(f'M{ax+(bx-ax)*a:.2f} {ay+(by-ay)*a:.2f}L{ax+(bx-ax)*b:.2f} {ay+(by-ay)*b:.2f}')
                cursor += amount
                distance += amount
        draw_d = ' '.join(commands)
    parts.append(f'<path d="{draw_d}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>')
    # Explicit arrow geometry keeps SVG and PNG exports identical in renderers
    # that do not implement SVG markers.
    if arrow:
        if x != old_x:
            sign = 1 if x > old_x else -1
            head = f'M{x - 11 * sign} {y - 5}L{x} {y}L{x - 11 * sign} {y + 5}'
        else:
            sign = 1 if y > old_y else -1
            head = f'M{x - 5} {y - 11 * sign}L{x} {y}L{x + 5} {y - 11 * sign}'
        parts.append(f'<path d="{head}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>')


def port(x, y, color=None):
    parts.append(f'<circle cx="{x}" cy="{y}" r="4" fill="{P["paper"]}" stroke="{color or P["line"]}" stroke-width="2"/>')


def node(x, y, w, h, kicker, title, body, dark=False, accent=False):
    fill = P["green"] if accent else P["ink"] if dark else P["white"]
    primary, secondary = (P["light"], P["dim"]) if dark or accent else (P["ink"], P["muted"])
    rect(x, y, w, h, fill, None if dark or accent else P["rule"])
    text(x + 26, y + 30, kicker, 16, secondary, mono=True, tracking="1.1")
    text(x + 26, y + 70, title, 29, primary, 500)
    lines(x + 26, y + 108, body, 21, secondary, gap=30)


# Editorial header.
parts.append(f'<path d="M80 70h30v30H80z M80 81h12V70 M92 81v19 M92 91h18" fill="none" stroke="{P["ink"]}" stroke-width="2"/>')
text(127, 94, "BLUEPRINTS TALK", 23, weight=500, tracking="2")
text(80, 193, "From drawings to answers", 68, weight=500)
text(80, 270, "you can verify.", 68, weight=500)
text(2318, 80, "SYSTEM MAP / 01", 18, mono=True, anchor="end", tracking="1.3")
text(2318, 115, "JUNCTION 2026 · STOA", 18, P["muted"], anchor="end", tracking="1")
text(2318, 224, "Read once. Reuse the scene.", 26, anchor="end")
text(2318, 261, "Escalate only when the fast path needs help.", 22, P["muted"], anchor="end")
path("M80 310H2320", P["rule"], False, width=1.5)
for x, n, label in [(80, "01", "BRING THE CONTEXT"), (470, "02", "BUILD UNDERSTANDING"),
                    (1320, "03", "REASON & CHECK"), (1840, "04", "VERIFY & REFINE")]:
    text(x, 354, n, 17, P["rust"], mono=True)
    text(x + 43, 354, label, 17, P["muted"], mono=True, tracking="0.8")

# All connectors go under the nodes. Curves keep orthogonal routes quiet.
# Drawing fan-out.
path("M360 480H430", arrow=False)
path("M430 450V625", arrow=False)
path("M430 450H470")
path("M430 625H470")
path("M430 625V790H470", P["rust"], dash=True)

# Reader fan-in to a common scene. The vision read is the fallback reader.
path("M810 460H863Q880 460 880 477V555Q880 570 897 570H920")
path("M810 625H920")
path("M810 790H865Q880 790 880 775V680Q880 665 897 665H920", P["rust"], dash=True)

# Fast path, planner, and the query plan's input into the executor.
path("M1200 592H1320")
path("M360 1095H470")
path("M810 1095H920")
path("M1200 1095H1232Q1250 1095 1250 1077V651Q1250 635 1268 635H1320")
path("M1480 695V769")

# Confidence gate: yes right to answer, no down to the agent.
path("M1570 835H1690Q1710 835 1710 815V611Q1710 592 1730 592H1840")
path("M1480 901V1020", P["rust"])
path("M1640 1140H1740Q1760 1140 1760 1120V671Q1760 650 1780 650H1840", P["rust"])

# Viewer handoff and the human correction loop.
path("M2055 695V1010")
path("M1840 1120H1820Q1800 1120 1800 1140V1360Q1800 1380 1780 1380H1300Q1280 1380 1280 1360V683Q1280 670 1294 670H1320", dash=True)

# Nodes: drawing + three readers.
node(80, 400, 280, 205, "DRAWING INPUT", "A drawing", ["Upload or dataset", "PDF · DXF · image", "IFC in the dataset"])
node(470, 400, 340, 140, "STRUCTURED", "Parse geometry", ["DXF · vector PDF · IFC"])
node(470, 565, 340, 140, "RASTER", "Detect objects", ["YOLO on Modal"])
node(470, 730, 340, 170, "FALLBACK READER", "Read with vision", ["Gemini → scene", "When no builder succeeds"])

text(486, 931, "Uploads: detector + vision rooms", 19, P["muted"])
text(486, 959, "Room labels normalized across languages", 19, P["muted"])

# Cached intermediate representation is the shared contract.
node(920, 490, 280, 205, "SHARED CONTRACT", "Scene", ["Entities + geometry", "Quantities + sources", "Cached per drawing"], dark=True)
node(80, 1010, 280, 205, "QUESTION INPUT", "Ask naturally", ["Type or dictate", "Any language", "About this drawing"])
node(470, 1010, 340, 205, "GEMINI FLASH", "Plan the question", ["Reuse a cached template", "Call Gemini on a miss", "Count · sum · formula"])
node(920, 1010, 280, 205, "SHARED CONTRACT", "QueryPlan", ["Queries + filters", "Formula + assumptions", "Cached by template"], dark=True)

# Deterministic arithmetic; models do not compute the quantities.
node(1320, 490, 320, 205, "EXACT EXECUTION", "Run the plan", ["Filter · count · sum", "Evaluate the formula", "Keep matched entity IDs"], dark=True)
parts.append(f'<path d="M1480 769 1570 835 1480 901 1390 835Z" fill="{P["sage"]}" stroke="{P["rule"]}" stroke-width="1.5"/>')
text(1480, 828, "Answer", 23, anchor="middle", weight=500)
text(1480, 858, "confident?", 23, anchor="middle", weight=500)
text(1800, 760, "YES", 17, P["muted"], mono=True)
text(1800, 792, "answer present", 19, P["muted"])
text(1800, 821, "confidence ≥ 0.50", 19, P["muted"])
text(1505, 952, "NO / NO ANSWER", 16, P["rust"], mono=True)
text(1505, 982, "confidence < 0.50", 19, P["rust"])
node(1320, 1020, 320, 250, "SUPER VL · AGENT", "Nemotron 3.5", ["Inspect scene entities", "Run plans with tools", "Ask Gemini to look", "Gemini agent fallback"], dark=False)
rect(1346, 1230, 268, 1, P["rule"], radius=0)
text(1346, 1254, "TOOL CALLS ARE TRACED", 15, P["rust"], mono=True, tracking="0.6")

# Evidence is first-class, not an explanatory afterthought.
node(1840, 490, 430, 205, "ANSWER + EVIDENCE", "Show the work", ["Answer · units · confidence", "Entity highlights on the drawing", "Formula · tool trace · actual cost"], accent=True)
text(2080, 846, "Evidence viewer", 22, P["muted"])
text(2080, 878, "2D overlay / floor stack", 20, P["muted"])
node(1840, 1010, 430, 205, "HUMAN IN THE LOOP", "Review & correct", ["Edit boxes, types or assumptions", "Recompute with the same executor", "Record training labels when chosen"])

# Port markings and quiet route labels.
for x, y in [(360,480), (920,570), (920,625), (1200,592), (1320,592),
             (360,1095), (920,1095), (1320,635), (2055,695), (1840,1120)]:
    port(x, y)
text(959, 743, "BUILD ONCE", 16, P["muted"], mono=True, tracking="1")
text(959, 773, "Reuse for follow-up questions", 19, P["muted"])
text(1309, 1339, "CORRECTED SCENE + SAME PLAN", 17, P["muted"], mono=True, tracking="0.7")
text(1309, 1366, "No model call", 20, P["ink"])
path("M80 1330H120", arrow=False)
text(137, 1337, "Core flow", 19, P["muted"])
path("M330 1330H370", P["rust"], arrow=False)
text(387, 1337, "Vision / escalation", 19, P["muted"])
path("M660 1330H700", arrow=False, dash=True)
text(717, 1337, "Fallback / correction return", 19, P["muted"])

# A succinct explanatory footer makes the simplifications explicit.
path("M80 1450H2320", P["rule"], False, width=1.5)
text(80, 1497, "ONE SCENE. MANY QUESTIONS.", 18, mono=True, tracking="1")
lines(80, 1533, ["Parsers and vision share the same entity contract.", "The executor owns all arithmetic and evidence IDs."], 20, P["muted"], gap=30)
text(920, 1497, "ESCALATION WITH CONTEXT", 18, mono=True, tracking="1")
lines(920, 1533, ["Unsupported plans and reader failures also escalate.", "If the drawing cannot support an answer, surface why."], 20, P["muted"], gap=30)
text(1840, 1497, "CURRENT IMPLEMENTATION", 18, mono=True, tracking="1")
lines(1840, 1533, ["Logical data flow · 14 November 2026", "Vision-only PDFs: read → plan → execute."], 19, P["muted"], gap=30)
parts.append('</svg>')

OUT.mkdir(parents=True, exist_ok=True)
svg = OUT / "pipeline.svg"
svg.write_text("\n".join(parts), encoding="utf-8")

if __name__ == "__main__":
    import pymupdf as fitz

    source = fitz.open(stream=svg.read_bytes(), filetype="svg")
    pdf = fitz.open("pdf", source.convert_to_pdf())
    pdf[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(OUT / "pipeline.png")
    print(f"Built {svg.name} and pipeline.png ({W}×{H} vector, {W * 3 // 2}×{H * 3 // 2} PNG)")
