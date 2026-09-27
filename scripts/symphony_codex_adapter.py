#!/usr/bin/env python3
"""Symphony label routing for a Codex app-server stdio connection.

Only the workflow-owned envelope at the beginning of the first turn selects
the route. Later turns remain pinned to that choice.
"""
import argparse
import copy
import json
import os
import queue
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import unquote_plus, urlsplit
from uuid import uuid4

BEGIN = '[SYMPHONY_ROUTING_V1]'
END = '[/SYMPHONY_ROUTING_V1]'
DEFAULT_MODEL = 'gpt-6-astra'
DEFAULT_EFFORT = 'low'
DEEPSEEK_MODEL = 'deepseek-flash'
DEEPSEEK_EFFORTS = frozenset({'low', 'high', 'max'})


class RoutingError(ValueError):
    """A route or protocol precondition failed without a safe fallback."""


def parse_envelope(params):
    inputs = params.get('input') if isinstance(params, dict) else None
    if not isinstance(inputs, list) or not inputs or not isinstance(inputs[0], dict):
        raise RoutingError('missing_routing_envelope')
    if inputs[0].get('type') != 'text' or not isinstance(inputs[0].get('text'), str):
        raise RoutingError('missing_routing_envelope')
    lines = inputs[0]['text'].splitlines()
    if not lines or lines[0] != BEGIN:
        raise RoutingError('missing_routing_envelope')
    issue = None
    labels = []
    for line in lines[1:]:
        if line == END:
            if issue is None:
                raise RoutingError('missing_issue_identifier')
            return issue, labels
        key, sep, encoded = line.partition('=')
        if not sep or key not in ('issue', 'label'):
            raise RoutingError('invalid_routing_envelope')
        if re.search(r'%(?![0-9a-fA-F]{2})', encoded):
            raise RoutingError('invalid_metadata_encoding')
        try:
            value = unquote_plus(encoded, errors='strict')
        except UnicodeError as exc:
            raise RoutingError('invalid_metadata_encoding') from exc
        if key == 'issue':
            if issue is not None or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value):
                raise RoutingError('invalid_issue_identifier')
            issue = value
        else:
            labels.append(value)
    raise RoutingError('unterminated_routing_envelope')


def add_catalog_page(catalog, result):
    if not isinstance(result, dict) or not isinstance(result.get('data'), list):
        raise RoutingError('invalid_model_catalog')
    for entry in result['data']:
        if not isinstance(entry, dict):
            raise RoutingError('invalid_model_catalog')
        model = entry.get('model')
        efforts = entry.get('supportedReasoningEfforts')
        if not isinstance(model, str) or not isinstance(efforts, list):
            raise RoutingError('invalid_model_catalog')
        supported = set()
        for item in efforts:
            if not isinstance(item, dict) or not isinstance(item.get('reasoningEffort'), str):
                raise RoutingError('invalid_model_catalog')
            supported.add(item['reasoningEffort'])
        catalog[model] = supported
    cursor = result.get('nextCursor')
    if cursor is not None and (not isinstance(cursor, str) or not cursor):
        raise RoutingError('invalid_model_catalog_cursor')
    return cursor


def select_route(labels, catalog):
    route = {}
    for key, default in (('model', DEFAULT_MODEL), ('effort', DEFAULT_EFFORT)):
        prefix = f'symphony:{key}:'
        matches = [label[len(prefix):] for label in labels if label.startswith(prefix)]
        if len(matches) > 1:
            raise RoutingError(f'duplicate_{key}_labels')
        route[key] = matches[0] if matches else default
    model = route['model']
    if model == DEEPSEEK_MODEL:
        supported = DEEPSEEK_EFFORTS
    elif re.fullmatch(r'gpt-[A-Za-z0-9._-]+', model) and model in catalog:
        supported = catalog[model]
    else:
        raise RoutingError('unknown_or_non_gpt_model')
    if route['effort'] not in supported:
        raise RoutingError('unsupported_reasoning_effort')
    return route


