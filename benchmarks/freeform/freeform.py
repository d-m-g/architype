"""Free-form questions: does the data-first resolver (blueprints/brain/resolve.py) answer what people actually ask?

The dataset's questions come in a few fixed wordings. These do not: ranking, comparison, threshold, relationship,
dimension, derived-cost, other-language and unanswerable questions over real scenes (Swiss DXF, vector PDF, IFC, and a
few image scenes that need the vision reader), and follow-up conversations of two and three turns, where a question
only makes sense read against the earlier ones ("and the kitchens?", "which of those is the smallest?").

Each case below holds three independent things:
  ref       hand-written Python over the scene's entities: the expected answer. Never touches the SQL path.
  how       how that reference is derived, in words.
  decision  the decision a good model would return (hand-written SQL). Running it through resolve.execute and getting
            the reference proves the tool can express the question, which is what can be checked without a key.

  uv run --extra parsers python -m benchmarks.freeform.freeform --write   # recompute references -> questions.jsonl
  uv run --extra parsers python -m benchmarks.freeform.freeform           # check; with GEMINI_API_KEY also ask the model

Without a key it validates the stored references and the hand-written decisions and reports the model step as
"needs key", with the prompt size and the estimated cost per question from costs.py prices.
"""
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

from blueprints import pipeline
from blueprints.brain import resolve
from blueprints.costs import TOKEN_PRICES
from blueprints.room_queries import endpoints

FILE = Path(__file__).with_name("questions.jsonl")
SCENES = {  # key -> (dataset path, reader)
    "de": ("data/swiss_dwellings/site3387_bldg5238_floor23498.pdf", "swiss_dxf"),
    "fr": ("data/swiss_dwellings/site3863_bldg6018_floor25218.pdf", "swiss_dxf"),
    "it": ("data/swiss_dwellings/site3667_bldg5654_floor24350.pdf", "pdf_vector"),  # the PDF alone, as an upload is read
    "duplex": ("data/ifc_models/bsc-duplex_duplex-a-20110907.ifc", "ifc"),
    "fzk": ("data/ifc_models/kit-fzk-haus_ac20-fzk-haus.ifc", "ifc"),
    "clinic": ("data/ifc_models/bsc-clinic_clinic-structural.ifc", "ifc"),  # a structural model: slabs, no rooms
}


def load(key: str):
    path, reader = SCENES[key]
    if reader == "pdf_vector":
        from blueprints.brain.room_types import add_room_types
        from blueprints.parsers import pdf
        return add_room_types(pdf.build_scene(f"uploads/freeform-{key}.pdf", pipeline.dataset_file(path)))
    return pipeline.get_scene(path)


# ---- expected answers ----

def N(value, unit=None, exact=False):
    return {"type": "number", "value": round(value, 4), "unit": unit, "tolerance": 0 if exact else 0.02}


def E(scene, ids, top=False):  # entities, by id
    ids = [e.id if hasattr(e, "id") else e for e in ids]
    by_id = {e.id: e for e in scene.entities}
    return {"type": "names", "ids": ids, "names": [resolve.name_of(by_id[i]) for i in ids], "tied": top and len(ids) > 1}


def G(names, top=True):  # groups, by name (an apartment, a level, a type)
    return {"type": "names", "ids": [], "names": list(names), "tied": top and len(names) > 1}


def X(*keywords):  # an abstention whose reason names one of these
    return {"type": "abstain", "missing": list(keywords)}


def of(scene, type, **f):
    return [e for e in scene.entities if e.type == type and all(getattr(e, k) == v for k, v in f.items())]


def best(items, key, least=False):
    """Every item sharing the best value: ties are part of the expected answer."""
    items = [i for i in items if key(i) is not None]
    top = (min if least else max)(key(i) for i in items)
    return [i for i in items if key(i) == top]


def at(scene, kind, rooms):
    """The doors, windows or fixtures linked to any of these rooms."""
    ids = {r.id for r in rooms}
    return [e for e in of(scene, kind) if endpoints(e, scene) & ids]


m2 = lambda e: e.qty.get("m2")
total = lambda items, unit="m2": sum(e.qty[unit] for e in items)
count = lambda sql_where: {"n": f"SELECT count(DISTINCT id) AS value, group_concat(DISTINCT id) AS ids FROM entities WHERE {sql_where}"}
ROOM = "type = 'room'"
IN_ROOMS = "id IN (SELECT l.entity_id FROM links l JOIN entities r ON r.id = l.room_id WHERE {})"
PER_ROOM = ("SELECT r.id, {agg} AS value, group_concat(x.id) AS ids FROM entities r JOIN links l ON l.room_id = r.id "
            "JOIN entities x ON x.id = l.entity_id AND x.type = '{kind}' WHERE r.type = 'room'{where} GROUP BY r.id ORDER BY value DESC")
missing = lambda what, **needs: {"kind": "missing", "missing": what, **({"needs": needs} if needs else {})}


def direct(sql=None, answer="value", unit=None, **queries):
    return {"kind": "direct", "queries": queries or {"q": sql}, "answer": answer, **({"unit": unit} if unit else {})}


def computed(formula, unit=None, assumptions=None, **queries):
    return {"kind": "computed", "queries": queries, "formula": formula, **({"unit": unit} if unit else {}),
            **({"assumptions": assumptions} if assumptions else {})}


def value(where, column, unit, type="room"):
    return direct(f"SELECT {column} AS value, id FROM entities WHERE type = '{type}' AND {where}", unit=unit)


def agg(fn, where, unit, type="room"):
    return direct(f"SELECT {fn} AS value, group_concat(id) AS ids FROM entities WHERE type = '{type}' AND {where}", unit=unit)


def ranked(where, column="m2", unit="m2", type="room", least=False):
    return direct(f"SELECT id, {column} AS value FROM entities WHERE type = '{type}' AND {column} IS NOT NULL AND {where} "
                  f"ORDER BY value {'ASC' if least else 'DESC'}", answer="top", unit=unit)


def listed(where, column="m2", unit="m2", type="room"):
    return direct(f"SELECT id, {column} AS value FROM entities WHERE type = '{type}' AND {where} ORDER BY value DESC", answer="rows", unit=unit)


