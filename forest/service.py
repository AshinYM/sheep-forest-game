"""SQLite state and durable request IDs for one local server process."""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading

from .domain import GameEngine, GameState, Action, validate_action
from .interpret import local_parse
from .llm import ProviderError


class GameError(Exception):
    def __init__(self, code, message, status=409): self.code, self.message, self.status = code, message, status


class GameService:
    def __init__(self, database, interpreter=None, *, call_limit=20, per_game_limit=10):
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.interpreter = interpreter
        if not 1 <= call_limit <= 1000 or not 1 <= per_game_limit <= 100:
            raise ValueError('Invalid API attempt limits')
        self.call_limit, self.per_game_limit = call_limit, per_game_limit
        self._locks, self._guard = {}, threading.Lock()
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, game_id TEXT UNIQUE, state TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS requests(token TEXT, request_id TEXT, fingerprint TEXT NOT NULL, response TEXT,
                    PRIMARY KEY(token,request_id));
                CREATE TABLE IF NOT EXISTS proposals(token TEXT, id TEXT PRIMARY KEY, revision INTEGER, action TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS budget(id INTEGER PRIMARY KEY CHECK(id=1), attempts INTEGER NOT NULL);
                INSERT OR IGNORE INTO budget VALUES(1,0);
            ''')
        self.path.chmod(0o600)
        from .workflow import build_workflow
        self.workflow = build_workflow(self)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally: db.close()

    def lock(self, token):
        with self._guard: return self._locks.setdefault(token, threading.RLock())

    @property
    def mode(self):
        return 'simulation' if getattr(self.interpreter, 'simulation', False) else 'openai' if self.interpreter else 'local'

    def create(self):
        token, game_id = secrets.token_urlsafe(32), secrets.token_hex(12)
        with self.db() as db:
            db.execute('INSERT INTO sessions(token,game_id,state) VALUES(?,?,?)', (token, game_id, json.dumps(asdict(GameState()))))
        return token, self.snapshot(token)

    def _row(self, db, token):
        row = db.execute('SELECT * FROM sessions WHERE token=?', (token,)).fetchone()
        if row is None: raise GameError('session_missing', '새 조사를 시작해 주세요.', 401)
        return row

    def snapshot(self, token):
        with self.db() as db:
            row = self._row(db, token)
            state = GameState(**json.loads(row['state']))
            return {**state.public(), 'game_id': row['game_id'],
                    'mode': self.mode,
                    'model_attempts': row['calls'], 'model_attempt_limit': self.per_game_limit}

    def _reserve_call(self, token):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._row(db, token)
            used = db.execute('SELECT attempts FROM budget WHERE id=1').fetchone()[0]
            if row['calls'] >= self.per_game_limit or used >= self.call_limit:
                raise ProviderError('call_limit')
            db.execute('UPDATE budget SET attempts=attempts+1 WHERE id=1')
            db.execute('UPDATE sessions SET calls=calls+1 WHERE token=?', (token,))

    def submit(self, token, payload):
        if not isinstance(payload, dict): raise GameError('invalid_request', '입력 형식을 확인해 주세요.', 400)
        operation = payload.get('operation')
        keys = {'operation', 'request_id', 'expected_revision', 'game_id'} | ({'text'} if operation == 'input' else {'proposal_id'})
        if operation not in {'input', 'confirm', 'cancel'} or set(payload) != keys:
            raise GameError('invalid_request', '지원하지 않는 요청입니다.', 400)
        request_id, expected = payload['request_id'], payload['expected_revision']
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', request_id) or type(expected) is not int or expected < 0:
            raise GameError('invalid_request', '요청 번호나 상태 버전을 확인해 주세요.', 400)
        if operation == 'input' and (not isinstance(payload['text'], str) or not payload['text'].strip() or len(payload['text']) > 1000):
            raise GameError('invalid_input', '행동은 1~1,000자로 입력해 주세요.', 400)
        if operation != 'input' and (not isinstance(payload['proposal_id'], str) or not 8 <= len(payload['proposal_id']) <= 80):
            raise GameError('invalid_request', '확인할 행동을 찾을 수 없습니다.', 400)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.lock(token):
            with self.db() as db:
                row = self._row(db, token)
                if payload['game_id'] != row['game_id']: raise GameError('stale_game', '이미 새 조사가 시작됐습니다. 화면을 갱신해 주세요.')
                state = GameState(**json.loads(row['state']))
                cached = db.execute('SELECT * FROM requests WHERE token=? AND request_id=?', (token, request_id)).fetchone()
                if cached:
                    if cached['fingerprint'] != fingerprint: raise GameError('request_id_conflict', '같은 요청 번호에 다른 행동을 보낼 수 없습니다.')
                    if cached['response'] is None: raise GameError('interrupted', '처리 중이던 요청이 중단됐습니다. 위반은 늘지 않았습니다. 새 입력으로 다시 시도해 주세요.')
                    result = json.loads(cached['response'])
                    result['replayed'] = True
                    result['state'] = self.snapshot(token)
                    if result.get('proposal'):
                        active = db.execute('SELECT id FROM proposals WHERE token=? AND id=? AND revision=?', (token, result['proposal']['id'], state.revision)).fetchone()
                        if active is None:
                            result['proposal'] = None
                            result['status'] = 'rejected'
                            result['message'] = '이미 처리되었거나 만료된 행동 확인입니다. 현재 상태에서 다시 입력하세요.'
                    return result
                if expected != state.revision: raise GameError('stale_revision', '상황이 이미 바뀌었습니다. 최신 상태를 확인하고 다시 입력해 주세요.')
                # Durable reservation: a crash cannot automatically repeat a paid call.
                db.execute('INSERT INTO requests(token,request_id,fingerprint) VALUES(?,?,?)', (token, request_id, fingerprint))
            try:
                result, new_state = self._process(token, payload, state)
            except GameError as error:
                result, new_state = {'status': 'rejected', 'message': error.message, 'error_code': error.code, 'hint': None}, state
            except ProviderError as error:
                messages = {'call_limit': '설정한 모델 호출 횟수에 도달했습니다. 번호·짧은 명령은 계속 사용할 수 있습니다.',
                            'timeout': '행동 해석이 늦어졌습니다. 위반과 시간은 늘지 않았습니다. 다시 입력할 수 있습니다.'}
                result, new_state = {'status': 'interpretation_error', 'message': messages.get(error.code, '행동 해석에 실패했습니다. 위반과 시간은 늘지 않았습니다. 번호나 짧은 명령으로 다시 시도하세요.'),
                                     'error_code': error.code, 'hint': None}, state
            with self.db() as db:
                row = self._row(db, token)
                current = GameState(**json.loads(row['state']))
                if current.revision != state.revision:
                    raise GameError('stale_revision', '처리 중 상황이 바뀌었습니다. 최신 상태를 확인하세요.')
                db.execute('UPDATE sessions SET state=? WHERE token=?', (json.dumps(asdict(new_state), ensure_ascii=False), token))
                if new_state.revision != state.revision: db.execute('DELETE FROM proposals WHERE token=?', (token,))
                result['state'] = {**new_state.public(), 'game_id': row['game_id'], 'mode': self.mode,
                                   'model_attempts': row['calls'], 'model_attempt_limit': self.per_game_limit}
                result['replayed'] = False
                db.execute('UPDATE requests SET response=? WHERE token=? AND request_id=?',
                           (json.dumps(result, ensure_ascii=False), token, request_id))
            return result

    def _process(self, token, payload, state):
        from .workflow import invoke
        output = invoke(self.workflow, token, payload, state)
        return output['result'], output['new_world']

    def _resolve_proposal(self, token, payload, state):
        with self.db() as db:
            proposal = db.execute('SELECT * FROM proposals WHERE token=? AND id=?', (token, payload['proposal_id'])).fetchone()
            if proposal is None or proposal['revision'] != state.revision:
                raise GameError('stale_proposal', '그 행동 확인은 만료됐습니다. 다시 입력해 주세요.')
            db.execute('DELETE FROM proposals WHERE id=?', (proposal['id'],))
        return validate_action(json.loads(proposal['action']))

    def _propose(self, token, state, action):
        proposal_id = secrets.token_urlsafe(18)
        with self.db() as db:
            db.execute('DELETE FROM proposals WHERE token=?', (token,))
            db.execute('INSERT INTO proposals VALUES(?,?,?,?)', (token, proposal_id, state.revision,
                       json.dumps({'action': action.name, 'target': action.target, 'item': action.item}, ensure_ascii=False)))
        return {'status': 'confirmation_required', 'message': '이 행동으로 이해했어요. 의도한 행동이 맞나요?', 'hint': None,
                'proposal': {'id': proposal_id, 'label': action.label()}}
