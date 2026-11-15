"""Offline regression tests for executable, internally consistent agent answers."""
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from blueprints.brain import escalate
from blueprints.schemas import Answer, Entity, Page, Scene, TraceStep


def scene():
    return Scene(path="uploads/plan.dxf", builder="test", tier="parse", covers=["room:bathroom", "door"],
                 pages=[Page(image="pages/plan.png", width=1000, height=800)], entities=[
                     Entity(id="bath", type="room", subtype="bathroom", label="Bain", number="4.17", apartment="B", qty={"m2": 3.4}),
                     Entity(id="smaller", type="room", subtype="bathroom", label="Bain", number="4.18", apartment="A", qty={"m2": 2.1}),
                 ])


def tile_plan():
    return {"queries": {"area": {"op": "sum", "type": "room", "subtype": "bathroom", "number": "4.17", "apartment": "B", "qty": "m2"}},
            "formula": "ceil(area * (1 + waste) / tile_m2)", "unit": "count", "assumptions": {"tile_m2": .36, "waste": .1},
            "interpretation": "An unverified explanation saying 86 tiles."}


def reply(arguments):
    call = SimpleNamespace(id="final", function=SimpleNamespace(name="answer", arguments=json.dumps(arguments)))
    message = SimpleNamespace(content=None, tool_calls=[call], model_dump=lambda **_: {"role": "assistant", "tool_calls": [
        {"id": call.id, "type": "function", "function": {"name": "answer", "arguments": call.function.arguments}}]})
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))