BEDS_A = "type = 'room' AND subtype = 'bedroom' AND apartment = 'A'"
CASES = [
    # ---- Swiss DXF, German labels (site3387): 24 rooms, 2 apartments, 18 doors, 11 windows, 11 fixtures ----
    ("de", "ranking", "en", "Which room is the largest?",
     lambda s: E(s, best(of(s, "room"), m2), top=True), "the room entity with the greatest qty.m2", ranked("1 = 1")),
    ("de", "ranking", "en", "What's the smallest room on this floor, balconies aside?",
     lambda s: E(s, best(of(s, "room", outdoor=False), m2, least=True), top=True), "least qty.m2 over rooms with outdoor false",
     ranked("outdoor = 0", least=True)),
    ("de", "ranking", "de", "Welches Schlafzimmer ist am grössten?",
     lambda s: E(s, best(of(s, "room", subtype="bedroom"), m2), top=True), "greatest qty.m2 over rooms of subtype bedroom", ranked("subtype = 'bedroom'")),
    ("de", "comparison", "en", "Which apartment has more indoor floor space, A or B?",
     lambda s: G(best("AB", lambda a: total(of(s, "room", apartment=a, outdoor=False)))), "sum of qty.m2 of indoor rooms per apartment label, larger first",
     direct("SELECT apartment AS name, sum(m2) AS value, group_concat(id) AS ids FROM entities WHERE type = 'room' AND outdoor = 0 "
            "AND apartment IN ('A', 'B') GROUP BY apartment ORDER BY value DESC", answer="top", unit="m2")),
    ("de", "relationship", "en", "Which room has the most doors?",
     lambda s: E(s, best(of(s, "room"), lambda r: len(at(s, "door", [r]))), top=True), "per room, the doors whose resolved endpoints include it; most first",
     direct(PER_ROOM.format(agg="count(x.id)", kind="door", where=""), answer="top", unit="count")),
    ("de", "relationship", "en", "Which bedroom gets the most window width?",
     lambda s: E(s, best(of(s, "room", subtype="bedroom"), lambda r: total(at(s, "window", [r]), "m")), top=True),
     "per bedroom, the sum of qty.m of the windows linked to it",
     direct(PER_ROOM.format(agg="sum(x.m)", kind="window", where=" AND r.subtype = 'bedroom'"), answer="top", unit="m")),
    ("de", "ranking", "en", "What is the widest door?",
     lambda s: E(s, best(of(s, "door"), lambda d: d.qty.get("m")), top=True), "the door with the greatest qty.m", ranked("1 = 1", "m", "m", "door")),
    ("de", "comparison", "en", "Is the kitchen in apartment A bigger than the one in B, or the other way round?",
     lambda s: E(s, best(of(s, "room", subtype="kitchen"), m2), top=True), "the kitchen with the greater qty.m2",
     ranked("subtype = 'kitchen' AND apartment IN ('A', 'B')")),
    ("de", "comparison", "en", "How much larger is A's living room than B's?",
     lambda s: N(of(s, "room", subtype="living", apartment="A")[0].qty["m2"] - of(s, "room", subtype="living", apartment="B")[0].qty["m2"], "m2"),
     "qty.m2 of the living room of A minus that of B",
     computed("a - b", "m2", a="SELECT m2 AS value, id FROM entities WHERE type = 'room' AND subtype = 'living' AND apartment = 'A'",
              b="SELECT m2 AS value, id FROM entities WHERE type = 'room' AND subtype = 'living' AND apartment = 'B'")),
    ("de", "comparison", "en", "Are there more doors or more windows?",
     lambda s: G(best(("door", "window"), lambda t: len(of(s, t)))), "entity counts per type, greater first",
     direct("SELECT type AS name, count(*) AS value, group_concat(id) AS ids FROM entities WHERE type IN ('door', 'window') GROUP BY type ORDER BY value DESC",
            answer="top", unit="count")),
    ("de", "threshold", "en", "Which rooms are bigger than 15 m²?",
     lambda s: E(s, [r for r in of(s, "room") if m2(r) > 15]), "rooms with qty.m2 > 15", listed("m2 > 15")),
    ("de", "threshold", "en", "How many rooms are under five square metres?",
     lambda s: N(sum(m2(r) < 5 for r in of(s, "room")), "count", exact=True), "count of rooms with qty.m2 < 5", agg("count(*)", "m2 < 5", "count")),
    ("de", "threshold", "en", "List the doors wider than a metre.",
     lambda s: E(s, [d for d in of(s, "door") if d.qty.get("m", 0) > 1]), "doors with qty.m > 1", listed("m > 1", "m", "m", "door")),
    ("de", "relationship", "en", "How many doors open off the corridor of apartment A?",
     lambda s: N(len(at(s, "door", of(s, "room", subtype="corridor", apartment="A"))), "count", exact=True), "doors whose endpoints include A's corridor",
     direct(unit="count", **count("type = 'door' AND " + IN_ROOMS.format("r.subtype = 'corridor' AND r.apartment = 'A'")))),
    ("de", "relationship", "en", "How many doors lead from a corridor straight into a bedroom?",
     lambda s: N(sum(1 for d in of(s, "door") if {r.subtype for r in of(s, "room") if r.id in endpoints(d, s)} >= {"corridor", "bedroom"}), "count", exact=True),
     "doors with one endpoint a corridor and the other a bedroom",
     direct(unit="count", **count("type = 'door' AND " + IN_ROOMS.format("r.subtype = 'corridor'") + " AND " + IN_ROOMS.format("r.subtype = 'bedroom'")))),
    ("de", "relationship", "en", "Which rooms can I walk into directly from kitchen 7.02?",
     lambda s: E(s, [r for r in of(s, "room") if r.number != "7.02" and any(r.id in endpoints(d, s) for d in at(s, "door", of(s, "room", number="7.02")))]),
     "the other endpoint of each door linked to room 7.02",
     direct("SELECT DISTINCT r.id FROM entities r JOIN links l ON l.room_id = r.id WHERE r.type = 'room' AND r.number <> '7.02' AND l.entity_id IN "
            "(SELECT d.id FROM entities d JOIN links k ON k.entity_id = d.id JOIN entities q ON q.id = k.room_id WHERE d.type = 'door' AND q.number = '7.02')",
            answer="rows")),
    ("de", "relationship", "en", "What is the total width of the windows in the kitchens?",
     lambda s: N(total(at(s, "window", of(s, "room", subtype="kitchen")), "m"), "m"), "sum of qty.m of windows linked to a kitchen",
     agg("sum(m)", IN_ROOMS.format("r.subtype = 'kitchen'"), "m", "window")),
    ("de", "relationship", "en", "Which bathrooms have no window?",
     lambda s: E(s, [r for r in of(s, "room", subtype="bathroom") if not at(s, "window", [r])]), "bathrooms no window is linked to",
     direct("SELECT r.id FROM entities r WHERE r.type = 'room' AND r.subtype = 'bathroom' AND NOT EXISTS (SELECT 1 FROM links l JOIN entities w "
            "ON w.id = l.entity_id WHERE l.room_id = r.id AND w.type = 'window')", answer="rows")),
    ("de", "relationship", "en", "How many toilets does apartment B have?",
     lambda s: N(len(of(s, "fixture", subtype="toilet", apartment="B")), "count", exact=True), "toilet fixtures whose apartment is B",
     agg("count(*)", "subtype = 'toilet' AND apartment = 'B'", "count", "fixture")),
    ("de", "relationship", "en", "Which bathroom has a shower?",
     lambda s: E(s, [r for r in of(s, "room", subtype="bathroom") if any(f.subtype == "shower" for f in at(s, "fixture", [r]))]),
     "bathrooms a shower fixture is linked to",
     direct("SELECT DISTINCT r.id FROM entities r JOIN links l ON l.room_id = r.id JOIN entities f ON f.id = l.entity_id WHERE r.type = 'room' "
            "AND r.subtype = 'bathroom' AND f.type = 'fixture' AND f.subtype = 'shower'", answer="rows")),
    ("de", "dimension", "en", "How wide is bedroom 7.08?",
     lambda s: N(of(s, "room", number="7.08")[0].attrs["width_m"], "m"), "attrs.width_m of room 7.08", value("number = '7.08'", "width_m", "m")),
    ("de", "dimension", "en", "What's the perimeter of the living room in apartment B?",
     lambda s: N(of(s, "room", subtype="living", apartment="B")[0].qty["m"], "m"), "qty.m of B's living room",
     value("subtype = 'living' AND apartment = 'B'", "m", "m")),
    ("de", "dimension", "en", "How long is the longest balcony?",
     lambda s: N(max(r.attrs["length_m"] for r in of(s, "room", subtype="balcony")), "m"), "greatest attrs.length_m over balconies",
     value("subtype = 'balcony' ORDER BY length_m DESC LIMIT 1", "length_m", "m")),
    ("de", "dimension", "en", "How thick is the wall where the lift door is?",
     lambda s: N(at(s, "door", of(s, "room", label="Lift"))[0].attrs["thickness_m"], "m"), "attrs.thickness_m of the door linked to the room labelled Lift",
     direct("SELECT d.thickness_m AS value, d.id FROM entities d JOIN links l ON l.entity_id = d.id JOIN entities r ON r.id = l.room_id "
            "WHERE d.type = 'door' AND r.label = 'Lift'", unit="m")),
    ("de", "derived", "en", "How much skirting board do I need for bedroom 7.09, leaving out the door openings?",
     lambda s: N(of(s, "room", number="7.09")[0].qty["m"] - total(at(s, "door", of(s, "room", number="7.09")), "m"), "m"),
     "qty.m (perimeter) of room 7.09 minus the qty.m (width) of each door linked to it",
     computed("p - d", "m", p="SELECT m AS value, id FROM entities WHERE type = 'room' AND number = '7.09'",
              d="SELECT sum(m) AS value, group_concat(id) AS ids FROM entities WHERE type = 'door' AND " + IN_ROOMS.format("r.number = '7.09'"))),
    ("de", "derived", "en", "What would parquet for all the bedrooms cost at 85 francs per square metre?",
     lambda s: N(85 * total(of(s, "room", subtype="bedroom"))), "85 times the sum of qty.m2 of the bedrooms",
     computed("area * price_per_m2", assumptions={"price_per_m2": 85},
              area="SELECT sum(m2) AS value, group_concat(id) AS ids FROM entities WHERE type = 'room' AND subtype = 'bedroom'")),
    ("de", "derived", "en", "How many 30 x 60 cm tiles do I need for the floor of bathroom 7.05 with 10% waste?",
     lambda s: N(math.ceil(of(s, "room", number="7.05")[0].qty["m2"] * 1.1 / 0.18), "count", exact=True), "ceil(qty.m2 of room 7.05 * 1.1 / 0.18)",
     computed("ceil(area * (1 + waste) / tile_m2)", "count", {"tile": "30 x 60 cm", "tile_m2": 0.18, "waste": 0.1},
              area="SELECT m2 AS value, id FROM entities WHERE type = 'room' AND number = '7.05'")),
    ("de", "derived", "en", "What percentage of apartment A's indoor area is bedrooms?",
     lambda s: N(100 * total(of(s, "room", subtype="bedroom", apartment="A")) / total(of(s, "room", apartment="A", outdoor=False))),
     "100 * sum of qty.m2 of A's bedrooms / sum of qty.m2 of A's indoor rooms",
     computed("beds / indoor * 100", beds=f"SELECT sum(m2) AS value, group_concat(id) AS ids FROM entities WHERE {BEDS_A}",
              indoor="SELECT sum(m2) AS value, group_concat(id) AS ids FROM entities WHERE type = 'room' AND apartment = 'A' AND outdoor = 0")),
    ("de", "derived", "en", "What's the average bedroom size?",
     lambda s: N(total(of(s, "room", subtype="bedroom")) / len(of(s, "room", subtype="bedroom")), "m2"), "mean qty.m2 of the bedrooms",
     agg("avg(m2)", "subtype = 'bedroom'", "m2")),
    ("de", "unanswerable", "en", "How high are the ceilings?", lambda s: X("height"), "no room carries height_m on a Swiss plan",
     missing("the ceiling height: no height_m is recorded for rooms in this drawing")),
    ("de", "unanswerable", "en", "How many litres of paint do I need for the walls of living room 7.03?", lambda s: X("height"),
     "wall area needs the ceiling height, which the plan does not record; a person can supply it",
     {**missing("the ceiling height of room 7.03: no height_m is recorded for rooms", ceiling_height_m="the ceiling height in metres",
                litres_per_m2="paint needed per square metre of wall"),
      "queries": {"p": "SELECT m AS value, id FROM entities WHERE type = 'room' AND number = '7.03'"}, "formula": "p * ceiling_height_m * litres_per_m2"}),
    ("de", "unanswerable", "en", "How many walls are there?", lambda s: X("wall"), "the Swiss reader does not cover walls: a zero would be false",
     agg("count(*)", "1 = 1", "count", "wall")),
    ("de", "unanswerable", "en", "When was this building built?", lambda s: X("year", "date", "built"), "not something a floor plan holds",
     missing("the construction year: a floor plan does not record when the building was built")),
    ("de", "unanswerable", "en", "How tall is the door to the lift?", lambda s: X("height"), "doors carry a width and wall thickness, no height",
     missing("the door's height: no height_m is recorded for doors in this drawing")),
    ("de", "unanswerable", "en", "Which storey of the building is this?", lambda s: X("level", "storey", "floor"), "no level entity and no level on any room",
     missing("the storey: no level is recorded in this drawing")),

    # ---- Swiss DXF, French labels (site3863), asked in French ----
    ("fr", "other-language", "fr", "Quelle est la plus grande chambre à coucher ?",
     lambda s: E(s, best(of(s, "room", label="Chambre à coucher"), m2), top=True), "greatest qty.m2 over rooms labelled 'Chambre à coucher'",
     ranked("label = 'Chambre à coucher'")),
    ("fr", "other-language", "fr", "Combien de portes donnent sur les couloirs ?",
     lambda s: N(len(at(s, "door", of(s, "room", label="Couloir"))), "count", exact=True), "doors linked to a room labelled 'Couloir', each once",
     direct(unit="count", **count("type = 'door' AND " + IN_ROOMS.format("r.label = 'Couloir'")))),
    ("fr", "other-language", "fr", "Quelle est la surface totale des balcons ?",
     lambda s: N(total(of(s, "room", label="Balcon")), "m2"), "sum of qty.m2 of rooms labelled 'Balcon'", agg("sum(m2)", "label = 'Balcon'", "m2")),
    ("fr", "other-language", "fr", "Quelles pièces font moins de 6 m² ?",
     lambda s: E(s, [r for r in of(s, "room") if m2(r) < 6]), "rooms with qty.m2 < 6", listed("m2 < 6")),
    ("fr", "other-language", "fr", "Quel est le périmètre du plus grand séjour ?",
     lambda s: N(best(of(s, "room", label="Séjour"), m2)[0].qty["m"], "m"), "qty.m of the 'Séjour' with the greatest qty.m2",
     value("label = 'Séjour' ORDER BY m2 DESC LIMIT 1", "m", "m")),
    ("fr", "other-language", "fr", "Quelle est la hauteur sous plafond ?", lambda s: X("haut", "height"), "no height_m on rooms",
     missing("la hauteur sous plafond (height_m): aucune hauteur n'est enregistrée pour les pièces")),
    ("fr", "other-language", "fr", "Combien de fenêtres y a-t-il dans les cuisines ?",
     lambda s: N(len(at(s, "window", of(s, "room", label="Cuisine"))), "count", exact=True), "windows linked to a room labelled 'Cuisine'",
     direct(unit="count", **count("type = 'window' AND " + IN_ROOMS.format("r.label = 'Cuisine'")))),
    ("fr", "other-language", "fr", "Combien coûterait un carrelage à 60 CHF le m² pour les salles de bain ?",
     lambda s: N(60 * total(of(s, "room", label="Bain"))), "60 times the sum of qty.m2 of rooms labelled 'Bain'",
     computed("surface * prix_m2", assumptions={"prix_m2": 60}, surface="SELECT sum(m2) AS value, group_concat(id) AS ids FROM entities WHERE type = 'room' AND label = 'Bain'")),

    # ---- vector PDF read on its own, Italian labels (site3667) ----
    ("it", "other-language", "it", "Qual è la stanza più grande?",
     lambda s: E(s, best(of(s, "room"), m2), top=True), "the room with the greatest qty.m2", ranked("1 = 1")),
    ("it", "other-language", "it", "Quante finestre hanno in totale le camere da letto?",
     lambda s: N(len(at(s, "window", of(s, "room", label="Camera da letto"))), "count", exact=True), "windows linked to a room labelled 'Camera da letto'",
     direct(unit="count", **count("type = 'window' AND " + IN_ROOMS.format("r.label = 'Camera da letto'")))),
    ("it", "dimension", "en", "How wide is the widest window?",
     lambda s: N(max(w.qty["m"] for w in of(s, "window") if "m" in w.qty), "m"), "greatest qty.m over windows",
     value("m IS NOT NULL ORDER BY m DESC LIMIT 1", "m", "m", "window")),
    ("it", "derived", "en", "How much indoor floor space is there altogether?",
     lambda s: N(total(of(s, "room", outdoor=False)), "m2"), "sum of qty.m2 of rooms with outdoor false", agg("sum(m2)", "outdoor = 0", "m2")),
    ("it", "relationship", "en", "Which rooms open onto a balcony?",
     lambda s: E(s, [r for r in of(s, "room", outdoor=False) if any(endpoints(d, s) & {b.id for b in of(s, "room", outdoor=True)} for d in at(s, "door", [r]))]),
     "indoor rooms sharing a door with an outdoor room",
     direct("SELECT DISTINCT r.id FROM entities r JOIN links l ON l.room_id = r.id WHERE r.type = 'room' AND r.outdoor = 0 AND l.entity_id IN "
            "(SELECT d.id FROM entities d JOIN links k ON k.entity_id = d.id JOIN entities b ON b.id = k.room_id WHERE d.type = 'door' AND b.outdoor = 1)",
            answer="rows")),
    ("it", "other-language", "it", "Quanto battiscopa serve per la cucina più piccola?",
     lambda s: N(best(of(s, "room", label="Cucina"), m2, least=True)[0].qty["m"], "m"), "qty.m (perimeter) of the 'Cucina' with the least qty.m2",
     value("label = 'Cucina' ORDER BY m2 ASC LIMIT 1", "m", "m")),
    ("it", "other-language", "it", "Quanto è alto il soffitto del bagno?", lambda s: X("alt", "height"), "no height_m on rooms",
     missing("l'altezza del soffitto (height_m): nessuna altezza è registrata per i locali")),
    ("it", "threshold", "en", "How many lifts are there?",
     lambda s: N(len(of(s, "room", label="Ascensore")), "count", exact=True), "rooms labelled 'Ascensore'", agg("count(*)", "label = 'Ascensore'", "count")),

    # ---- IFC, no quantity sets (duplex): sizes measured on geometry, 4 levels, no room links ----
    ("duplex", "ranking", "en", "Which level has the most rooms?",
     lambda s: G(best(sorted({r.level for r in of(s, "room") if r.level}), lambda lv: len(of(s, "room", level=lv)))), "rooms per level label, most first",
     direct("SELECT level AS name, count(*) AS value, group_concat(id) AS ids FROM entities WHERE type = 'room' AND level IS NOT NULL GROUP BY level "
            "ORDER BY value DESC", answer="top", unit="count")),
    ("duplex", "dimension", "en", "What is the total length of the exterior brick-on-block walls?",
     lambda s: N(total([w for w in of(s, "wall") if "Exterior - Brick on Block" in w.label], "m"), "m"), "sum of qty.m of walls whose label names that wall type",
     agg("sum(m)", "label LIKE '%Exterior - Brick on Block%'", "m", "wall")),
    ("duplex", "derived", "en", "How much wall surface is there on Level 1, one side only?",
     lambda s: N(total(of(s, "wall", level="Level 1")), "m2"), "sum of qty.m2 (net side area) of walls on Level 1", agg("sum(m2)", "level = 'Level 1'", "m2", "wall")),
    ("duplex", "ranking", "en", "Which door is the tallest?",
     lambda s: E(s, best(of(s, "door"), lambda d: d.attrs.get("height_m")), top=True), "greatest attrs.height_m over doors; equal ones are a tie",
     ranked("1 = 1", "height_m", "m", "door")),
    ("duplex", "threshold", "en", "How many doors are wider than 80 cm?",
     lambda s: N(sum(d.qty["m"] > 0.8 for d in of(s, "door")), "count", exact=True), "doors with qty.m > 0.8", agg("count(*)", "m > 0.8", "count", "door")),
    ("duplex", "ranking", "en", "Which slab is the thickest?",
     lambda s: E(s, best(of(s, "slab"), lambda x: x.attrs.get("thickness_m")), top=True), "greatest attrs.thickness_m over slabs",
     ranked("1 = 1", "thickness_m", "m", "slab")),
    ("duplex", "dimension", "en", "What's the ceiling height in living room A102?",
     lambda s: N(of(s, "room", number="A102")[0].attrs["height_m"], "m"), "attrs.height_m of room A102", value("number = 'A102'", "height_m", "m")),
    ("duplex", "derived", "en", "What is the air volume of Bedroom 1 (A202)?",
     lambda s: N(of(s, "room", number="A202")[0].qty["m2"] * of(s, "room", number="A202")[0].attrs["height_m"], "m3"), "qty.m2 times attrs.height_m of room A202",
     value("number = 'A202'", "m2 * height_m", "m3")),
    ("duplex", "unanswerable", "en", "Which room has the most doors?", lambda s: X("room link", "room connection"),
     "the IFC reader records no room links: no door can be assigned to a room", direct(PER_ROOM.format(agg="count(x.id)", kind="door", where=""), answer="top")),
    ("duplex", "relationship", "en", "How many windows are on Level 2?",
     lambda s: N(len(of(s, "window", level="Level 2")), "count", exact=True), "windows whose level is 'Level 2'", agg("count(*)", "level = 'Level 2'", "count", "window")),
    ("duplex", "derived", "en", "What is the combined glazing area of all the windows?",
     lambda s: N(sum(w.qty["m"] * w.attrs["height_m"] for w in of(s, "window")), "m2"), "sum over windows of qty.m (width) times attrs.height_m",
     agg("sum(m * height_m)", "1 = 1", "m2", "window")),
    ("duplex", "relationship", "en", "How many staircases does the model contain?",
     lambda s: N(sum(e.attrs.get("category") == "stairs" for e in of(s, "other")), "count", exact=True), "entities of type other with attrs.category 'stairs'",
     agg("count(*)", "category = 'stairs'", "count", "other")),
    ("duplex", "derived", "en", "How many square metres of ceramic tile flooring are there?",
     lambda s: N(total([x for x in of(s, "slab") if "Ceramic Tile" in x.label]), "m2"), "sum of qty.m2 of slabs whose label names ceramic tile",
     agg("sum(m2)", "label LIKE '%Ceramic Tile%'", "m2", "slab")),
    ("duplex", "threshold", "en", "Which walls are thicker than 40 cm?",
     lambda s: E(s, [w for w in of(s, "wall") if w.attrs.get("thickness_m", 0) > 0.4]), "walls with attrs.thickness_m > 0.4",
     listed("thickness_m > 0.4", "thickness_m", "m", "wall")),
    ("duplex", "unanswerable", "en", "What is the fire rating of the doors?", lambda s: X("fire"), "no fire-rating attribute is recorded",
     missing("the doors' fire rating: no such attribute is recorded in this model's scene")),
    ("duplex", "relationship", "en", "How many columns does the model have?",
     lambda s: N(len(of(s, "column")), "count", exact=True), "column entities: the reader covers columns, so none found means none", agg("count(*)", "1 = 1", "count", "column")),
    ("duplex", "unanswerable", "en", "How many apartments are in this building?", lambda s: X("apartment"), "the IFC reader does not cover apartments",
     agg("count(*)", "1 = 1", "count", "apartment")),
    ("duplex", "other-language", "fi", "Mikä on tason Level 2 huoneiden yhteenlaskettu pinta-ala?",
     lambda s: N(total(of(s, "room", level="Level 2")), "m2"), "sum of qty.m2 of rooms on Level 2", agg("sum(m2)", "level = 'Level 2'", "m2")),

    # ---- IFC with quantity sets (FZK house) ----
    ("fzk", "ranking", "en", "Which room is the largest?", lambda s: E(s, best(of(s, "room"), m2), top=True), "the room with the greatest qty.m2", ranked("1 = 1")),
    ("fzk", "dimension", "en", "What is the total wall length?", lambda s: N(total(of(s, "wall"), "m"), "m"), "sum of qty.m of walls", agg("sum(m)", "1 = 1", "m", "wall")),
    ("fzk", "derived", "en", "What is the average room height?",
     lambda s: N(sum(r.attrs["height_m"] for r in of(s, "room")) / len(of(s, "room")), "m"), "mean attrs.height_m of rooms", agg("avg(height_m)", "1 = 1", "m")),
    ("fzk", "other-language", "de", "Wie gross ist die gesamte Fläche aller Räume?",
     lambda s: N(total(of(s, "room")), "m2"), "sum of qty.m2 of rooms", agg("sum(m2)", "1 = 1", "m2")),
    ("fzk", "threshold", "en", "Which rooms are smaller than 10 m²?",
     lambda s: E(s, [r for r in of(s, "room") if m2(r) < 10]), "rooms with qty.m2 < 10", listed("m2 < 10")),
    ("fzk", "ranking", "en", "Which wall is the longest?",
     lambda s: E(s, best(of(s, "wall"), lambda w: w.qty.get("m")), top=True), "the wall with the greatest qty.m", ranked("1 = 1", "m", "m", "wall")),

    # ---- IFC structural model with no rooms (clinic): floor area is the floor slabs, never the roofs ----
    # From a real wrong answer: every slab summed gave 7036.57 m2, 2286.9 m2 of it roof. Rooms first; floor slabs only
    # when the model has no rooms; roofs never.
    ("clinic", "derived", "en", "What is the total area of this building?",
     lambda s: N(total(of(s, "room")) if of(s, "room") else total(of(s, "slab", subtype="floor")), "m2"),
     "the model has no rooms, so the sum of qty.m2 of slabs of subtype floor (4749.63 m2); roof slabs are not floor area",
     {**agg("sum(m2)", "subtype = 'floor'", "m2", "slab"), "interpretation": "Floor area: the model has no rooms, so the floor slabs; roof slabs left out."}),
]
BEDS = "type = 'room' AND subtype = 'bedroom'"
WALL_788 = {"p": "SELECT m AS value, id FROM entities WHERE type = 'room' AND number = '7.08'"}
room = lambda s, number: of(s, "room", number=number)[0]
indoor = lambda s, apt: total(of(s, "room", apartment=apt, outdoor=False))
INDOOR = "SELECT sum(m2) AS value, group_concat(id) AS ids FROM entities WHERE type = 'room' AND outdoor = 0 AND apartment = '{}'"
on_level = lambda kind, lv: agg("count(*)", f"level = '{lv}'", "count", kind)
# Conversations: (what the follow-up refers by, [turns]); a turn is a case as above. Each follow-up's decision is
# self-contained: it selects from the scene again, by the earlier conditions or by ids from the earlier evidence.
CONVERSATIONS = [
    ("pronoun", [
        ("de", "derived", "en", "How big are the bathrooms in apartment A?",
         lambda s: N(total(of(s, "room", subtype="bathroom", apartment="A")), "m2"), "sum of qty.m2 of A's bathrooms",
         agg("sum(m2)", "subtype = 'bathroom' AND apartment = 'A'", "m2")),
        ("de", "follow-up", "en", "How many 30 x 30 cm tiles would that take, with 10% waste?",
         lambda s: N(math.ceil(total(of(s, "room", subtype="bathroom", apartment="A")) * 1.1 / 0.09), "count", exact=True),
         "ceil(the same sum, derived again, * 1.1 / 0.09)",
         {**computed("ceil(area * (1 + waste) / tile_m2)", "count", {"tile": "30 x 30 cm", "tile_m2": 0.09, "waste": 0.1},
                     area="SELECT sum(m2) AS value, group_concat(id) AS ids FROM entities WHERE type = 'room' AND subtype = 'bathroom' AND apartment = 'A'"),
          "interpretation": "30 x 30 cm floor tiles for the bathrooms of apartment A, with 10% waste"})]),
    ("omitted subject", [
        ("de", "relationship", "en", "How many windows do the bedrooms have?",
         lambda s: N(len(at(s, "window", of(s, "room", subtype="bedroom"))), "count", exact=True), "windows linked to a bedroom",
         direct(unit="count", **count("type = 'window' AND " + IN_ROOMS.format("r.subtype = 'bedroom'")))),
        ("de", "follow-up", "en", "And the kitchens?",
         lambda s: N(len(at(s, "window", of(s, "room", subtype="kitchen"))), "count", exact=True), "windows linked to a kitchen",
         {**direct(unit="count", **count("type = 'window' AND " + IN_ROOMS.format("r.subtype = 'kitchen'"))), "interpretation": "Number of windows in the kitchens"})]),
    ("changed scope", [
        ("de", "derived", "en", "What's the indoor area of apartment A?", lambda s: N(indoor(s, "A"), "m2"), "sum of qty.m2 of A's indoor rooms",
         direct(INDOOR.format("A"), unit="m2")),
        ("de", "follow-up", "en", "And B?", lambda s: N(indoor(s, "B"), "m2"), "the same for apartment B",
         {**direct(INDOOR.format("B"), unit="m2"), "interpretation": "Indoor area of apartment B"}),
        ("de", "follow-up", "en", "What's the difference between the two?", lambda s: N(indoor(s, "A") - indoor(s, "B"), "m2"),
         "both sums derived again from the rooms, A minus B",
         {**computed("a - b", "m2", a=INDOOR.format("A"), b=INDOOR.format("B")), "interpretation": "Indoor area of apartment A minus that of apartment B"})]),
    ("which of those", [
        ("de", "threshold", "en", "Which rooms are bigger than 15 m²?", lambda s: E(s, [r for r in of(s, "room") if m2(r) > 15]), "rooms with qty.m2 > 15",
         listed("m2 > 15")),
        ("de", "follow-up", "en", "Which of those is the smallest?",
         lambda s: E(s, best([r for r in of(s, "room") if m2(r) > 15], m2, least=True), top=True), "least qty.m2 among the rooms over 15 m2",
         {**ranked("m2 > 15", least=True), "interpretation": "The smallest of the rooms bigger than 15 m2"})]),
    ("changed assumption", [
        ("de", "derived", "en", "What would parquet for all the bedrooms cost at 85 francs per square metre?",
         lambda s: N(85 * total(of(s, "room", subtype="bedroom"))), "85 times the sum of qty.m2 of the bedrooms",
         computed("area * price_per_m2", assumptions={"price_per_m2": 85}, area=f"SELECT sum(m2) AS value, group_concat(id) AS ids FROM entities WHERE {BEDS}")),
        ("de", "follow-up", "en", "And at 110?", lambda s: N(110 * total(of(s, "room", subtype="bedroom"))), "110 times the same sum, derived again",
         {**computed("area * price_per_m2", assumptions={"price_per_m2": 110}, area=f"SELECT sum(m2) AS value, group_concat(id) AS ids FROM entities WHERE {BEDS}"),
          "interpretation": "Parquet for all the bedrooms at 110 francs per m2"})]),
    ("supplies what was missing", [
        ("de", "unanswerable", "en", "How much wall area is there to paint in bedroom 7.08?", lambda s: X("height"),
         "wall area needs the ceiling height, which the plan does not record",
         {**missing("the ceiling height of room 7.08: no height_m is recorded for rooms", ceiling_height_m="the ceiling height in metres"),
          "queries": WALL_788, "formula": "p * ceiling_height_m", "unit": "m2"}),
        ("de", "follow-up", "en", "The ceilings are 2.6 m high.", lambda s: N(room(s, "7.08").qty["m"] * 2.6, "m2"),
         "qty.m (perimeter) of room 7.08 times the 2.6 m the user states",
         {**computed("p * ceiling_height_m", "m2", {"ceiling_height_m": 2.6}, **WALL_788), "interpretation": "Wall area of bedroom 7.08 with a 2.6 m ceiling, openings not deducted"}),
        ("de", "follow-up", "en", "And with the door and window openings taken off?", lambda s: X("height"),
         "the openings' widths are recorded, their heights are not: still not answerable",
         missing("the heights of the doors and windows of room 7.08: no height_m is recorded for doors or windows", opening_height_m="the height of the openings in metres"))]),
    ("pronoun", [
        ("de", "ranking", "en", "Which room is the largest?", lambda s: E(s, best(of(s, "room"), m2), top=True), "the room with the greatest qty.m2", ranked("1 = 1")),
        ("de", "follow-up", "en", "How many doors does it have?", lambda s: N(len(at(s, "door", best(of(s, "room"), m2))), "count", exact=True),
         "doors linked to that room (room-7.03, from the earlier evidence)",
         {**direct(unit="count", **count("type = 'door' AND id IN (SELECT entity_id FROM links WHERE room_id = 'room-7.03')")), "interpretation": "Number of doors of Wohnen 7.03"}),
        ("de", "follow-up", "en", "And windows?", lambda s: N(len(at(s, "window", best(of(s, "room"), m2))), "count", exact=True),
         "windows linked to that room: none, and windows are covered, so zero",
         {**direct(unit="count", **count("type = 'window' AND id IN (SELECT entity_id FROM links WHERE room_id = 'room-7.03')")), "interpretation": "Number of windows of Wohnen 7.03"})]),
    ("the same for", [
        ("de", "dimension", "en", "What is the perimeter of kitchen 7.02?", lambda s: N(room(s, "7.02").qty["m"], "m"), "qty.m of room 7.02", value("number = '7.02'", "m", "m")),
        ("de", "follow-up", "en", "The same for 7.13.", lambda s: N(room(s, "7.13").qty["m"], "m"), "qty.m of room 7.13",
         {**value("number = '7.13'", "m", "m"), "interpretation": "Perimeter of room 7.13"})]),
    ("those", [
        ("de", "relationship", "en", "Which room has the most doors?",
         lambda s: E(s, best(of(s, "room"), lambda r: len(at(s, "door", [r]))), top=True), "per room, the doors linked to it; most first",
         direct(PER_ROOM.format(agg="count(x.id)", kind="door", where=""), answer="top", unit="count")),
        ("de", "follow-up", "en", "Which rooms do those doors lead to?",
         lambda s: E(s, [r for r in of(s, "room") if r.number != "7.06" and any(r.id in endpoints(d, s) for d in at(s, "door", [room(s, "7.06")]))]),
         "the other endpoint of each door linked to Korridor 7.06",
         {**direct("SELECT DISTINCT r.id FROM entities r JOIN links l ON l.room_id = r.id WHERE r.type = 'room' AND r.id <> 'room-7.06' AND l.entity_id IN "
                   "(SELECT d.id FROM entities d JOIN links k ON k.entity_id = d.id WHERE d.type = 'door' AND k.room_id = 'room-7.06')", answer="rows"),
          "interpretation": "The rooms on the other side of the doors of Korridor 7.06"})]),
    ("omitted subject", [
        ("fr", "other-language", "fr", "Quelle est la surface totale des chambres à coucher ?",
         lambda s: N(total(of(s, "room", label="Chambre à coucher")), "m2"), "sum of qty.m2 of rooms labelled 'Chambre à coucher'", agg("sum(m2)", "label = 'Chambre à coucher'", "m2")),
        ("fr", "follow-up", "fr", "Et celle des balcons ?", lambda s: N(total(of(s, "room", label="Balcon")), "m2"), "sum of qty.m2 of rooms labelled 'Balcon'",
         {**agg("sum(m2)", "label = 'Balcon'", "m2"), "interpretation": "Surface totale des balcons"})]),
    ("changed scope", [
        ("duplex", "relationship", "en", "How many rooms are on Level 1?", lambda s: N(len(of(s, "room", level="Level 1")), "count", exact=True), "rooms with level 'Level 1'",
         on_level("room", "Level 1")),
        ("duplex", "follow-up", "en", "What about Level 2?", lambda s: N(len(of(s, "room", level="Level 2")), "count", exact=True), "rooms with level 'Level 2'",
         {**on_level("room", "Level 2"), "interpretation": "Number of rooms on Level 2"}),
        ("duplex", "follow-up", "en", "Which of the two has more floor area?",
         lambda s: G(best(("Level 1", "Level 2"), lambda lv: total(of(s, "room", level=lv)))), "sum of qty.m2 of rooms per level, of those two",
         {**direct("SELECT level AS name, sum(m2) AS value, group_concat(id) AS ids FROM entities WHERE type = 'room' AND level IN ('Level 1', 'Level 2') "
                   "GROUP BY level ORDER BY value DESC", answer="top", unit="m2"), "interpretation": "Which of Level 1 and Level 2 has more room floor area"})]),
    ("of those", [
        ("duplex", "threshold", "en", "Which doors are wider than 80 cm?", lambda s: E(s, [d for d in of(s, "door") if d.qty["m"] > 0.8]), "doors with qty.m > 0.8",
         listed("m > 0.8", "m", "m", "door")),
        ("duplex", "follow-up", "en", "How many of those are on Level 2?",
         lambda s: N(sum(d.qty["m"] > 0.8 for d in of(s, "door", level="Level 2")), "count", exact=True), "doors with qty.m > 0.8 and level 'Level 2'",
         {**agg("count(*)", "m > 0.8 AND level = 'Level 2'", "count", "door"), "interpretation": "Doors wider than 80 cm on Level 2"})]),
    ("pronoun", [
        ("duplex", "ranking", "en", "Which slab is the thickest?", lambda s: E(s, best(of(s, "slab"), lambda x: x.attrs.get("thickness_m")), top=True),
         "greatest attrs.thickness_m over slabs", ranked("1 = 1", "thickness_m", "m", "slab")),
        ("duplex", "follow-up", "en", "What's its area?", lambda s: N(best(of(s, "slab"), lambda x: x.attrs.get("thickness_m"))[0].qty["m2"], "m2"), "qty.m2 of that slab",
         {**value("thickness_m IS NOT NULL ORDER BY thickness_m DESC LIMIT 1", "m2", "m2", "slab"), "interpretation": "Top area of the thickest slab"})]),
    ("switched drawing", [
        ("de", "threshold", "en", "How many bedrooms are there?", lambda s: N(len(of(s, "room", subtype="bedroom")), "count", exact=True), "rooms of subtype bedroom",
         agg("count(*)", "subtype = 'bedroom'", "count")),
        ("fr", "follow-up", "en", "And in this one?", lambda s: N(len(of(s, "room", subtype="bedroom")), "count", exact=True),
         "the same count on the drawing now open; the earlier turn's ids belong to the other drawing and are dropped",
         {**agg("count(*)", "subtype = 'bedroom'", "count"), "interpretation": "Number of bedrooms in this drawing"})]),
    ("pronoun", [
        ("it", "other-language", "it", "Qual è la stanza più grande?", lambda s: E(s, best(of(s, "room"), m2), top=True), "the room with the greatest qty.m2", ranked("1 = 1")),
        ("it", "follow-up", "it", "Quante finestre ha?", lambda s: N(len(at(s, "window", best(of(s, "room"), m2))), "count", exact=True), "windows linked to that room",
         {**direct(unit="count", **count("type = 'window' AND id IN (SELECT entity_id FROM links WHERE room_id = "
                                         "(SELECT id FROM entities WHERE type = 'room' AND m2 IS NOT NULL ORDER BY m2 DESC LIMIT 1))")),
          "interpretation": "Numero di finestre della stanza più grande"})]),
    ("pronoun", [
        ("fzk", "dimension", "en", "What is the total wall length?", lambda s: N(total(of(s, "wall"), "m"), "m"), "sum of qty.m of walls", agg("sum(m)", "1 = 1", "m", "wall")),
        ("fzk", "follow-up", "en", "And their combined side area?", lambda s: N(total(of(s, "wall")), "m2"), "sum of qty.m2 of walls",
         {**agg("sum(m2)", "1 = 1", "m2", "wall"), "interpretation": "Combined net side area of all walls"})]),
    ("changed assumption", [
        ("de", "threshold", "en", "How many rooms are under five square metres?", lambda s: N(sum(m2(r) < 5 for r in of(s, "room")), "count", exact=True),
         "rooms with qty.m2 < 5", agg("count(*)", "m2 < 5", "count")),
        ("de", "follow-up", "en", "And under ten?", lambda s: N(sum(m2(r) < 10 for r in of(s, "room")), "count", exact=True), "rooms with qty.m2 < 10",
         {**agg("count(*)", "m2 < 10", "count"), "interpretation": "Number of rooms under 10 m2"})]),
]
BY_SUBTYPE = "SELECT count(*) AS value, group_concat(id) AS ids FROM entities WHERE type = 'room' AND subtype = '{}'"
BY_TYPE = "SELECT count(*) AS value, group_concat(id) AS ids FROM entities WHERE type = '{}'"


