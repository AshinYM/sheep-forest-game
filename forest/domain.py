from dataclasses import dataclass, field, asdict
from copy import deepcopy

LOCATIONS = {'forest_entrance': '숲 입구', 'deep_forest': '깊은 숲',
             'poster_warehouse': '포스터 창고', 'control_station': '산림 통제소'}
ADJACENCY = {'forest_entrance': {'deep_forest'}, 'deep_forest': {'forest_entrance', 'poster_warehouse'},
             'poster_warehouse': {'deep_forest', 'control_station'}, 'control_station': {'poster_warehouse'}}
RULES = {
    1: '빛나는 사람을 정면으로 바라보지 않는다.',
    2: '변이 중인 사람 곁에 두 턴 이상 머물지 않는다.',
    3: '숲 안쪽에서 들려오는 양 울음소리를 따라가지 않는다.',
    4: '죽은 양의 수를 세지 않는다.',
    5: '포스터가 발견된 방의 문은 닫지 않는다.',
    6: '문밖에서 자신의 이름을 불러도 대답하지 않는다.',
}
HINTS = {
    1: ('형체의 빛이 눈 안쪽에 오래 남는다. 시선이 닿는 곳에서 기척이 자란다.', '그 얼굴을 확인하려 할수록 가까워진다. 눈길을 다른 곳으로 돌릴 틈은 남아 있다.'),
    2: ('곁에 오래 머무를수록 동료와 네 숨소리가 하나로 겹친다.', '변이는 가까운 곳으로 번진다. 지금은 붙잡기보다 거리를 둘 때다.'),
    3: ('울음소리는 길을 알려주는 대신 네 걸음에 맞춰 멀어진다.', '울음이 부르는 방향은 지도에 없다. 소리가 아닌 네가 확인한 길을 골라라.'),
    4: ('수를 떠올리는 순간, 보이지 않던 한 마리가 더 생긴다.', '숫자로 남기려 하지 마라. 시체가 있다는 사실만 기록할 수 있다.'),
    5: ('문이 좁아질수록 방 바깥의 소리도 함께 사라진다.', '포스터가 있는 방은 바깥과 이어져 있어야 한다. 문틈을 남길 방법을 찾아라.'),
    6: ('문밖의 목소리는 네 이름을 알지만, 네 대답을 기다리고 있다.', '익숙한 목소리라도 응답할 필요는 없다. 문 너머와 대화를 시작하지 마라.'),
}
SCENES = {
    'luminous': '나무 사이의 사람이 천천히 고개를 든다. 얼굴에서 새어 나온 빛이 네 발끝까지 닿는다.',
    'companion': '뒤따르던 동료의 손이 굳어간다. 눈동자에 양의 가로로 긴 틈이 보인다.',
    'poster_door': '방 안에서 바람이 불지 않는데도 포스터 창고의 문이 서서히 닫히고 있다.',
    'bleating': '지도에 없는 숲 안쪽에서 양 울음이 들린다. 한 발 옮길 때마다 소리도 조금 멀어진다.',
    'dead_sheep': '길가에 움직이지 않는 양들이 쓰러져 있다. 조사 기록의 빈칸이 눈에 들어온다.',
    'name_call': '문밖에서 네 이름을 부르는 목소리가 들린다. 함께 온 동료의 목소리와 똑같다.',
}
CHOICES = {
    'luminous': [('look_away', 'luminous'), ('stare', 'luminous')],
    'companion': [('stay_with', 'companion'), ('step_back', 'companion')],
    'poster_door': [('close_door', 'door'), ('open_door', 'door')],
    'bleating': [('follow_sound', 'bleating'), ('ignore_sound', 'bleating')],
    'dead_sheep': [('count_sheep', 'dead_sheep'), ('record', 'dead_sheep')],
    'name_call': [('answer_call', 'name_call'), ('keep_silent', 'name_call')],
}
ACTION_TARGETS = {
    'inspect': set(LOCATIONS) | {None}, 'move': set(LOCATIONS), 'look_away': {'luminous'}, 'stare': {'luminous'},
    'step_back': {'companion'}, 'stay_with': {'companion'}, 'open_door': {'door'}, 'close_door': {'door'},
    'follow_sound': {'bleating'}, 'ignore_sound': {'bleating'}, 'count_sheep': {'dead_sheep'},
    'answer_call': {'name_call'}, 'keep_silent': {'name_call'},
    'record': {'luminous', 'dead_sheep', None}, 'talk': {'companion', 'luminous', 'missing_person'},
    'read_rules': {None}, 'escape': {None}, 'wait': {None}, 'use_item': {'control_station'},
    'unknown': {None},
}
LABELS = {'inspect': '주변을 조사한다', 'move': '이동한다', 'look_away': '빛나는 사람에게서 시선을 피한다',
          'stare': '빛나는 사람을 정면으로 바라본다', 'step_back': '변이 중인 동료에게서 물러난다',
          'stay_with': '변이 중인 동료 곁에 머문다', 'open_door': '포스터 창고 문을 열어둔다',
          'close_door': '포스터 창고 문을 닫는다', 'follow_sound': '양 울음소리를 따라간다',
          'ignore_sound': '양 울음소리를 무시한다', 'count_sheep': '죽은 양의 수를 센다',
          'answer_call': '문밖의 부름에 대답한다', 'keep_silent': '문밖의 부름에 대답하지 않는다',
          'record': '관찰 내용을 기록한다', 'talk': '말을 건다', 'read_rules': '규칙 포스터를 읽는다',
          'escape': '관리도로로 탈출한다', 'wait': '잠시 기다린다', 'use_item': '열쇠로 통제소에 들어간다',
          'unknown': '행동을 다시 설명한다'}


