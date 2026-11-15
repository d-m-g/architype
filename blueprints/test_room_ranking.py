"""Interval regressions use no paid model calls."""
import json
import unittest
from unittest.mock import patch

from blueprints.schemas import Entity, Scene, Page, Query, QueryPlan
from blueprints.room_ranking import rank, answer_question, requested
from blueprints.executor import execute


def source(a=6, b=4, unknown=1):
    rooms = [Entity(id='a', type='room', label='Korridor', number='0.05', conf=.7),
             Entity(id='b', type='room', label='Korridor', number='0.13', conf=.7)]
    doors = [Entity(id=f'{rid}{i}', type='door', conf=.9, attrs={'room_id': rid, 'room_link_status': 'linked'})
             for rid, n in [('a', a), ('b', b)] for i in range(n)]
    doors += [Entity(id=f'u{i}', type='door', conf=.9, attrs={'room_link_status': 'uncertain'}) for i in range(unknown)]
    return Scene(path='uploads/test.png', builder='test', tier='detect', covers=['room','door','window'],
                 pages=[Page(image='uploads/test.png', width=100, height=100)], entities=rooms+doors)


class RoomRanking(unittest.TestCase):
    def test_one_unknown_door_cannot_overturn_six_vs_four(self):
        result = rank(source())
        self.assertEqual(result.answer, 'Korridor 0.05')
        self.assertIn('6–7 doors', result.interpretation)
        rows = json.loads(result.trace[0].detail)['rooms']
        self.assertEqual([(r['lower'],r['upper']) for r in rows], [(6,7),(4,5)])
        self.assertIn('u0', result.evidence)
        audit = json.loads(result.trace[1].detail)['queries'][0]
        self.assertEqual(len(audit['included']),6)
        self.assertIn('u0',[e['id'] for e in audit['excluded']])

    def test_two_unknown_doors_can_tie_and_must_not_pick_winner(self):
        self.assertIsNone(rank(source(unknown=2)).answer)

    def test_fewest_uses_upper_bound_against_other_lower_bounds(self):
        self.assertEqual(rank(source(),order='fewest').answer,'Korridor 0.13')
        self.assertIsNone(rank(source(unknown=2),order='fewest').answer)

    def test_exact_tie_is_reported_as_both_rooms(self):
        result=rank(source(4,4,0))
        self.assertEqual(result.answer,'Korridor 0.05, Korridor 0.13')

    def test_uncertainty_is_limited_to_its_page(self):
        s=source(5,4,1);s.entities[-1].page=1
        s.pages.append(Page(image='other.png',width=100,height=100))
        self.assertEqual(rank(s).answer,'Korridor 0.05')

    def test_candidate_rooms_narrow_upper_bound_but_bad_data_is_unknown(self):
        s=source(5,4,1);s.entities[-1].attrs['room_link_candidates']='["a"]'
        self.assertIsNotNone(rank(s).answer)
        s.entities[-1].attrs['room_link_candidates']='["missing"]'
        self.assertIsNone(rank(s).answer)

    def test_partial_connection_counts_once_and_can_affect_other_room(self):
        s=source(5,4,1);s.entities[-1].attrs.update(room_id='a',room_link_status='partial')
        result=rank(s);rows=json.loads(result.trace[0].detail)['rooms']
        self.assertEqual([(r['lower'],r['upper']) for r in rows],[(6,6),(4,5)])

    def test_excluded_and_low_score_symbols_do_not_inflate_counts(self):
        s=source(6,4,2);s.entities[-1].conf=.59;s.entities[-2].attrs['calculation_excluded']=True
        result=rank(s)
        self.assertIn('6 doors',result.interpretation)
        self.assertNotIn('6–',result.interpretation)

    def test_missing_relationships_preserve_existing_fallback(self):
        s=source()
        for e in s.entities:e.attrs={}
        self.assertIsNone(rank(s))

    def test_narrow_shortcut_does_not_swallow_scoped_or_other_questions(self):
        self.assertEqual(requested('Which room has the most doors?'),('door','most'))
        self.assertIsNone(requested('Which room has the most doors in apartment A?'))
        self.assertIsNone(requested('How many doors?'))

    def test_proved_winner_spends_nothing_and_ambiguous_result_refines_once(self):
        with patch('blueprints.brain.room_link_vision.resolve',side_effect=AssertionError('No call needed')):
            self.assertIsNotNone(answer_question('which room has the most doors',source()).answer)
        with patch('blueprints.brain.room_link_vision.resolve',return_value=[]) as refine:
            self.assertIsNone(answer_question('which room has the most doors',source(unknown=2)).answer)
            refine.assert_called_once()

    def test_selected_object_id_distinguishes_identical_labels(self):
        s=source(1,1,0)
        for e in s.entities[2:]:e.label='Door';e.qty={'m':.8 if e.id=='a0' else 1.2}
        q=QueryPlan(queries={'width':Query(op='sum',type='door',qty='m',where={'entity_id':'b0'})},unit='m')
        result=execute(q,s)
        self.assertEqual(result.answer,1.2)
        self.assertEqual(result.evidence,['b0'])

    def test_pipeline_returns_proof_before_paid_planner(self):
        from blueprints import pipeline
        with patch.object(pipeline,'get_scene',return_value=source()),patch('blueprints.brain.plan.plan_with_trace',side_effect=AssertionError('paid planner')):
            result=pipeline.answer('uploads/test.png','Which room has the most doors?')
        self.assertEqual(result.answer,'Korridor 0.05')
        self.assertEqual(result.cost_usd,0)

    def test_weak_room_scores_do_not_bypass_the_existing_cutoff(self):
        s=source();s.entities[0].conf=.4
        with patch('blueprints.brain.room_link_vision.resolve',side_effect=AssertionError('No link review can fix the room score')):
            result=answer_question('which room has the most doors',s)
        self.assertIsNone(result.answer)
        self.assertIn('40%',result.error)

    def test_agent_tool_uses_same_proof_and_selected_id_final_plan_is_valid(self):
        from blueprints.brain.escalate import _tool, _verified_plan
        value,steps=_tool('rank_rooms',{'type':'door','order':'most'},source())
        self.assertEqual(json.loads(value)['answer'],'Korridor 0.05')
        self.assertEqual(steps,[])
        q=QueryPlan(queries={'n':Query(op='count',type='door',where={'entity_id':'a0'})},unit='count')
        self.assertEqual(_verified_plan(q.model_dump_json(),source()).answer,1)

    def test_agent_final_ranking_recomputes_instead_of_trusting_its_proposed_label(self):
        import time
        from types import SimpleNamespace as NS
        from blueprints.brain.escalate import _agent_loop
        from blueprints.schemas import Answer
        call=NS(id='call',function=NS(name='answer',arguments=json.dumps({'answer':'WRONG ROOM','explanation':'guess',
            'ranking':{'type':'door','order':'most'}})))
        message=NS(content=None,tool_calls=[call],model_dump=lambda **kw:{'role':'assistant'})
        response=NS(choices=[NS(message=message)],usage=NS(prompt_tokens=1,completion_tokens=1))
        client=NS(chat=NS(completions=NS(create=lambda **kw:response)))
        with patch('openai.OpenAI',return_value=client):
            result=_agent_loop('Which room has the greatest number of doors?',source(),Answer(),False,
                               'https://unused.test','offline','gemini-3.8-flash',{},time.monotonic()+100,[])
        self.assertEqual(result.answer,'Korridor 0.05')
        self.assertIn('rank_rooms',[step.step for step in result.trace])

    def test_proved_winners_survive_every_assignment_of_unknown_doors(self):
        from itertools import product
        # Independent exhaustive oracle: each unknown door can touch neither, either, or both rooms.
        for a,b,n in product(range(4),range(4),range(3)):
            for order in ('most','fewest'):
                result=rank(source(a,b,n),order=order)
                if result is None or result.answer is None:
                    continue
                claimed=set(result.answer.split(', '))
                for assignment in product(((0,0),(1,0),(0,1),(1,1)),repeat=n):
                    actual=[a+sum(x[0] for x in assignment),b+sum(x[1] for x in assignment)]
                    best=(max if order=='most' else min)(actual)
                    winners={name for name,count in zip(('Korridor 0.05','Korridor 0.13'),actual) if count==best}
                    self.assertEqual(claimed,winners,(a,b,n,order,assignment))

    def test_api_uses_corrected_scene_and_serves_the_new_browser_module(self):
        from fastapi.testclient import TestClient
        from blueprints import api
        with patch.object(api,'PASSWORD_HASH',None),TestClient(api.app) as client:
            response=client.post('/api/room-ranking',json={'question':'Which room has the most doors?', 'scene':source().model_dump()})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['answer'],'Korridor 0.05')
            changed=source(2,4,0)
            response=client.post('/api/room-ranking',json={'question':'Which room has the most doors?', 'scene':changed.model_dump()})
            self.assertEqual(response.json()['answer'],'Korridor 0.13')
            self.assertEqual(client.post('/api/room-ranking',json={'question':'How wide?', 'scene':changed.model_dump()}).status_code,422)
            self.assertEqual(client.get('/object-question.js').status_code,200)
            self.assertEqual(client.get('/object-question.test.js').status_code,404)


if __name__=='__main__':unittest.main()