def _image_cases() -> list[dict]:
    """Image scenes need the detector and the vision reader, which need keys. Their references come from the dataset's
    own gold counts for one cubicasa5k plan of the test split: free-form questions whose answers follow from them."""
    from blueprints.brain.plan import _rule
    from blueprints.scoring import split_of
    gold: dict[str, dict] = {}
    for line in (pipeline.DATA_ROOT / "questions.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if r["path"].startswith("data/cubicasa5k/") and r["path"].endswith(".png") and split_of(r["path"]) == "test" and (plan := _rule(r["question"])):
            q = next(iter(plan.queries.values()))
            if len(plan.queries) == 1 and q.op == "count":
                gold.setdefault(r["path"], {})[q.subtype or q.type] = int(r["answer"])
    need = ("bedroom", "bathroom", "door", "window")
    path, g = next((p, g) for p, g in sorted(gold.items()) if all(k in g for k in need) and g["bedroom"] != g["bathroom"] and g["bathroom"] > 0)
    how = f"from the dataset's gold counts for this plan: {', '.join(f'{k} {g[k]}' for k in need)}"
    make = lambda kind, question, expect, decision: {"scene": "img", "path": path, "reader": "image", "needs_vision": True, "kind": kind, "lang": "en",
                                                     "question": question, "expect": expect, "how": how, "decision": decision}
    return [
        make("comparison", "Are there more bedrooms or more bathrooms in this home?", G(["bedroom" if g["bedroom"] > g["bathroom"] else "bathroom"]),
             direct("SELECT subtype AS name, count(*) AS value, group_concat(id) AS ids FROM entities WHERE type = 'room' AND subtype IN ('bedroom', 'bathroom') "
                    "GROUP BY subtype ORDER BY value DESC", answer="top", unit="count")),
        make("derived", "How many doors and windows are there altogether?", N(g["door"] + g["window"], "count", exact=True),
             computed("d + w", "count", d=BY_TYPE.format("door"), w=BY_TYPE.format("window"))),
        make("derived", "How many bedrooms are there per bathroom?", N(g["bedroom"] / g["bathroom"]),
             computed("beds / baths", beds=BY_SUBTYPE.format("bedroom"), baths=BY_SUBTYPE.format("bathroom"))),
        make("relationship", "How many rooms to sleep in does this place have?", N(g["bedroom"], "count", exact=True),
             direct(BY_SUBTYPE.format("bedroom"), unit="count")),
    ]


def build(scenes: dict) -> list[dict]:
    row = lambda key, kind, lang, question, ref, how, decision: {
        "scene": key, "path": SCENES[key][0], "reader": SCENES[key][1], "kind": kind, "lang": lang, "question": question,
        "expect": ref(scenes[key]), "how": how, "decision": decision}
    rows = [row(*case) for case in CASES] + _image_cases()
    for n, (refers, turns) in enumerate(CONVERSATIONS, 1):
        rows += [{**row(*turn), "conversation": f"conv-{n:02d}", "turn": k, **({"refers": refers} if k > 1 else {})} for k, turn in enumerate(turns, 1)]
    return [{"id": f"ff-{i + 1:03d}", **row} for i, row in enumerate(rows)]


def turn_of(row: dict, answer) -> dict:
    """An answered question as the browser sends it back in `history` (frontend-d/history-state.js, conversationTurns)."""
    query = " | ".join(s.detail for s in answer.trace if s.step in ("plan", "execute") and s.detail)
    return {"question": row["question"], "answer": answer.answer, "unit": answer.unit, "error": answer.error if answer.answer is None else None,
            "interpretation": answer.interpretation, "query": query, "assumptions": answer.assumptions, "evidence": answer.evidence[:12],
            "evidence_count": len(answer.evidence), "path": row["path"]}


# ---- scoring ----

def said_names(text: str) -> list[str]:
    """The names in a composed answer: 'A, B and C (tied)' -> [A, B, C]."""
    text = re.sub(r" \(tied\)$", "", str(text))
    return [] if text == "None" else sorted(p for part in text.split(", ") for p in part.split(" and "))


def correct(expect: dict, answer) -> bool:
    if expect["type"] == "abstain":
        return answer.answer is None and any(k.casefold() in (answer.error or "").casefold() for k in expect["missing"])
    if answer.answer is None:
        return False
    if expect["type"] == "number":
        try:
            got = float(answer.answer)
        except (TypeError, ValueError):
            return False
        return abs(got - expect["value"]) <= expect["tolerance"] * abs(expect["value"]) + 1e-9
    said, want = said_names(answer.answer), sorted(expect["names"])
    more = [m for m in map(re.compile(r"(\d+) more").fullmatch, said) if m]  # a long list names its first twelve: "..., X and 11 more"
    if more:
        named = Counter(n for n in said if n != more[0][0])
        return sum(named.values()) + int(more[0][1]) == len(want) and not named - Counter(want)
    return said == want and str(answer.answer).endswith("(tied)") == expect["tied"]


def main() -> None:
    scenes = {key: load(key) for key in SCENES if SCENES[key][1] != "image"}
    fresh = build(scenes)
    if "--write" in sys.argv:
        FILE.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in fresh), encoding="utf-8")
        print(f"wrote {len(fresh)} questions to {FILE}")
    stored = [json.loads(line) for line in FILE.open(encoding="utf-8")]
    key = bool(os.environ.get("GEMINI_API_KEY"))
    stale = [r["id"] for r, f in zip(stored, fresh) if r != f] + ["count"] * (len(stored) != len(fresh))
    print(f"{len(stored)} questions; references recomputed from the scenes: {'all match the file' if not stale else f'STALE {stale}'}")
    print(Counter(r["kind"] for r in stored), Counter(r["scene"] for r in stored), Counter(r["lang"] for r in stored), sep="\n")
    print(f"{len({r['conversation'] for r in stored if 'conversation' in r})} conversations; follow-ups by what they refer with: "
          f"{dict(Counter(r['refers'] for r in stored if r.get('refers')))}")

    price_in, price_out = TOKEN_PRICES[resolve.GEMINI_MODEL]
    tally, usd, sizes, added = Counter(), 0.0, {}, []
    talks: dict[str, dict] = {}  # conversation -> the turns so far, by hand and by the model
    for r in stored:
        talk = talks.setdefault(r.get("conversation", r["id"]), {"hand": [], "model": []})
        if r.get("needs_vision"):
            tally["hand", "needs vision"] += 1
            if not key:
                tally["model", "needs key"] += 1
                continue
            got = pipeline.answer(r["path"], r["question"])
        else:
            scene = scenes[r["scene"]]
            inv = resolve.inventory(scene)
            history = resolve.clean_history(talk["hand"], scene)
            alone = resolve.tokens("".join(m["content"] for m in resolve.messages(r["question"], inv)))
            if r.get("turn", 1) > 1:
                added.append(resolve.tokens("".join(m["content"] for m in resolve.messages(r["question"], inv, None, history))) - alone)
            else:
                sizes[r["scene"]] = alone
            try:
                decision = resolve.parse(json.dumps(r["decision"]))
                resolve._not_copied(decision, r["question"], history)  # a follow-up's query stands on its own
                by_hand = resolve.execute(decision, scene)
                ok = correct(r["expect"], by_hand)
            except resolve.QueryError as e:
                by_hand, ok = resolve.Answer(error=str(e)), False
            talk["hand"].append(turn_of(r, by_hand))
            tally["hand", "right" if ok else "WRONG"] += 1
            tally["hand follow-up", ok] += r.get("turn", 1) > 1
            if not ok:
                print(f"  HAND-WRITTEN DECISION WRONG {r['id']} {r['question']}\n     expected {r['expect']}\n     got {by_hand.answer!r} {by_hand.error or ''}")
            if not key:
                tally["model", "needs key"] += 1
                continue
            try:
                got = resolve.resolve(r["question"], scene, None, talk["model"])
            except NotImplementedError as e:
                got = resolve.Answer(error=str(e))
            talk["model"].append(turn_of(r, got))
        ok = correct(r["expect"], got)
        kind = f"follow-up ({r['refers']})" if r.get("refers") else r["kind"]
        tally["model", "right" if ok else "wrong"] += 1
        tally[kind, ok] += 1
        tally["abstained", r["expect"]["type"] == "abstain", got.answer is None] += 1
        usd += got.cost_usd
        if not ok:
            print(f"  model wrong {r['id']} [{kind}] {r['question']}\n     expected {r['expect']}\n     got {got.answer!r} {got.error or ''}")

    hand = {k[1]: v for k, v in tally.items() if k[0] == "hand"}
    print(f"\nhand-written decisions through the executor: {hand.get('right', 0)} right, {hand.get('WRONG', 0)} wrong, "
          f"{hand.get('needs vision', 0)} not runnable here (image scenes need the detector and vision reader)")
    print(f"of which follow-up turns: {tally['hand follow-up', True]} right, {tally['hand follow-up', False]} wrong")
    print("decision prompt, estimated tokens (system + inventory + question): " + ", ".join(f"{k} ~{v}" for k, v in sizes.items()))
    extra = sum(added) / max(1, len(added))
    print(f"a follow-up adds the history rules and the earlier turns: ~{extra:.0f} tokens on average (min {min(added)}, max {max(added)}), "
          f"${extra * price_in / 1e6:.5f} at these prices")
    typical = sum(sizes.values()) / max(1, len(sizes))
    print(f"estimated cost of a new question at {resolve.GEMINI_MODEL} prices (${price_in}/M in, ${price_out}/M out), ~{typical:.0f} in + ~120 out: "
          f"${(typical * price_in + 120 * price_out) / 1e6:.5f}; a repeat: $0")
    if not key:
        print(f"model step: needs key ({tally['model', 'needs key']} questions not asked; set GEMINI_API_KEY to measure accuracy and real cost)")
        raise SystemExit(bool(stale) or bool(hand.get("WRONG")))
    n = tally["model", "right"] + tally["model", "wrong"]
    print(f"model: {tally['model', 'right']}/{n} right ({tally['model', 'right'] / n:.0%}); ${usd:.4f} in all, ${usd / n:.5f} per question")
    for kind in sorted({k[0] for k in tally if k[0] not in ("hand", "model", "abstained", "hand follow-up")}):
        print(f"  {kind:36} {tally[kind, True]}/{tally[kind, True] + tally[kind, False]}")
    print(f"abstention: abstained when it should {tally['abstained', True, True]}/{tally['abstained', True, True] + tally['abstained', True, False]}; "
          f"abstained when an answer exists {tally['abstained', False, True]}/{tally['abstained', False, True] + tally['abstained', False, False]}")


if __name__ == "__main__":
    main()
