"""Local parser fixture check, never a paid model benchmark."""
import json
from pathlib import Path
from .domain import GameState
from .interpret import local_parse


def evaluate(path):
    dataset = json.loads(Path(path).read_text())
    failures = []
    for row in dataset['items']:
        result = local_parse(row['text'], GameState(pending_event=row['event']))
        actual = (result.kind, result.action.name if result.action else None, result.action.target if result.action else None)
        if actual != (row['kind'], row['action'], row['target']): failures.append(row['id'])
    return {'scope': dataset['scope'], 'cases': len(dataset['items']), 'passed': len(dataset['items'])-len(failures),
            'failed_ids': failures, 'real_model_calls': 0, 'real_korean_language_accuracy_measured': False}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, default=Path('examples/actions.json'))
    args = parser.parse_args()
    result = evaluate(args.fixtures)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result['failed_ids']))
