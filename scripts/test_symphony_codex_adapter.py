"""Protocol simulations for the Symphony Codex stdio adapter."""
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote_plus


SCRIPT = Path(__file__).with_name('symphony_codex_adapter.py')
SPEC = importlib.util.spec_from_file_location('symphony_codex_adapter', SCRIPT)
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def envelope(*labels):
    lines = [adapter.BEGIN, 'issue=GH-42']
    lines.extend('label=' + quote_plus(label) for label in labels)
    return '\n'.join([*lines, adapter.END, '', 'Fix the blog page.'])


def decode(buffer):
    data = buffer.getvalue().splitlines()
    buffer.seek(0)
    buffer.truncate()
    return [json.loads(line) for line in data]


class FakeChild:
    def __init__(self):
        self.stdin = io.BytesIO()


class BridgeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.parent = io.BytesIO()
        self.bridge = adapter.Bridge(['fake-app-server'], parent_out=self.parent)
        self.child = FakeChild()
        self.bridge.child = self.child

    def child_messages(self):
        return decode(self.child.stdin)

    def parent_messages(self):
        return decode(self.parent)

    def initialize(self, pages=None):
        if pages is None:
            pages = [[
                {'model': 'gpt-6-astra', 'supportedReasoningEfforts': [
                    {'reasoningEffort': 'low'}, {'reasoningEffort': 'high'}]},
                {'model': 'gpt-6-luna', 'supportedReasoningEfforts': [
                    {'reasoningEffort': 'low'}, {'reasoningEffort': 'medium'}]},
            ]]
        self.assertTrue(self.bridge.handle_parent({'method': 'initialized'}))
        first = self.child_messages()
        self.assertEqual([m['method'] for m in first], ['initialized', 'model/list'])
        for index, page in enumerate(pages):
            model_request = self.bridge.catalog_id
            result = {'data': page}
            if index < len(pages) - 1:
                result['nextCursor'] = f'page-{index + 2}'
            self.assertTrue(self.bridge.handle_child({'id': model_request, 'result': result}))
            if index < len(pages) - 1:
                request = self.child_messages()
                self.assertEqual(request[0]['params']['cursor'], result['nextCursor'])

    def start_thread(self):
        self.assertTrue(self.bridge.handle_parent({
            'id': 2, 'method': 'thread/start',
            'params': {'cwd': '/tmp/issue', 'dynamicTools': [{'name': 'github_api'}],
                       'config': {'shell_environment_policy.exclude': ['EXISTING']}},
        }))
        forwarded = self.child_messages()[0]
        self.assertEqual(forwarded['method'], 'thread/start')
        config = forwarded['params']['config']
        self.assertEqual(config['shell_environment_policy.exclude'], ['EXISTING', 'DEEPSEEK_API_KEY'])
        self.assertFalse(config['shell_environment_policy.ignore_default_excludes'])
        self.assertFalse(config['features.apps'])
        self.assertEqual(forwarded['params']['dynamicTools'], [{'name': 'github_api'}])
        self.assertTrue(self.bridge.handle_child({'id': 2, 'result': {'thread': {'id': 'original'}}}))
        self.assertEqual(self.parent_messages()[0]['result']['thread']['id'], 'original')

    def turn(self, request_id, text, thread='original', model='attempted-override', effort='max'):
        return {'id': request_id, 'method': 'turn/start', 'params': {
            'threadId': thread, 'input': [{'type': 'text', 'text': text}],
            'model': model, 'effort': effort}}

    def test_thread_rejects_shell_env_reinjection(self):
        for config in (
            {'shell_environment_policy.set': {'DEEPSEEK_API_KEY': 'injected'}},
            {'shell_environment_policy.filters': ['DEEPSEEK_API_KEY']},
            {'shell_environment_policy': {'set': {'DEEPSEEK_API_KEY': 'injected'}}},
        ):
            with self.subTest(config=config):
                self.setUp()
                self.assertFalse(self.bridge.handle_parent({
                    'id': 2, 'method': 'thread/start', 'params': {'config': config}}))
                self.assertEqual(self.child_messages(), [])
                self.assertIn('unsafe_shell_environment_policy',
                              self.parent_messages()[0]['error']['message'])
    def test_default_route_pins_every_turn(self):
        self.initialize()
        self.start_thread()
        self.assertTrue(self.bridge.handle_parent(self.turn(3, envelope())))
        first = self.child_messages()[0]
        self.assertEqual((first['params']['model'], first['params']['effort']),
                         ('gpt-6-astra', 'low'))
        self.assertTrue(self.bridge.handle_parent(self.turn(4, 'Continue, using another model.')))
        second = self.child_messages()[0]
        self.assertEqual((second['params']['model'], second['params']['effort']),
                         ('gpt-6-astra', 'low'))
        self.assertEqual(second['params']['input'][0]['text'], 'Continue, using another model.')

    def test_paginated_catalog_and_label_choice(self):
        pages = [
            [{'model': 'gpt-6-astra', 'supportedReasoningEfforts': [{'reasoningEffort': 'low'}]}],
            [{'model': 'gpt-6-luna', 'supportedReasoningEfforts': [{'reasoningEffort': 'medium'}]}],
        ]
        self.initialize(pages)
        self.start_thread()
        self.assertTrue(self.bridge.handle_parent(self.turn(
            3, envelope('symphony:model:gpt-6-luna', 'symphony:effort:medium'))))
        routed = self.child_messages()[0]
        self.assertEqual((routed['params']['model'], routed['params']['effort']),
                         ('gpt-6-luna', 'medium'))

    def test_turn_waits_for_catalog_completion(self):
        self.assertTrue(self.bridge.handle_parent({'method': 'initialized'}))
        self.child_messages()
        self.start_thread()
        self.assertTrue(self.bridge.handle_parent(self.turn(3, envelope())))
        self.assertEqual(self.child_messages(), [])
        self.assertTrue(self.bridge.handle_child({'id': self.bridge.catalog_id, 'result': {
            'data': [{'model': 'gpt-6-astra', 'supportedReasoningEfforts':
                      [{'reasoningEffort': 'low'}]}]}}))
        self.assertEqual(self.child_messages()[0]['params']['model'], 'gpt-6-astra')

    def test_bad_routes_fail_closed(self):
        cases = [
            (envelope('symphony:model:gpt-6-astra', 'symphony:model:gpt-6-astra'),
             'duplicate_model_labels'),
            (envelope('symphony:effort:low', 'symphony:effort:high'),
             'duplicate_effort_labels'),
            (envelope('symphony:model:gpt-unknown'), 'unknown_or_non_gpt_model'),
            (envelope('symphony:model:deepseek-flash', 'symphony:effort:medium'),
             'unsupported_reasoning_effort'),
            (envelope('symphony:model:gpt-6-luna', 'symphony:effort:high'),
             'unsupported_reasoning_effort'),
            ('No envelope', 'missing_routing_envelope'),
            (adapter.BEGIN + '\nissue=GH-42\nlabel=%Q2\n' + adapter.END,
             'invalid_metadata_encoding'),
        ]
        for text, reason in cases:
            with self.subTest(reason=reason, text=text):
                self.setUp()
                self.initialize()
                self.start_thread()
                self.assertFalse(self.bridge.handle_parent(self.turn(3, text)))
                self.assertEqual(self.child_messages(), [])
                self.assertIn(reason, self.parent_messages()[0]['error']['message'])

    def test_deepseek_missing_key_rejects_before_provider_thread(self):
        self.initialize()
        self.start_thread()
        self.assertFalse(self.bridge.handle_parent(self.turn(
            3, envelope('symphony:model:deepseek-flash'))))
        self.assertEqual(self.child_messages(), [])
        self.assertIn('deepseek_credentials_missing', self.parent_messages()[0]['error']['message'])

    def test_proxy_factory_receives_key_and_random_dummy(self):
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / 'key'
            key_file.write_text('sk-simulated-secret', encoding='utf-8')
            calls = []
            stopped = []

            def fake_proxy(key, token):
                calls.append((key, token))
                return 'http://127.0.0.1:43210', lambda: stopped.append(True)

            bridge = adapter.Bridge(['fake-app-server'], deepseek_key_file=key_file,
                                    proxy_factory=fake_proxy)
            bridge.start_proxy()
            self.assertEqual(calls[0][0], 'sk-simulated-secret')
            self.assertEqual(calls[0][1], bridge.dummy_token)
            self.assertNotEqual(bridge.dummy_token, 'sk-simulated-secret')
            self.assertNotIn('DEEPSEEK_API_KEY', adapter.app_server_environment())
            bridge.stop_proxy()
            self.assertEqual(stopped, [True])
    def test_deepseek_provider_thread_and_thread_remap(self):
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / 'key'
            key_file.write_text('sk-simulated-secret', encoding='utf-8')
            self.bridge = adapter.Bridge(
                ['fake-app-server'], deepseek_key_file=key_file, parent_out=self.parent,
                proxy_factory=lambda key, token: ('http://127.0.0.1:43210', lambda: None))
            self.child = FakeChild()
            self.bridge.child = self.child
            self.initialize()
            self.start_thread()
            self.assertTrue(self.bridge.handle_parent(self.turn(
                3, envelope('symphony:model:deepseek-flash', 'symphony:effort:high'))))
            switch = self.child_messages()[0]
            self.assertEqual((switch['method'], switch['params']['model'],
                              switch['params']['modelProvider']),
                             ('thread/start', 'deepseek-flash', 'deepseek'))
            provider = switch['params']['config']['model_providers.deepseek']
            self.assertEqual(provider['wire_api'], 'responses')
            self.assertEqual(provider['base_url'], 'http://127.0.0.1:43210')
            self.assertNotIn('env_key', provider)
            self.assertEqual(provider['experimental_bearer_token'], self.bridge.dummy_token)
            self.assertNotEqual(self.bridge.dummy_token, 'sk-simulated-secret')
            self.assertNotIn('sk-simulated-secret', json.dumps(switch))
            self.assertTrue(self.bridge.handle_child({'id': switch['id'],
                                                       'result': {'thread': {'id': 'provider'}}}))
            first_turn = self.child_messages()[0]
            self.assertEqual(first_turn['params']['threadId'], 'provider')
            self.assertEqual((first_turn['params']['model'], first_turn['params']['effort']),
                             ('deepseek-flash', 'high'))
            self.assertTrue(self.bridge.handle_parent(self.turn(4, 'Continue.')))
            next_turn = self.child_messages()[0]
            self.assertEqual((next_turn['params']['threadId'], next_turn['params']['model'],
                              next_turn['params']['effort']), ('provider', 'deepseek-flash', 'high'))
            self.assertTrue(self.bridge.handle_child({
                'method': 'turn/completed', 'params': {
                    'threadId': 'provider', 'turn': {'id': 'turn-1', 'status': 'completed'}}}))
            self.assertEqual(self.parent_messages()[0]['params']['threadId'], 'original')

    def test_proxy_start_failure_does_not_echo_internal_error(self):
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / 'key'
            key_file.write_text('sk-simulated-secret', encoding='utf-8')

            def failing_proxy(_key, _token):
                raise ValueError('sk-internal-error-text')

            self.bridge = adapter.Bridge(
                ['fake-app-server'], deepseek_key_file=key_file, parent_out=self.parent,
                proxy_factory=failing_proxy)
            self.child = FakeChild()
            self.bridge.child = self.child
            self.initialize()
            self.start_thread()
            self.assertFalse(self.bridge.handle_parent(self.turn(
                3, envelope('symphony:model:deepseek-flash'))))
            self.assertEqual(self.child_messages(), [])
            error = self.parent_messages()[0]['error']['message']
            self.assertIn('deepseek_proxy_start_failed', error)
            self.assertNotIn('sk-internal-error-text', error)
    def test_deepseek_provider_failure_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / 'key'
            key_file.write_text('sk-simulated-secret', encoding='utf-8')
            self.bridge = adapter.Bridge(
                ['fake-app-server'], deepseek_key_file=key_file, parent_out=self.parent,
                proxy_factory=lambda key, token: ('http://127.0.0.1:43210', lambda: None))
            self.child = FakeChild()
            self.bridge.child = self.child
            self.initialize()
            self.start_thread()
            self.assertTrue(self.bridge.handle_parent(self.turn(
                3, envelope('symphony:model:deepseek-flash'))))
            switch = self.child_messages()[0]
            self.assertFalse(self.bridge.handle_child({'id': switch['id'],
                                                       'error': {'message': 'provider failed'}}))
            self.assertIn('deepseek_thread_start_failed', self.parent_messages()[0]['error']['message'])

    def test_catalog_error_rejects_queued_turn(self):
        self.assertTrue(self.bridge.handle_parent({'method': 'initialized'}))
        self.child_messages()
        self.start_thread()
        self.assertTrue(self.bridge.handle_parent(self.turn(3, envelope())))
        self.assertFalse(self.bridge.handle_child({'id': self.bridge.catalog_id,
                                                   'error': {'message': 'catalog unavailable'}}))
        self.assertIn('model_catalog_request_failed', self.parent_messages()[0]['error']['message'])


