"""Lane C. Question -> QueryPlan.

Today: hand-written rules for the dataset's English templates on cubicasa5k and swiss_dwellings, so lanes A
and B can measure their scenes before the model parser exists. Next: for anything the rules miss (other
languages, free wording, derived questions), call Gemini with QueryPlan as the structured output and cache the
result by template.
"""
import re

from ..schemas import Query, QueryPlan, Scene


def _count(type: str, subtype: str | None = None, **filters) -> QueryPlan:
    return QueryPlan(queries={"n": Query(op="count", type=type, subtype=subtype, **filters)}, unit="count")


def _area(**filters) -> QueryPlan:
    return QueryPlan(queries={"area": Query(op="sum", type="room", qty="m2", **filters)}, unit="m2")


def _showers_and_bathtubs(_match) -> QueryPlan:
    return QueryPlan(
        queries={"showers": Query(op="count", type="fixture", subtype="shower"),
                 "bathtubs": Query(op="count", type="fixture", subtype="bathtub")},
        formula="showers + bathtubs", unit="count")


RULES = [
    (r"^How many windows are (shown|drawn) on this floor plan", lambda m: _count("window")),
    (r"^How many doors\b.*this floor plan", lambda m: _count("door")),
    (r"^How many floors \(storeys\)", lambda m: _count("level")),
    (r"^How many (toilet bowls|toilets \(WC\))", lambda m: _count("fixture", "toilet")),
    (r"^How many (sinks and wash basins|washbasins)", lambda m: _count("fixture", "sink")),
    (r"^How many showers and bathtubs", _showers_and_bathtubs),
    (r"^How many bathtubs", lambda m: _count("fixture", "bathtub")),
    (r"^How many showers are", lambda m: _count("fixture", "shower")),
    (r"^How many saunas", lambda m: _count("room", "sauna")),
    (r"^How many kitchens", lambda m: _count("room", "kitchen")),
    (r"^How many bedrooms", lambda m: _count("room", "bedroom")),
    (r"^How many bathrooms or washrooms", lambda m: _count("room", "bathroom")),
    (r"^How many separate WC rooms", lambda m: _count("room", "wc_room")),
    (r"^How many balconies", lambda m: _count("room", "balcony")),
    (r"^How many apartments", lambda m: _count("apartment")),
    (r"^How many rooms labelled '(.+)' are there on this floor", lambda m: _count("room", label=m[1])),
    (r"^What is the total net area in m² of all rooms labelled '(.+)' on this floor", lambda m: _area(label=m[1])),
    (r"^What is the net floor area of room (\S+) \('", lambda m: _area(number=m[1])),
    (r"^What is the net floor area of apartment (\S+) in m²", lambda m: _area(apartment=m[1], outdoor=False)),
    (r"^What is the total net floor area in m² of all numbered indoor rooms on this floor", lambda m: _area(outdoor=False)),
]


def make_plan(question: str, scene: Scene) -> QueryPlan:
    for pattern, build in RULES:
        match = re.match(pattern, question)
        if match:
            return build(match)
    raise NotImplementedError("lane C: no rule for this question and the model parser is not written yet")
