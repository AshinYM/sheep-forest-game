"""Compatibility entry point for the current deterministic engine.

python engine.py runs the core regression suite without a provider or HTTP server.
The previous instant-death version is archived in reference/legacy_engine.py.
"""
from forest.domain import Action, GameState, GameEngine as CoreEngine, RULES, LOCATIONS
from forest.interpret import local_parse


class GameEngine(CoreEngine):
    def play(self, text):
        interpretation = local_parse(text, self.state)
        action = interpretation.action or Action('unknown')
        result = self.apply(action)
        return result.message + ('\n힌트: '+result.hint if result.hint else '')


if __name__ == '__main__':
    import unittest
    suite = unittest.TestSuite()
    for name in ['test_domain.py']:
        suite.addTests(unittest.defaultTestLoader.discover('tests', pattern=name))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    raise SystemExit(not result.wasSuccessful())
