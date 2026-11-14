"""Offline connection/count regressions; no model calls or dataset files required."""
import json
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from blueprints.executor import execute
from blueprints.image_room_links import attach_room_links
from blueprints.schemas import Entity, Page, Query, QueryPlan, Scene


def rectangle(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def source():
    return Scene(path="uploads/test.png", builder="test", tier="detect", covers=["room", "door", "window"],
                 pages=[Page(image="uploads/test.png", width=400, height=400)], entities=[
                     Entity(id="a", type="room", label="Bedroom", number="1", apartment="A", conf=.8,
                            geom=rectangle(.05, .05, .49, .95)),
                     Entity(id="b", type="room", label="Bedroom", number="2", apartment="B", conf=.9,
                            geom=rectangle(.51, .05, .95, .95)),
                     Entity(id="d", type="door", conf=.95, geom=rectangle(.46, .4, .59, .6)),
                 ])


def count(scene, **where):
    return execute(QueryPlan(queries={"n": Query(op="count", type="door", where=where)}, unit="count"), scene)


class ImageConnections(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name)/"plan.png"
        image = Image.new("RGB", (400, 400), "white")
        draw = ImageDraw.Draw(image)
        draw.line((200, 20, 200, 160), fill="black", width=5)
        draw.line((200, 240, 200, 380), fill="black", width=5)
        image.save(self.file)

    def test_wall_supported_connection_and_unique_count(self):
        scene = source()
        self.assertTrue(attach_room_links(scene, self.file))
        self.assertEqual({scene.entities[-1].attrs.get(k) for k in ("room_id", "room_id2")}, {"a", "b"})
        self.assertEqual(count(scene, connected_room_label="Bedroom").answer, 1)
        self.assertEqual(count(scene, connected_room_number="1").answer, 1)
        result = count(scene, connected_room_id="a")
        self.assertEqual(set(result.evidence), {"a", "d"})
        self.assertEqual(result.confidence, .8)
        self.assertEqual(scene.entities[-1].conf, .95)

    def test_blank_image_does_not_prove_door_connections(self):
        Image.new("RGB", (400, 400), "white").save(self.file)
        scene = source()
        attach_room_links(scene, self.file)
        self.assertEqual(scene.entities[-1].attrs["room_link_status"], "uncertain")
        self.assertIsNone(count(scene, connected_room_id="a").answer)

    def test_cached_geometry_does_not_open_image_again(self):
        scene = source()
        attach_room_links(scene, self.file)
        with patch("PIL.Image.open", side_effect=AssertionError("must reuse links")):
            self.assertFalse(attach_room_links(scene, self.file))

    def test_changed_room_boxes_refresh_links(self):
        scene = source()
        attach_room_links(scene, self.file)
        scene.entities[0].geom = rectangle(.01, .01, .1, .1)
        self.assertTrue(attach_room_links(scene, self.file))
        self.assertNotEqual(scene.entities[-1].attrs.get("room_link_status"), "linked")

    def test_source_links_and_unrelated_metadata_are_preserved(self):
        scene = source()
        scene.entities[-1].attrs = {"room_id": "a", "room_height_m": 2.7}
        self.assertFalse(attach_room_links(scene, self.file))
        self.assertEqual(scene.entities[-1].attrs, {"room_id": "a", "room_height_m": 2.7})
        scene.entities[-1].attrs = {"room_height_m": 2.7}
        attach_room_links(scene, self.file)
        self.assertEqual(scene.entities[-1].attrs["room_height_m"], 2.7)

    def test_adjacent_swing_boxes_abstain(self):
        scene = source()
        scene.entities.append(Entity(id="d2", type="door", geom=rectangle(.46, .605, .59, .8)))
        attach_room_links(scene, self.file)
        self.assertEqual(scene.entities[-1].attrs["room_link_status"], "uncertain")
        self.assertIsNone(count(scene, connected_room_id="a").answer)

    def test_missing_full_room_coverage_or_multi_page_is_not_inferred(self):
        for covers, pages in [(["door"], source().pages), (["room", "door"], source().pages*2)]:
            scene = source().model_copy(update={"covers": covers, "pages": pages})
            self.assertFalse(attach_room_links(scene, self.file))

    def test_scene_cache_enrichment_does_not_rebill_extraction(self):
        from blueprints import pipeline
        from blueprints.brain import vision
        root = Path(self.temp.name)
        (root/"uploads").mkdir()
        shutil.copyfile(self.file, root/"uploads"/"test.png")
        cached = root/"scenes"/"uploads"/(hashlib.md5(b"uploads/test.png").hexdigest()+".json")
        cached.parent.mkdir(parents=True)
        cached.write_text(source().model_dump_json(), encoding="utf-8")
        pipeline.get_scene.cache_clear()
        pipeline._BUILT_NOW.discard("uploads/test.png")
        self.addCleanup(pipeline.get_scene.cache_clear)
        with patch.object(pipeline, "CACHE", root), patch.object(pipeline, "READ_CACHE", True), patch.object(vision, "UPLOADS", root/"uploads"), \
             patch.object(pipeline, "build_scene", side_effect=AssertionError("no detector/model call")):
            result = pipeline.get_scene("uploads/test.png")
        self.assertEqual(result.entities[-1].attrs["room_link_status"], "linked")
        self.assertNotIn("uploads/test.png", pipeline._BUILT_NOW)
        self.assertIn("room_link_status", cached.read_text(encoding="utf-8"))

    def test_vision_cache_enrichment_is_not_a_fresh_paid_read(self):
        from blueprints import pipeline
        from blueprints.brain import vision
        root = Path(self.temp.name)
        (root/"uploads").mkdir()
        shutil.copyfile(self.file, root/"uploads"/"test.png")
        cached = root/"scenes"/"vision"/(hashlib.md5(f"{vision.BUILDER}:uploads/test.png".encode()).hexdigest()+".json")
        cached.parent.mkdir(parents=True)
        cached.write_text(source().model_dump_json(), encoding="utf-8")
        with patch.object(pipeline, "CACHE", root), patch.object(pipeline, "READ_CACHE", True), patch.object(vision, "UPLOADS", root/"uploads"):
            result, fresh = vision.read_scene("uploads/test.png")
        self.assertFalse(fresh)
        self.assertEqual(result.entities[-1].attrs["room_link_status"], "linked")


class ConnectionQueries(unittest.TestCase):
    def test_filters_must_match_the_same_endpoint(self):
        scene = source()
        scene.entities[-1].attrs = {"room_id": "a", "room_id2": "b"}
        self.assertEqual(count(scene, connected_room_number="1", connected_room_apartment="B").answer, None)
        self.assertEqual(count(scene, connected_room_number="1", connected_room_apartment="A").answer, 1)

    def test_unresolved_elsewhere_does_not_block_scope(self):
        scene = source()
        scene.entities[-1].attrs = {"room_id": "a", "room_link_status": "partial", "room_link_candidates": '["b"]'}
        self.assertEqual(count(scene, connected_room_id="a").answer, 1)
        self.assertIsNone(count(scene, connected_room_id="b").answer)
        scene.entities.append(Entity(id="c", type="room", number="3"))
        self.assertEqual(count(scene, connected_room_id="c").answer, 0)

    def test_unknown_room_or_missing_connections_is_not_zero(self):
        scene = source()
        self.assertIsNone(count(scene, connected_room_id="missing").answer)
        self.assertIsNone(count(scene, connected_room_id="a").answer)
        self.assertEqual(count(scene).answer, 1)  # ordinary counts remain unchanged
        scene.entities[-1].attrs = {"room_id": "deleted-room"}
        self.assertIsNone(count(scene, connected_room_id="a").answer)

    def test_legacy_cad_endpoints_resolve_without_duplicate_count(self):
        scene = source()
        scene.entities[-1].attrs = {"room": "1", "room2": "2"}
        self.assertEqual(count(scene, connected_room_label="bedroom").answer, 1)

    def test_agent_can_see_links_and_execute_virtual_filter(self):
        from blueprints.brain.escalate import _tool, _verified_plan
        scene = source()
        scene.entities[-1].attrs = {"room_id": "a", "room_id2": "b", "room_link_status": "linked"}
        rows = json.loads(_tool("list_entities", {"type": "door"}, scene)[0])
        self.assertEqual(rows["entities"][0]["connections"]["room_id"], "a")
        plan = QueryPlan(queries={"n": Query(op="count", type="door", where={"connected_room_id": "a"})}, unit="count")
        self.assertEqual(_verified_plan(plan.model_dump_json(), scene).answer, 1)
        response = _tool("run_plan", {"plan": plan.model_dump_json()}, scene)[0]
        self.assertEqual(json.loads(response)["answer"], 1)
        bad = plan.model_copy(deep=True)
        bad.queries["n"].where = {"connected_rooom_id": "a"}
        self.assertIn("invalid plan", _tool("run_plan", {"plan": bad.model_dump_json()}, scene)[0])

    def test_old_png_room_plan_is_replaced_once_then_reused(self):
        from blueprints import pipeline
        from blueprints.brain import plan
        from blueprints.schemas import TraceStep
        scene = source()
        scene.entities[-1].attrs = {"room_link_version": "image-room-links@1"}
        question = 'How many doors are in room "Bedroom"?'
        template, slots = plan.mask(question)
        old = QueryPlan(queries={"n": Query(op="count", type="door", label="{label}")}, unit="count")
        new = QueryPlan(queries={"n": Query(op="count", type="door", where={"connected_room_label": "{label}"})}, unit="count")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            local = root/"plans"/(hashlib.md5(template.encode()).hexdigest()+".json")
            local.parent.mkdir()
            local.write_text(old.model_dump_json(), encoding="utf-8")
            with patch.object(pipeline, "CACHE", root), patch.object(plan, "_ask_gemini", return_value=(new, [TraceStep(step="plan")])) as ask:
                for _ in range(2):
                    result = plan._plan_with_trace(question, scene)[0]
                    self.assertEqual(result.queries["n"].where, {"connected_room_label": "Bedroom"})
                self.assertEqual(ask.call_count, 1)


if __name__ == "__main__":
    unittest.main()
