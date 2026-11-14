"""Legend/window regression, provisional cutoff and auditable calculation evidence."""
import json
import unittest
from unittest.mock import patch

from blueprints.schemas import Entity, Page, Scene, Query, QueryPlan
from blueprints.executor import execute
from blueprints.brain.room_link_vision import Link, _accept, _directory
from blueprints.calculation_evidence import deduplicate_corrected


def box(a, b, c, d):
    return [(a, b), (c, b), (c, d), (a, d)]


def scene():
    room = Entity(id="corridor", type="room", label="Couloir", number="4.05", conf=.7, geom=box(.5, .34, .68, .41))
    doors = [Entity(id=f"d{i}", type="door", conf=.9, geom=box(.51+i*.02, .34, .52+i*.02, .36),
                    attrs={"room_id": room.id, "room_link_status": "linked"}) for i in range(7)]
    legend = Entity(id="legend", type="door", conf=.75, geom=box(.138, .904, .15, .924),
                    attrs={"room_link_status": "uncertain", "room_link_candidates": "[]"})
    return Scene(path="uploads/swiss.png", builder="test", tier="detect", pages=[Page(image="uploads/swiss.png", width=2480, height=1753)],
                 covers=["door", "window", "room"], entities=[room]+doors+[legend])


def ask(source, **where):
    return execute(QueryPlan(queries={"n": Query(op="count", type="door", where=where)}, unit="count"), source)


def audit(answer):
    return json.loads(next(step.detail for step in answer.trace if step.step == "calculation_evidence"))


class CalculationEvidence(unittest.TestCase):
    def test_cached_legend_review_unblocks_seven_doors_without_a_model_call(self):
        source = scene()
        self.assertIsNone(ask(source, connected_room_number="4.05").answer)
        review = Link(object_id="legend", clear=False, endpoints=[], reason="Legend item outside floor plan")
        with patch("blueprints.brain.room_link_vision._request", side_effect=AssertionError("no new call")):
            self.assertTrue(_accept(source, source.entities[-1], review))
            answer = ask(source, connected_room_number="4.05")
        self.assertEqual(answer.answer, 7)
        self.assertAlmostEqual(answer.confidence, .7)
        record = audit(answer)
        self.assertEqual(record["threshold"], .6)
        self.assertEqual(record["queries"][0]["included"], [f"d{i}" for i in range(7)])
        excluded = {e["id"]: e["reason"] for e in record["queries"][0]["excluded"]}
        self.assertIn("Legend", excluded["legend"])
        self.assertNotIn("legend", answer.evidence)

    def test_legend_description_inside_plan_does_not_exclude_a_real_door(self):
        source = scene()
        source.entities[-1].geom = box(.51, .35, .52, .37)
        self.assertFalse(_accept(source, source.entities[-1], Link(object_id="legend", clear=False, endpoints=[], reason="Legend item outside floor plan")))
        self.assertIsNone(ask(source, connected_room_number="4.05").answer)

    def test_explicit_new_classification_separates_legend_from_endpoint_certainty(self):
        source = scene()
        review = Link(object_id="legend", clear=False, endpoints=[], reason="A labelled sample symbol", observed_type="non_object",
                      classification_clear=True, non_object_kind="legend")
        self.assertTrue(_accept(source, source.entities[-1], review))
        self.assertEqual(ask(source, connected_room_number="4.05").answer, 7)

    def test_legacy_window_correction_preserves_cache_key_and_original_detection(self):
        source = scene()
        entity = source.entities[1]
        before = _directory(source)
        _accept(source, entity, Link(object_id=entity.id, clear=False, endpoints=[], reason="Exterior window on Pièce (4.21)"))
        self.assertEqual(entity.type, "window")
        self.assertEqual(entity.attrs["detection_original_type"], "door")
        self.assertEqual(_directory(source), before)
        self.assertEqual(entity.conf, .9)

    def test_correction_does_not_duplicate_an_already_detected_window(self):
        source = scene()
        corrected = source.entities[1]
        _accept(source, corrected, Link(object_id=corrected.id, clear=False, endpoints=[], reason="Exterior window/opening on the top wall"))
        source.entities.append(corrected.model_copy(deep=True, update={"id": "existing-window", "conf": .95, "attrs": {}}))
        deduplicate_corrected(source)
        answer = execute(QueryPlan(queries={"n": Query(op="count", type="window")}, unit="count"), source)
        self.assertEqual(answer.answer, 1)
        self.assertEqual(answer.evidence, ["existing-window"])
        self.assertEqual(corrected.attrs["calculation_duplicate_of"], "existing-window")

    def test_narrative_window_mention_does_not_reclassify_a_door(self):
        source = scene()
        _accept(source, source.entities[1], Link(object_id="d0", clear=False, endpoints=[], reason="Door beside an exterior window"))
        self.assertEqual(source.entities[1].type, "door")

    def test_cutoff_is_not_a_confidence_cap_and_lower_scores_remain_auditable(self):
        source = scene()
        source.entities.pop()  # no legend
        source.entities[1].conf = .59
        answer = ask(source, connected_room_number="4.05")
        self.assertEqual(answer.answer, 6)
        self.assertAlmostEqual(answer.confidence, .7*6/7)
        self.assertEqual(audit(answer)["queries"][0]["coverage"], 6/7)
        self.assertNotIn("d0", answer.evidence)
        self.assertIn("below", next(e["reason"] for e in audit(answer)["queries"][0]["excluded"] if e["id"] == "d0"))

    def test_unsupported_calculation_and_all_below_cutoff_are_not_confident_zero(self):
        source = scene()
        source.entities.pop()
        for entity in source.entities[1:]:
            entity.conf = .59
        answer = ask(source, connected_room_number="4.05")
        self.assertIsNone(answer.answer)
        self.assertEqual(answer.confidence, 0)
        self.assertIn("60%", answer.error)

    def test_exactly_sixty_percent_is_included(self):
        source = scene()
        source.entities = source.entities[:2]
        source.entities[1].conf = .6
        answer = ask(source, connected_room_number="4.05")
        self.assertEqual(answer.answer, 1)
        self.assertAlmostEqual(answer.confidence, .6)

    def test_real_unresolved_connection_keeps_supported_doors_visible_without_a_total(self):
        source = scene()
        answer = ask(source, connected_room_number="4.05")
        self.assertIsNone(answer.answer)
        self.assertEqual(audit(answer)["queries"][0]["unresolved"], ["legend"])
        self.assertTrue(set(f"d{i}" for i in range(7)).issubset(answer.evidence))

    def test_native_cad_does_not_inherit_the_raster_cutoff(self):
        source = scene().model_copy(update={"path": "uploads/swiss.dxf", "tier": "parse"})
        source.entities = source.entities[:2]
        source.entities[1].conf = .3
        self.assertEqual(ask(source).answer, 1)


if __name__ == "__main__":
    unittest.main()
