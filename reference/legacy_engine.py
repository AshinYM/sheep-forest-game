from dataclasses import dataclass, field, asdict
from typing import Optional
import html, json, os, re

TURN_MINUTES = 5
MAX_TURNS = 96
CONTAMINATION_LIMIT = 6

RULES = [
    '빛나는 사람을 정면으로 바라보지 않는다.',
    '변이 중인 사람 곁에 두 턴 이상 머물지 않는다.',
    '숲 안쪽에서 들려오는 양 울음소리를 따라가지 않는다.',
    '죽은 양의 수를 세지 않는다.',
    '포스터가 발견된 방의 문은 닫지 않는다.',
    '문밖에서 자신의 이름을 불러도 대답하지 않는다.',
]

LOCATIONS = {
    'forest_entrance': '숲 입구',
    'deep_forest': '깊은 숲',
    'poster_warehouse': '포스터 창고',
    'control_station': '산림 통제소',
}

ADJACENCY = {
    'forest_entrance': {'deep_forest'},
    'deep_forest': {'forest_entrance', 'poster_warehouse'},
    'poster_warehouse': {'deep_forest', 'control_station'},
    'control_station': {'poster_warehouse'},
}

@dataclass
class Action:
    name: str
    target: Optional[str] = None
    item: Optional[str] = None
    raw: str = ''
    confidence: float = 1.0
    source: str = 'rule'

@dataclass
class GameState:
    turn: int = 0
    location: str = 'forest_entrance'
    contamination: int = 0
    exposure_turns: int = 0
    rules_found: list[int] = field(default_factory=list)
    clues: list[str] = field(default_factory=list)
    inventory: list[str] = field(default_factory=lambda: ['조사 카메라', '손전등'])
    visited: list[str] = field(default_factory=lambda: ['forest_entrance'])
    pending_event: Optional[str] = None
    event_history: list[str] = field(default_factory=list)
    poster_room_door_open: bool = True
    off_route_attempts: int = 0
    game_over: bool = False
    ending: Optional[str] = None

    @property
    def time(self):
        total = (22 * 60 + self.turn * TURN_MINUTES) % (24 * 60)
        return f'{total // 60:02d}:{total % 60:02d}'

    def snapshot(self):
        data = asdict(self)
        data['time'] = self.time
        data['location_name'] = LOCATIONS[self.location]
        return data


# 자연어를 명시적인 Action 객체로 변환합니다.
ALLOWED_ACTIONS = {
    'inspect', 'move', 'look_away', 'stare', 'record', 'open_door', 'close_door',
    'use_item', 'talk', 'step_back', 'stay_with', 'escape', 'read_rules', 'wait', 'unknown'
}

def _compact(text):
    return re.sub(r'\s+', '', (text or '').lower())

def _contains(text, words):
    return any(word in text for word in words)

def _destination(t):
    if _contains(t, ['통제소', '관리동']): return 'control_station'
    if _contains(t, ['포스터창고', '창고']): return 'poster_warehouse'
    if _contains(t, ['깊은숲', '숲안쪽']): return 'deep_forest'
    if _contains(t, ['숲입구', '입구로']): return 'forest_entrance'
    return None