def deepseek_config(base_url, dummy_token):
    return {
        'model_providers.deepseek': {
            'name': 'DeepSeek', 'base_url': base_url,
            'experimental_bearer_token': dummy_token, 'wire_api': 'responses',
            'requires_openai_auth': False,
        },
        'model_context_window': 1048576,
        'model_auto_compact_token_limit': 900000,
        'model_reasoning_summary': 'none',
    }


def read_deepseek_key(path):
    if path is None or not path.is_file():
        raise RoutingError('deepseek_credentials_missing')
    try:
        key = path.read_text(encoding='utf-8-sig').strip()
    except OSError as exc:
        raise RoutingError('deepseek_credentials_unreadable') from exc
    if not re.fullmatch(r'sk-[A-Za-z0-9_-]+', key):
        raise RoutingError('invalid_deepseek_key_file')
    return key


def app_server_environment():
    child_env = os.environ.copy()
    for name in ('DEEPSEEK_API_KEY', 'GITHUB_TOKEN', 'GH_TOKEN'):
        child_env.pop(name, None)
    return child_env

def safe_thread_params(params):
    routed = copy.deepcopy(params)
    config = routed.get('config') or {}
    if not isinstance(config, dict):
        raise RoutingError('invalid_thread_config')
    if any(key in ('shell_environment_policy.set', 'shell_environment_policy.filters')
           or key.startswith(('shell_environment_policy.set.', 'shell_environment_policy.filters.'))
           for key in config):
        raise RoutingError('unsafe_shell_environment_policy')
    nested_policy = config.get('shell_environment_policy')
    if isinstance(nested_policy, dict) and ('set' in nested_policy or 'filters' in nested_policy):
        raise RoutingError('unsafe_shell_environment_policy')
    config['features.apps'] = False
    config['shell_environment_policy.ignore_default_excludes'] = False
    excluded = config.get('shell_environment_policy.exclude') or []
    if not isinstance(excluded, list) or any(not isinstance(item, str) for item in excluded):
        raise RoutingError('invalid_thread_config')
    # Codex matches exclude entries as WildMatch patterns; regex anchors would be literal.
    config['shell_environment_policy.exclude'] = list(dict.fromkeys([*excluded, 'DEEPSEEK_API_KEY']))
    routed['config'] = config
    return routed


def remap_thread(message, source, target):
    if not source or not target or source == target:
        return message
    routed = copy.deepcopy(message)
    for obj in (routed.get('params'), routed.get('result')):
        if not isinstance(obj, dict):
            continue
        if obj.get('threadId') == source:
            obj['threadId'] = target
        thread = obj.get('thread')
        if isinstance(thread, dict) and thread.get('id') == source:
            thread['id'] = target
    return routed


def send(stream, message):
    stream.write((json.dumps(message, ensure_ascii=True, separators=(',', ':')) + '\n').encode('utf-8'))
    stream.flush()