@dataclass(frozen=True)
class Action:
    name: str
    target: str | None = None
    item: str | None = None

    def label(self):
        if self.name == 'move': return LOCATIONS[self.target]+'으로 이동한다'
        if self.name == 'inspect' and self.target: return LOCATIONS[self.target]+'을 조사한다'
        return LABELS[self.name]


def validate_action(value):
    if not isinstance(value, dict) or set(value) != {'action', 'target', 'item'}:
        raise ValueError('Action must have action, target, item only')
    name, target, item = value['action'], value['target'], value['item']
    if not isinstance(name, str) or name not in ACTION_TARGETS or (target is not None and not isinstance(target, str)):
        raise ValueError('Unknown action or target type')
    if target not in ACTION_TARGETS[name]: raise ValueError('Unsupported action target')
    if item is not None and item not in {'조사 카메라', '손전등', '창고 열쇠'}: raise ValueError('Unsupported item')
    if item and name not in {'use_item', 'record', 'open_door'}: raise ValueError('Item not applicable')
    if name == 'use_item' and item != '창고 열쇠': raise ValueError('Key is required')
    return Action(name, target, item)


@dataclass
class GameState:
    revision: int = 0
    turn: int = 0
    location: str = 'forest_entrance'
    violations: int = 0
    violation_history: list[int] = field(default_factory=list)
    exposure_turns: int = 0
    inventory: list[str] = field(default_factory=lambda: ['조사 카메라', '손전등'])
    clues: list[str] = field(default_factory=list)
    rules_found: list[int] = field(default_factory=list)
    pending_event: str | None = None
    event_history: list[str] = field(default_factory=list)
    poster_room_door_open: bool = True
    game_over: bool = False
    ending: str | None = None

    @property
    def time(self):
        total = (22*60+self.turn*5) % (24*60)
        return f'{total//60:02d}:{total%60:02d}'

    def public(self):
        return {'revision': self.revision, 'turn': self.turn, 'time': self.time,
                'location': self.location, 'location_name': LOCATIONS[self.location],
                'violations': self.violations, 'violation_limit': 3,
                'inventory': list(self.inventory), 'clues': list(self.clues),
                'rules_found': {str(i): RULES[i] for i in self.rules_found},
                'scene': SCENES.get(self.pending_event, '숲이 조용하다. 주변을 조사하거나 길을 따라 이동할 수 있다.'),
                'event': self.pending_event, 'game_over': self.game_over, 'ending': self.ending}


@dataclass
class TurnResult:
    status: str
    message: str
    state: GameState
    hint: str | None = None

    def public(self):
        return {'status': self.status, 'message': self.message, 'hint': self.hint, 'state': self.state.public()}


