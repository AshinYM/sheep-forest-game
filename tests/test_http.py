from dataclasses import asdict
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from forest.__main__ import make_handler
from forest.domain import GameState
from forest.llm import OpenAIInterpreter
from forest.service import GameService


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory()
        cls.service=GameService(Path(cls.directory.name)/'http.db')
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(cls.service))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.directory.cleanup()
    def setUp(self):
        self.service.interpreter=None
        self.cookie=None
        code,data=self.request('POST','/api/new',{})
        self.assertEqual(code,201);self.state=data['state']
    def request(self,method,path,body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        supplied={'Content-Type':'application/json'}
        if self.cookie: supplied['Cookie']=self.cookie
        supplied.update(headers or {})
        connection.request(method,path,json.dumps(body) if body is not None else None,supplied)
        response=connection.getresponse()
        if response.getheader('Set-Cookie'): self.cookie=response.getheader('Set-Cookie').split(';',1)[0]
        data=response.read();status=response.status;connection.close()
        return status,json.loads(data)
    def action(self,text,request_id='http-request-01',state=None):
        s=state or self.state
        return {'operation':'input','request_id':request_id,'game_id':s['game_id'],'expected_revision':s['revision'],'text':text}
    def seed(self,violations=0):
        token=self.cookie.split('=',1)[1]
        with self.service.db() as db:
            db.execute('UPDATE sessions SET state=? WHERE token=?',(json.dumps(asdict(GameState(pending_event='luminous',violations=violations))),token))

    def test_cookie_state_and_durable_double_submit(self):
        self.seed();payload=self.action('쳐다본다')
        code,result=self.request('POST','/api/turn',payload)
        self.assertEqual((code,result['state']['violations']),(200,1))
        code,replay=self.request('POST','/api/turn',payload)
        self.assertTrue(replay['replayed']);self.assertEqual(replay['state']['violations'],1)
        code,replay=self.request('GET','/api/state')
        self.assertEqual(replay['state']['violations'],1)

    def test_three_strikes_and_ended_request_do_not_add_more(self):
        self.seed()
        for i in range(3):
            _,result=self.request('POST','/api/turn',self.action('쳐다본다',f'http-danger-{i}'))
            self.state=result['state']
            self.assertEqual(self.state['violations'],i+1)
            self.assertEqual(self.state['game_over'],i==2)
        _,result=self.request('POST','/api/turn',self.action('쳐다본다','http-after-death'))
        self.assertEqual(result['state']['violations'],3)

    def test_stale_revision_and_new_game_binding(self):
        old=self.state
        _,first=self.request('POST','/api/turn',self.action('주변을 조사하면 어떤 일이 생길까?'))
        self.assertEqual(first['state']['revision'],0)
        _,first=self.request('POST','/api/turn',self.action('주변 조사를 할지 말지 고민 중이야','ambiguous-002'))
        self.assertEqual(first['state']['violations'],0)
        _,first=self.request('POST','/api/turn',self.action('발밑을 조심스럽게 볼까?' ,'ambiguous-003'))
        _,advanced=self.request('POST','/api/turn',self.action('주변을 조사한다','http-advance'))
        self.assertEqual(advanced['state']['revision'],1)
        code,result=self.request('POST','/api/turn',self.action('기다린다','http-stale',old))
        self.assertEqual(code,409);self.assertEqual(result['error_code'],'stale_revision')
        self.request('POST','/api/new',{})
        code,result=self.request('POST','/api/turn',self.action('기다린다','http-old-game',old))
        self.assertEqual(code,409);self.assertEqual(result['error_code'],'stale_game')

    def test_origin_body_and_client_state_injection_rejected(self):
        code,_=self.request('POST','/api/turn',self.action('주변을 조사한다'),{'Origin':'https://foreign.example'})
        self.assertEqual(code,403)
        code,_=self.request('POST','/api/turn',{**self.action('주변을 조사한다'),'violations':0})
        self.assertEqual(code,400)
        code,_=self.request('POST','/api/turn',self.action('x'*1001))
        self.assertEqual(code,400)
        _,data=self.request('GET','/api/state');self.assertEqual(data['state']['revision'],0)

    def test_cookie_is_http_only_and_ui_assets_are_local(self):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        c.request('POST','/api/new','{}',{'Content-Type':'application/json'})
        r=c.getresponse();cookie=r.getheader('Set-Cookie');r.read();c.close()
        self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)
        self.assertTrue(cookie.startswith(f'forest_session_{self.server.server_port}='))
        for path,content_type in [('/','text/html'),('/app.js','text/javascript'),('/style.css','text/css')]:
            c=http.client.HTTPConnection('127.0.0.1',self.server.server_port);c.request('GET',path)
            r=c.getresponse();self.assertEqual(r.status,200);self.assertIn(content_type,r.getheader('Content-Type'));r.read();c.close()

    @unittest.skipUnless(os.getenv('FOREST_SIMULATION_URL'),'Start local Postman simulation and set FOREST_SIMULATION_URL')
    def test_real_http_provider_failures_confirmation_and_retry(self):
        interpreter=OpenAIInterpreter(key='simulation-only',model='simulation-model',
                                     base_url=os.environ['FOREST_SIMULATION_URL'],timeout=.15,simulation=True)
        self.service.interpreter=interpreter
        self.service.per_game_limit=30;self.service.call_limit=50
        self.seed(violations=2)
        errors={'SIM_429':'rate_limited','SIM_500':'provider_unavailable','SIM_HTML':'provider_unavailable',
                'SIM_BAD_JSON':'invalid_response','SIM_REFUSAL':'refused','SIM_TIMEOUT':'timeout',
                'SIM_INCOMPLETE':'incomplete_response','SIM_BAD_TARGET':'invalid_response',
                'SIM_UNKNOWN_ACTION':'invalid_response','SIM_TAMPER':'invalid_response'}
        for i,(text,error) in enumerate(errors.items()):
            with self.subTest(scenario=text):
                payload=self.action(text,f'provider-fail-{i}')
                code,result=self.request('POST','/api/turn',payload)
                self.assertEqual(code,200);self.assertEqual(result.get('error_code'),error)
                for key,value in [('violations',2),('turn',0),('revision',0),('game_over',False)]:self.assertEqual(result['state'][key],value)
                self.assertNotIn('simulation-only',json.dumps(result))
                _,again=self.request('POST','/api/turn',payload)
                self.assertTrue(again['replayed'])
                self.assertEqual(again['state']['model_attempts'],i+1)
        for i,text in enumerate(['SIM_AMBIGUOUS','SIM_DIALOGUE']):
            _,result=self.request('POST','/api/turn',self.action(text,f'provider-kind-{i}'))
            self.assertEqual(result['state']['violations'],2)
            self.assertNotIn('proposal',result)
        for i in range(2):
            _,result=self.request('POST','/api/turn',self.action('SIM_DANGER',f'provider-danger-{i}'))
            self.assertEqual(result['status'],'confirmation_required');self.assertEqual(result['state']['violations'],2)
            operation='cancel' if i==0 else 'confirm'
            payload={k:v for k,v in self.action('','provider-confirm-'+str(i)).items() if k!='text'}
            payload.update(operation=operation,proposal_id=result['proposal']['id'])
            _,result=self.request('POST','/api/turn',payload)
            self.assertEqual(result['state']['violations'],2 if i==0 else 3)
            self.assertEqual(result['state']['game_over'],i==1)
        self.assertEqual(result['state']['model_attempts'],14)


if __name__=='__main__':unittest.main()