class VerifiedAgentAnswers(unittest.TestCase):
    def run_agent(self, arguments, *, inline=False, draft=None, source=None):
        client = Mock()
        client.chat.completions.create.side_effect = [reply(a) for a in arguments]
        with patch("openai.OpenAI", return_value=client), patch.object(escalate, "MAX_TURNS", len(arguments)), \
             patch.object(escalate, "llm_step", return_value=TraceStep(step="escalate", usd=.001)):
            result = escalate._agent_loop("Which bathroom needs the most 60 x 60 cm tiles with 10% waste?", source or scene(),
                                          draft or Answer(), inline, "https://unused.test", "test-only", "test", {}, time.monotonic() + 60, [])
        return result, client

    def test_86_headline_and_11_explanation_become_one_verified_result(self):
        result, _ = self.run_agent([{"answer": 86, "unit": "count", "explanation": "3.4 / .36 * 1.1 gives 11 tiles.",
                                     "evidence": ["smaller", "invented"], "plan": json.dumps(tile_plan())}], inline=True)
        self.assertEqual(result.answer, 11)
        self.assertEqual(result.unit, "count")
        self.assertEqual(result.evidence, ["bath"])
        self.assertEqual([e.id for e in result.extra_entities], ["bath"])
        self.assertIn("gives 11", result.formula)
        self.assertIn("area = 3.4", result.formula)
        self.assertIn("Bain", result.interpretation)
        self.assertIn("4.17", result.interpretation)
        self.assertNotIn("86", result.interpretation)
        self.assertEqual(result.assumptions, {"tile_m2": .36, "waste": .1})
        self.assertIn("execute", [s.step for s in result.trace])

    def test_missing_plan_is_retried_instead_of_accepting_a_number(self):
        result, client = self.run_agent([
            {"answer": "86", "unit": "count", "explanation": "11 tiles"},
            {"answer": "86", "unit": "count", "explanation": "11 tiles", "plan": json.dumps(tile_plan())},
        ])
        self.assertEqual(result.answer, 11)
        self.assertEqual(client.chat.completions.create.call_count, 2)
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertTrue(any(m.get("role") == "tool" and "Final answer not verified" in m.get("content", "") for m in messages))

    def test_no_verified_plan_abstains_even_when_the_draft_contains_86(self):
        result, _ = self.run_agent([{"answer": 86, "explanation": "11 tiles"}] * 2, draft=Answer(answer=86, unit="count"))
        self.assertIsNone(result.answer)
        self.assertEqual(result.confidence, 0)
        self.assertIn("Could not verify", result.error)

    def test_bad_plans_cannot_publish_a_numeric_answer(self):
        plans = []
        for formula in ["86", "ceil(area / 0)", "ceil(", "area * 1e309"]:
            plan = tile_plan()
            plan["formula"] = formula
            plans.append(plan)
        no_query = tile_plan()
        no_query["queries"] = {}
        plans.append(no_query)
        unknown_attribute = tile_plan()
        unknown_attribute["queries"]["area"]["where"] = {"invented": True}
        plans.append(unknown_attribute)
        no_quantity = tile_plan()
        no_quantity["queries"]["area"]["qty"] = "m"
        plans.append(no_quantity)
        for plan in plans:
            with self.subTest(plan=plan):
                result, _ = self.run_agent([{"answer": 86, "unit": "count", "explanation": "86 tiles", "plan": json.dumps(plan)}])
                self.assertIsNone(result.answer)

    def test_zero_is_a_valid_verified_count(self):
        plan = {"queries": {"doors": {"op": "count", "type": "door"}}, "unit": "count"}
        result, _ = self.run_agent([{"answer": 86, "explanation": "86 doors", "plan": json.dumps(plan)}])
        self.assertEqual(result.answer, 0)
        self.assertEqual(result.evidence, [])
        self.assertIn("no matching objects", result.interpretation)

    def test_verified_answers_keep_source_scores_without_a_fixed_cap_or_inflation(self):
        for score in [.2, .6, .9, 1.0]:
            with self.subTest(score=score):
                source = scene()
                source.entities[0].conf = score
                result, _ = self.run_agent([{"answer": 11, "plan": json.dumps(tile_plan())}], source=source)
                self.assertAlmostEqual(result.confidence, score)
                self.assertIn("Source-derived score", result.confidence_note)
                self.assertIn("Arithmetic verified", result.confidence_note)
                self.assertIn("interpretation is not independently verified", result.confidence_note)
                self.assertNotIn("capped", result.confidence_note)
                self.assertIn("verify_answer", [step.step for step in result.trace])

    def test_explicit_abstention_and_non_numeric_answers_still_work(self):
        result, _ = self.run_agent([{"answer": "cannot answer", "explanation": "Ceiling height is not given."}])
        self.assertIsNone(result.answer)
        self.assertEqual(result.error, "Ceiling height is not given.")
        result, _ = self.run_agent([{"answer": "Bain", "explanation": "The room is labelled Bain.", "evidence": ["bath"]}])
        self.assertEqual(result.answer, "Bain")
        self.assertEqual(result.evidence, ["bath"])

    def test_area_range_filter(self):
        from blueprints.executor import execute
        from blueprints.schemas import QueryPlan
        plan = lambda **f: QueryPlan(queries={"n": {"op": "count", "type": "room", "subtype": "bathroom", **f}}, unit="count")
        self.assertEqual(execute(plan(min_m2=3), scene()).answer, 1)
        self.assertEqual(execute(plan(max_m2=3), scene()).answer, 1)
        self.assertEqual(execute(plan(min_m2=2, max_m2=4), scene()).answer, 2)

    def test_abstention_hands_over_to_the_next_model_and_keeps_its_cost(self):
        client = Mock()
        client.chat.completions.create.side_effect = [reply({"answer": "cannot answer", "explanation": "unsure"}),
                                                      reply({"answer": 11, "plan": json.dumps(tile_plan())})]
        with patch("openai.OpenAI", return_value=client), \
             patch.object(escalate, "llm_step", return_value=TraceStep(step="escalate", usd=.001)):
            result = escalate.agent("tiles?", scene(), Answer(), False,
                                    providers=[("u", "k", "first", {}), ("u", "k", "second", {})])
        self.assertEqual(result.answer, 11)
        self.assertIn("escalate:abstained first", [s.step for s in result.trace])
        self.assertEqual(sum(s.step == "escalate" for s in result.trace), 2)


def measured_scene(tier="parse"):
    """A parsed plan as the parsers record it: a room's perimeter and a door's width in qty.m, the rest in attrs."""
    return Scene(path="uploads/plan.dxf", builder="test", tier=tier, covers=["room", "door", "window"],
                 pages=[Page(image="pages/plan.png", width=1000, height=800)], entities=[
                     Entity(id="bed", type="room", label="Schlafzimmer", number="7.08", qty={"m2": 15.66, "m": 15.926},
                            attrs={"width_m": 3.545, "length_m": 4.418}),
                     Entity(id="door-1", type="door", qty={"m": 0.878}, attrs={"thickness_m": 0.151, "room": "7.08"}),
                     Entity(id="window-1", type="window", attrs={"room": "7.08"}),  # a box the parser could not size
                     Entity(id="level-1", type="level", attrs={"elevation_m": 0.0, "frame_x_m": -3.2, "frame_w_m": 40.0}),
                 ])