class Bridge:
    def __init__(self, command, catalog_timeout=30.0, deepseek_key_file=None, parent_out=None, proxy_factory=None):
        self.command = command
        self.catalog_timeout = catalog_timeout
        self.deepseek_key_file = deepseek_key_file
        self.parent_out = parent_out or sys.stdout.buffer
        self.proxy_factory = proxy_factory
        self.proxy_base_url = None
        self.proxy_stop = None
        self.events = queue.SimpleQueue()
        self.child = None
        self.catalog = {}
        self.catalog_id = None
        self.catalog_deadline = None
        self.catalog_error = None
        self.catalog_ready = False
        self.cursors = set()
        self.pending_turn = None
        self.start_params = None
        self.thread_id = None
        self.actual_thread_id = None
        self.route = None
        self.issue = None
        self.switch_id = None
        self.switch_deadline = None
        self.switch_turn = None
        self.key_error = None
        self.key = None
        try:
            self.key = read_deepseek_key(deepseek_key_file)
        except RoutingError as exc:
            self.key_error = str(exc)
        self.dummy_token = secrets.token_urlsafe(32) if self.key else None

    def start_proxy(self):
        if self.proxy_stop is not None:
            return
        try:
            if self.proxy_factory is None:
                from symphony_deepseek_proxy import start_proxy
                factory = start_proxy
            else:
                factory = self.proxy_factory
            base_url, stop = factory(self.key, self.dummy_token)
            parsed = urlsplit(base_url)
            if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
                    or parsed.port is None or parsed.path not in ('', '/')
                    or parsed.query or parsed.fragment or not callable(stop)):
                if callable(stop):
                    stop()
                raise ValueError('invalid_proxy_endpoint')
            self.proxy_base_url = base_url.rstrip('/')
            self.proxy_stop = stop
        except Exception as exc:
            # The external error may contain request data; never serialize it.
            raise RoutingError('deepseek_proxy_start_failed') from exc

    def stop_proxy(self):
        stop, self.proxy_stop = self.proxy_stop, None
        if stop is not None:
            stop()

    def log(self, event, **fields):
        # Never log prompt content, full parameters, paths, or credentials.
        sys.stderr.write(json.dumps({'event': event, 'issue': self.issue, **fields}, ensure_ascii=True) + '\n')
        sys.stderr.flush()

    def reject(self, message, reason):
        if 'id' in message:
            send(self.parent_out, {'id': message['id'], 'error': {
                'code': -32602, 'message': 'Symphony model routing: ' + reason}})
        self.log('routing_rejected', reason=reason)
        return False

    def request_catalog(self, cursor=None):
        self.catalog_id = 'symphony-routing-' + uuid4().hex
        if self.catalog_deadline is None:
            self.catalog_deadline = time.monotonic() + self.catalog_timeout
        params = {'limit': 100, 'includeHidden': False}
        if cursor is not None:
            params['cursor'] = cursor
        send(self.child.stdin, {'id': self.catalog_id, 'method': 'model/list', 'params': params})

    def forward_turn(self, message):
        try:
            params = message.get('params')
            if not isinstance(params, dict) or not isinstance(params.get('threadId'), str):
                raise RoutingError('invalid_turn_params')
            if self.start_params is None:
                raise RoutingError('thread_start_required')
            if self.route is None:
                self.issue, labels = parse_envelope(params)
                self.route = select_route(labels, self.catalog)
                self.thread_id = params['threadId']
                if self.route['model'] == DEEPSEEK_MODEL:
                    if self.key_error:
                        raise RoutingError(self.key_error)
                    self.start_proxy()
                    routed = copy.deepcopy(self.start_params)
                    routed.update(model=DEEPSEEK_MODEL, modelProvider='deepseek')
                    routed['config'].update(deepseek_config(self.proxy_base_url, self.dummy_token))
                    self.switch_id = 'symphony-provider-' + uuid4().hex
                    self.switch_deadline = time.monotonic() + self.catalog_timeout
                    self.switch_turn = message
                    send(self.child.stdin, {'id': self.switch_id, 'method': 'thread/start', 'params': routed})
                    return True
            elif params['threadId'] != self.thread_id:
                raise RoutingError('one_thread_per_adapter_required')
            routed = copy.deepcopy(message)
            routed['params']['model'] = self.route['model']
            routed['params']['effort'] = self.route['effort']
            send(self.child.stdin, remap_thread(routed, self.thread_id, self.actual_thread_id))
            self.log('turn_routed', model=self.route['model'], effort=self.route['effort'])
            return True
        except RoutingError as exc:
            return self.reject(message, str(exc))

    def handle_parent(self, message):
        method = message.get('method')
        if method == 'thread/start':
            if self.start_params is not None:
                return self.reject(message, 'one_thread_per_adapter_required')
            params = message.get('params')
            if not isinstance(params, dict):
                return self.reject(message, 'invalid_thread_params')
            try:
                self.start_params = safe_thread_params(params)
            except RoutingError as exc:
                return self.reject(message, str(exc))
            send(self.child.stdin, {**message, 'params': copy.deepcopy(self.start_params)})
            return True
        if method == 'turn/start':
            if self.switch_id or self.pending_turn is not None:
                return self.reject(message, 'concurrent_turn_start')
            if self.catalog_error:
                return self.reject(message, self.catalog_error)
            if self.catalog_ready:
                return self.forward_turn(message)
            if self.catalog_deadline is None:
                return self.reject(message, 'initialize_handshake_required')
            self.pending_turn = message
            return True
        send(self.child.stdin, remap_thread(message, self.thread_id, self.actual_thread_id))
        if method == 'initialized' and self.catalog_deadline is None:
            self.request_catalog()
        return True

    def handle_child(self, message):
        if self.catalog_id is not None and message.get('id') == self.catalog_id:
            try:
                if 'error' in message:
                    raise RoutingError('model_catalog_request_failed')
                cursor = add_catalog_page(self.catalog, message.get('result'))
                if cursor:
                    if cursor in self.cursors:
                        raise RoutingError('repeated_model_catalog_cursor')
                    self.cursors.add(cursor)
                    self.request_catalog(cursor)
                    return True
                self.catalog_id = None
                self.catalog_ready = True
            except RoutingError as exc:
                self.catalog_id = None
                self.catalog_error = str(exc)
            if self.pending_turn is not None:
                pending, self.pending_turn = self.pending_turn, None
                return self.handle_parent(pending)
            return True
        if self.switch_id and message.get('method') == 'thread/started':
            return True
        if self.switch_id and message.get('id') == self.switch_id and 'method' not in message:
            pending, self.switch_turn = self.switch_turn, None
            self.switch_id = None
            actual = (message.get('result') or {}).get('thread', {}).get('id')
            if 'error' in message or not isinstance(actual, str) or not actual:
                return self.reject(pending, 'deepseek_thread_start_failed')
            self.actual_thread_id = actual
            self.log('provider_selected', provider='deepseek')
            return self.forward_turn(pending)
        if (self.actual_thread_id and message.get('method') == 'thread/started'
                and (message.get('params') or {}).get('thread', {}).get('id') == self.actual_thread_id):
            return True
        send(self.parent_out, remap_thread(message, self.actual_thread_id, self.thread_id))
        return True

    def read_lines(self, stream, source):
        pending = b''
        try:
            while True:
                chunk = os.read(stream.fileno(), 65536)
                if not chunk:
                    if pending:
                        self.events.put((source, pending))
                    break
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    self.events.put((source, line))
        except OSError:
            pass
        finally:
            self.events.put((source, None))

    def stop_child(self):
        if self.child is None:
            return
        try:
            self.child.stdin.close()
        except OSError:
            pass
        if self.child.poll() is None:
            self.child.terminate()
        try:
            self.child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait(timeout=3)

    def run(self):
        child_env = app_server_environment()
        self.child = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=sys.stderr, env=child_env)
        for stream, source in ((sys.stdin.buffer, 'parent'), (self.child.stdout, 'child')):
            threading.Thread(target=self.read_lines, args=(stream, source), daemon=True).start()
        try:
            while True:
                now = time.monotonic()
                if self.catalog_id and now >= self.catalog_deadline:
                    if self.pending_turn is not None:
                        self.reject(self.pending_turn, 'model_catalog_timeout')
                    raise RoutingError('model_catalog_timeout')
                if self.switch_id and now >= self.switch_deadline:
                    self.reject(self.switch_turn, 'deepseek_thread_start_timeout')
                    raise RoutingError('deepseek_thread_start_timeout')
                try:
                    source, line = self.events.get(timeout=0.1)
                except queue.Empty:
                    continue
                if line is None:
                    if source == 'child':
                        raise RoutingError('app_server_exited')
                    return 0
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError()
                except (ValueError, UnicodeError) as exc:
                    raise RoutingError('invalid_protocol_json') from exc
                if not (self.handle_parent(message) if source == 'parent' else self.handle_child(message)):
                    return 1
        finally:
            self.stop_child()
            try:
                self.stop_proxy()
            except Exception:
                self.log('deepseek_proxy_stop_failed')


