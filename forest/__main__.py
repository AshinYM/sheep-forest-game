"""Local web game: python -m forest --port 8063. LangGraph workflow with a standard-library HTTP server."""
import argparse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re

from .domain import CHOICES, Action
from .llm import from_environment, OpenAIInterpreter
from .service import GameService, GameError

WEB = Path(__file__).resolve().parents[1]/'web'


def make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass  # No input bodies, cookies or tokens in access logs.

        def _valid_host(self):
            expected = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != expected:
                raise GameError('invalid_host', '로컬 주소로 접속해 주세요.', 403)
            origin = self.headers.get('Origin')
            if origin and origin != 'http://'+expected:
                raise GameError('invalid_origin', '다른 사이트에서 보낸 요청은 처리하지 않습니다.', 403)

        def _token(self):
            cookie = SimpleCookie()
            try: cookie.load(self.headers.get('Cookie', ''))
            except Exception: raise GameError('session_missing', '새 조사를 시작하세요.', 401)
            name = f'forest_session_{self.server.server_port}'
            token = cookie[name].value if name in cookie else ''
            if not re.fullmatch(r'[A-Za-z0-9_-]{40,60}', token): raise GameError('session_missing', '새 조사를 시작하세요.', 401)
            return token

        def _send(self, status, value, cookie=None):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if cookie: self.send_header('Set-Cookie', f'forest_session_{self.server.server_port}={cookie}; HttpOnly; SameSite=Strict; Path=/')
            self.end_headers()
            self.wfile.write(body)

        def _error(self, error):
            value = {'status': 'error', 'error_code': error.code, 'message': error.message}
            try: value['state'] = service.snapshot(self._token())
            except GameError: pass
            self._send(error.status, value)

        def do_GET(self):
            try:
                self._valid_host()
                if self.path == '/api/state': return self._send(200, {'state': service.snapshot(self._token())})
                if self.path == '/api/choices':
                    state = service.snapshot(self._token())
                    options = CHOICES.get(state['event'], [])
                    return self._send(200, {'choices': [{'text': str(i), 'label': Action(*a).label()} for i, a in enumerate(options, 1)]})
                if self.path == '/health': return self._send(200, {'status': 'ok', 'mode': service.mode})
                path = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}.get(self.path)
                if path is None: return self._send(404, {'message': '페이지를 찾지 못했습니다.'})
                data = (WEB/path).read_bytes()
                content_type = {'html': 'text/html', 'js': 'text/javascript', 'css': 'text/css'}[path.rsplit('.', 1)[1]]
                self.send_response(200)
                self.send_header('Content-Type', content_type+'; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
                self.end_headers()
                self.wfile.write(data)
            except GameError as error: self._error(error)

        def do_POST(self):
            try:
                self.connection.settimeout(10)
                self._valid_host()
                if self.headers.get('Transfer-Encoding'):
                    raise GameError('invalid_body', '이 전송 형식은 지원하지 않습니다.', 400)
                try: length = int(self.headers.get('Content-Length', '-1'))
                except ValueError: length = -1
                if not 0 <= length <= 16384: raise GameError('invalid_body', '요청 크기를 확인해 주세요.', 413)
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise GameError('invalid_body', 'JSON 요청만 처리합니다.', 415)
                try:
                    data = self.rfile.read(length)
                    if len(data) != length: raise ValueError()
                    payload = json.loads(data)
                except (ValueError, UnicodeError, TimeoutError): raise GameError('invalid_body', '요청을 읽지 못했습니다.', 400)
                if self.path == '/api/new':
                    if payload != {}: raise GameError('invalid_request', '새 조사 요청을 확인하세요.', 400)
                    token, state = service.create()
                    return self._send(201, {'state': state}, token)
                if self.path == '/api/turn': return self._send(200, service.submit(self._token(), payload))
                return self._send(404, {'message': '지원하지 않는 요청입니다.'})
            except GameError as error: self._error(error)
            except (BrokenPipeError, ConnectionResetError): pass
            except Exception:
                self._send(500, {'status': 'error', 'error_code': 'internal_error', 'message': '처리를 완료하지 못했습니다. 상태를 확인한 뒤 같은 요청을 다시 보내세요.'})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8063)
    parser.add_argument('--database', type=Path, default=Path('data/forest.db'))
    parser.add_argument('--simulation-url', help='개발 검증용 localhost 모의 API 주소. 실제 키는 사용하지 않음')
    args = parser.parse_args()
    interpreter = OpenAIInterpreter(key='simulation-only', model='simulation-model', base_url=args.simulation_url, simulation=True) if args.simulation_url else from_environment()
    service = GameService(args.database, interpreter)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(service))
    descriptions = {'local': '로컬 모드 — API 호출 없음', 'openai': 'OpenAI 해석 모드 — 모델 호출 시 비용 발생', 'simulation': '모의 해석 모드 — 실제 유료 API 호출 없음'}
    print(f'양들이 잠든 숲: http://127.0.0.1:{server.server_port} | '+descriptions[service.mode], flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__': main()