class ParsedFilesAreNeverMeasuredFromThePicture(unittest.TestCase):
    run_agent = VerifiedAgentAnswers.run_agent

    def tool_names(self, source):
        _, client = self.run_agent([{"answer": "cannot answer", "explanation": "x"}], source=source)
        sent = client.chat.completions.create.call_args.kwargs
        return [t["function"]["name"] for t in sent["tools"]], sent["messages"][0]["content"]

    def test_a_parsed_scene_offers_no_look_tool_and_says_where_sizes_come_from(self):
        names, system = self.tool_names(measured_scene())
        self.assertEqual(names, ["rank_rooms", "list_entities", "run_plan", "answer"])
        self.assertNotIn("Use look", system)
        self.assertIn("Sizes come only from the scene's\n  recorded quantities", system)
        self.assertIn('"cannot answer" and say which measurement is missing', system)

    def test_an_image_scene_keeps_look(self):
        names, system = self.tool_names(measured_scene(tier="detect"))
        self.assertEqual(names, ["rank_rooms", "resolve_room_connections", "list_entities", "run_plan", "look", "answer"])
        self.assertEqual(system, escalate.SYSTEM)
        self.assertIn("Use look only when the scene lacks something you need", system)

    def test_look_is_refused_on_a_parsed_scene_even_if_a_model_calls_it(self):
        with patch.object(escalate, "_look", side_effect=AssertionError("no picture-reading on a parsed file")) as look:
            text, steps = escalate._tool("look", {"question": "How wide is the bedroom?"}, measured_scene())
        look.assert_not_called()
        self.assertIn("not available", text)
        self.assertEqual(steps, [])
        with patch.object(escalate, "_look", return_value=("about 3.5 m", TraceStep(step="look"))):
            self.assertEqual(escalate._tool("look", {"question": "?"}, measured_scene(tier="detect"))[0], "about 3.5 m")

    def test_list_entities_shows_every_recorded_size(self):
        rows = {r["id"]: r for r in json.loads(escalate._tool("list_entities", {}, measured_scene())[0])["entities"]}
        self.assertEqual((rows["bed"]["area_m2"], rows["bed"]["qty"], rows["bed"]["sizes"]),
                         (15.66, {"m": 15.926}, {"width_m": 3.545, "length_m": 4.418}))
        self.assertEqual((rows["door-1"]["qty"], rows["door-1"]["sizes"]), ({"m": 0.878}, {"thickness_m": 0.151}))
        self.assertEqual(rows["door-1"]["connections"], {"room": "7.08"})
        self.assertNotIn("qty", rows["window-1"])
        self.assertEqual(rows["level-1"]["sizes"], {"elevation_m": 0.0})  # the page frame is for the viewer

    def test_list_entities_stays_within_what_the_agent_is_shown(self):
        source = measured_scene()
        source.entities = [Entity(id=f"wall-{i:04d}-{'x' * 22}", type="wall", label="Basic Wall:Interior - Partition",
                                  qty={"m": 4.16, "m2": 10.7952, "m3": 1.64}, level="Level 1",
                                  attrs={"height_m": 2.595, "thickness_m": 0.152, "size_source": "geometry"}) for i in range(400)]
        text = escalate._tool("list_entities", {"type": "wall"}, source)[0]
        said = json.loads(text)  # whole rows: valid JSON, not cut off mid-entity
        self.assertLessEqual(len(text), escalate.TOOL_REPLY_CHARS)
        self.assertEqual((said["count"], said["shown"]), (400, len(said["entities"])))
        self.assertGreater(said["shown"], 20)

    def test_a_recorded_length_answers_through_the_executor(self):
        plan = {"queries": {"skirting": {"op": "sum", "type": "room", "number": "7.08", "qty": "m"}}, "unit": "m"}
        result, _ = self.run_agent([{"answer": 16, "unit": "m", "explanation": "about 16 m", "plan": json.dumps(plan)}],
                                   source=measured_scene())
        self.assertEqual((result.answer, result.unit, result.evidence), (15.926, "m", ["bed"]))

    def test_an_unrecorded_measurement_is_not_answered(self):
        plan = {"queries": {"w": {"op": "sum", "type": "window", "qty": "m"}}, "unit": "m"}
        result, client = self.run_agent([  # a guessed width cannot be published: the window has no recorded width
            {"answer": 1.2, "unit": "m", "explanation": "It looks about 1.2 m wide.", "plan": json.dumps(plan)},
            {"answer": "cannot answer", "explanation": "The window's width is not recorded in the scene."},
        ], source=measured_scene())
        self.assertIsNone(result.answer)
        self.assertEqual(result.error, "The window's width is not recorded in the scene.")
        refusal = [m["content"] for m in client.chat.completions.create.call_args.kwargs["messages"] if m.get("role") == "tool"]
        self.assertTrue(any("no m on window-1" in text for text in refusal))


if __name__ == "__main__":
    unittest.main()
