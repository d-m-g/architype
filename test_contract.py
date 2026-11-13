"""The contract check. Run it before you push:  uv run python test_contract.py

It keeps fixtures/ valid against the schemas and pins the behaviour all four lanes rely on.
The expected numbers are the real gold answers for that drawing in questions.jsonl.
"""
import json
from pathlib import Path

from blueprints import pipeline
from blueprints.brain.plan import make_plan
from blueprints.executor import evaluate, execute
from blueprints.schemas import Answer, Entity, Query, QueryPlan, Scene
from blueprints.scoring import is_correct, split_of

FIXTURES = Path(__file__).parent / "fixtures"
load = lambda name: json.loads((FIXTURES / name).read_text(encoding="utf-8"))
SCENE = Scene.model_validate(load("scene.example.json"))


def ask(question: str) -> Answer:
    return execute(make_plan(question, SCENE), SCENE)


def test_fixtures_match_the_schemas():
    plan = QueryPlan.model_validate(load("plan.example.json"))
    stored = Answer.model_validate(load("answer.example.json"))
    got = execute(plan, SCENE)
    assert (got.answer, got.unit, got.evidence, got.formula) == (stored.answer, stored.unit, stored.evidence, stored.formula)
    assert all(json.loads(line) for line in (FIXTURES / "run.example.jsonl").read_text(encoding="utf-8").splitlines())


def test_dataset_questions_on_the_example_scene():
    assert ask("How many apartments (residential units) are shown on this floor plan?").answer == 1
    assert ask("How many toilets (WC) are drawn on this floor plan?").answer == 1
    assert ask("How many washbasins are drawn on this floor plan?").answer == 1
    assert ask("How many rooms labelled 'Ripostiglio' are there on this floor?").answer == 2
    assert ask("What is the net floor area of room 5.05 ('Cucina') in m²?").answer == 5.34
    apartment = ask("What is the net floor area of apartment A in m², summing all its rooms except balconies, loggias, terraces and other outdoor spaces?")
    assert is_correct(apartment.answer, apartment.unit, 72.0, "m2", "area")
    total = ask("What is the total net floor area in m² of all numbered indoor rooms on this floor (excluding balconies, loggias, terraces and other outdoor spaces, shafts and voids)?")
    assert is_correct(total.answer, total.unit, 86.11, "m2", "area") and len(total.evidence) == 8


def test_confidence_and_evidence_come_from_the_entities():
    bathtubs = ask("How many bathtubs are drawn on this floor plan?")
    assert (bathtubs.answer, bathtubs.confidence, bathtubs.evidence) == (1, 0.9, ["fix-bathtub-1"])


def test_uncovered_or_unmatched_is_refused_not_zero():
    doors = ask("How many doors (interior doors and entrance doors together) are drawn on this floor plan?")
    assert doors.answer is None and "does not cover door" in doors.error
    nothing = ask("What is the total net area in m² of all rooms labelled 'Atrium' on this floor?")
    assert nothing.answer is None and nothing.error


def test_derived_question_uses_the_formula():
    tiles = execute(QueryPlan.model_validate(load("plan.tiles.example.json")), SCENE)
    assert tiles.answer == 31 and tiles.unit == "count" and tiles.evidence == ["room-5.06"]


def test_formula_is_arithmetic_only():
    assert evaluate("ceil(a * 1.1 / b)", {"a": 4.49, "b": 0.16}) == 31
    for hostile in ["__import__('os').system('true')", "a.real", "[a][0]", "a ** 99", "unknown + 1"]:
        try:
            evaluate(hostile, {"a": 1})
        except ValueError:
            continue
        raise AssertionError(f"evaluate accepted {hostile!r}")


def test_schema_rejects_the_likely_mistakes():
    for bad in [
        dict(id="x", type="fixture", subtype="wc"),            # not in SUBTYPES
        dict(id="x", type="door", geom=[[120, 40], [1, 1]]),   # pixels, not normalised
        dict(id="x", type="door", sub_type="a"),               # misspelled field
    ]:
        try:
            Entity.model_validate(bad)
        except ValueError:
            continue
        raise AssertionError(f"Entity accepted {bad}")
    try:
        Query(op="sum", type="room")
    except ValueError:
        pass
    else:
        raise AssertionError("a sum without qty was accepted")


def test_scoring_rules():
    assert is_correct(13, "count", 13, "count", "count") and not is_correct(12, "count", 13, "count", "count")
    assert is_correct(101.9, "m2", 100, "m2", "area") and not is_correct(102.1, "m2", 100, "m2", "area")
    assert not is_correct(100, "ft2", 100, "m2", "area")       # right number, wrong unit
    assert not is_correct(None, None, 5, "count", "count")     # no answer is a wrong answer
    assert is_correct("anything", None, "free text", None, "text") is None


def test_split_is_stable():
    pdf = "data/swiss_dwellings/site1101_bldg2241_floor10275.pdf"
    assert split_of(pdf) == split_of(pdf[:-3] + "dxf") == "train"


def test_paths_outside_the_dataset_are_refused():
    for path in ["../PLAN.md", "data/../../PLAN.md", "/etc/hosts"]:
        try:
            pipeline.dataset_file(path)
        except ValueError:
            continue
        raise AssertionError(f"dataset_file accepted {path!r}")


if __name__ == "__main__":
    tests = [fn for name, fn in sorted(globals().items()) if name.startswith("test_")]
    for fn in tests:
        fn()
    print(f"ok: {len(tests)} checks passed")
