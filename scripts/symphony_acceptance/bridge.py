"""Small app-server-compatible facade used by Symphony's codex.command."""
import json
import os
import re
import sys
import threading
from uuid import uuid4

from symphony_codex_adapter import parse_envelope

from .controller import Controller


class Bridge:
    def __init__(self, config, source=None, output=None, controller_factory=Controller):
        self.config = config
        self.source = source or sys.stdin
        self.output = output or sys.stdout
        self.controller_factory = controller_factory
        self.write_lock = threading.Lock()
        self.thread_id = 'acceptance-' + uuid4().hex
        self.cwd = None
        self.worker = None
        self.turn_id = None
        self.finished = threading.Event()

    def send(self, value):
        with self.write_lock:
            self.output.write(json.dumps(value, ensure_ascii=True) + '\n')
            self.output.flush()

    def notify(self, method, params):
        self.send({'method': method, 'params': params})

    def progress(self, message):
        self.notify('item/agentMessage/delta', {'threadId': self.thread_id,
                    'turnId': self.turn_id, 'itemId': self.turn_id + '-progress',
                    'delta': message + '\n'})

    def execute(self, issue):
        try:
            result = self.controller_factory(self.config, progress=self.progress).run(issue, self.cwd)
            text = f"自动验收任务 #{issue}：{result['phase']}。" + str(result.get('reason', ''))
            turn = {'id': self.turn_id, 'status': 'completed', 'error': None}
        except Exception as exc:
            # Avoid printing request payloads, environments or credential-bearing tracebacks.
            text = '自动验收未启动：' + type(exc).__name__ + ': ' + str(exc)[:300]
            turn = {'id': self.turn_id, 'status': 'failed',
                    'error': {'message': text, 'codexErrorInfo': None, 'additionalDetails': None}}
        item = {'id': self.turn_id + '-final', 'type': 'agentMessage', 'text': text}
        self.notify('item/completed', {'threadId': self.thread_id, 'turnId': self.turn_id, 'item': item})
        turn['items'] = [item]
        self.notify('turn/completed', {'threadId': self.thread_id, 'turn': turn})
        self.finished.set()

    def heartbeat(self):
        while not self.finished.wait(15):
            self.progress('控制器正在执行检查或独立验收。')

    def handle(self, message):
        method, request_id = message.get('method'), message.get('id')
        params = message.get('params') or {}
        if request_id is None:
            return
        if method == 'initialize':
            result = {'userAgent': 'symphony-acceptance/1.0', 'platformFamily': 'unix',
                      'platformOs': sys.platform, 'capabilities': {}}
        elif method in {'thread/start', 'thread/resume'}:
            self.cwd = params.get('cwd')
            if not self.cwd:
                return self.send({'id': request_id, 'error': {'code': -32602, 'message': 'cwd_required'}})
            result = {'thread': {'id': self.thread_id, 'turns': []}}
        elif method == 'turn/start':
            if self.worker:
                return self.send({'id': request_id, 'error': {'code': -32600, 'message': 'one_dispatch_per_process'}})
            try:
                identifier, _labels = parse_envelope(params)
                match = re.fullmatch(r'(?:GH-)?([1-9][0-9]*)', identifier)
                if not match or not self.cwd:
                    raise ValueError('invalid_issue_identifier')
            except ValueError as exc:
                return self.send({'id': request_id, 'error': {'code': -32602, 'message': str(exc)}})
            self.turn_id = 'acceptance-turn-' + uuid4().hex
            result = {'turn': {'id': self.turn_id, 'status': 'inProgress', 'items': [], 'error': None}}
            self.send({'id': request_id, 'result': result})
            self.notify('turn/started', {'threadId': self.thread_id, 'turn': result['turn']})
            self.worker = threading.Thread(target=self.execute, args=(int(match[1]),), daemon=True)
            self.worker.start()
            threading.Thread(target=self.heartbeat, daemon=True).start()
            return
        else:
            return self.send({'id': request_id, 'error': {'code': -32601, 'message': 'unsupported_controller_method'}})
        self.send({'id': request_id, 'result': result})

    def run(self):
        for line in self.source:
            if len(line) > 4 * 1024 * 1024:
                return 1
            try:
                self.handle(json.loads(line))
            except (ValueError, TypeError):
                self.send({'id': None, 'error': {'code': -32700, 'message': 'invalid_json_rpc'}})
        if self.worker:
            if self.source is sys.stdin and not self.finished.is_set():
                # The supervising runner disappeared. Persisted phase will be
                # resumed on dispatch; Linux child guards reap agent/check trees.
                os._exit(1)
            self.worker.join()
        return 0
