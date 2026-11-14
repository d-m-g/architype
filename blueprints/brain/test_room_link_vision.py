"""Offline validation/cost tests for the vision fallback; no real provider calls."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from blueprints import pipeline
from blueprints.schemas import Query, QueryPlan, TraceStep
from blueprints.test_image_room_links import source
from blueprints.brain import room_link_vision as links


def decision(**overrides):
    return links.Link.model_validate({"object_id": "d", "clear": True, "reason": "Jambs show one room on each side.",
        "endpoints": [{"kind": "room", "room_id": "a", "point": [.48, .5]},
                      {"kind": "room", "room_id": "b", "point": [.52, .5]}], **overrides})


class VisionRoomLinks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scene = source()
        self.scene.entities[-1].attrs = {"room_link_status": "uncertain", "room_link_candidates": '["a", "b"]',
                                       "room_link_geometry": "fixture-input", "room_link_version": "image-room-links@1"}
        self.plan = QueryPlan(queries={"n": Query(op="count", type="door", where={"connected_room_id": "a"})}, unit="count")
        self.cache = patch.object(pipeline, "CACHE", self.root)
        self.cache.start()
        self.addCleanup(self.cache.stop)
        self.key = patch.dict("os.environ", {"GEMINI_API_KEY": "offline-test-only"})
        self.key.start()
        self.addCleanup(self.key.stop)

    def mock_request(self, records):
        return patch.object(links, "_request", return_value=(records, TraceStep(step="resolve_room_connections", usd=.002)))

    def test_resolves_then_executes_and_accounts_for_cost_once(self):
        original = self.scene.model_copy(deep=True)
        with self.mock_request([decision()]) as request:
            answer = links.execute_with_links(self.plan, self.scene)
            self.assertEqual(answer.answer, 1)
            self.assertEqual(sum(s.usd for s in answer.trace), .002)
            self.assertIn("vision model", answer.confidence_note)
            second = links.execute_with_links(self.plan, original)
            self.assertEqual(second.answer, 1)
            self.assertEqual(sum(s.usd for s in second.trace), 0)
            self.assertEqual(request.call_count, 1)
        self.assertEqual(self.scene.entities[-1].conf, .95)

    def test_negative_decision_is_cached_without_repeated_paid_attempts(self):
        with self.mock_request([decision(clear=False)]) as request:
            self.assertIsNone(links.execute_with_links(self.plan, self.scene).answer)
            self.assertIsNone(links.execute_with_links(self.plan, self.scene).answer)
            self.assertEqual(request.call_count, 1)

    def test_omitted_duplicate_and_invented_ids_are_not_connections(self):
        for records in [[], [decision(), decision()], [decision(object_id="invented")]]:
            with self.subTest(records=records), tempfile.TemporaryDirectory() as temp, patch.object(pipeline, "CACHE", Path(temp)), self.mock_request(records):
                self.assertIsNone(links.execute_with_links(self.plan, self.scene.model_copy(deep=True)).answer)

    def test_unknown_room_far_endpoint_and_unmapped_space_remain_uncertain(self):
        bad = [
            {"kind": "room", "room_id": "invented", "point": [.48, .5]},
            {"kind": "room", "room_id": "a", "point": [.1, .1]},
            {"kind": "unmapped", "room_id": None, "point": [.48, .5]},
            {"kind": "room", "room_id": "a", "point": [-1, .5]},
        ]
        for endpoint in bad:
            record = decision()
            record.endpoints[0] = links.Endpoint.model_validate(endpoint)
            self.assertFalse(links._accept(self.scene, self.scene.entities[-1], record))

    def test_exterior_endpoint_is_allowed_but_not_invented_as_a_room(self):
        record = decision()
        record.endpoints[1] = links.Endpoint(kind="outside", room_id=None, point=[.52, .5])
        with self.mock_request([record]):
            self.assertEqual(links.execute_with_links(self.plan, self.scene).answer, 1)
        self.assertNotIn("room_id2", self.scene.entities[-1].attrs)

    def test_changed_room_geometry_invalidates_model_decisions(self):
        original = self.scene.model_copy(deep=True)
        with self.mock_request([decision()]) as request:
            links.resolve(self.scene, "door")
            original.entities[0].label = "Changed room"
            links.resolve(original, "door")
            self.assertEqual(request.call_count, 2)

    def test_global_count_and_already_resolved_count_do_not_call_model(self):
        with self.mock_request([decision()]) as request:
            all_doors = QueryPlan(queries={"n": Query(op="count", type="door")}, unit="count")
            self.assertEqual(links.execute_with_links(all_doors, self.scene).answer, 1)
            self.scene.entities[-1].attrs = {"room_id": "a", "room_link_status": "linked"}
            self.assertEqual(links.execute_with_links(self.plan, self.scene).answer, 1)
            request.assert_not_called()

    def test_missing_key_keeps_uncertainty_and_spends_nothing(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}), self.mock_request([decision()]) as request:
            self.assertIsNone(links.execute_with_links(self.plan, self.scene).answer)
            request.assert_not_called()

    def test_failures_have_cooldown_and_a_three_batch_cap(self):
        with patch.object(links, "_request", side_effect=RuntimeError("provider unavailable")) as request:
            for _ in range(3):
                links.resolve(self.scene, "door")
                count = request.call_count
                links.resolve(self.scene, "door")
                self.assertEqual(request.call_count, count)
                budget = links._directory(self.scene)/"budget.json"
                state = json.loads(budget.read_text())
                state["retry_after"] = time.time()-1
                budget.write_text(json.dumps(state))
            links.resolve(self.scene, "door")
            self.assertEqual(request.call_count, 3)
        self.assertEqual(self.scene.entities[-1].attrs["room_link_status"], "uncertain")

    def test_final_answer_validation_does_not_hide_paid_calls_in_an_error(self):
        from blueprints.brain.escalate import _verified_plan
        with self.mock_request([decision()]) as request:
            with self.assertRaises(ValueError):
                _verified_plan(self.plan.model_dump_json(), self.scene)
            request.assert_not_called()

    def test_batch_size_is_bounded_and_cached_omissions_are_not_retried(self):
        prototype = self.scene.entities.pop()
        self.scene.entities += [prototype.model_copy(deep=True, update={"id": f"d{i}"}) for i in range(49)]
        with self.mock_request([]) as request:
            for _ in range(4):
                links.resolve(self.scene, "door")
            self.assertEqual([len(call.args[1]) for call in request.call_args_list], [24, 24, 1])

    def test_malformed_provider_response_still_records_actual_token_cost(self):
        from PIL import Image
        file = self.root/"plan.png"
        Image.new("RGB", (400, 400), "white").save(file)
        response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=100),
                                   choices=[SimpleNamespace(message=SimpleNamespace(content="{broken"))])
        with patch.object(links, "drawing_file", return_value=file), patch("openai.OpenAI") as client:
            client.return_value.chat.completions.create.return_value = response
            records, step = links._request(self.scene, [self.scene.entities[-1]])
        self.assertEqual(records, [])
        self.assertEqual((step.in_tokens, step.out_tokens), (1000, 100))
        self.assertGreater(step.usd, 0)

    def test_provider_totals_include_thinking_cost_and_bad_record_does_not_drop_valid_one(self):
        from PIL import Image
        file = self.root/"plan.png"
        Image.new("RGB", (400, 400), "white").save(file)
        response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=100, total_tokens=1500),
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps({"links": [
                decision().model_dump(), {"object_id": "bad", "missing": "required fields"}]})))])
        with patch.object(links, "drawing_file", return_value=file), patch("openai.OpenAI") as client:
            client.return_value.chat.completions.create.return_value = response
            records, step = links._request(self.scene, [self.scene.entities[-1]])
            self.assertEqual(client.return_value.chat.completions.create.call_args.kwargs["reasoning_effort"], "low")
        self.assertEqual([r.object_id for r in records], ["d"])
        self.assertEqual(step.out_tokens, 500)

    def test_exact_count_does_not_pay_for_agent_after_negative_vision_review(self):
        from blueprints.brain import plan, escalate
        with patch.object(pipeline, "get_scene", return_value=self.scene), \
             patch.object(plan, "plan_with_trace", return_value=(self.plan, [])), \
             patch.object(escalate, "escalate", side_effect=AssertionError("no redundant agent")), \
             self.mock_request([decision(clear=False)]) as request:
            self.assertIsNone(pipeline.answer(self.scene.path, "How many doors in room 1?").answer)
            self.assertIsNone(pipeline.answer(self.scene.path, "How many doors in room 1?").answer)
            self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
