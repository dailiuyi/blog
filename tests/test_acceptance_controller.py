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
from symphony_acceptance.controller import Controller, git, parse_coder_result
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

    def publish(self, root, base, branch, expected, title, body, issue, candidate_sha=None, on_candidate=None):
        self.publish_count += 1
        candidate_sha = candidate_sha or f'{self.publish_count:040x}'
        if on_candidate:
            on_candidate(candidate_sha)
        self.head = candidate_sha
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
        self.coder_status = 'ready'
        self.coder_summary = 'implemented'
        self.prompts = []

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
                scenario.prompts.append((readonly, prompt))
                if not readonly:
                    if scenario.coder_status == 'ready':
                        value = scenario.codes.pop(0) if len(scenario.codes) > 1 else scenario.codes[0]
                        (Path(cwd) / 'value.txt').write_text(value, encoding='utf-8')
                    payload = {'status': scenario.coder_status, 'summary': scenario.coder_summary}
                else:
                    request, _ = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])
                    verdict = scenario.verdicts.pop(0) if len(scenario.verdicts) > 1 else scenario.verdicts[0]
                    payload = dict(request['binding'], verdict=verdict, summary='reviewed',
                                   criteria=[{'criterion': criterion,
                                              'status': 'met' if verdict == 'pass' else 'unmet',
                                              'evidence': 'value.txt inspected and unit check observed'}
                                             for criterion in request['criteria']], findings=[])
                    if verdict in {'rework', 'recapture'}:
                        payload['findings'] = [{'blocking': True, 'path': 'value.txt', 'line': 1,
                                                'evidence': 'value violates requested boundary',
                                                'required_fix': 'correct the value'}]
                    if scenario.on_review:
                        scenario.on_review(payload, cwd)
                return {'text': json.dumps(payload), 'thread_id': 'fresh-' + str(len(scenario.sessions)), 'turn_id': 'turn'}
        return Session()


