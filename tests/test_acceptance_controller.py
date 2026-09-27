import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from symphony_acceptance.controller import Controller, git
from symphony_acceptance.core import StateStore, fingerprint, PipelineError, _git as core_git
from symphony_acceptance.agents import AgentError
from symphony_acceptance.github import APIError, GitHub


class FakeGitHub:
    def __init__(self, base):
        self.base_sha = base
        self.head = None
        self.publish_count = 0
        self.statuses = []
        self.drafts = []
        self.comment_list = []
        self.labels = set()
        self.notifications = {}
        self.ci_state = 'success'
        self.body = '<!-- workflow-plan\n' + json.dumps({
            'title': 'Fix value', 'acceptance': 'The value is correct.',
            'allowedPaths': ['value.txt'], 'checks': ['unit']}) + '\n-->'

    def issue(self, number):
        return {'number': number, 'state': 'open', 'body': self.body, 'labels': list(self.labels)}

    def base(self, branch='main'):
        return self.base_sha

    def branch_head(self, branch):
        return self.head

    def comments(self, number):
        return self.comment_list

    def set_labels(self, number, add, remove):
        self.labels.update(add)
        self.labels.difference_update(remove)

    def status(self, sha, state, description, target_url=None):
        self.statuses.append((sha, state))

    def draft(self, number, draft):
        self.drafts.append(draft)

    def summary(self, number, body):
        self.summary_body = body

    def notify(self, number, body, key):
        self.notifications[key] = body

    def publish(self, root, base, branch, expected, title, body, issue):
        self.publish_count += 1
        self.head = f'{self.publish_count:040x}'
        return {'number': 99, 'head_sha': self.head, 'base_sha': base,
                'url': 'https://github.com/example/repo/pull/99', 'branch': branch}

    def pr(self, number):
        return {'number': number, 'state': 'open', 'head': {'sha': self.head}, 'draft': self.drafts[-1]}

    def ci(self, sha, required=None):
        return {'state': self.ci_state, 'checks': [{'name': 'Build', 'state': self.ci_state}]}


class Scenario:
    def __init__(self):
        self.codes = ['good']
        self.verdicts = ['pass']
        self.sessions = []
        self.on_review = None
        self.unavailable = False

    def factory(self, command, cwd, model, effort, readonly=False, **kwargs):
        scenario = self

        class Session:
            def __enter__(self):
                scenario.sessions.append({'cwd': str(cwd), 'readonly': readonly})
                return self

            def __exit__(self, *args):
                pass

            def catalog(self):
                return {} if scenario.unavailable else {'gpt-6-astra': {'low', 'high'}}

            def start(self):
                return 'fresh-' + str(len(scenario.sessions))

            def turn(self, prompt, schema):
                if not readonly:
                    value = scenario.codes.pop(0) if len(scenario.codes) > 1 else scenario.codes[0]
                    (Path(cwd) / 'value.txt').write_text(value, encoding='utf-8')
                    payload = {'status': 'ready', 'summary': 'implemented'}
                else:
                    request = json.loads(prompt[prompt.index('{'):])
                    verdict = scenario.verdicts.pop(0) if len(scenario.verdicts) > 1 else scenario.verdicts[0]
                    payload = dict(request['binding'], verdict=verdict, summary='reviewed',
                                   criteria=[{'criterion': criterion,
                                              'status': 'met' if verdict == 'pass' else 'unmet',
                                              'evidence': 'value.txt inspected and unit check observed'}
                                             for criterion in request['criteria']], findings=[])
                    if verdict == 'rework':
                        payload['findings'] = [{'blocking': True, 'path': 'value.txt', 'line': 1,
                                                'evidence': 'value violates requested boundary',
                                                'required_fix': 'correct the value'}]
                    if scenario.on_review:
                        scenario.on_review(payload, cwd)
                return {'text': json.dumps(payload), 'thread_id': 'fresh-' + str(len(scenario.sessions)), 'turn_id': 'turn'}
        return Session()


