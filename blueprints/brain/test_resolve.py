"""Offline tests of the data-first resolver: every path runs with canned model replies, so no key is needed."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from blueprints import pipeline
from blueprints.brain import escalate, resolve
from blueprints.brain.resolve import Decision, QueryError, execute, inventory, run, table
from blueprints.schemas import Answer, Entity, Page, Scene


def scene(tier="parse", covers=("room", "room:bedroom", "room:corridor", "door", "window", "fixture:toilet", "apartment")):
    """Two apartments off a corridor. Door d3 joins the corridor and bedroom b1; window w2 touches no room."""
    room = lambda i, label, sub, m2, apt=None, **more: Entity(id=i, type="room", subtype=sub, label=label, number=i.upper(), apartment=apt,
                                                              qty={"m2": m2, "m": round(4 * m2 ** 0.5, 2)}, attrs={"width_m": 3.0, "length_m": 4.0}, **more)
    door = lambda i, w, *rooms: Entity(id=i, type="door", qty={"m": w}, attrs={f"room_id{s}": r for s, r in zip(("", "2"), rooms)})
    return Scene(path="uploads/plan.dxf", builder="test@1", tier=tier, covers=list(covers), pages=[Page(image="pages/p.png", width=10, height=10)], entities=[
        room("k1", "Küche", "kitchen", 10.0, "A"), room("b1", "Zimmer", "bedroom", 14.0, "A"), room("b2", "Zimmer", "bedroom", 14.0, "B"),
        room("c1", "Korridor", "corridor", 6.0), room("t1", "Balkon", "balcony", 5.0, "B", outdoor=True),
        door("d1", 0.9, "c1", "k1"), door("d2", 0.8, "c1", "b2"), door("d3", 0.8, "c1", "b1"), door("d4", 1.6, "b2", "t1"),
        Entity(id="w1", type="window", qty={"m": 1.2}, attrs={"room_id": "k1"}), Entity(id="w2", type="window", qty={"m": 2.0}),
        Entity(id="wc", type="fixture", subtype="toilet", attrs={"room_id": "b1"}),
        Entity(id="apt-A", type="apartment", label="A"), Entity(id="apt-B", type="apartment", label="B"),
    ])


ROOMS_BY_AREA = "SELECT id, m2 AS value FROM entities WHERE type = 'room' AND m2 IS NOT NULL ORDER BY value DESC"
DOORS_PER_ROOM = ("SELECT r.id, count(d.id) AS value, group_concat(d.id) AS ids FROM entities r JOIN links l ON l.room_id = r.id "
                  "JOIN entities d ON d.id = l.entity_id AND d.type = 'door' WHERE r.type = 'room' GROUP BY r.id ORDER BY value DESC")


def decide(**fields):
    return Decision(**{"kind": "direct", **fields})


class Inventory(unittest.TestCase):
    def test_it_says_what_is_there_what_it_means_and_what_is_not(self):
        text = inventory(scene())
        for part in ("read by test@1, parsed from the file itself", "room x5: m2 = net floor area (e.g. 10)", "m = perimeter (skirting)",
                     "door x4: m = width of the opening", "window x2", "'Zimmer' 2", "'K1'", "outdoor = 1): 1", "'A', 'B'; 1 rooms belong to none",
                     "Linked: 4 of 4 doors, 1 of 2 windows, 1 of 1 fixtures", "Covers", "room: no ceiling height (height_m)",
                     "not looked for, so their absence means nothing: wall, slab, level"):
            self.assertIn(part, text)
        self.assertEqual(text, inventory(scene()))  # a pure function of the scene

    def test_an_image_scene_says_it_has_no_scale_and_no_links(self):
        s = scene(tier="detect")
        for e in s.entities:
            e.qty, e.attrs = {}, {}
        text = inventory(s)
        self.assertIn("detected on an image", text)
        self.assertIn("no scale: nothing is measured in metres", text)
        self.assertIn("no room links", text)

    def test_it_is_bounded_on_a_large_model(self):
        s = scene()
        s.entities += [Entity(id=f"r{i}", type="room", label=f"Office {i} with a long name", number=f"{i}.{i}", level=f"Level {i % 40}",
                              qty={"m2": 12.5}, attrs={f"custom_{i % 90}": "x" * 50}) for i in range(3000)]
        text = inventory(s)
        self.assertLessEqual(len(text), resolve.INVENTORY_CHARS + 80)
        self.assertIn("more of", text)  # truncation is said, with the count
        self.assertLessEqual(len(resolve._attr_columns(s)), resolve.MAX_ATTR_COLUMNS)


class SqlSafety(unittest.TestCase):
    def setUp(self):
        self.conn = table(scene())

    def test_reads_work(self):
        self.assertEqual(run(self.conn, "SELECT count(*) AS value FROM entities WHERE type = 'door'")[0]["value"], 4)
        self.assertEqual(run(self.conn, "SELECT json_extract(attrs, '$.room_id') AS r FROM entities WHERE id = 'w1';")[0]["r"], "k1")
        self.assertEqual(len(run(self.conn, "WITH big AS (SELECT * FROM entities WHERE m2 > 9) SELECT id FROM big")), 3)

    def test_everything_else_is_refused_and_the_data_is_unchanged(self):
        attacks = [
            "DELETE FROM entities", "UPDATE entities SET m2 = 999", "INSERT INTO entities (id) VALUES ('x')", "DROP TABLE entities",
            "CREATE TABLE t (a)", "ALTER TABLE entities ADD COLUMN x", "ATTACH DATABASE 'C:/x.db' AS x", "ATTACH DATABASE ':memory:' AS m",
            "PRAGMA table_info(entities)", "PRAGMA writable_schema = 1", "VACUUM INTO 'C:/x.db'", "BEGIN", "REINDEX",
            "SELECT 1; DROP TABLE entities", "SELECT 1; SELECT 2", "SELECT * FROM sqlite_master", "SELECT * FROM sqlite_schema",
            "SELECT * FROM pragma_table_info('entities')", "SELECT load_extension('evil')", "SELECT readfile('C:/Windows/win.ini')",
            "SELECT zeroblob(1000000000)", "SELECT randomblob(100000000)", "SELECT hex(zeroblob(50000000))", "SELECT printf('%1000000000d', 1)",
            "SELECT sqlite_version()", "SELECT writefile('x', 'y')", "SELECT edit('x')", "SELECT fts3_tokenizer('x')",
            "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT x FROM n",
            "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n LIMIT 1000000000) SELECT count(*) FROM n",
            "WITH d AS (DELETE FROM entities RETURNING id) SELECT * FROM d", "EXPLAIN SELECT 1",
            "/* hi */ DROP TABLE entities", "select id from entities where id = 'a' union select sql from sqlite_master",
            "SELECT 1 /*" + "x" * 3000 + "*/",
        ]
        for sql in attacks:
            with self.subTest(sql=sql[:60]):
                with self.assertRaises(QueryError):
                    run(self.conn, sql)
        self.assertEqual(run(self.conn, "SELECT count(*) AS n, sum(m2) AS a FROM entities")[0], {"n": 14, "a": 49.0})
        self.assertEqual(run(self.conn, "SELECT count(*) AS n FROM links")[0]["n"], 10)

    def test_runaway_joins_stop_at_the_step_limit(self):
        big = scene()
        big.entities += [Entity(id=f"x{i}", type="wall", qty={"m": 1.0}) for i in range(1500)]
        conn = table(big)
        t0 = time.monotonic()
        with self.assertRaises(QueryError) as stopped:
            run(conn, "SELECT count(*) AS value FROM entities a, entities b, entities c, entities d WHERE a.m + b.m + c.m + d.m > 0")
        self.assertIn("too much work", str(stopped.exception))
        self.assertLess(time.monotonic() - t0, resolve.MAX_SECONDS + 3)
        self.assertEqual(run(conn, "SELECT count(*) AS value FROM entities")[0]["value"], 1514)  # the connection still works

    def test_a_result_is_capped(self):
        big = scene()
        big.entities += [Entity(id=f"x{i}", type="wall") for i in range(resolve.MAX_ROWS + 5)]
        with self.assertRaises(QueryError) as capped:
            run(table(big), "SELECT id FROM entities")
        self.assertIn("more than", str(capped.exception))


class Execution(unittest.TestCase):
    def test_a_value_and_its_evidence_come_from_one_execution(self):
        a = execute(decide(queries={"a": "SELECT sum(m2) AS value, group_concat(id) AS ids FROM entities WHERE type = 'room' AND subtype = 'bedroom'"}, unit="m2"), scene())
        self.assertEqual((a.answer, a.unit, a.evidence, a.confidence, a.tier), (28.0, "m2", ["b1", "b2"], 1.0, "parse"))
        self.assertIn("execute", [s.step for s in a.trace])

    def test_arithmetic_is_done_here_with_the_stated_assumptions(self):
        d = Decision(kind="computed", queries={"area": "SELECT m2 AS value, id FROM entities WHERE id = 'k1'"},
                     formula="ceil(area * (1 + waste) / tile_m2)", unit="count", assumptions={"tile_m2": 0.36, "waste": 0.1, "tile": "60 x 60 cm"})
        a = execute(d, scene())
        self.assertEqual((a.answer, a.evidence), (31, ["k1"]))
        self.assertIn("gives 31", a.formula)
        self.assertEqual(a.assumptions["tile"], "60 x 60 cm")

    def test_no_ids_no_answer(self):
        for sql in ("SELECT sum(m2) AS value FROM entities WHERE type = 'room'",  # an aggregate with no ids
                    "SELECT 28.0 AS value, 'invented' AS ids", "SELECT 5 AS value, NULL AS ids",
                    "SELECT m2 AS value, id FROM entities WHERE type = 'room'",  # five rows for one value
                    "SELECT label AS value, id FROM entities WHERE id = 'k1'"):
            with self.subTest(sql=sql):
                with self.assertRaises(QueryError):
                    execute(decide(queries={"q": sql}), scene())
        with self.assertRaises(QueryError):  # a number with no query behind it
            execute(Decision(kind="computed", queries={"q": "SELECT m2 AS value, id FROM entities WHERE id = 'k1'"}, formula="86"), scene())
        with self.assertRaises(QueryError):
            resolve.parse('{"kind": "direct", "queries": {}}')
        with self.assertRaises(QueryError):
            resolve.parse('{"kind": "direct", "queries": {"q": "SELECT 1"}, "value": 42}')  # no field takes a number

    def test_the_largest_is_named_with_its_value_and_ties_are_reported(self):
        a = execute(decide(queries={"q": ROOMS_BY_AREA}, answer="top", unit="m2"), scene())
        self.assertEqual((a.answer, a.unit, a.evidence), ("Zimmer B1 and Zimmer B2 (tied)", None, ["b1", "b2"]))
        self.assertIn("Zimmer B1 = 14 m2", a.formula)
        self.assertIn("2 are tied on 14 m2", a.interpretation)
        one = execute(decide(queries={"q": ROOMS_BY_AREA.replace("DESC", "ASC")}, answer="top", unit="m2"), scene())
        self.assertEqual((one.answer, one.evidence), ("Balkon T1", ["t1"]))
        self.assertIn("ranked first of 5, next Korridor C1 = 6 m2", one.formula)

    def test_a_list_names_every_row(self):
        a = execute(decide(queries={"q": "SELECT id, m2 AS value FROM entities WHERE type = 'room' AND m2 > 9 ORDER BY m2 DESC"}, answer="rows", unit="m2"), scene())
        self.assertEqual((a.answer, a.evidence), ("Zimmer B1, Zimmer B2 and Küche K1", ["b1", "b2", "k1"]))
        none = execute(decide(queries={"q": "SELECT id FROM entities WHERE type = 'room' AND m2 > 100"}, answer="rows"), scene())
        self.assertEqual((none.answer, none.evidence), ("None", []))

    def test_relationships_go_through_links_and_cite_the_linked_items(self):
        a = execute(decide(queries={"q": DOORS_PER_ROOM}, answer="top", unit="count"), scene())
        self.assertEqual(a.answer, "Korridor C1")
        self.assertEqual(a.evidence, ["c1", "d1", "d2", "d3"])
        between = ("SELECT count(*) AS value, group_concat(id) AS ids FROM entities d WHERE d.type = 'door' "
                   "AND EXISTS (SELECT 1 FROM links l JOIN entities r ON r.id = l.room_id WHERE l.entity_id = d.id AND r.subtype = 'corridor') "
                   "AND EXISTS (SELECT 1 FROM links l JOIN entities r ON r.id = l.room_id WHERE l.entity_id = d.id AND r.subtype = 'bedroom')")
        b = execute(decide(queries={"n": between}, unit="count"), scene())
        self.assertEqual((b.answer, b.evidence), (2, ["d2", "d3"]))
        widths = execute(decide(queries={"w": "SELECT sum(m) AS value, group_concat(id) AS ids FROM entities WHERE type = 'window' AND id IN "
                                              "(SELECT entity_id FROM links WHERE room_id = 'k1')"}, unit="m"), scene())
        self.assertEqual((widths.answer, widths.evidence), (1.2, ["w1"]))
        self.assertIn("1 window item(s) touch no room", widths.confidence_note)  # w2: a fact of a parsed drawing, and said

    def test_groups_are_named_by_the_query(self):
        sql = "SELECT apartment AS name, sum(m2) AS value, group_concat(id) AS ids FROM entities WHERE type = 'room' AND apartment IS NOT NULL GROUP BY apartment ORDER BY value DESC"
        a = execute(decide(queries={"q": sql}, answer="top", unit="m2"), scene())
        self.assertEqual((a.answer, a.evidence), ("A", ["k1", "b1"]))

    def test_unresolved_or_missing_links_refuse_a_per_room_answer(self):
        s = scene(tier="detect")
        s.entities[5].attrs.update(room_link_status="uncertain", room_link_candidates='["k1", "b1"]')
        a = execute(decide(queries={"q": DOORS_PER_ROOM}, answer="top"), s)
        self.assertIsNone(a.answer)
        self.assertIn("unresolved or ambiguous room link", a.error)
        loose = scene(tier="detect")  # on an image, an item with no link is not evidence of no connection
        self.assertIn("have no room link", execute(decide(queries={"q": DOORS_PER_ROOM.replace("'door'", "'window'")}, answer="top"), loose).error)
        self.assertIsNotNone(execute(decide(queries={"q": ROOMS_BY_AREA}, answer="top"), s).answer)  # other answers are unaffected

    def test_covers_a_zero_about_something_not_looked_for_is_an_abstention(self):
        count = lambda kind: decide(queries={"n": f"SELECT count(*) AS value, group_concat(id) AS ids FROM entities WHERE type = '{kind}'"}, unit="count")
        wall = execute(count("wall"), scene())
        self.assertIsNone(wall.answer)
        self.assertIn("does not cover wall", wall.error)
        self.assertEqual(execute(count("door"), scene(covers=("room", "door"))).answer, 4)
        no_doors = scene()
        no_doors.entities = [e for e in no_doors.entities if e.type != "door"]
        self.assertEqual(execute(count("door"), no_doors).answer, 0)  # covered, so none found means none
        sink = decide(queries={"n": "SELECT count(*) AS value, group_concat(id) AS ids FROM entities WHERE type = 'fixture' AND subtype = 'sink'"})
        self.assertIn("does not cover fixture:sink", execute(sink, scene()).error)
        untyped = decide(queries={"n": "SELECT count(*) AS value, group_concat(id) AS ids FROM entities WHERE label = 'Sauna'"})
        self.assertIn("names no entity type", execute(untyped, scene()).error)

    def test_a_size_that_is_not_recorded_is_not_a_zero(self):
        a = execute(decide(queries={"h": "SELECT sum(height_m) AS value, group_concat(id) AS ids FROM entities WHERE type = 'door'"}), scene())
        self.assertIsNone(a.answer)
        self.assertIn("not recorded", a.error)

    def test_missing_names_the_data_and_offers_what_a_person_can_supply(self):
        d = Decision(kind="missing", missing="the ceiling height of room K1: no height is recorded for rooms", needs={"ceiling_height_m": "the ceiling height in metres"},
                     queries={"p": "SELECT m AS value, id FROM entities WHERE id = 'k1'"}, formula="p * ceiling_height_m", unit="m2")
        a = execute(d, scene())
        self.assertIsNone(a.answer)
        self.assertIn("Not in this drawing's data: the ceiling height of room K1", a.error)
        self.assertIn("If you can supply the ceiling height in metres", a.error)
        self.assertEqual(a.assumptions, {"ceiling_height_m": "needed: the ceiling height in metres"})
        filled = execute(d, scene(), supplied={"ceiling_height_m": 2.5, "unrelated": 9})
        self.assertEqual((filled.answer, filled.unit, filled.assumptions, filled.evidence), (31.625, "m2", {"ceiling_height_m": 2.5}, ["k1"]))
        dead_end = execute(Decision(kind="missing", missing="the construction year: a drawing does not hold it"), scene())
        self.assertEqual((dead_end.answer, dead_end.assumptions), (None, {}))


class OneCallThenNone(unittest.TestCase):
    """resolve(): the path of a question with no cached plan, with the model replaced by canned replies."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        cache = patch.object(pipeline, "CACHE", Path(self.temp.name))
        cache.start()
        self.addCleanup(cache.stop)

    def ask(self, *replies):
        replies = [json.dumps(r) if not isinstance(r, str) else r for r in replies]
        mock = patch.object(resolve, "_ask_model", side_effect=[(r, 1800, 60) for r in replies])
        self.addCleanup(mock.stop)
        return mock.start()

    def test_a_new_question_costs_one_call_and_a_repeat_none(self):
        ask = self.ask({"kind": "direct", "queries": {"q": ROOMS_BY_AREA}, "answer": "top", "unit": "m2"})
        first = resolve.resolve("Which room is the biggest?", scene())
        self.assertEqual(first.answer, "Zimmer B1 and Zimmer B2 (tied)")
        self.assertEqual([(s.step, s.cached, s.in_tokens) for s in first.trace], [("decide", False, 1800), ("execute", False, 0)])
        self.assertAlmostEqual(first.cost_usd, (1800 * 0.75 + 60 * 3.75) / 1e6)
        sent = ask.call_args.args[0]
        self.assertIn("Inventory:\nDrawing read by test@1", sent[1]["content"])  # it looked at the data first
        self.assertIn("Question: Which room is the biggest?", sent[1]["content"])
        again = resolve.resolve("  which ROOM is the biggest? ", scene())
        self.assertEqual((again.answer, again.cost_usd, again.trace[0].cached), (first.answer, 0.0, True))
        self.assertEqual(ask.call_count, 1)

    def test_nothing_is_shared_with_a_drawing_whose_data_differs(self):
        ask = self.ask(*[{"kind": "direct", "queries": {"q": ROOMS_BY_AREA}, "answer": "top"}] * 2)
        resolve.resolve("Which room is the biggest?", scene())
        other = scene()
        other.entities[0].label = "Cuisine"
        resolve.resolve("Which room is the biggest?", other)
        self.assertEqual(ask.call_count, 2)

    def test_a_query_error_goes_back_once_then_it_abstains(self):
        bad = {"kind": "direct", "queries": {"q": "SELECT sum(area) AS value FROM rooms"}}
        ask = self.ask(bad, {"kind": "direct", "queries": {"q": "SELECT sum(m2) AS value, group_concat(id) AS ids FROM entities WHERE type = 'room'"}, "unit": "m2"})
        fixed = resolve.resolve("Total area?", scene())
        self.assertEqual((fixed.answer, ask.call_count), (49.0, 2))
        self.assertIn("That cannot be run: SQL error", ask.call_args.args[0][-1]["content"])
        self.assertEqual([s.step for s in fixed.trace], ["decide", "decide", "execute"])
        ask = self.ask(bad, "not json at all")
        failed = resolve.resolve("Total area, again?", scene())
        self.assertIsNone(failed.answer)
        self.assertIn("No unverified answer is shown", failed.error)
        self.assertEqual(ask.call_count, 2)
        self.assertFalse(resolve.cache_file("Total area, again?", inventory(scene())).exists())  # a failure is not cached

    def test_without_a_key_it_says_so_and_the_caller_keeps_its_fallbacks(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            with self.assertRaises(NotImplementedError):
                resolve.resolve("Which room is the biggest?", scene())

    def answer(self, source, question="Which room is the biggest?"):
        with patch.object(pipeline, "get_scene", return_value=source), patch.object(pipeline, "_BUILT_NOW", set()):
            return pipeline.answer(source.path, question)

    def test_pipeline_a_seeded_shape_never_reaches_the_model(self):
        self.ask()  # any call would raise StopIteration
        with patch.object(escalate, "escalate", side_effect=AssertionError("no agent")):
            a = self.answer(scene(), "How many doors (interior doors and entrance doors together) are drawn on this floor plan?")
        self.assertEqual((a.answer, a.cost_usd, a.trace[0].step), (4, 0.0, "plan"))

    def test_pipeline_a_parsed_file_is_settled_by_its_data(self):
        self.ask({"kind": "missing", "missing": "door heights: no height_m is recorded for doors", "needs": {}})
        with patch.object(escalate, "escalate", side_effect=AssertionError("a parsed file is never read off its picture")):
            a = self.answer(scene(), "How tall are the doors?")
        self.assertEqual((a.answer, a.tier), (None, "parse"))
        self.assertIn("door heights", a.error)

    def test_pipeline_an_image_scene_may_look_elsewhere_after_missing(self):
        self.ask({"kind": "missing", "missing": "the scale: nothing is measured in metres"})
        seen = Answer(answer="about 40 m2", tier="llm", confidence=0.6)
        with patch.object(escalate, "escalate", return_value=seen) as agent:
            a = self.answer(scene(tier="detect"), "How big is the flat?")
        self.assertEqual(a.answer, "about 40 m2")
        self.assertIn("the scale", agent.call_args.args[3].error)  # the agent is told what is missing


class Agent(unittest.TestCase):
    def test_the_query_tool_returns_rows_and_refuses_writes(self):
        said = json.loads(escalate._tool("query", {"sql": ROOMS_BY_AREA + " LIMIT 2"}, scene())[0])
        self.assertEqual((said["rows"], [r["id"] for r in said["result"]]), (2, ["b1", "b2"]))
        self.assertIn("query failed", escalate._tool("query", {"sql": "DELETE FROM entities"}, scene())[0])
        s = scene(tier="detect")
        s.entities[5].attrs["room_link_status"] = "uncertain"
        with patch.object(resolve, "refine_links", return_value=[]):
            self.assertIn("NOT USABLE for a per-room answer", json.loads(escalate._tool("query", {"sql": DOORS_PER_ROOM}, s)[0])["note"])

    def test_a_final_ranking_is_bound_to_an_executed_decision(self):
        from blueprints.brain.test_escalate import VerifiedAgentAnswers
        run_agent = VerifiedAgentAnswers.run_agent
        d = {"kind": "direct", "queries": {"q": DOORS_PER_ROOM}, "answer": "top", "unit": "count"}
        result, _ = run_agent(self, [{"answer": "Küche", "explanation": "The kitchen has most doors.", "decision": json.dumps(d)}], source=scene())
        self.assertEqual((result.answer, result.evidence, result.tier), ("Korridor C1", ["c1", "d1", "d2", "d3"], "llm"))
        self.assertIn("verify_answer", [s.step for s in result.trace])
        bad = dict(d, queries={"q": "SELECT 'Küche' AS name, 9 AS value"})
        refused, _ = run_agent(self, [{"answer": "Küche", "explanation": "x", "decision": json.dumps(bad)}], source=scene())
        self.assertIsNone(refused.answer)


if __name__ == "__main__":
    unittest.main()
