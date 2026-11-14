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

    def test_source_uncertainty_is_not_increased(self):
        source = scene()
        source.entities[0].conf = .2
        result, _ = self.run_agent([{"answer": 11, "plan": json.dumps(tile_plan())}], source=source)
        self.assertAlmostEqual(result.confidence, .2)
        self.assertIn("capped at 60%", result.confidence_note)

    def test_explicit_abstention_and_non_numeric_answers_still_work(self):
        result, _ = self.run_agent([{"answer": "cannot answer", "explanation": "Ceiling height is not given."}])
        self.assertIsNone(result.answer)
        self.assertEqual(result.error, "Ceiling height is not given.")
        result, _ = self.run_agent([{"answer": "Bain", "explanation": "The room is labelled Bain.", "evidence": ["bath"]}])
        self.assertEqual(result.answer, "Bain")
        self.assertEqual(result.evidence, ["bath"])


if __name__ == "__main__":
    unittest.main()