def local_parse(text, state=None):
    raw, t = text or '', _compact(text)
    # 사건 선택 번호는 현재 사건의 문맥 안에서만 의미를 가집니다.
    if state and state.pending_event and t in {'1', '2'}:
        mapping = {
            'luminous': {'1': 'look_away', '2': 'stare'},
            'companion': {'1': 'stay_with', '2': 'step_back'},
            'poster_door': {'1': 'close_door', '2': 'open_door'},
        }
        return Action(mapping[state.pending_event][t], raw=raw)

    # 부정·대안 표현에서는 문장 뒤쪽의 최종 의도를 우선합니다.
    safe_pos = max(t.rfind(k) for k in ['시선을피', '고개를돌', '보지않', '쳐다보지않'])
    stare_pos = max(t.rfind(k) for k in ['쳐다본', '바라본', '응시'])
    if safe_pos >= 0 and safe_pos >= stare_pos:
        return Action('look_away', target='luminous', raw=raw)
    if stare_pos >= 0 and not _contains(t, ['바라보지않', '쳐다보지않', '응시하지않']):
        return Action('stare', target='luminous', raw=raw)

    if _contains(t, ['두걸음물러', '거리벌', '뒤로물러', '떨어진다']):
        return Action('step_back', target='companion', raw=raw)
    if _contains(t, ['다가간', '곁에머문', '부축', '함께있는']):
        return Action('stay_with', target='companion', raw=raw)
    if _contains(t, ['촬영', '녹화', '영상으로남', '카메라로찍']):
        return Action('record', target='luminous', item='조사 카메라', raw=raw)
    if _contains(t, ['문을닫', '문닫']): return Action('close_door', target='door', raw=raw)
    if _contains(t, ['문을열', '열어둔', '문이닫히지않']): return Action('open_door', target='door', raw=raw)
    if '열쇠' in t and _contains(t, ['사용', '넣', '연다', '열어']):
        return Action('use_item', target='control_station', item='창고 열쇠', raw=raw)
    if _contains(t, ['말을건', '대화', '질문', '물어본', '말한다']):
        target = 'missing_person' if '실종자' in t else ('luminous' if '빛나는' in t else 'companion')
        return Action('talk', target=target, raw=raw)
    if _contains(t, ['포스터를읽', '규칙을읽', '포스터확인', '규칙확인']):
        return Action('read_rules', target='poster', raw=raw)
    if _contains(t, ['탈출', '관리도로로나', '밖으로나']): return Action('escape', raw=raw)
    destination = _destination(t)
    if destination and _contains(t, ['이동', '간다', '들어간', '향한다', '나간다']):
        return Action('move', target=destination, raw=raw)
    if _contains(t, ['조사', '수색', '살펴', '둘러', '뒤져', '찾아본']):
        return Action('inspect', raw=raw)
    if _contains(t, ['기다', '가만히', '숨는다', '조용히']): return Action('wait', raw=raw)
    return Action('unknown', raw=raw, confidence=0.0)

def build_optional_llm():
    # LLM_PROVIDER가 없으면 완전 무료 규칙 모드입니다.
    provider = os.getenv('LLM_PROVIDER', '').lower()
    try:
        if provider == 'ollama':
            from langchain_ollama import ChatOllama
            return ChatOllama(model=os.getenv('OLLAMA_MODEL', 'qwen2.5:3b'), temperature=0)
        if provider == 'gemini' and os.getenv('GOOGLE_API_KEY'):
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(model=os.getenv('GEMINI_MODEL', 'gemini-2.0-flash-lite'), temperature=0)
    except Exception as exc:
        print('선택 LLM 연결 생략:', exc)
    return None

OPTIONAL_LLM = build_optional_llm()

def parse_action(text, state):
    parsed = local_parse(text, state)
    if parsed.name != 'unknown' or OPTIONAL_LLM is None:
        return parsed
    prompt = f'''규칙괴담 게임의 사용자 행동을 JSON 한 줄로 분류하세요.
허용 action: {sorted(ALLOWED_ACTIONS - {'unknown'})}
현재 위치: {state.location}, 진행 사건: {state.pending_event}, 소지품: {state.inventory}
출력 키: action, target, item. 허용되지 않은 행동은 wait가 아니라 unknown으로 표시하세요.
사용자 입력: {text}'''
    try:
        content = OPTIONAL_LLM.invoke(prompt).content
        if isinstance(content, list):
            content = ''.join(x.get('text', '') if isinstance(x, dict) else str(x) for x in content)
        match = re.search(r'\{.*?\}', str(content), re.S)
        data = json.loads(match.group())
        name = data.get('action', 'unknown')
        if name in ALLOWED_ACTIONS:
            return Action(name, data.get('target'), data.get('item'), text, 0.7, 'llm')
    except Exception:
        pass
    return parsed


