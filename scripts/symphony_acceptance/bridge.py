"""Small app-server-compatible facade used by Symphony's codex.command."""
import json
import inspect
import os
import re
import sys
import threading
from uuid import uuid4

from symphony_codex_adapter import parse_envelope

from .controller import Controller
from .telemetry import Telemetry


_SECRET_PATTERNS = (
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{12,}\b"), "[redacted credential]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{12,}\b"), "[redacted credential]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "[redacted credential]"),
    (re.compile(r"(?i)\bBearer\s+\S+"), "Bearer [redacted credential]"),
    (re.compile(r"(?i)\b(?:api[_-]?key|token|secret)\s*[:=]\s*\S+"), "[redacted credential]"),
)
_PHASES = {"queued", "coding", "checking", "publishing", "reviewing",
           "waiting_ci", "ready", "blocked", "closed"}


def _safe_reason(value):
    text = " ".join(str(value or "").split())
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:300]


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
        self.telemetry = None
        self.finished = threading.Event()

    def send(self, value):
        with self.write_lock:
            self.output.write(json.dumps(value, ensure_ascii=True) + '\n')
            self.output.flush()

    def notify(self, method, params):
        self.send({'method': method, 'params': params})

    def progress(self, message):
        if self.telemetry is not None:
            self.telemetry.progress(message)
        self._send_progress(message)

    def _send_progress(self, message):
        self.notify('item/agentMessage/delta', {'threadId': self.thread_id,
                    'turnId': self.turn_id, 'itemId': self.turn_id + '-progress',
                    'delta': message + '\n'})

    def event_callback(self, method, params):
        """Accept internal agent events; Telemetry forwards only safe counters."""
        if self.telemetry is not None:
            self.telemetry.event_callback(method, params)

    def _make_controller(self):
        kwargs = {'progress': self.progress}
        try:
            signature = inspect.signature(self.controller_factory)
            supports_events = ('event_callback' in signature.parameters or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()))
        except (TypeError, ValueError):
            supports_events = False
        if supports_events:
            kwargs['event_callback'] = self.event_callback
        return self.controller_factory(self.config, **kwargs)

    def execute(self, issue):
        try:
            result = self._make_controller().run(issue, self.cwd)
            if not isinstance(result, dict):
                raise ValueError('invalid_controller_result')
            phase = result.get('phase')
            reason = _safe_reason(result.get('reason', ''))
            if phase == 'blocked':
                text = f"自动验收阻塞：{reason or '未提供原因'}"
                turn = {'id': self.turn_id, 'status': 'failed',
                        'error': {'message': text, 'codexErrorInfo': None, 'additionalDetails': None}}
            elif phase == 'ready':
                text = f"自动验收任务 #{issue}：ready。" + reason
                turn = {'id': self.turn_id, 'status': 'completed', 'error': None}
            elif phase == 'closed':
                text = f"自动验收任务 #{issue}：closed。" + reason
                turn = {'id': self.turn_id, 'status': 'completed', 'error': None}
            else:
                text = '自动验收失败：invalid_terminal_phase'
                turn = {'id': self.turn_id, 'status': 'failed',
                        'error': {'message': text, 'codexErrorInfo': None, 'additionalDetails': None}}
        except Exception as exc:
            # Avoid printing request payloads, environments or credential-bearing tracebacks.
            text = '自动验收未启动：' + type(exc).__name__ + ': ' + _safe_reason(exc)
            turn = {'id': self.turn_id, 'status': 'failed',
                    'error': {'message': text, 'codexErrorInfo': None, 'additionalDetails': None}}
        item = {'id': self.turn_id + '-final', 'type': 'agentMessage', 'text': text}
        self.notify('item/completed', {'threadId': self.thread_id, 'turnId': self.turn_id, 'item': item})
        turn['items'] = [item]
        self.notify('turn/completed', {'threadId': self.thread_id, 'turn': turn})
        self.finished.set()

    def heartbeat(self):
        while not self.finished.wait(15):
            message = (self.telemetry.heartbeat_message() if self.telemetry is not None
                       else '自动验收心跳：阶段 starting；等待控制器活动。')
            self._send_progress(message)

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
            self.telemetry = Telemetry(self.thread_id, self.turn_id, self.notify)
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
