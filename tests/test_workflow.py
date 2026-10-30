from dataclasses import asdict
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from forest.domain import GameState
from forest.service import GameService
from forest.workflow import invoke
from forest.interpret import model_interpretation


class Fake:
    def __init__(self): self.calls=0
    def interpret(self,text,state):
        self.calls+=1
        return model_interpretation({'kind':'action','action':'stare','target':'luminous','item':None})


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.service=GameService(Path(self.directory.name)/'graph.db')
        self.token,self.state=self.service.create()
    def tearDown(self):self.directory.cleanup()
    def ctx(self,text):return {'operation':'input','text':text}

    def test_actual_compiled_graph_routes_local_action_and_ambiguity(self):
        out=invoke(self.service.workflow,self.token,self.ctx('주변을 조사한다'),GameState())
        self.assertEqual(out['visited'],['route_request','interpret_local','route_intent','execute_rules'])
        self.assertEqual(out['new_world'].turn,1)
        out=invoke(self.service.workflow,self.token,self.ctx('뜻이 모호한 문장'),GameState())
        self.assertEqual(out['visited'][-1],'no_action')
        self.assertEqual(out['new_world'].turn,0)

    def test_model_node_ends_in_proposal_then_separate_confirmation_reaches_rules(self):
        fake=Fake();self.service.interpreter=fake
        world=GameState(pending_event='luminous')
        out=invoke(self.service.workflow,self.token,self.ctx('눈으로 정체를 확인해볼래'),world)
        self.assertEqual(out['visited'],['route_request','interpret_local','interpret_model','route_intent','propose_action'])
        self.assertEqual(out['new_world'].violations,0)
        confirm={'operation':'confirm','proposal_id':out['result']['proposal']['id']}
        out=invoke(self.service.workflow,self.token,confirm,world)
        self.assertEqual(out['visited'],['route_request','resolve_confirmation','execute_rules'])
        self.assertEqual(out['new_world'].violations,1)
        self.assertEqual(fake.calls,1)

    def test_confirmation_survives_service_restart_without_repeating_model_node(self):
        fake=Fake();self.service.interpreter=fake
        with self.service.db() as db:db.execute('UPDATE sessions SET state=? WHERE token=?',(json.dumps(asdict(GameState(pending_event='luminous'))),self.token))
        payload={'operation':'input','text':'자유로운 표현','request_id':'graph-proposal','expected_revision':0,'game_id':self.state['game_id']}
        proposed=self.service.submit(self.token,payload)
        restarted=GameService(self.service.path,Fake())
        payload={k:v for k,v in payload.items() if k!='text'}
        payload.update(operation='confirm',request_id='graph-confirm',proposal_id=proposed['proposal']['id'])
        out=restarted.submit(self.token,payload)
        self.assertEqual(out['state']['violations'],1)
        self.assertEqual(restarted.interpreter.calls,0)

    def test_local_graph_turn_does_not_enable_langsmith_network_tracing(self):
        with patch.dict('os.environ',{'LANGSMITH_TRACING':'true','LANGSMITH_API_KEY':'test-placeholder-not-real'}), \
             patch.object(socket.socket,'connect',side_effect=AssertionError('External tracing forbidden')) as connect:
            out=invoke(self.service.workflow,self.token,self.ctx('주변을 조사한다'),GameState())
            self.assertEqual(out['new_world'].turn,1)
            connect.assert_not_called()


if __name__=='__main__':unittest.main()