EVENT_SCHEDULE = {4: 'luminous', 8: 'companion', 12: 'poster_door'}
EVENT_PROMPTS = {
    'luminous': '[사건] 빛나는 사람이 고개를 들었습니다.\n1) 시선을 피한다  2) 계속 바라본다',
    'companion': '[사건] 동료가 빛나는 형체를 바라보고 있습니다.\n1) 동료에게 다가간다  2) 두 걸음 물러난다',
    'poster_door': '[사건] 포스터 창고의 문이 닫히고 있습니다.\n1) 문을 닫는다  2) 문을 열어둔다',
}

class GameEngine:
    def __init__(self, state=None):
        self.state = state or GameState()

    def end(self, ending):
        self.state.game_over = True
        self.state.ending = ending

    def _advance(self):
        self.state.turn += 1
        if self.state.contamination >= CONTAMINATION_LIMIT and not self.state.game_over:
            self.end('변이 엔딩: 누적된 오염으로 조사원의 몸이 양의 형태로 굳었다.')
        if self.state.turn >= MAX_TURNS and not self.state.game_over:
            self.end('시간 초과 엔딩: 해가 떴지만 조사원은 돌아오지 않았다.')

    def _trigger_event(self):
        event = EVENT_SCHEDULE.get(self.state.turn)
        if event and event not in self.state.event_history and not self.state.game_over:
            self.state.pending_event = event
            self.state.event_history.append(event)
            return '\n\n' + EVENT_PROMPTS[event]
        return ''

    def _finish_turn(self, message):
        before = self.state.time
        self._advance()
        status = f'[{before} → {self.state.time}] {message}'
        if self.state.game_over:
            return status + f'\nGAME OVER: {self.state.ending}'
        return status + self._trigger_event()

    def _resolve_event(self, action):
        event = self.state.pending_event
        safe = {
            'luminous': 'look_away', 'companion': 'step_back', 'poster_door': 'open_door'
        }[event]
        if action.name == safe:
            self.state.pending_event = None
            if event == 'companion': self.state.exposure_turns = 0
            if event == 'poster_door': self.state.poster_room_door_open = True
            messages = {
                'luminous': '시선을 돌리자 빛나는 형체가 나무 사이로 사라졌다.',
                'companion': '동료와 거리를 벌리자 눈동자의 빛이 희미해졌다.',
                'poster_door': '손전등을 문틈에 끼워 포스터 창고의 문을 열어두었다.',
            }
            return self._finish_turn(messages[event])
        unsafe = {
            'luminous': 'stare', 'companion': 'stay_with', 'poster_door': 'close_door'
        }[event]
        if action.name != unsafe:
            return EVENT_PROMPTS[event] + '\n번호 또는 행동을 다시 입력하세요. 이 입력은 턴을 소비하지 않습니다.'
        endings = {
            'luminous': '응시 엔딩: 빛나는 얼굴을 본 순간 조사원의 피부가 털로 덮였다.',
            'companion': '동료 변이 엔딩: 가까이 다가가자 동료의 얼굴이 양처럼 변했다.',
            'poster_door': '낙원 편입 엔딩: 문이 닫히자 숲 밖으로 나가고 싶다는 생각이 사라졌다.',
        }
        self.end(endings[event])
        return f'[{self.state.time}] 위험한 선택을 했다.\nGAME OVER: {self.state.ending}'

    def _inspect(self):
        s, loc = self.state, self.state.location
        rewards = {
            'forest_entrance': ('선발 조사대 카메라', '카메라 기록: 포스터 창고의 위치', '통제선 아래에서 선발 조사대의 카메라를 찾았다.'),
            'deep_forest': ('산길 지도', '지도 뒷면의 관리도로 표시', '방수 봉투에서 산길 지도를 발견했다.'),
            'poster_warehouse': ('창고 열쇠', '규칙 포스터 원본', '포스터 뒤에서 창고 열쇠와 규칙 원본을 발견했다.'),
            'control_station': ('관리도로 열쇠', '통제소 출입기록', '통제소 출입기록과 관리도로 열쇠를 확보했다.'),
        }
        item, clue, message = rewards[loc]
        if clue in s.clues:
            return self._finish_turn(f'{LOCATIONS[loc]}에는 더 이상 새로운 단서가 없다.')
        if item not in s.inventory: s.inventory.append(item)
        s.clues.append(clue)
        if loc == 'poster_warehouse': s.rules_found = list(range(1, 7))
        if loc == 'control_station' and '이상현상 관찰 보고서' not in s.clues:
            s.clues.append('이상현상 관찰 보고서')
        return self._finish_turn(message)

    def _move(self, destination):
        s = self.state
        if destination == s.location:
            return f'[{s.time}] 이미 {LOCATIONS[s.location]}에 있다.'
        if destination not in ADJACENCY[s.location]:
            return f'[{s.time}] {LOCATIONS[s.location]}에서 {LOCATIONS.get(destination, destination)}로 바로 이동할 수 없다.'
        if destination == 'poster_warehouse' and '산길 지도' not in s.inventory:
            return f'[{s.time}] 창고로 가는 길을 모른다. 깊은 숲을 먼저 조사해야 한다.'
        if destination == 'control_station' and '창고 열쇠' not in s.inventory:
            return f'[{s.time}] 통제소로 이어지는 철문이 잠겨 있다. 창고를 조사해야 한다.'
        s.location = destination
        if destination not in s.visited: s.visited.append(destination)
        return self._finish_turn(f'{LOCATIONS[destination]}에 도착했다.')

    def _talk(self, target):
        s = self.state
        if target == 'missing_person':
            s.exposure_turns += 1
            message = '실종자는 웃으며 “이쪽은 안전합니다”라고 말했다. 기록과 모순되는 말이다.'
        elif target == 'luminous':
            s.contamination += 2
            message = '빛나는 존재가 “당신은 아직 숲 밖에 있다고 생각합니까?”라고 물었다.'
        else:
            s.exposure_turns += 1
            message = '동료는 “빛나는 것을 오래 보지 마세요”라고 낮게 말했다.'
        if s.exposure_turns >= 2:
            self.end('동행 변이 엔딩: 변이 중인 사람 곁에 너무 오래 머물렀다.')
        return self._finish_turn(message)

    def _execute(self, action):
        s = self.state
        if action.name == 'inspect': return self._inspect()
        if action.name == 'move': return self._move(action.target)
        if action.name == 'use_item': return self._move('control_station')
        if action.name == 'read_rules':
            if s.location != 'poster_warehouse': return f'[{s.time}] 이곳에는 규칙 포스터가 없다.'
            s.rules_found = list(range(1, 7))
            return self._finish_turn('포스터에서 여섯 가지 생존 규칙을 확인했다.\n' + '\n'.join(f'{i+1}. {r}' for i, r in enumerate(RULES)))
        if action.name == 'record':
            s.contamination += 1
            if '빛나는 사람의 영상' not in s.clues: s.clues.append('빛나는 사람의 영상')
            return self._finish_turn('카메라 화면에 빛나는 형체가 기록되었다.')
        if action.name == 'stare':
            s.contamination += 3
            return self._finish_turn('빛나는 사람의 윤곽을 정면으로 바라보았다.')
        if action.name == 'look_away': return self._finish_turn('시선을 피하고 호흡을 가다듬었다.')
        if action.name == 'talk': return self._talk(action.target)
        if action.name == 'step_back':
            s.exposure_turns = 0
            return self._finish_turn('위험한 사람에게서 두 걸음 물러났다.')
        if action.name == 'stay_with':
            s.exposure_turns += 1
            if s.exposure_turns >= 2: self.end('동행 변이 엔딩: 변이 중인 동료 곁에 너무 오래 머물렀다.')
            return self._finish_turn('동료 곁에 머물렀다. 눈동자에서 미세한 빛이 번졌다.')
        if action.name in {'open_door', 'close_door'}:
            if s.location != 'poster_warehouse':
                return f'[{s.time}] 현재 위치에는 조작할 포스터 창고 문이 없다.'
            if action.name == 'close_door':
                s.poster_room_door_open = False
                self.end('낙원 편입 엔딩: 포스터 창고 문을 닫자 숲 밖으로 나가고 싶지 않아졌다.')
                return f'[{s.time}] 문을 닫았다.\nGAME OVER: {s.ending}'
            s.poster_room_door_open = True
            return self._finish_turn('포스터 창고 문을 열어두었다.')
        if action.name == 'escape':
            required = {'통제소 출입기록', '이상현상 관찰 보고서'}
            if s.location == 'control_station' and required.issubset(s.clues) and '관리도로 열쇠' in s.inventory and s.poster_room_door_open:
                self.end('정상 탈출: 조사 기록을 확보하고 통제구역 밖으로 빠져나왔다.')
                return f'[{s.time}] 관리도로 차단기를 열었다.\nENDING: {s.ending}'
            return f'[{s.time}] 아직 탈출 조건이 부족하다. 통제소 기록과 관리도로 열쇠가 필요하다.'
        if action.name == 'wait': return self._finish_turn('움직이지 않고 숲의 소리를 들었다.')
        s.off_route_attempts += 1
        return f'[{s.time}] 이해하지 못한 행동입니다. 가능한 행동: 조사 / 이동 / 대화 / 아이템 사용 / 탈출'

    def play(self, text):
        if self.state.game_over:
            return f'GAME OVER: {self.state.ending}'
        action = parse_action(text, self.state)
        if self.state.pending_event:
            return self._resolve_event(action)
        return self._execute(action)


