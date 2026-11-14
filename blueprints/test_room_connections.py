"""Hand-labelled graph tests and no-network pipeline checks for room rankings."""
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from blueprints.schemas import Answer, Entity, Page, Scene, TraceStep
from blueprints.room_connections import direct_room_ranking, rank_rooms


def room(id, number=None, *, page=0, apartment=None, label=None, **kwargs):
    return Entity(id=id, type="room", number=number or id, page=page, apartment=apartment, label=label or id,
                  geom=[(0, 0), (.4, 0), (.4, .4), (0, .4)], **kwargs)


def obj(id, a=None, b=None, *, kind="door", page=0, attrs=None, **kwargs):
    return Entity(id=id, type=kind, page=page, geom=[(.3, .1), (.4, .1), (.4, .2), (.3, .2)],
                  attrs=attrs if attrs is not None else {k: v for k, v in {"room": a, "room2": b}.items() if v is not None}, **kwargs)


def scene(entities, covers=None):
    return Scene(path="data/test/rooms.dxf", builder="hand-labelled-test", tier="parse", covers=covers or ["room", "door", "window", "fixture"],
                 pages=[Page(image="pages/test.png", width=1000, height=800), Page(image="pages/test2.png", width=1000, height=800)], entities=entities)


class RoomConnections(unittest.TestCase):
    def test_agent_receives_connection_fields_without_large_unrelated_property_bags(self):
        from blueprints.brain.escalate import _tool
        source = scene([room("a"), obj("d", attrs={"room": "a", "copies": 2, "unrelated_property": "x"*10000})])
        payload = json.loads(_tool("list_entities", {"type": "door"}, source)[0])
        self.assertEqual(payload["entities"][0]["attrs"], {"room": "a", "copies": 2})
        self.assertLess(len(json.dumps(payload)), 1000)

    def test_agent_result_id_binds_the_winner_count_ties_and_evidence(self):
        from blueprints.brain import escalate
        source = scene([room("a"), room("b"), obj("shared", "a", "b")])
        client = Mock()
        def response(**kwargs):
            tool_messages = [m for m in kwargs["messages"] if m.get("role") == "tool"]
            if not tool_messages:
                name, args = "rank_rooms", {"object_type": "door", "limit": 1}
            else:
                result = json.loads(tool_messages[-1]["content"])
                name, args = "answer", {"answer": 999, "explanation": "invented winner", "result_id": result["result_id"], "evidence": ["invented"]}
            call = SimpleNamespace(id=name, function=SimpleNamespace(name=name, arguments=json.dumps(args)))
            message = SimpleNamespace(content=None, tool_calls=[call], model_dump=lambda **_: {"role": "assistant"})
            return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
        client.chat.completions.create.side_effect = response
        with patch("openai.OpenAI", return_value=client), patch.object(escalate, "MAX_TURNS", 2), \
             patch.object(escalate, "llm_step", side_effect=lambda *a, **k: TraceStep(step="escalate")):
            answer = escalate._agent_loop("Rank rooms by their door count", source, Answer(),
                                          True, "https://unused.test", "test-only", "test", {}, time.monotonic()+30, [])
        self.assertEqual(answer.answer, 1)
        self.assertEqual(set(answer.evidence), {"a", "b", "shared"})
        self.assertEqual({e.id for e in answer.extra_entities}, {"a", "b", "shared"})
        self.assertIn("Tie between 2 rooms", answer.interpretation)
        self.assertNotIn("invented", answer.interpretation)

    def test_unknown_result_ids_never_become_an_answer(self):
        from blueprints.brain import escalate
        call = SimpleNamespace(id="bad", function=SimpleNamespace(name="answer", arguments=json.dumps({"answer": 999, "result_id": "invented"})))
        message = SimpleNamespace(content=None, tool_calls=[call], model_dump=lambda **_: {"role": "assistant"})
        reply = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
        client = Mock();client.chat.completions.create.return_value = reply
        with patch("openai.OpenAI", return_value=client), patch.object(escalate, "MAX_TURNS", 1), \
             patch.object(escalate, "llm_step", return_value=TraceStep(step="escalate")):
            answer = escalate._agent_loop("Rank rooms", scene([room("a"),obj("d","a")]), Answer(), False,
                                          "https://unused.test", "test-only", "test", {}, time.monotonic()+30, [])
        self.assertIsNone(answer.answer)

    def test_shared_doors_count_at_both_rooms_and_drawing_copies_count_once(self):
        source = scene([room("hall"), room("bath"), room("bed"), obj("shared", "hall", "bath"), obj("other", "hall", "bed"),
                        obj("self", attrs={"room": "hall", "room2": "hall", "copies": 5})])
        result = rank_rooms(source)
        self.assertEqual(result.answer.answer, 3)
        self.assertEqual(result.winners, ["hall"])
        self.assertEqual(set(result.answer.evidence), {"hall", "shared", "other", "self"})
        self.assertEqual({r["room_id"]: r["count"] for r in result.rows}, {"hall": 3, "bath": 1, "bed": 1})

    def test_ties_are_not_broken_arbitrarily_and_include_all_room_and_object_evidence(self):
        result = rank_rooms(scene([room("a"), room("b"), obj("shared", "a", "b")]))
        self.assertEqual(set(result.winners), {"a", "b"})
        self.assertEqual(result.answer.answer, 1)
        self.assertIn("Tie between 2 rooms", result.answer.interpretation)
        self.assertEqual(set(result.answer.evidence), {"a", "b", "shared"})
        shown = result.tool_result("saved", limit=1)
        self.assertEqual(len(shown["ranking"]), 1)
        self.assertEqual(shown["winner_count"], 2)
        self.assertEqual(set(shown["winning_room_ids"]), {"a", "b"})

    def test_duplicate_numbers_on_different_pages_never_cross_connect(self):
        result = rank_rooms(scene([room("down", "1", page=0), room("up", "1", page=1), obj("d", "1", page=0), obj("u", "1", page=1)]))
        self.assertEqual({r["room_id"]: r["object_ids"] for r in result.rows}, {"down": ["d"], "up": ["u"]})
        self.assertIn("page 1", result.answer.interpretation)
        self.assertIn("page 2", result.answer.interpretation)

    def test_large_tie_keeps_all_evidence_while_the_model_payload_stays_bounded(self):
        source = scene([room(f"room-{i}") for i in range(40)] + [obj(f"door-{i}", f"room-{i}") for i in range(40)])
        result = rank_rooms(source)
        payload = result.tool_result("bound-result", limit=3)
        self.assertEqual(result.answer.answer, 1)
        self.assertEqual(len(result.winners), 40)
        self.assertEqual(len(result.answer.evidence), 80)
        self.assertEqual(len(payload["ranking"]), 3)
        self.assertEqual(payload["winner_count"], 40)
        self.assertTrue(payload["winning_room_ids_truncated"])
        self.assertTrue(payload["ranking_truncated"])

    def test_ambiguous_numbers_refuse_to_guess_from_the_doors_apartment(self):
        source = scene([room("a", "1", apartment="A"), room("b", "1", apartment="B"), obj("d", "1", apartment="A")])
        result = rank_rooms(source)
        self.assertIsNone(result.answer.answer)
        self.assertEqual(result.answer.confidence, 0)
        self.assertIn("ambiguous", result.answer.error)

    def test_explicit_room_ids_resolve_duplicate_numbers(self):
        result = rank_rooms(scene([room("a", "1"), room("b", "1"), obj("d", attrs={"room_id": "b"})]))
        self.assertEqual(result.winners, ["b"])
        self.assertEqual(result.answer.answer, 1)

    def test_labels_can_disambiguate_duplicate_numbers_without_guessing(self):
        result = rank_rooms(scene([room("a", "1", label="Hall"), room("b", "1", label="Bath"),
                                   obj("d", attrs={"room": "1", "room_label": "Bath"})]))
        self.assertEqual(result.winners, ["b"])

    def test_missing_unknown_and_wrong_page_links_abstain(self):
        for attributes in [{}, {"room": "missing"}, {"room_id": "up"}, {"room": "a", "room2": "missing"}]:
            with self.subTest(attributes=attributes):
                result = rank_rooms(scene([room("a"), room("up", page=1), obj("d", attrs=attributes)]), page=0)
                self.assertIsNone(result.answer.answer)
                self.assertTrue(result.unresolved)

    def test_no_coverage_no_rooms_or_no_objects_are_not_claimed_as_verified_zero(self):
        for source in [scene([room("a")]), scene([obj("d", "a")]), scene([room("a"), obj("d", "a")], covers=["room"]),
                       scene([room("a"), obj("d", "a")], covers=["door", "room:bathroom"])]:
            self.assertIsNone(rank_rooms(source).answer.answer)

    def test_scope_is_on_rooms_not_on_a_shared_doors_apartment(self):
        source = scene([room("a", apartment="A"), room("b", apartment="B"), obj("shared", "a", "b", apartment="A"), obj("a-only", "a")])
        self.assertEqual(rank_rooms(source, apartment="b").answer.answer, 1)
        self.assertEqual(rank_rooms(source, apartment="b").winners, ["b"])
        self.assertIsNone(rank_rooms(source, apartment="missing").answer.answer)

    def test_fewest_includes_verified_zero_degree_rooms(self):
        result = rank_rooms(scene([room("a"), room("b"), obj("d", "a")]), order="asc")
        self.assertEqual(result.answer.answer, 0)
        self.assertEqual(result.winners, ["b"])

    def test_windows_are_separate_from_doors_and_outdoor_scope_is_explicit(self):
        source = scene([room("a"), room("balcony", outdoor=True), obj("d", "a"), obj("w1", "balcony", kind="window"), obj("w2", "balcony", kind="window")])
        self.assertEqual(rank_rooms(source).winners, ["a"])
        self.assertEqual(rank_rooms(source, object_type="window").winners, ["balcony"])
        self.assertEqual(rank_rooms(source, object_type="window", include_outdoor=False).winners, ["a"])

    def test_source_scores_are_not_replaced_by_a_fixed_agent_rate(self):
        source = scene([room("a"), obj("d", "a", conf=.81)])
        self.assertAlmostEqual(rank_rooms(source).answer.confidence, .81)
        self.assertIn("not a calibrated", rank_rooms(source).answer.confidence_note.lower())

    def test_direct_intent_route_is_narrow_and_does_not_swallow_compound_questions(self):
        source = scene([room("a"), obj("d", "a")])
        for question in ["Which room has the most doors?", "What rooms have most windows?", "Which room has the fewest doors?"]:
            self.assertIsNotNone(direct_room_ranking(question, source))
        for question in ["Which room in apartment A has the most doors?", "Which room has the most doors and what is its area?", "Which room has the most doors on floor 2?", "How many doors are there?"]:
            self.assertIsNone(direct_room_ranking(question, source))

    def test_pipeline_ranking_and_incomplete_link_abstention_use_no_paid_planner_or_agent(self):
        from blueprints import pipeline
        for source in [scene([room("a"), obj("d", "a")]), scene([room("a"), obj("d")])]:
            with patch.object(pipeline, "get_scene", return_value=source), \
                 patch("blueprints.brain.plan.plan_with_trace", side_effect=AssertionError("paid planner called")), \
                 patch("blueprints.brain.escalate.escalate", side_effect=AssertionError("paid agent called")):
                result = pipeline.answer(source.path, "Which room has the most doors?")
                self.assertEqual(result.cost_usd, 0)

    def test_vision_uploads_reuse_the_same_ranking_without_another_model_or_planner_call(self):
        from blueprints.brain import escalate
        source = scene([room("a"), obj("d", "a")])
        source.trace = [TraceStep(step="scene", usd=.02)]
        with patch("blueprints.brain.vision.read_scene", return_value=(source, True)), \
             patch.object(escalate, "plan_with_trace", side_effect=AssertionError("paid planner called")):
            result = escalate.answer_from_vision("Which room has the most doors?", "uploads/scan.pdf")
        self.assertEqual(result.answer, 1)
        self.assertEqual(set(result.evidence), {"a", "d"})
        self.assertEqual({e.id for e in result.extra_entities}, {"a", "d"})
        self.assertEqual(result.cost_usd, .02, "only the already-required reading cost is retained")

    def test_missing_links_on_a_vision_upload_do_not_trigger_costly_guessing(self):
        from blueprints.brain import escalate
        source = scene([room("a"), obj("d")])
        with patch("blueprints.brain.vision.read_scene", return_value=(source, False)), \
             patch.object(escalate, "plan_with_trace", side_effect=AssertionError("paid planner called")), \
             patch.object(escalate, "agent", side_effect=AssertionError("paid guessing called")):
            result = escalate.escalate("Which room has the most doors?", "uploads/scan.pdf", None, Answer())
        self.assertIsNone(result.answer)
        self.assertIn("missing or ambiguous", result.error)
        self.assertEqual(result.cost_usd, 0)

    def test_invalid_tool_scope_is_rejected(self):
        source = scene([room("a"), obj("d", "a")])
        for parameters in [{"object_type": "wall"}, {"order": "random"}, {"page": -1}, {"page": 99}, {"page": True}, {"include_outdoor": "false"}]:
            with self.assertRaises(ValueError):
                rank_rooms(source, **parameters)


