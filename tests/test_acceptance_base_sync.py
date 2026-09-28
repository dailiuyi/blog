import tempfile
from pathlib import Path
import unittest
from unittest import mock

import test_acceptance_controller as fixtures
from symphony_acceptance.controller import Controller, git
from symphony_acceptance.core import StateStore, PipelineError


class BaseSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.remote = self.home / 'remote.git'
        self.seed = self.home / 'seed'
        self.seed.mkdir()
        git(self.seed, 'init', '-q', '-b', 'main')
        git(self.seed, 'config', 'user.name', 'test')
        git(self.seed, 'config', 'user.email', 'test@example.com')
        for name in ('value.txt', 'other.txt'):
            (self.seed / name).write_text('base\n')
        git(self.seed, 'add', '.')
        git(self.seed, 'commit', '-qm', 'base')
        git(self.home, 'clone', '--bare', str(self.seed), str(self.remote))
        git(self.seed, 'remote', 'add', 'origin', str(self.remote))
        self.root = self.home / 'workspaces' / 'GH-1'
        self.root.parent.mkdir()
        git(self.home, 'clone', str(self.remote), str(self.root))
        self.base = git(self.root, 'rev-parse', 'HEAD').stdout.strip()
        self.github = fixtures.FakeGitHub(self.base)
        self.config = {'repository': 'example/base-sync', 'control_root': str(self.home / 'control'),
                       'workspace_root': str(self.root.parent), 'base_branch': 'main',
                       'required_checks': ['unit'], 'checks': {'unit': {}}, 'protected_paths': [],
                       'reviewer': {'model': 'gpt-6-astra', 'effort': 'high'}}
        self.controller = Controller(self.config, github=self.github)
        self.controller.store = StateStore(self.config['control_root'], self.config['repository'], 1)
        self.controller.state = {'version': 1, 'repository': self.config['repository'], 'issue': 1,
                                'run_id': 'test-cycle', 'phase': 'checking', 'repairs': 0,
                                'workspace': str(self.root), 'base_sha': self.base, 'branch': 'codex/task',
                                'plan': {'title': 'task', 'acceptance': 'value is correct',
                                         'allowedPaths': ['value.txt'], 'checks': ['unit']}}
        self.controller.store.save(self.controller.state)
        (self.root / 'value.txt').write_text('task\n')

    def advance(self, path='other.txt'):
        (self.seed / path).write_text('new main\n')
        git(self.seed, 'add', '.')
        git(self.seed, 'commit', '-qm', 'advance main')
        git(self.seed, 'push', '-q', 'origin', 'main')
        self.github.base_sha = git(self.seed, 'rev-parse', 'HEAD').stdout.strip()
        return self.github.base_sha

    def restart(self):
        fresh = Controller(self.config, github=self.github)
        fresh.store = self.controller.store
        fresh.state = fresh.store.load()
        self.controller = fresh

    def test_unrelated_main_change_syncs_without_spending_repair(self):
        target = self.advance()
        self.assertTrue(self.controller.sync_base())
        self.assertEqual(self.controller.state['base_sha'], target)
        self.assertEqual(self.controller.state['repairs'], 0)
        self.assertEqual((self.root / 'other.txt').read_text(), 'new main\n')
        self.assertEqual((self.root / 'value.txt').read_text(), 'task\n')

    def test_crash_after_merge_reconciles_before_old_base_scope_check(self):
        target = self.advance()
        def merge_then_crash(root, *args, **kwargs):
            result = git(root, *args, **kwargs)
            if 'merge' in args:
                raise SystemExit('crash after Git mutation')
            return result
        with mock.patch('symphony_acceptance.controller.git', side_effect=merge_then_crash):
            with self.assertRaises(SystemExit):
                self.controller.sync_base()
        self.assertEqual(self.controller.store.load()['base_sha'], self.base)
        self.restart()
        self.assertTrue(self.controller.sync_base())
        self.assertEqual(self.controller.state['base_sha'], target)
        self.assertIsNone(self.controller.state['base_sync_intent'])

    def test_conflict_charged_once_then_controller_stages_coder_resolution(self):
        self.advance('value.txt')
        self.assertFalse(self.controller.sync_base())
        self.assertEqual(self.controller.state['repairs'], 1)
        self.restart()
        self.assertFalse(self.controller.sync_base())
        self.assertEqual(self.controller.state['repairs'], 1)
        (self.root / 'value.txt').write_text('resolved task\n')
        self.controller.phase('checking')
        self.assertTrue(self.controller.sync_base())
        self.assertEqual(self.controller.state['repairs'], 1)
        self.assertNotEqual(git(self.root, 'rev-parse', '--verify', 'MERGE_HEAD', check=False).returncode, 0)

    def test_outside_scope_and_remote_task_head_are_not_overwritten(self):
        self.advance()
        (self.root / 'other.txt').write_text('outside task edit\n')
        with self.assertRaises(PipelineError):
            self.controller.sync_base()
        self.assertEqual((self.root / 'other.txt').read_text(), 'outside task edit\n')
        (self.root / 'other.txt').write_text('base\n')
        self.controller.state['head_sha'] = 'a' * 40
        self.github.head = 'b' * 40
        with self.assertRaisesRegex(PipelineError, 'remote_head_changed'):
            self.controller.sync_base()
        self.assertEqual(git(self.root, 'rev-parse', 'HEAD').stdout.strip(), self.base)


if __name__ == '__main__':
    unittest.main()