# 회귀 테스트: 기존 노트북에서 발견된 주요 오류가 다시 발생하지 않는지 확인합니다.
def run_regression_tests():
    # 번호 선택 처리
    e = GameEngine(); e.state.pending_event = 'luminous'
    assert not e.play('1').startswith('GAME OVER') and not e.state.game_over

    # 부정·대안 표현 처리
    e = GameEngine(); e.play('쳐다본다 대신 시선을 피한다')
    assert e.state.contamination == 0

    # 잘못된 장소에서 문을 닫아도 엔딩이 나지 않음
    e = GameEngine(); result = e.play('문을 닫는다')
    assert not e.state.game_over and '현재 위치' in result

    # 이동 경로 우회 차단
    e = GameEngine(); e.play('포스터 창고로 이동한다')
    assert e.state.location == 'forest_entrance'

    # 반복 촬영으로도 오염 임계값이 적용됨
    e = GameEngine(); e.state.turn = 20
    for _ in range(6):
        if not e.state.game_over: e.play('영상으로 남긴다')
    assert e.state.game_over

    # 실종자 대상 보존
    action = local_parse('실종자에게 말을 건다')
    assert action.name == 'talk' and action.target == 'missing_person'

    # 정상 완주
    e = GameEngine()
    route = [
        '입구를 조사한다', '깊은 숲으로 이동한다', '숲을 조사한다',
        '포스터 창고로 이동한다', '1', '창고를 조사한다',
        '통제소로 이동한다', '통제소 기록을 조사한다', '2',
        '관리도로로 탈출한다'
    ]
    for command in route:
        e.play(command)
    assert e.state.game_over and e.state.ending.startswith('정상 탈출')
    print('✅ 회귀 테스트 7종 통과')
    print('정상 완주:', e.state.turn, '턴 /', e.state.time, '/', e.state.ending)

run_regression_tests()