class CorrectedRoomRankingAPI(unittest.IsolatedAsyncioTestCase):
    async def test_corrected_scene_changes_the_ranking_without_a_planner_call(self):
        import httpx
        from blueprints import api
        source = scene([room("a"), room("b"), obj("shared", "a", "b"), obj("extra", "a")])
        with patch.object(api, "PASSWORD_HASH", ""), \
             patch("blueprints.brain.plan.plan_with_trace", side_effect=AssertionError("paid planner called")):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="https://test") as client:
                question = "Which room has the most doors?"
                first = await client.post("/api/room-ranking", json={"scene": source.model_dump(), "question": question})
                self.assertEqual(first.status_code, 200)
                self.assertEqual(first.json()["answer"], 2)
                corrected = source.model_copy(update={"entities": [e for e in source.entities if e.id != "extra"]})
                second = await client.post("/api/room-ranking", json={"scene": corrected.model_dump(), "question": question})
                self.assertEqual(second.json()["answer"], 1)
                self.assertIn("Tie between 2 rooms", second.json()["interpretation"])
                unsupported = await client.post("/api/room-ranking", json={"scene": source.model_dump(), "question": "What is the room area?"})
                self.assertEqual(unsupported.status_code, 422)
        self.assertEqual(len(source.entities), 4)

    async def test_new_endpoint_is_covered_by_the_existing_sign_in_guard(self):
        import httpx
        from blueprints import api
        with patch.object(api, "PASSWORD_HASH", "test-login-required"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="https://test") as client:
                response = await client.post("/api/room-ranking", json={})
                self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
