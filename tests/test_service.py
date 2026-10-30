from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forest.domain import GameState
from forest.interpret import model_interpretation
from forest.llm import ProviderError, from_environment, OpenAIInterpreter
from forest.service import GameService, GameError


class FakeInterpreter:
    def __init__(self, error=None): self.calls, self.error = 0, error
    def interpret(self, text, state):
        self.calls += 1
        if self.error: raise self.error
        return model_interpretation({'kind':'action','action':'stare','target':'luminous','item':None})


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)/'game.db'
        self.service = GameService(self.path)
        self.token, self.initial = self.service.create()
    def tearDown(self): self.directory.cleanup()
    def payload(self, *, request_id='request-0001', text='쳐다본다', operation='input', proposal=None, state=None):
        state = state or self.service.snapshot(self.token)
        return {'operation':operation,'request_id':request_id,'expected_revision':state['revision'],
                'game_id':state['game_id'], **({'text':text} if operation=='input' else {'proposal_id':proposal})}
    def scene(self):
        with self.service.db() as db:
            db.execute('UPDATE sessions SET state=? WHERE token=?',(json.dumps(asdict(GameState(pending_event='luminous'))),self.token))

    def test_request_replay_persists_across_restart(self):
        self.scene(); payload=self.payload()
        first=self.service.submit(self.token,payload)
        restarted=GameService(self.path)
        replay=restarted.submit(self.token,payload)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['state']['violations'],1)
        self.assertEqual(restarted.snapshot(self.token)['revision'],first['state']['revision'])

    def test_same_request_id_different_body_rejected(self):
        self.scene(); payload=self.payload(); self.service.submit(self.token,payload)
        with self.assertRaises(GameError) as error:
            self.service.submit(self.token,{**payload,'text':'시선을 피한다'})
        self.assertEqual(error.exception.code,'request_id_conflict')
        self.assertEqual(self.service.snapshot(self.token)['violations'],1)

    def test_concurrent_duplicate_only_one_strike(self):
        self.scene(); payload=self.payload()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.service.submit(self.token,payload),range(8)))
        self.assertEqual(sum(not r['replayed'] for r in results),1)
        self.assertEqual(self.service.snapshot(self.token)['violations'],1)

    def test_distinct_concurrent_requests_reject_stale_revision(self):
        self.scene(); initial=self.service.snapshot(self.token)
        def submit(i):
            try: return self.service.submit(self.token,self.payload(request_id=f'request-{i:04}',state=initial))['status']
            except GameError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(submit,range(2)))
        self.assertEqual(set(results),{'violation','stale_revision'})
        self.assertEqual(self.service.snapshot(self.token)['violations'],1)

    def test_model_proposes_and_confirmation_alone_can_change_state(self):
        self.scene(); fake=FakeInterpreter(); self.service.interpreter=fake
        proposal=self.service.submit(self.token,self.payload(text='낯선 얼굴의 정체를 눈으로 확인해볼래'))
        self.assertEqual(proposal['status'],'confirmation_required')
        self.assertEqual(proposal['state']['violations'],0)
        self.assertEqual(proposal['state']['revision'],0)
        confirmation=self.payload(operation='confirm',proposal=proposal['proposal']['id'],request_id='confirm-0001')
        result=self.service.submit(self.token,confirmation)
        self.assertEqual(result['state']['violations'],1)
        replay=self.service.submit(self.token,confirmation)
        self.assertEqual(replay['state']['violations'],1)
        self.assertEqual(fake.calls,1)

    def test_cancel_and_cross_session_proposal_cannot_penalize(self):
        self.scene(); self.service.interpreter=FakeInterpreter()
        result=self.service.submit(self.token,self.payload(text='낯선 표현의 행동'))
        other_token, other_state=self.service.create()
        payload=self.payload(operation='confirm',proposal=result['proposal']['id'],state=other_state)
        self.assertEqual(self.service.submit(other_token,payload)['status'],'rejected')
        cancelled=self.service.submit(self.token,self.payload(operation='cancel',proposal=result['proposal']['id'],request_id='cancel-0001'))
        self.assertEqual(cancelled['state']['violations'],0)
        self.assertEqual(cancelled['state']['turn'],0)

    def test_provider_failure_and_replay_never_change_world(self):
        self.scene(); fake=FakeInterpreter(ProviderError('timeout')); self.service.interpreter=fake
        before=self.service.snapshot(self.token); payload=self.payload(text='자유로운 표현')
        result=self.service.submit(self.token,payload)
        self.assertEqual(result['status'],'interpretation_error')
        for key in ['violations','turn','revision','location','event']: self.assertEqual(result['state'][key],before[key])
        self.assertEqual(self.service.submit(self.token,payload)['state']['violations'],0)
        self.assertEqual(fake.calls,1)

    def test_crash_after_reservation_does_not_automatically_call_again(self):
        self.scene(); fake=FakeInterpreter(RuntimeError('simulated crash')); self.service.interpreter=fake
        payload=self.payload(text='뜻밖의 말')
        with self.assertRaises(RuntimeError): self.service.submit(self.token,payload)
        self.assertEqual(fake.calls,1)
        restarted=GameService(self.path,FakeInterpreter())
        with self.assertRaises(GameError) as error: restarted.submit(self.token,payload)
        self.assertEqual(error.exception.code,'interrupted')
        self.assertEqual(restarted.interpreter.calls,0)
        self.assertEqual(restarted.snapshot(self.token)['violations'],0)

    def test_call_budget_is_durable_and_reset_does_not_bypass_it(self):
        fake=FakeInterpreter(ProviderError('timeout'))
        self.service=GameService(self.path,fake,call_limit=1,per_game_limit=1)
        self.service.submit(self.token,self.payload(text='모호한 말'))
        other_token,state=self.service.create()
        result=self.service.submit(other_token,self.payload(text='모호한 말',state=state))
        self.assertEqual(result['error_code'],'call_limit')
        self.assertEqual(fake.calls,1)
        restarted=GameService(self.path,fake,call_limit=1)
        self.assertEqual(restarted.snapshot(self.token)['model_attempts'],1)

    def test_input_cannot_set_strikes_or_state_and_old_game_payload_is_rejected(self):
        with self.assertRaises(GameError): self.service.submit(self.token,{**self.payload(),'violations':0})
        token,state=self.service.create()
        with self.assertRaises(GameError): self.service.submit(token,self.payload())
        self.assertEqual(self.service.snapshot(token)['violations'],0)

    def test_empty_ambiguous_dialogue_inputs_and_game_over_do_not_call_provider(self):
        fake=FakeInterpreter(); self.service.interpreter=fake
        self.service.submit(self.token,self.payload(text='안녕'))
        self.assertEqual(fake.calls,0)
        with self.assertRaises(GameError): self.service.submit(self.token,self.payload(text=' '))
        with self.service.db() as db: db.execute('UPDATE sessions SET state=? WHERE token=?',(json.dumps(asdict(GameState(game_over=True,ending='death',violations=3))),self.token))
        result=self.service.submit(self.token,self.payload(text='자유로운 말',request_id='ended-0001'))
        self.assertEqual(result['state']['violations'],3)
        self.assertEqual(fake.calls,0)

    def test_paid_mode_requires_explicit_opt_in_and_live_keys_cannot_use_custom_origin(self):
        with patch.dict('os.environ',{'FOREST_LLM_MODE':'local','OPENAI_API_KEY':'unused'},clear=True):
            self.assertIsNone(from_environment())
        with patch.dict('os.environ',{'FOREST_LLM_MODE':'openai'},clear=True):
            with self.assertRaises(ValueError): from_environment()
        with self.assertRaises(ValueError): OpenAIInterpreter(key='unused',model='explicit',base_url='http://example.com')
        with self.assertRaises(ValueError): OpenAIInterpreter(key='real-key-not-allowed',model='explicit',base_url='http://127.0.0.1:4567',simulation=True)

    def test_replay_returns_latest_state_and_does_not_reopen_cancelled_proposal(self):
        self.scene();self.service.interpreter=FakeInterpreter()
        payload=self.payload(text='낯선 말로 표현한 행동')
        proposal=self.service.submit(self.token,payload)
        self.service.submit(self.token,self.payload(operation='cancel',proposal=proposal['proposal']['id'],request_id='cancel-action'))
        replay=self.service.submit(self.token,payload)
        self.assertIsNone(replay.get('proposal'))
        first=self.payload(request_id='first-strike')
        result=self.service.submit(self.token,first)
        self.service.submit(self.token,self.payload(request_id='second-strike'))
        replay=self.service.submit(self.token,first)
        self.assertEqual(replay['state']['violations'],2)


if __name__=='__main__': unittest.main()
