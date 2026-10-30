"""Conservative local commands and validated action proposals."""
from dataclasses import dataclass
import re
import unicodedata
from .domain import Action, ACTION_TARGETS, CHOICES, validate_action


@dataclass(frozen=True)
class Interpretation:
    kind: str
    action: Action | None = None
    source: str = 'local'


def model_interpretation(value):
    if not isinstance(value, dict) or set(value) != {'kind', 'action', 'target', 'item'}:
        raise ValueError('Invalid interpretation schema')
    if value['kind'] not in {'action', 'ambiguous', 'dialogue'}: raise ValueError('Invalid intent kind')
    action = validate_action({k: value[k] for k in ['action', 'target', 'item']})
    if (value['kind'] == 'action') == (action.name == 'unknown'):
        raise ValueError('Action and intent disagree')
    return Interpretation(value['kind'], action if value['kind'] == 'action' else None, 'openai')


def compact(text): return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text).lower())


def local_parse(text, state):
    t = compact(text)
    if t in {'1', '2'} and state.pending_event:
        name, target = CHOICES[state.pending_event][int(t)-1]
        return Interpretation('action', Action(name, target))
    if any(word in t for word in ['?', '？', '되나요', '해도되', '만약', '가정', '어떻게', '왜', '라고', '라는', '생각', '"', '“', '”', "'"]):
        return Interpretation('ambiguous')
    if any(word in t for word in ['시스템', '프롬프트', '규칙을무시', '위반횟수', '상태를변경', '살아있다고']):
        return Interpretation('ambiguous')
    if any(word in t for word in ['그리고', '한뒤', '그다음', '한후', '하면서']):
        return Interpretation('ambiguous')
    # An explicit correction selects its final clause. Do not guess compound plans.
    for pivot in ['대신', '말고']:
        if pivot in t: t = t.rsplit(pivot, 1)[1]
    if t in {'안녕', '안녕하세요', '무서워', '무서워요', '고마워', '고마워요'}:
        return Interpretation('dialogue')
    exact = {
        'inspect': ['주변을조사한다', '입구를조사한다', '숲을조사한다', '창고를조사한다', '통제소를조사한다', '통제소기록을조사한다', '주변을살펴본다', '조사', '조사한다'],
        'read_rules': ['규칙을읽는다', '포스터를읽는다', '규칙확인', '포스터확인'],
        'escape': ['탈출한다', '탈출', '관리도로로탈출한다', '관리도로로나간다'],
        'wait': ['기다린다', '가만히있는다', '잠시기다린다', '기다려본다'],
    }
    for name, forms in exact.items():
        if t in forms:
            target = {'입구를조사한다':'forest_entrance', '숲을조사한다':'deep_forest',
                      '창고를조사한다':'poster_warehouse', '통제소를조사한다':'control_station',
                      '통제소기록을조사한다':'control_station'}.get(t) if name == 'inspect' else None
            return Interpretation('action', Action(name, target))
    if t in {'열쇠를사용한다', '창고열쇠를사용한다'}:
        return Interpretation('action', Action('use_item', 'control_station', '창고 열쇠'))
    destinations = [('숲입구', 'forest_entrance'), ('깊은숲', 'deep_forest'),
                    ('포스터창고', 'poster_warehouse'), ('창고', 'poster_warehouse'),
                    ('산림통제소', 'control_station'), ('통제소', 'control_station')]
    candidates = {target for word, target in destinations if word in t}
    if len(candidates) == 1 and re.search(r'(?:로|으로)(?:이동한다|간다|향한다|들어간다)$', t):
        if any(re.fullmatch(re.escape(word)+r'(?:로|으로)(?:이동한다|간다|향한다|들어간다)', t) for word, _ in destinations):
            return Interpretation('action', Action('move', next(iter(candidates))))
    patterns = [
        (r'(시선을피|고개를돌|바라보지않|쳐다보지않|응시하지않)', 'look_away', 'luminous'),
        (r'(물러난|거리(?:를)?벌|두걸음물러|떨어진다)', 'step_back', 'companion'),
        (r'(문을열어|문을연다|문을닫지않|문이닫히지않)', 'open_door', 'door'),
        (r'(울음소리(?:를)?무시|소리(?:를)?무시|따라가지않)', 'ignore_sound', 'bleating'),
        (r'(대답하지않|응답하지않|침묵한다)', 'keep_silent', 'name_call'),
    ]
    safe = [Action(name, target) for pattern, name, target in patterns if re.search(pattern, t)]
    # Remove negated predicates before searching for dangerous verbs.
    remaining = re.sub(r'(?:바라보|쳐다보|응시하|문을닫|대답하|응답하|따라가)지않(?:는다|고|음)?', '', t)
    unsafe = []
    for pattern, name, target in [
        (r'(정면으로바라본|쳐다본|응시한다|계속바라본)', 'stare', 'luminous'),
        (r'(곁에머문|동료에게다가간|동료를부축)', 'stay_with', 'companion'),
        (r'(문을닫는다|문닫는다)', 'close_door', 'door'),
        (r'(울음소리(?:를)?따라|양소리(?:를)?따라)', 'follow_sound', 'bleating'),
        (r'(죽은양(?:의)?(?:수를|을)센다|죽은양을세어본다)', 'count_sheep', 'dead_sheep'),
        (r'(대답한다|응답한다|제이름을말한다)', 'answer_call', 'name_call'),
    ]:
        if re.search(pattern, remaining): unsafe.append(Action(name, target))
    if re.search(r'(죽은양.*세지않|죽은양.*세는대신기록)', t):
        safe.append(Action('record', 'dead_sheep'))
    actions = set(safe+unsafe)
    forms = {
        'look_away': {'시선을피한다', '고개를돌린다', '바라보지않는다', '쳐다보지않는다', '빛나는사람을쳐다보지않는다', '응시하지않는다'},
        'step_back': {'물러난다', '뒤로물러난다', '두걸음물러난다', '동료에게서물러난다', '거리를벌린다', '동료와거리를벌린다', '떨어진다'},
        'open_door': {'문을연다', '문을열어둔다', '문을닫지않는다', '창고문을열어둔다', '포스터창고문을열어둔다'},
        'ignore_sound': {'울음소리를무시한다', '양울음소리를무시한다', '소리를무시한다', '따라가지않는다'},
        'keep_silent': {'대답하지않는다', '응답하지않는다', '침묵한다'},
        'stare': {'정면으로바라본다', '빛나는사람을정면으로바라본다', '쳐다본다', '빛나는사람을쳐다본다', '계속바라본다', '응시한다'},
        'stay_with': {'동료곁에머문다', '곁에머문다', '동료에게다가간다', '동료를부축한다'},
        'close_door': {'문을닫는다', '창고문을닫는다', '포스터창고문을닫는다'},
        'follow_sound': {'울음소리를따라간다', '양울음소리를따라간다', '양소리를따라간다'},
        'count_sheep': {'죽은양의수를센다', '죽은양을센다', '죽은양을세어본다'},
        'answer_call': {'대답한다', '응답한다', '제이름을말한다', '문밖의목소리에대답한다'},
        'record': {'죽은양의수를세지않는다', '죽은양을세지않는다'},
    }
    if len(actions) == 1:
        action = next(iter(actions))
        if t in forms.get(action.name, set()): return Interpretation('action', action)
        return Interpretation('ambiguous')
    if len(actions) > 1: return Interpretation('ambiguous')
    if t in {'촬영한다', '녹화한다', '영상으로남긴다', '기록한다'}:
        target = 'dead_sheep' if state.pending_event == 'dead_sheep' else 'luminous' if state.pending_event == 'luminous' else None
        return Interpretation('action', Action('record', target, '조사 카메라'))
    if t in {'동료에게말을건다', '동료에게질문한다'}:
        return Interpretation('action', Action('talk', 'companion'))
    if t == '실종자에게말을건다': return Interpretation('action', Action('talk', 'missing_person'))
    return Interpretation('ambiguous')


SCHEMA = {'type': 'object', 'properties': {
    'kind': {'type': 'string', 'enum': ['action', 'ambiguous', 'dialogue']},
    'action': {'type': 'string', 'enum': sorted(ACTION_TARGETS)},
    'target': {'type': ['string', 'null'], 'enum': sorted({t for values in ACTION_TARGETS.values() for t in values if t is not None})+[None]},
    'item': {'type': ['string', 'null'], 'enum': ['조사 카메라', '손전등', '창고 열쇠', None]},
}, 'required': ['kind', 'action', 'target', 'item'], 'additionalProperties': False}