class GameEngine:
    """Each action returns a new state. Invalid/unclear input never mutates it."""
    def __init__(self, state=None): self.state = state or GameState()

    def _violation(self, rule):
        s = self.state
        s.violations += 1
        s.violation_history.append(rule)
        s.revision += 1
        if s.violations == 3:
            s.game_over, s.ending = True, 'death'
            return TurnResult('game_over', '세 번째 경고가 끊겼다. 나무 사이에서 양 한 마리가 네 목소리로 울었다.\n조사원 사망. GAME OVER.', s)
        # Hints grow clearer by GLOBAL strike, while remaining bound to the rule.
        return TurnResult('violation', f'{s.violations}번째 규칙 위반. 낯선 기척이 손을 멈춰 세웠다. 아직 행동을 바꿀 수 있다.', s, HINTS[rule][s.violations-1])

    def _blocked(self, message): return TurnResult('clarify', message, self.state)

    def _advance(self, message):
        s = self.state
        s.turn += 1
        s.revision += 1
        if s.turn >= 96:
            s.game_over, s.ending = True, 'daybreak'
            return TurnResult('ended', '날이 밝아 통제구역 수색이 종료됐다. 조사 기록을 확보하지 못했다.', s)
        if s.pending_event is None:
            schedule = [(4, 'luminous', {'deep_forest', 'poster_warehouse'}),
                        (8, 'companion', {'deep_forest', 'poster_warehouse'}),
                        (12, 'poster_door', {'poster_warehouse'}),
                        (16, 'bleating', {'deep_forest'}),
                        (20, 'dead_sheep', {'deep_forest'}),
                        (24, 'name_call', {'poster_warehouse', 'control_station'})]
            for turn, event, locations in schedule:
                if s.turn >= turn and s.location in locations and event not in s.event_history:
                    s.pending_event = event
                    s.event_history.append(event)
                    break
        return TurnResult('advanced', message, s)

    def apply(self, action):
        # Defence in depth for callers bypassing the interpreter.
        validate_action({'action': action.name, 'target': action.target, 'item': action.item})
        original = self.state
        self.state = deepcopy(original)
        s = self.state
        if s.game_over: return TurnResult('game_over' if s.ending == 'death' else 'ended', '이미 끝난 조사입니다. 새 조사를 시작하세요.', s)
        if action.name == 'unknown': return self._blocked('어떤 행동을 할지 한 가지로 다시 설명해 주세요. 위반과 시간은 늘지 않았습니다.')
        name, event = action.name, s.pending_event
        contextual = {'stare': 'luminous', 'look_away': 'luminous', 'stay_with': 'companion',
                      'step_back': 'companion', 'follow_sound': 'bleating', 'ignore_sound': 'bleating',
                      'count_sheep': 'dead_sheep', 'answer_call': 'name_call', 'keep_silent': 'name_call'}
        if name in contextual and event != contextual[name]:
            return self._blocked('지금 장면에는 그 대상이 없습니다. 현재 상황에서 할 행동을 다시 말해 주세요.')
        if name in {'close_door', 'open_door'} and s.location != 'poster_warehouse':
            return self._blocked('현재 위치에는 포스터 창고 문이 없습니다.')
        if action.item and action.item not in s.inventory: return self._blocked('그 물건을 가지고 있지 않습니다.')
        if name == 'inspect' and action.target is not None and action.target != s.location:
            return self._blocked('그 장소에 있지 않습니다. 먼저 이동하거나 지금 주변을 조사해 주세요.')
        violation = {'stare': 1, 'follow_sound': 3, 'count_sheep': 4, 'close_door': 5, 'answer_call': 6}.get(name)
        if (name in {'stay_with', 'wait'} and event == 'companion') or (name == 'talk' and action.target == 'companion' and event == 'companion'):
            if event == 'companion' and s.exposure_turns >= 1: violation = 2
        if violation: return self._violation(violation)
        safe_event = {'look_away': 'luminous', 'step_back': 'companion', 'open_door': 'poster_door',
                      'ignore_sound': 'bleating', 'keep_silent': 'name_call'}
        if name in safe_event and event == safe_event[name]:
            s.pending_event = None
            if name == 'step_back': s.exposure_turns = 0
            if name == 'open_door': s.poster_room_door_open = True
            return self._advance(action.label()+'. 기척이 조금씩 멀어진다.')
        if name == 'record' and event == 'dead_sheep':
            s.pending_event = None
            return self._advance('수를 세지 않고 현장의 형태만 기록했다. 길이 다시 눈에 들어온다.')
        if name in {'stay_with', 'wait'} and event == 'companion':
            s.exposure_turns += 1
            return self._advance('동료 곁에 잠시 머물렀다. 그 사람의 숨소리가 네 숨과 겹친다.')
        if name == 'talk':
            if event == 'companion' and action.target == 'companion':
                s.exposure_turns += 1
                return self._advance('동료가 알아듣기 힘든 말을 중얼거린다. 얼굴의 윤곽이 흔들린다.')
            if event == 'luminous' and action.target == 'luminous': return self._advance('눈을 마주치지 않고 말을 건넸다. 빛나는 형체는 대답하지 않았다.')
            return self._blocked('지금 대화할 대상이 뚜렷하지 않습니다. 장소나 대상을 다시 확인해 주세요.')
        if event and name == 'wait': return self._advance('기다렸지만 눈앞의 기척은 물러나지 않았다.')
        if event and name == 'record' and event == 'luminous': return self._advance('눈을 마주치지 않고 카메라 화면에 형체를 남겼다. 형체는 여전히 그 자리에 있다.')
        if event:
            return self._blocked('눈앞의 상황이 아직 끝나지 않았습니다. 무엇을 할지 구체적으로 말해 주세요. 위반과 시간은 늘지 않았습니다.')
        if name in {'move', 'use_item'}:
            destination = action.target
            if destination not in ADJACENCY[s.location]: return self._blocked('바로 이어진 길이 아닙니다. 현재 위치에서 갈 수 있는 길을 확인하세요.')
            if destination == 'poster_warehouse' and '산길 지도' not in s.inventory: return self._blocked('창고로 가는 길을 모릅니다. 깊은 숲을 먼저 조사하세요.')
            if destination == 'control_station' and '창고 열쇠' not in s.inventory: return self._blocked('통제소 철문이 잠겨 있습니다. 창고에서 열쇠를 찾아보세요.')
            s.location = destination
            return self._advance(LOCATIONS[destination]+'에 도착했다.')
        if name == 'inspect':
            item, clue = {'forest_entrance': ('선발 조사대 카메라', '포스터 창고의 위치'),
                          'deep_forest': ('산길 지도', '지도 뒷면의 관리도로 표시'),
                          'poster_warehouse': ('창고 열쇠', '규칙 포스터 원본'),
                          'control_station': ('관리도로 열쇠', '통제소 출입기록')}[s.location]
            if item in s.inventory: return self._advance('주변을 다시 살폈지만 새로운 단서는 없었다.')
            s.inventory.append(item)
            s.clues.append(clue)
            if s.location == 'poster_warehouse': s.rules_found = list(RULES)
            if s.location == 'control_station': s.clues.append('이상현상 관찰 보고서')
            return self._advance('물건과 단서를 확보했다.\n'+item+' · '+clue)
        if name == 'read_rules':
            if s.location != 'poster_warehouse': return self._blocked('이곳에는 규칙 포스터가 없습니다.')
            s.rules_found = list(RULES)
            return self._advance('포스터에서 여섯 가지 규칙을 읽었다. 발견한 규칙 목록에 기록했다.')
        if name == 'escape':
            if s.location != 'control_station' or '관리도로 열쇠' not in s.inventory or '이상현상 관찰 보고서' not in s.clues:
                return self._blocked('관리도로 출구와 조사 기록이 필요합니다. 통제소를 먼저 조사하세요.')
            s.game_over, s.ending, s.revision = True, 'escaped', s.revision+1
            return TurnResult('escaped', '관리도로 차단기가 열렸다. 조사 기록을 품고 숲 밖으로 걸어 나왔다.\n정상 탈출.', s)
        if name == 'open_door': return self._advance('포스터 창고의 문을 열어두었다.')
        if name == 'record': return self._advance('현장의 풍경을 기록했다. 기록만으로 새로운 단서가 생기지는 않았다.')
        if name == 'wait': return self._advance('발걸음을 멈추고 숲의 소리를 들었다.')
        return self._blocked('현재 장면에서 처리할 수 없는 행동입니다. 다시 설명해 주세요.')