class CoderResultParsingTests(unittest.TestCase):
    def test_coder_result_requires_exact_json_contract(self):
        cases = [
            ('non-dict result', None, None, 'missing_text'),
            ('missing text', {}, None, 'missing_text'),
            ('non-string text', {'text': 42}, None, 'missing_text'),
            ('raw Markdown prose', {'text': '# Done\n\nImplemented successfully.'}, None, 'not_json'),
            ('fenced JSON', {'text': '```json\n{"status":"ready","summary":"done"}\n```'}, None, 'not_json'),
            ('array', {'text': '[]'}, None, 'schema_mismatch'),
            ('null', {'text': 'null'}, None, 'schema_mismatch'),
            ('extra key', {'text': '{"status":"ready","summary":"done","extra":true}'}, None,
             'schema_mismatch'),
            ('missing summary', {'text': '{"status":"ready"}'}, None, 'schema_mismatch'),
            ('non-string summary', {'text': '{"status":"ready","summary":1}'}, None, 'schema_mismatch'),
            ('empty summary', {'text': '{"status":"ready","summary":""}'}, None, 'schema_mismatch'),
            ('whitespace summary', {'text': '{"status":"blocked","summary":"  \\n\\t"}'}, None,
             'schema_mismatch'),
            ('invalid status', {'text': '{"status":"complete","summary":"done"}'}, None,
             'schema_mismatch'),
            ('valid ready', {'text': '{"status":"ready","summary":"Implemented the approved change."}'},
             {'status': 'ready', 'summary': 'Implemented the approved change.'}, None),
            ('valid blocked', {'text': '{"status":"blocked","summary":"Need one missing detail."}'},
             {'status': 'blocked', 'summary': 'Need one missing detail.'}, None),
        ]
        for name, result, expected, error_suffix in cases:
            with self.subTest(name=name):
                if error_suffix:
                    with self.assertRaises(PipelineError) as caught:
                        parse_coder_result(result)
                    self.assertEqual(str(caught.exception), f'invalid_coder_result:{error_suffix}')
                else:
                    self.assertEqual(parse_coder_result(result), expected)


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

    def test_merged_pr_refreshes_the_single_reply_and_recovers_after_write_failure(self):
        self.run_task()
        self.github.pr = lambda number: {'number': number, 'state': 'closed', 'merged': True,
                                         'head': {'sha': self.github.head}, 'draft': False}
        original = self.github.summary
        self.github.summary = lambda *args: (_ for _ in ()).throw(SystemExit('crash while refreshing summary'))
        with self.assertRaises(SystemExit):
            self.controller.watch_once()
        self.assertEqual(self.controller.state['phase'], 'closed')
        self.assertTrue(self.controller.state['summary_pending'])
        self.github.summary = original
        self.controller.watch_once()
        self.assertFalse(self.controller.state['summary_pending'])
        self.assertTrue(self.github.summary_body.startswith('**已合并。**'))
        self.assertEqual(len(self.github.notifications), 0)

    def test_legitimate_coder_block_stops_before_checks(self):
        self.scenario.coder_status = 'blocked'
        self.scenario.coder_summary = 'Need one missing acceptance detail.'
        state = self.run_task()
        self.assertEqual(state['phase'], 'blocked')
        self.assertEqual(state['reason'], 'coder_blocked')
        self.assertEqual(state['agent_blocker'], 'Need one missing acceptance detail.')
        self.assertEqual(self.check_calls, [])
        self.assertEqual(self.github.publish_count, 0)

    def test_coder_and_reviewer_forward_live_and_final_usage_with_stable_keys(self):
        events = []
        self.controller.event_callback = lambda method, params: events.append((method, params))
        factory = self.scenario.factory
        usage = {'total': {'inputTokens': 10, 'outputTokens': 2, 'totalTokens': 12}}
        original_params = {'threadId': 'provider-thread', 'tokenUsage': usage}
        def instrumented(*args, **kwargs):
            session = factory(*args, **kwargs)
            turn = session.turn
            def observed(prompt, schema):
                kwargs['event_callback']('thread/tokenUsage/updated', original_params)
                result = turn(prompt, schema)
                result.update(thread_id='provider-remapped-thread', usage=usage)
                return result
            session.turn = observed
            return session
        self.controller.agent_factory = instrumented
        self.assertEqual(self.run_task()['phase'], 'ready')
        self.assertEqual(len(events), 4)
        self.assertEqual([event[1]['role'] for event in events], ['coder', 'coder', 'reviewer', 'reviewer'])
        keys = [event[1]['sessionKey'] for event in events]
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(keys[2], keys[3])
        self.assertNotEqual(keys[0], keys[2])
        self.assertNotIn('sessionKey', original_params)
        self.assertNotIn('role', original_params)

    def test_review_repair_then_pass_same_pr(self):
        self.scenario.codes = ['good-but-incomplete', 'good-fixed']
        self.scenario.verdicts = ['rework', 'pass']
        state = self.run_task()
        self.assertEqual(state['phase'], 'ready')
        self.assertEqual(state['repairs'], 1)
        self.assertEqual(self.github.publish_count, 2)
        self.assertEqual(state['pr']['number'], 99)
        coder_prompts = [prompt for readonly, prompt in self.scenario.prompts if not readonly]
        self.assertIn('correct the value', coder_prompts[1])
        self.assertIn('value violates requested boundary', coder_prompts[1])
        self.assertIn('若源码已满足要求且仅等待控制器补截图或执行检查，必须返回ready', coder_prompts[1])

    def test_evidence_retry_preserves_candidate_and_does_not_run_coder(self):
        self.scenario.verdicts = ['recapture', 'pass']
        with mock.patch('symphony_acceptance.browser.needs_browser', return_value=True), \
                mock.patch.object(self.controller, 'browser_review', return_value={'status': 'passed', 'screenshots': []}):
            state = self.run_task()
        self.assertEqual(state['phase'], 'ready')
        self.assertEqual(state['repairs'], 1)
        self.assertEqual(self.github.publish_count, 1)
        self.assertEqual(len([p for readonly, p in self.scenario.prompts if not readonly]), 1)
        reviewer_prompts = [p for readonly, p in self.scenario.prompts if readonly]
        self.assertIn('correct the value', reviewer_prompts[1])

    def test_evidence_and_code_repairs_share_budget(self):
        self.scenario.codes = ['good-first', 'good-fixed']
        self.scenario.verdicts = ['rework', 'recapture', 'recapture', 'recapture']
        with mock.patch('symphony_acceptance.browser.needs_browser', return_value=True), \
                mock.patch.object(self.controller, 'browser_review', return_value={'status': 'passed', 'screenshots': []}):
            state = self.run_task()
        self.assertEqual(state['reason'], 'repair_limit_reached')
        self.assertEqual(state['repairs'], 3)
        self.assertEqual(self.github.publish_count, 2)
        self.assertEqual(state['feedback']['verdict'], 'recapture')

    def test_real_blocker_is_saved_without_automatic_coding(self):
        self.scenario.verdicts = ['blocked']
        state = self.run_task()
        self.assertEqual(state['reason'], 'review_blocked')
        self.assertEqual(state['repairs'], 0)
        self.assertEqual(state['feedback'], state['review'])

    def test_reregister_same_plan_preserves_reviewer_feedback(self):
        self.scenario.verdicts = ['blocked']
        blocked = self.run_task()
        expected = blocked['review']
        registered = self.controller.register(1, update_plan=True)
        self.assertEqual(registered['feedback'], expected)
        self.assertEqual(registered['head_sha'], blocked['head_sha'])

    def test_recapture_without_browser_stays_blocked(self):
        self.scenario.verdicts = ['recapture']
        self.assertEqual(self.run_task()['reason'], 'browser_recapture_unavailable')

    def test_latest_ci_feedback_survives_reregister_and_resume(self):
        state = self.run_task()
        feedback = {'reason': 'GitHub CI failed', 'ci': {'state': 'failure', 'checks': ['Build']}}
        state.update(phase='blocked', repairs=3, resume_phase='reviewing', feedback=feedback,
                     reason='repair_limit_reached')
        store = StateStore(self.config['control_root'], self.config['repository'], 1)
        store.save(state)
        self.assertEqual(self.controller.register(1, update_plan=True)['feedback'], feedback)
        store.save(state)
        comment = {'id': 42, 'body': '/symphony resume', 'user': {'login': 'owner', 'type': 'User'}}
        self.assertTrue(self.controller.command(store, state, comment))
        self.assertEqual(store.load()['feedback'], feedback)

    def test_browser_plan_receives_feedback_and_observed_themes_are_required(self):
        self.run_task()
        self.controller.state['feedback'] = {'required_fix': 'Capture footer in Night'}
        self.config['browser'] = {'required_screenshot_themes': ['summer', 'night']}
        plan = {'pages': []}
        session = mock.Mock()
        session.turn.return_value = {'text': json.dumps(plan)}
        report = {'status': 'passed', 'pages': [{'path': '/about/', 'width': w, 'checks': []}
                                              for w in (1440, 390, 320)],
                  'screenshots': [{'path': '/about/', 'width': w, 'step_index': 1,
                                   'label': 'claims Night but actual Summer',
                                   'theme': {'html_data_environment': 'summer'}} for w in (1440, 390, 320)]}
        with mock.patch('symphony_acceptance.browser.validate_browser_plan', side_effect=[PipelineError('invalid_browser_attribute'), plan]), \
                mock.patch('symphony_acceptance.browser.run_browser', return_value=report), \
                mock.patch('symphony_acceptance.evidence.publish_browser_evidence', side_effect=lambda api, state, value: value):
            result = self.controller.browser_review(self.root, session, lambda *args: None)
        self.assertIn('Capture footer in Night', session.turn.call_args.args[0])
        self.assertIn('invalid_browser_attribute', session.turn.call_args.args[0])
        self.assertEqual(session.turn.call_count, 2)
        self.assertEqual(self.controller.state['repairs'], 1)
        self.assertEqual(result['status'], 'failed')
        for page in result['pages']:
            self.assertEqual([item['passed'] for item in page['checks']], [True, False])

    def test_invalid_browser_plan_retries_are_bounded_and_preserved(self):
        self.run_task()
        session = mock.Mock()
        session.turn.return_value = {'text': '{}'}
        with self.assertRaisesRegex(PipelineError, 'repair_limit_reached'):
            self.controller.browser_review(self.root, session, lambda *args: None)
        self.assertEqual(session.turn.call_count, 4)
        self.assertEqual(self.controller.state['repairs'], 3)
        saved = list(self.controller.store.home.rglob('*browser-plan-*.json'))
        self.assertEqual(len(saved), 4)
        self.assertEqual(self.controller.state['browser_plan_error'], 'invalid_browser_plan')

    def test_browser_plan_error_survives_interruption(self):
        self.run_task()
        session = mock.Mock()
        session.turn.side_effect = [{'text': '{}'}, SystemExit('interrupted')]
        with self.assertRaises(SystemExit):
            self.controller.browser_review(self.root, session, lambda *args: None)
        self.controller.state = self.controller.store.load()
        self.assertEqual(self.controller.state['repairs'], 1)
        session = mock.Mock()
        session.turn.side_effect = SystemExit('inspect restored prompt')
        with self.assertRaises(SystemExit):
            self.controller.browser_review(self.root, session, lambda *args: None)
        prompt = session.turn.call_args.args[0]
        self.assertIn('"previous_plan_error": "invalid_browser_plan"', prompt)
        self.assertEqual(self.controller.state['repairs'], 1)

    def test_candidate_sha_is_durable_before_ref_write_and_survives_restart(self):
        publish = self.github.publish
        store = StateStore(self.config['control_root'], self.config['repository'], 1)
        def interrupted(*args, **kwargs):
            callback = kwargs['on_candidate']
            def persist(sha):
                self.assertIsNone(self.github.head)
                callback(sha)
                self.assertEqual(store.load()['publication_intent']['candidate_sha'], sha)
            kwargs['on_candidate'] = persist
            publish(*args, **kwargs)
            raise SystemExit('lost publish response')
        self.github.publish = interrupted
        with self.assertRaises(SystemExit):
            self.run_task()
        published = self.github.head
        self.assertEqual(store.load()['publication_intent']['candidate_sha'], published)
        self.github.publish = publish
        recovered = self.run_task()
        self.assertEqual(recovered['phase'], 'ready')
        self.assertEqual(recovered['head_sha'], published)
        self.assertIsNone(recovered['publication_intent'])

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

    def test_resume_reconciles_only_matching_pending_publication(self):
        self.run_task()
        store = StateStore(self.config['control_root'], self.config['repository'], 1)
        state = store.load()
        previous = state['head_sha']
        source = fingerprint(self.root)
        state.update(phase='blocked', resume_phase='publishing',
                     publication_intent={'expected_head': previous, 'fingerprint': source, 'candidate_sha': 'd' * 40})
        store.save(state)
        self.github.head = 'd' * 40
        matched = []
        self.github.matches_publication = lambda *args: matched.append(args) or True
        self.github.comment_list = [{'id': 42, 'body': '/symphony resume',
                                    'user': {'login': 'owner', 'type': 'User'}}]
        self.controller.watch_once()
        resumed = store.load()
        self.assertEqual(resumed['phase'], 'publishing')
        self.assertEqual(resumed['head_sha'], 'd' * 40)
        self.assertEqual(resumed['pr']['head_sha'], 'd' * 40)
        self.assertEqual(resumed['publication_intent']['expected_head'], previous)
        self.assertEqual(matched[0][-2:], (previous, 'd' * 40))
        self.assertEqual(resumed['processed_commands'], [42])
        self.assertEqual(self.github.publish_count, 1)
        self.assertEqual(self.github.statuses[-1], ('d' * 40, 'pending'))

    def test_resume_rejects_foreign_or_changed_pending_candidate(self):
        self.run_task()
        store = StateStore(self.config['control_root'], self.config['repository'], 1)
        state = store.load()
        state.update(phase='blocked', resume_phase='publishing',
                     publication_intent={'expected_head': state['head_sha'], 'fingerprint': fingerprint(self.root),
                                         'candidate_sha': 'd' * 40})
        store.save(state)
        self.github.head = 'd' * 40
        self.github.matches_publication = lambda *args: False
        self.github.comment_list = [{'id': 42, 'body': '/symphony resume',
                                    'user': {'login': 'owner', 'type': 'User'}}]
        self.controller.watch_once()
        self.assertEqual(store.load()['phase'], 'blocked')
        self.assertEqual(store.load()['processed_commands'], [])
        (self.root / 'value.txt').write_text('changed after checks')
        self.github.matches_publication = lambda *args: self.fail('changed source must not reach reconciliation')
        self.controller.watch_once()
        self.assertEqual(store.load()['phase'], 'blocked')
        self.assertEqual(store.load()['processed_commands'], [])

    def test_resume_without_exact_candidate_journal_does_not_adopt_remote_head(self):
        self.run_task()
        store = StateStore(self.config['control_root'], self.config['repository'], 1)
        state = store.load()
        state.update(phase='blocked', resume_phase='publishing',
                     publication_intent={'expected_head': state['head_sha'], 'fingerprint': fingerprint(self.root)})
        store.save(state)
        self.github.head = 'd' * 40
        self.github.matches_publication = lambda *args: self.fail('missing SHA must fail closed')
        self.github.comment_list = [{'id': 42, 'body': '/symphony resume',
                                    'user': {'login': 'owner', 'type': 'User'}}]
        self.controller.watch_once()
        self.assertEqual(store.load()['phase'], 'blocked')
        self.assertEqual(store.load()['processed_commands'], [])

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
        original = self.github.summary
        def crash(*args):
            raise SystemExit('simulated process loss after terminal state persistence')
        self.github.summary = crash
        with self.assertRaises(SystemExit):
            self.run_task()
        store = StateStore(self.config['control_root'], self.config['repository'], 1)
        self.assertTrue(store.load()['delivery_pending'])
        self.github.summary = original
        self.controller.watch_once()
        self.assertFalse(store.load()['delivery_pending'])
        self.assertFalse(self.github.drafts[-1])
        self.assertEqual(len(self.github.notifications), 0)
        self.assertIn("自动审查通过", self.github.summary_body)

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
        original = self.github.summary
        self.github.summary = lambda *args: (_ for _ in ()).throw(SystemExit('crash'))
        with self.assertRaises(SystemExit):
            self.run_task()
        self.github.summary = original
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