class TestController(Controller):
    def snapshot(self):
        path = Path(self.config['workspace_root']) / ('review-' + str(len(self.state.get('history', []))) + '-' + str(self.state['repairs']))
        if path.exists():
            path = path.with_name(path.name + '-retry')
        shutil.copytree(self.state['workspace'], path)
        self.save(review_workspace=str(path))
        return path


class ControllerTests(unittest.TestCase):
    def test_controller_alias_survives_tracker_filter_but_not_git_children(self):
        with mock.patch.dict(os.environ, {'SYMPHONY_ACCEPTANCE_GITHUB_TOKEN': 'test-only-token'}, clear=True):
            controller = Controller({'repository': 'o/r'})
            self.assertEqual(controller.github.token, 'test-only-token')
            with mock.patch('symphony_acceptance.controller.subprocess.run') as run:
                run.return_value.returncode = 0
                git('.', 'status')
                self.assertNotIn('SYMPHONY_ACCEPTANCE_GITHUB_TOKEN', run.call_args.kwargs['env'])
                core_git(Path('.'), ['status'])
                self.assertNotIn('SYMPHONY_ACCEPTANCE_GITHUB_TOKEN', run.call_args.kwargs['env'])
                GitHub._git(Path('.'), 'status')
                self.assertNotIn('SYMPHONY_ACCEPTANCE_GITHUB_TOKEN', run.call_args.kwargs['env'])
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(PipelineError, 'github_credentials_missing'):
                Controller({'repository': 'o/r'})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / 'workspaces' / 'GH-1'
        self.root.mkdir(parents=True)
        git(self.root, 'init', '-q')
        git(self.root, 'config', 'user.name', 'test')
        git(self.root, 'config', 'user.email', 'test@example.com')
        (self.root / 'value.txt').write_text('original', encoding='utf-8')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-qm', 'base')
        self.github = FakeGitHub(git(self.root, 'rev-parse', 'HEAD').stdout.strip())
        self.scenario = Scenario()
        self.config = {'repository': 'example/second-stack', 'control_root': str(self.home / 'control'),
                       'workspace_root': str(self.home / 'workspaces'), 'allowed_users': ['owner'],
                       'required_checks': ['unit'], 'conditional_checks': [], 'protected_paths': [],
                       'checks': {'unit': {'command': [sys.executable, '-c', 'pass'], 'cwd': '.'}},
                       'reviewer': {'model': 'gpt-6-astra', 'effort': 'high'},
                       'coding_command': ['fake'], 'reviewer_command': ['fake'], 'required_ci': ['Build']}
        self.check_calls = []

        def checks(root, plan, config, evidence):
            value = (Path(root) / 'value.txt').read_text(encoding='utf-8')
            self.check_calls.append((str(root), value))
            return {'status': 'failed' if value.startswith('bad') else 'passed', 'reason': 'unit_failed',
                    'fingerprint_before': fingerprint(root), 'fingerprint_after': fingerprint(root),
                    'checks': [{'name': 'unit', 'exit_code': 1 if value.startswith('bad') else 0, 'log': str(evidence)}]}

        self.controller = TestController(self.config, self.github, self.scenario.factory,
                                         check_runner=checks, preparer=lambda *args: None, sleep=lambda delay: None)
        self.controller.register(1)
        self.controller.enqueue(1)

    def run_task(self):
        return self.controller.run(1, self.root)

    def test_first_pass_independent_checkout_final_pr(self):
        state = self.run_task()
        self.assertEqual(state['phase'], 'ready')
        self.assertEqual(state['repairs'], 0)
        self.assertEqual(len(self.check_calls), 2)
        self.assertNotEqual(self.check_calls[0][0], self.check_calls[1][0])
        self.assertEqual(self.github.publish_count, 1)
        self.assertEqual(self.github.drafts[-1], False)
        self.assertNotIn('symphony:ready', self.github.labels)
        self.assertEqual(self.run_task()['phase'], 'ready')
        self.assertEqual(self.github.publish_count, 1)

    def test_review_repair_then_pass_same_pr(self):
        self.scenario.codes = ['good-but-incomplete', 'good-fixed']
        self.scenario.verdicts = ['rework', 'pass']
        state = self.run_task()
        self.assertEqual(state['phase'], 'ready')
        self.assertEqual(state['repairs'], 1)
        self.assertEqual(self.github.publish_count, 2)
        self.assertEqual(state['pr']['number'], 99)

    def test_checks_and_review_share_three_repairs(self):
        self.scenario.codes = ['bad-0', 'good-1', 'bad-2', 'good-3']
        self.scenario.verdicts = ['rework', 'rework']
        state = self.run_task()
        self.assertEqual(state['phase'], 'blocked')
        self.assertEqual(state['reason'], 'repair_limit_reached')
        self.assertEqual(state['repairs'], 3)
        self.assertTrue(self.github.drafts[-1])

    def test_unchanged_failed_submission_stops(self):
        self.scenario.codes = ['bad']
        state = self.run_task()
        self.assertEqual(state['reason'], 'unchanged_failed_submission')
        self.assertEqual(state['repairs'], 1)

    def test_old_sha_cannot_pass(self):
        self.scenario.on_review = lambda payload, root: payload.update(head_sha='f' * 40)
        state = self.run_task()
        self.assertEqual(state['phase'], 'blocked')
        self.assertNotIn((state['head_sha'], 'success'), self.github.statuses)

    def test_review_source_tamper_blocks(self):
        def tamper(payload, root):
            (Path(root) / 'value.txt').write_text('tampered', encoding='utf-8')
        self.scenario.on_review = tamper
        self.assertEqual(self.run_task()['reason'], 'reviewer_changed_source')

    def test_changed_remote_head_not_overwritten(self):
        self.scenario.on_review = lambda payload, root: setattr(self.github, 'head', 'e' * 40)
        self.assertEqual(self.run_task()['reason'], 'remote_head_changed')
        self.assertEqual(self.github.publish_count, 1)

    def test_model_unavailable_stops_before_coding(self):
        self.scenario.unavailable = True
        state = self.run_task()
        self.assertEqual(state['reason'], 'configured_model_unavailable')
        self.assertEqual((self.root / 'value.txt').read_text(), 'original')

    def test_command_dedup_and_human_budget_reset(self):
        self.run_task()
        self.github.comment_list = [{'id': 42, 'body': '/symphony rework revise requirement',
                                      'user': {'login': 'owner', 'type': 'User'}}]
        self.controller.watch_once()
        self.controller.watch_once()
        state = StateStore(self.config['control_root'], self.config['repository'], 1).load()
        self.assertEqual(state['processed_commands'], [42])
        self.assertEqual(state['repairs'], 0)
        self.assertEqual(len(state['history']), 1)
        self.assertTrue(self.github.drafts[-1])
        self.assertIn('symphony:ready', self.github.labels)

    def test_ordinary_and_foreign_comments_do_not_restart(self):
        self.run_task()
        self.github.comment_list = [
            {'id': 43, 'body': 'please fix it', 'user': {'login': 'owner', 'type': 'User'}},
            {'id': 44, 'body': '/symphony resume', 'user': {'login': 'stranger', 'type': 'User'}}]
        self.controller.watch_once()
        state = StateStore(self.config['control_root'], self.config['repository'], 1).load()
        self.assertEqual(state['phase'], 'ready')
        self.assertEqual(state['processed_commands'], [])

    def test_changed_plan_requires_operator_registration(self):
        self.github.body = self.github.body.replace('The value is correct.', 'A different requirement.')
        self.assertEqual(self.run_task()['reason'], 'plan_changed_requires_registration')

    def test_transient_retry_is_bounded(self):
        attempts = []
        def fail():
            attempts.append(1)
            raise APIError('network', transient=True)
        with self.assertRaises(APIError):
            self.controller.retry(fail)
        self.assertEqual(len(attempts), 3)

    def test_ready_delivery_crash_is_reconciled_by_watcher(self):
        original = self.github.notify
        def crash(*args):
            raise SystemExit('simulated process loss after terminal state persistence')
        self.github.notify = crash
        with self.assertRaises(SystemExit):
            self.run_task()
        store = StateStore(self.config['control_root'], self.config['repository'], 1)
        self.assertTrue(store.load()['delivery_pending'])
        self.github.notify = original
        self.controller.watch_once()
        self.assertFalse(store.load()['delivery_pending'])
        self.assertFalse(self.github.drafts[-1])
        self.assertEqual(len(self.github.notifications), 1)

    def test_blocked_delivery_crash_is_reconciled_by_watcher(self):
        self.scenario.unavailable = True
        original = self.github.summary
        self.github.summary = lambda *args: (_ for _ in ()).throw(SystemExit('crash'))
        with self.assertRaises(SystemExit):
            self.run_task()
        store = StateStore(self.config['control_root'], self.config['repository'], 1)
        self.assertEqual(store.load()['phase'], 'blocked')
        self.assertTrue(store.load()['delivery_pending'])
        self.github.summary = original
        self.controller.watch_once()
        self.assertFalse(store.load()['delivery_pending'])
        self.assertNotIn('symphony:ready', self.github.labels)

    def test_resume_ci_block_reestablishes_review_before_ci(self):
        state = self.run_task()
        store = StateStore(self.config['control_root'], self.config['repository'], 1)
        state.update(phase='blocked', resume_phase='waiting_ci', reason='ci_wait_timeout')
        store.save(state)
        self.github.comment_list = [{'id': 42, 'body': '/symphony resume',
                                      'user': {'login': 'owner', 'type': 'User'}}]
        self.controller.watch_once()
        self.assertEqual(store.load()['phase'], 'reviewing')
        self.assertIsNone(store.load()['review'])
        self.assertEqual(self.run_task()['phase'], 'ready')

    def test_suggestion_does_not_block(self):
        def suggest(payload, root):
            payload['findings'] = [{'blocking': False, 'path': 'value.txt', 'line': 1,
                                    'evidence': 'Optional naming preference', 'required_fix': ''}]
        self.scenario.on_review = suggest
        self.assertEqual(self.run_task()['phase'], 'ready')

    def test_plan_change_during_review_cannot_pass(self):
        self.scenario.on_review = lambda payload, root: setattr(
            self.github, 'body', self.github.body.replace('The value is correct.', 'Changed acceptance.'))
        state = self.run_task()
        self.assertEqual(state['reason'], 'plan_changed_requires_registration')
        self.assertNotIn((state['head_sha'], 'success'), self.github.statuses)

    def test_ready_watcher_invalidates_changed_plan(self):
        self.run_task()
        self.github.body = self.github.body.replace('The value is correct.', 'Changed acceptance.')
        self.controller.watch_once()
        self.assertEqual(self.controller.state['phase'], 'blocked')
        self.assertTrue(self.github.drafts[-1])

    def test_ready_replay_rechecks_ci_after_crash(self):
        original = self.github.notify
        self.github.notify = lambda *args: (_ for _ in ()).throw(SystemExit('crash'))
        with self.assertRaises(SystemExit):
            self.run_task()
        self.github.notify = original
        self.github.ci_state = 'pending'
        self.controller.watch_once()
        self.assertEqual(self.controller.state['phase'], 'waiting_ci')
        self.assertFalse(self.controller.state['delivery_pending'])
        self.assertTrue(self.github.drafts[-1])
        self.assertEqual(self.github.statuses[-1][1], 'pending')
        self.assertEqual(len(self.github.notifications), 0)

    def test_ready_watcher_reopens_repair_when_ci_rerun_fails(self):
        self.run_task()
        self.github.ci_state = 'failure'
        self.controller.watch_once()
        self.assertEqual(self.controller.state['phase'], 'coding')
        self.assertEqual(self.controller.state['repairs'], 1)
        self.assertTrue(self.controller.state['enqueue_pending'])
        self.assertTrue(self.github.drafts[-1])

    def test_new_head_during_ready_conversion_is_redrafted(self):
        original = self.github.draft
        def update(number, draft):
            original(number, draft)
            if not draft:
                self.github.head = 'd' * 40
        self.github.draft = update
        state = self.run_task()
        self.assertEqual(state['phase'], 'blocked')
        self.assertEqual(state['reason'], 'remote_identity_changed')
        self.assertTrue(self.github.drafts[-1])

    def test_human_restart_clears_old_terminal_outbox(self):
        state = self.run_task()
        store = StateStore(self.config['control_root'], self.config['repository'], 1)
        state.update(phase='blocked', delivery_pending=True)
        store.save(state)
        comment = {'id': 42, 'body': '/symphony rework revise value',
                   'user': {'login': 'owner', 'type': 'User'}}
        self.controller.command(store, state, comment)
        self.assertFalse(store.load()['delivery_pending'])
        self.assertEqual(store.load()['phase'], 'coding')

    def test_changed_plan_registration_invalidates_approved_candidate_without_enqueuing(self):
        self.run_task()
        self.github.body = self.github.body.replace('The value is correct.', 'New requirement.')
        state = self.controller.register(1, update_plan=True)
        self.assertEqual(state['phase'], 'registered')
        self.assertTrue(self.github.drafts[-1])
        self.assertEqual(self.github.statuses[-1][1], 'pending')
        self.assertNotIn('symphony:ready', self.github.labels)
        self.assertNotIn('review', state)

    def test_registration_crash_replays_invalidation_without_dispatch(self):
        self.run_task()
        self.github.body = self.github.body.replace('The value is correct.', 'New requirement.')
        original = self.github.draft
        self.github.draft = lambda *args: (_ for _ in ()).throw(SystemExit('crash'))
        with self.assertRaises(SystemExit):
            self.controller.register(1, update_plan=True)
        self.github.draft = original
        self.controller.watch_once()
        self.assertEqual(self.controller.state['phase'], 'registered')
        self.assertFalse(self.controller.state['invalidation_pending'])
        self.assertTrue(self.github.drafts[-1])
        self.assertEqual(self.github.statuses[-1][1], 'pending')
        self.assertNotIn('symphony:ready', self.github.labels)

    def test_human_request_is_in_review_criteria_and_fingerprint(self):
        initial = self.run_task()['review']['plan_hash']
        self.github.comment_list = [{'id': 42, 'body': '/symphony rework cover the new boundary',
                                      'user': {'login': 'owner', 'type': 'User'}}]
        self.controller.watch_once()
        state = self.run_task()
        self.assertEqual(state['phase'], 'ready')
        self.assertIn('人工修改要求：cover the new boundary', [item['criterion'] for item in state['review']['criteria']])
        self.assertNotEqual(initial, state['review']['plan_hash'])

    def test_reregistration_does_not_replay_historical_pr_commands(self):
        self.run_task()
        old_command = {'id': 42, 'body': '/symphony rework old request',
                       'user': {'login': 'owner', 'type': 'User'}}
        self.github.comments = lambda target: [old_command] if target == 99 else []
        self.github.body = self.github.body.replace('The value is correct.', 'Changed plan.')
        self.controller.register(1, update_plan=True)
        self.controller.watch_once()
        self.assertEqual(self.controller.state['phase'], 'registered')
        self.assertEqual(self.controller.state['command_floor'], 42)
        self.assertNotIn('symphony:ready', self.github.labels)


if __name__ == '__main__':
    unittest.main()
