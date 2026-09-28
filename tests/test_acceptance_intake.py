import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from symphony_acceptance.controller import Controller
from symphony_acceptance.core import PipelineError, StateStore
from symphony_acceptance.github import APIError
from symphony_acceptance.intake import IntakeStore, scan


class GitHub:
    def __init__(self):
        self.labels = ['symphony:model:deepseek-flash', 'symphony:effort:high', 'symphony:ready']
        self.events = [self.event(1, self.labels[0]), self.event(2, self.labels[1]), self.event(3, self.labels[2])]
        self.body = '<!-- workflow-plan\n' + json.dumps({'title': 'fix', 'acceptance': 'correct',
                    'allowedPaths': ['src/**'], 'checks': ['site']}) + '\n-->'
        self.edited = None
        self.notifications = {}
        self.writes = 0

    @staticmethod
    def event(identifier, label, actor='owner', kind='User'):
        return {'id': identifier, 'event': 'labeled', 'label': {'name': label},
                'actor': {'login': actor, 'type': kind}, 'created_at': '2099-01-01T00:00:00Z'}

    def issue(self, number):
        return {'number': number, 'body': self.body, 'state': 'open', 'labels': list(self.labels)}

    def issues_with_label(self, label):
        return [self.issue(1)] if label in self.labels else []

    def issue_events(self, number):
        return copy.deepcopy(self.events)

    def issue_last_edited_at(self, number):
        return self.edited

    def comments(self, number):
        return []

    def set_labels(self, number, add, remove):
        self.writes += 1
        self.labels = list((set(self.labels) | set(add)) - set(remove))

    def notify(self, number, body, key):
        self.notifications[key] = body


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = {'repository': 'owner/repo', 'control_root': self.temp.name,
                       'workspace_root': self.temp.name + '/workspace', 'label_intake': True,
                       'allowed_users': ['owner'], 'required_checks': ['site'],
                       'checks': {'site': {'command': ['true'], 'cwd': '.'}},
                       'protected_paths': [], 'conditional_checks': []}
        self.github = GitHub()
        self.controller = Controller(self.config, self.github)
        self.controller.validate_models = lambda *args: None
        self.store = StateStore(self.temp.name, 'owner/repo', 1)
        self.receipts = IntakeStore(self.config)
        self.receipts.save({'enabled_at': '2098-12-31T23:00:00Z', 'events': {}})

    def test_label_only_registers_consents_and_enqueues_once_across_restart(self):
        self.assertEqual(scan(self.controller)[0]['status'], 'queued')
        state = self.store.load()
        self.assertEqual(state['phase'], 'queued')
        self.assertEqual(state['provider_consent']['actor'], 'owner')
        self.assertEqual(state['provider_consent']['event_id'], 3)
        self.assertEqual(state['provider_consent']['plan_hash'], state['plan_hash'])
        self.assertEqual(state['label_authorization']['route'], {'model': 'deepseek-flash', 'effort': 'high'})
        self.assertIn('symphony:acceptance', self.github.labels)
        writes = self.github.writes
        restarted = Controller(self.config, self.github)
        restarted.validate_models = self.controller.validate_models
        self.assertEqual(scan(restarted), [])
        self.assertEqual(self.github.writes, writes)
        self.assertEqual(self.store.load()['run_id'], state['run_id'])

    def test_disabled_or_historical_labels_never_authorize(self):
        self.config['label_intake'] = False
        self.assertEqual(scan(self.controller), [])
        self.config['label_intake'] = True
        self.receipts.save({'enabled_at': '2099-01-02T00:00:00Z', 'events': {}})
        self.assertEqual(scan(self.controller), [])
        self.assertIsNone(self.store.load())
        self.assertEqual(self.github.writes, 0)

    def test_first_scan_sets_durable_activation_boundary(self):
        self.receipts.state_path.unlink()
        self.github.events[-1]['created_at'] = '2000-01-01T00:00:00Z'
        scan(self.controller)
        boundary = self.receipts.load()['enabled_at']
        scan(self.controller)
        self.assertEqual(self.receipts.load()['enabled_at'], boundary)
        self.assertIsNone(self.store.load())

    def test_unauthorized_and_bot_ready_are_rejected(self):
        for actor, kind in [('intruder', 'User'), ('owner', 'Bot')]:
            with self.subTest(actor=actor, kind=kind):
                self.setUp()
                self.github.events[-1]['actor'] = {'login': actor, 'type': kind}
                self.assertIn('ready_label_actor_not_authorized', scan(self.controller)[0]['error'])
                self.assertIsNone(self.store.load())
                self.assertNotIn('symphony:ready', self.github.labels)

    def test_model_event_after_ready_in_same_second_rejected(self):
        self.github.events.append(self.github.event(4, 'symphony:effort:high'))
        self.assertEqual(scan(self.controller)[0]['error'], 'add_ready_after_model_labels')
        self.assertIsNone(self.store.load())

    def test_plan_edit_after_or_same_second_as_ready_rejected(self):
        self.github.edited = '2099-01-01T00:00:00Z'
        self.assertEqual(scan(self.controller)[0]['error'], 'issue_edited_reapply_ready')
        self.assertIsNone(self.store.load())

    def test_model_or_plan_change_during_registration_cannot_inherit_authorization(self):
        original = self.controller.register
        def changed(number, **kwargs):
            self.github.body = self.github.body.replace('correct', 'different')
            return original(number, **kwargs)
        self.controller.register = changed
        self.assertEqual(scan(self.controller)[0]['error'], 'label_authorization_changed')
        self.assertIsNone(self.store.load())

    def test_route_change_after_registration_blocks_preflight(self):
        scan(self.controller)
        self.github.labels.remove('symphony:effort:high')
        self.github.labels.append('symphony:effort:max')
        with self.assertRaisesRegex(PipelineError, 'model_changed_reapply_ready'):
            self.controller.preflight(self.store.load(), self.github.issue(1))

    def test_crash_after_register_resumes_without_registering_twice(self):
        with patch.object(self.controller, 'enqueue', side_effect=SystemExit('crash')):
            with self.assertRaises(SystemExit):
                scan(self.controller)
        run_id = self.store.load()['run_id']
        self.assertEqual(scan(self.controller)[0]['status'], 'queued')
        self.assertEqual(self.store.load()['run_id'], run_id)

    def test_crash_after_enqueue_does_not_restart_terminal_task(self):
        original = self.controller.enqueue
        def crash(number):
            original(number)
            raise SystemExit('crash')
        with patch.object(self.controller, 'enqueue', side_effect=crash):
            with self.assertRaises(SystemExit):
                scan(self.controller)
        state = self.store.load()
        state['phase'] = 'blocked'
        self.store.save(state)
        scan(self.controller)
        self.assertEqual(self.store.load()['run_id'], state['run_id'])
        self.assertEqual(self.store.load()['phase'], 'blocked')

    def test_invalid_plan_notifies_once_and_new_event_can_retry(self):
        body = self.github.body
        self.github.body = 'missing plan'
        scan(self.controller)
        scan(self.controller)
        self.assertEqual(len(self.github.notifications), 1)
        self.github.body = body
        self.github.labels.append('symphony:ready')
        self.github.events.append(self.github.event(4, 'symphony:ready'))
        self.assertEqual(scan(self.controller)[0]['status'], 'queued')

    def test_delivery_retry_survives_ready_removal(self):
        self.github.body = 'invalid'
        original = self.github.notify
        with patch.object(self.github, 'notify', side_effect=APIError('temporary', transient=True)):
            scan(self.controller)
        self.assertNotIn('symphony:ready', self.github.labels)
        scan(self.controller)
        self.assertEqual(len(self.github.notifications), 1)
        self.assertFalse(self.receipts.load()['events']['3']['delivery_pending'])

    def test_transient_failure_is_retried_without_rejection(self):
        with patch.object(self.controller, 'register', side_effect=APIError('temporary', transient=True)):
            scan(self.controller)
        self.assertEqual(self.receipts.load()['events']['3']['status'], 'pending')
        self.assertIn('symphony:ready', self.github.labels)
        self.assertEqual(scan(self.controller)[0]['status'], 'queued')

    def test_registered_manual_task_gets_new_label_authorization(self):
        self.controller.register(1, deepseek_consent=True)
        self.assertEqual(scan(self.controller)[0]['status'], 'queued')
        self.assertEqual(self.store.load()['label_authorization']['event_id'], 3)

    def test_failed_enqueue_then_new_event_rebinds_registered_state(self):
        with patch.object(self.controller, 'enqueue', side_effect=PipelineError('ready_label_required')):
            scan(self.controller)
        self.assertEqual(self.store.load()['phase'], 'registered')
        self.github.labels.append('symphony:ready')
        self.github.events.append(self.github.event(4, 'symphony:ready'))
        self.assertEqual(scan(self.controller)[0]['status'], 'queued')
        self.assertEqual(self.store.load()['label_authorization']['event_id'], 4)

    def test_registered_task_cannot_dispatch_before_enqueue(self):
        self.controller.register(1, deepseek_consent=True)
        with self.assertRaisesRegex(PipelineError, 'task_not_enqueued'):
            self.controller.run(1, self.config['workspace_root'])

    def test_dispatch_revalidates_ready_event(self):
        scan(self.controller)
        self.github.events.append(self.github.event(4, 'symphony:ready'))
        # Stop at block() so no live delivery or coding is possible in this test.
        with patch.object(self.controller, 'block', side_effect=RuntimeError('blocked-before-coding')) as block:
            with self.assertRaisesRegex(RuntimeError, 'blocked-before-coding'):
                self.controller.run(1, self.config['workspace_root'])
        block.assert_called_once_with('label_authorization_changed')

    def test_new_same_second_event_is_not_in_activation_baseline(self):
        self.receipts.state_path.unlink()
        with patch('symphony_acceptance.intake.datetime') as clock:
            from datetime import datetime, timezone
            clock.now.return_value = datetime(2099, 1, 1, tzinfo=timezone.utc)
            clock.fromisoformat.side_effect = datetime.fromisoformat
            self.assertEqual(scan(self.controller), [])
        self.github.events.append(self.github.event(4, 'symphony:ready'))
        self.assertEqual(scan(self.controller)[0]['status'], 'queued')

    def test_closed_task_requires_new_issue_instead_of_reusing_closed_pr(self):
        scan(self.controller)
        state = self.store.load()
        state['phase'] = 'closed'
        self.store.save(state)
        self.github.events.append(self.github.event(4, 'symphony:ready'))
        self.assertEqual(scan(self.controller)[0]['error'], 'closed_task_create_new_issue')
        self.assertEqual(self.store.load()['run_id'], state['run_id'])


if __name__ == '__main__':
    unittest.main()