def query_catalog(command, timeout):
    """Read the app-server model catalog without starting a thread or turn."""
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=sys.stderr, env=app_server_environment())
    messages = queue.SimpleQueue()

    def read_responses():
        for line in iter(child.stdout.readline, b''):
            messages.put(line)
        messages.put(None)

    threading.Thread(target=read_responses, daemon=True).start()
    deadline = time.monotonic() + timeout

    def response(request_id):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RoutingError('model_catalog_timeout')
            try:
                line = messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise RoutingError('model_catalog_timeout') from exc
            if line is None:
                raise RoutingError('app_server_exited')
            try:
                message = json.loads(line)
            except (ValueError, UnicodeError) as exc:
                raise RoutingError('invalid_protocol_json') from exc
            if not isinstance(message, dict):
                raise RoutingError('invalid_protocol_json')
            if message.get('id') == request_id:
                return message

    try:
        send(child.stdin, {'id': 'symphony-probe-initialize', 'method': 'initialize', 'params': {'clientInfo': {'name': 'symphony-route-check', 'version': '1.0.0'}}})
        initialized = response('symphony-probe-initialize')
        if 'error' in initialized:
            raise RoutingError('app_server_initialize_failed')
        send(child.stdin, {'method': 'initialized'})
        catalog = {}
        cursors = set()
        cursor = None
        while True:
            request_id = 'symphony-probe-' + uuid4().hex
            params = {'limit': 100, 'includeHidden': False}
            if cursor:
                params['cursor'] = cursor
            send(child.stdin, {'id': request_id, 'method': 'model/list', 'params': params})
            page = response(request_id)
            if 'error' in page:
                raise RoutingError('model_catalog_request_failed')
            cursor = add_catalog_page(catalog, page.get('result'))
            if not cursor:
                return catalog
            if cursor in cursors:
                raise RoutingError('repeated_model_catalog_cursor')
            cursors.add(cursor)
    finally:
        try:
            child.stdin.close()
        except OSError:
            pass
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog-timeout', type=float, default=30.0)
    parser.add_argument('--deepseek-key-file', type=Path,
                        help='Read key outside workspace and pass it only to the local DeepSeek proxy')
    parser.add_argument('--probe-deepseek', action='store_true',
                        help='Check key-file readiness only; does not exercise authenticated inference')
    parser.add_argument('--validate-labels', action='store_true',
                        help='Check labels against the real model/list catalog without starting a turn')
    parser.add_argument('--label', action='append', default=[],
                        help='Issue label to validate; repeat for each model/effort label')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.catalog_timeout <= 0:
        parser.error('--catalog-timeout must be positive')
    if args.probe_deepseek and args.validate_labels:
        parser.error('choose one probe mode')
    command = args.command
    if command and command[0] == '--':
        command = command[1:]
    if not command:
        command = ['codex', 'app-server']
    if args.probe_deepseek:
        try:
            read_deepseek_key(args.deepseek_key_file)
        except RoutingError as exc:
            print('DeepSeek provider unavailable: ' + str(exc), file=sys.stderr)
            return 1
        print('DeepSeek provider configuration ready; authenticated inference unverified.')
        return 0
    if args.validate_labels:
        try:
            route = select_route(args.label, query_catalog(command, args.catalog_timeout))
            if route['model'] == DEEPSEEK_MODEL:
                read_deepseek_key(args.deepseek_key_file)
            print(json.dumps({**route, 'validation': (
                'provider_configuration_only' if route['model'] == DEEPSEEK_MODEL
                else 'app_server_model_list')}, ensure_ascii=True))
            return 0
        except (OSError, RoutingError, subprocess.SubprocessError) as exc:
            reason = str(exc) if isinstance(exc, RoutingError) else type(exc).__name__
            print('Symphony model routing: ' + reason, file=sys.stderr)
            return 1
    bridge = Bridge(command, args.catalog_timeout, args.deepseek_key_file)
    try:
        return bridge.run()
    except KeyboardInterrupt:
        return 130
    except (OSError, RoutingError, subprocess.SubprocessError) as exc:
        bridge.log('adapter_failed', reason=str(exc) if isinstance(exc, RoutingError) else type(exc).__name__)
        return 1

if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda _signal, _frame: sys.exit(130))
    sys.exit(main())