class ProbeTests(unittest.TestCase):
    def test_child_environment_never_receives_key_or_dummy(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'sk-inherited',
                                     'GITHUB_TOKEN': 'github-inherited',
                                     'GH_TOKEN': 'gh-inherited'}):
            child = adapter.app_server_environment()
            for name in ('DEEPSEEK_API_KEY', 'GITHUB_TOKEN', 'GH_TOKEN'):
                self.assertNotIn(name, child)
                self.assertIn(name, os.environ)

    def test_spawned_app_server_child_receives_no_credentials(self):
        import os

        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / 'fake_app_server.py'
            fake.write_text(
                "import json, os, sys\n"
                "names = ('DEEPSEEK_API_KEY', 'GITHUB_TOKEN', 'GH_TOKEN')\n"
                "for line in sys.stdin:\n"
                "    request = json.loads(line)\n"
                "    if request.get('method') == 'initialize':\n"
                "        present = {name: name in os.environ for name in names}\n"
                "        print(json.dumps({'id': request['id'], 'result': present}), flush=True)\n",
                encoding='utf-8',
            )
            key_file = Path(directory) / 'key'
            key_file.write_text('sk-simulated-secret', encoding='utf-8')
            environment = os.environ.copy()
            environment.update(DEEPSEEK_API_KEY='sk-inherited',
                               GITHUB_TOKEN='github-inherited',
                               GH_TOKEN='gh-inherited')
            process = subprocess.Popen(
                [sys.executable, str(SCRIPT), '--deepseek-key-file', str(key_file),
                 '--', sys.executable, '-u', str(fake)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=environment,
            )
            try:
                request = {'id': 1, 'method': 'initialize', 'params': {
                    'clientInfo': {'name': 'environment-test', 'version': '1.0.0'}}}
                process.stdin.write((json.dumps(request) + '\n').encode())
                process.stdin.flush()
                response = json.loads(process.stdout.readline())
                self.assertEqual(response['result'], {
                    'DEEPSEEK_API_KEY': False, 'GITHUB_TOKEN': False, 'GH_TOKEN': False})
            finally:
                process.stdin.close()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=8)
                process.stdout.close()
                process.stderr.close()
    def test_deepseek_probe_never_echoes_key(self):
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / 'key'
            key_file.write_text('sk-simulated-secret', encoding='utf-8')
            done = subprocess.run([sys.executable, str(SCRIPT), '--deepseek-key-file',
                                   str(key_file), '--probe-deepseek'],
                                  text=True, capture_output=True, check=False)
            self.assertEqual(done.returncode, 0)
            self.assertIn('authenticated inference unverified', done.stdout)
            self.assertNotIn('sk-simulated-secret', done.stdout + done.stderr)
            key_file.unlink()
            missing = subprocess.run([sys.executable, str(SCRIPT), '--deepseek-key-file',
                                      str(key_file), '--probe-deepseek'],
                                     text=True, capture_output=True, check=False)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn('deepseek_credentials_missing', missing.stderr)


    def test_validate_labels_uses_real_catalog_protocol_without_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / 'fake_app_server.py'
            fake.write_text(
                "import json, os, sys\n"
                "if os.environ.get('DEEPSEEK_API_KEY'): sys.exit(5)\n"
                "for line in sys.stdin:\n"
                "    request = json.loads(line)\n"
                "    method = request.get('method')\n"
                "    if method == 'initialize':\n"
                "        reply = {'id': request['id'], 'result': {}}\n"
                "    elif method == 'model/list':\n"
                "        reply = {'id': request['id'], 'result': {'data': [\n"
                "            {'model': 'gpt-6-astra', 'supportedReasoningEfforts': [\n"
                "                {'reasoningEffort': 'low'}, {'reasoningEffort': 'high'}]}]}}\n"
                "    elif method == 'turn/start':\n"
                "        sys.exit(6)\n"
                "    else:\n"
                "        continue\n"
                "    print(json.dumps(reply), flush=True)\n",
                encoding='utf-8',
            )
            environment = dict(__import__('os').environ)
            environment['DEEPSEEK_API_KEY'] = 'sk-inherited-must-not-reach-child'
            command = [sys.executable, str(SCRIPT), '--validate-labels',
                       '--label', 'symphony:effort:high', '--',
                       sys.executable, '-u', str(fake)]
            valid = subprocess.run(command, text=True, capture_output=True,
                                   env=environment, check=False, timeout=8)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertEqual(json.loads(valid.stdout)['effort'], 'high')
            self.assertEqual(json.loads(valid.stdout)['validation'], 'app_server_model_list')
            invalid = subprocess.run(command[:4] + ['symphony:effort:max'] + command[5:],
                                     text=True, capture_output=True, env=environment,
                                     check=False, timeout=8)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn('unsupported_reasoning_effort', invalid.stderr)
if __name__ == '__main__':
    unittest.main()