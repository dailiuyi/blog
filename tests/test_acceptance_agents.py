"""Protocol tests for the isolated Codex app-server client."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.symphony_acceptance.agents import AgentError, CodexSession  # noqa: E402


FAKE_SERVER = r'''
import json, os, sys, time

mode = os.environ.get("FAKE_MODE", "success")
log_path = os.environ["FAKE_LOG"]

def emit(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()

def log(value):
    with open(log_path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, separators=(",", ":")) + "\n")

for line in sys.stdin:
    request = json.loads(line)
    log(request)
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        if mode == "stderr":
            sys.stderr.write("safe diagnostic " * 800 + " Bearer secret-value-123\n")
            sys.stderr.flush()
        emit({"id": request_id, "result": {"userAgent": "fake"}})
        emit({"method": "fake/env", "params": {
            "github": bool(os.environ.get("GITHUB_TOKEN")),
            "gh": bool(os.environ.get("GH_TOKEN")),
            "deepseek": bool(os.environ.get("DEEPSEEK_API_KEY")),
            "openai": bool(os.environ.get("OPENAI_API_KEY")),
        }})
    elif method == "model/list":
        if mode == "repeated_cursor":
            emit({"id": request_id, "result": {"data": [], "nextCursor": "same"}})
        elif params.get("cursor") == "page-2":
            emit({"id": request_id, "result": {"data": [
                {"model": "gpt-6-luna", "supportedReasoningEfforts": [
                    {"reasoningEffort": "medium"}]}
            ], "nextCursor": None}})
        else:
            emit({"id": request_id, "result": {"data": [
                {"model": "gpt-6-astra", "supportedReasoningEfforts": [
                    {"reasoningEffort": "low"}, {"reasoningEffort": "high"}]}
            ], "nextCursor": "page-2"}})
    elif method == "thread/start":
        emit({"id": request_id, "result": {"thread": {"id": "fresh-thread"}}})
        emit({"method": "thread/started", "params": {"thread": {"id": "fresh-thread"}}})
    elif method == "turn/start":
        if mode == "failed_turn":
            emit({"id": request_id, "error": {"code": -32001,
                                                   "message": "hidden details ghp_supersecretvalue"}})
        elif mode == "eof_turn":
            os._exit(0)
        elif mode == "timeout_turn":
            time.sleep(10)
        elif mode in ("approval", "dynamic_tool"):
            call_id = 700 if mode == "approval" else 701
            call_method = ("item/commandExecution/requestApproval" if mode == "approval"
                           else "item/tool/call")
            emit({"id": call_id, "method": call_method, "params": {"threadId": "fresh-thread"}})
        else:
            emit({"id": request_id, "result": {"turn": {
                "id": "turn-1", "status": "inProgress", "items": []}}})
            emit({"method": "turn/started", "params": {"turn": {"id": "turn-1"}}})
            emit({"method": "item/started", "params": {"item": {
                "id": "message-1", "type": "agentMessage"}}})
            emit({"method": "item/agentMessage/delta", "params": {"delta": "partial"}})
            emit({"method": "item/completed", "params": {"item": {
                "id": "commentary-1", "type": "agentMessage", "phase": "commentary",
                "text": "progress only"}}})
            final = {"id": "message-1", "type": "agentMessage", "phase": "final_answer",
                     "text": "final answer"}
            emit({"method": "item/completed", "params": {"item": final}})
            emit({"method": "turn/completed", "params": {"turn": {
                "id": "turn-1", "status": "completed", "items": [final],
                "tokenUsage": {"inputTokens": 12, "outputTokens": 4}}}})
    elif request_id in (700, 701):
        emit({"method": "fake/denial_received", "params": {"request": request}})
    elif not method:
        # Keep any other client response visible in the input log.
        emit({"method": "fake/response_received", "params": {"response": request}})
'''


class CodexSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="acceptance-agents-")
        self.temp_path = Path(self.temp.name)
        self.log_path = self.temp_path / "server.jsonl"
        self.cwd = self.temp_path

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_session(self, mode: str = "success", **kwargs: object) -> CodexSession:
        child_env = {
            "FAKE_MODE": mode,
            "FAKE_LOG": str(self.log_path),
            "GITHUB_TOKEN": "ghp_supersecretvalue",
            "GH_TOKEN": "github-secret-value",
            "DEEPSEEK_API_KEY": "sk-deepseek-secretvalue",
            "OPENAI_API_KEY": "sk-openai-secretvalue",
        }
        return CodexSession(
            [sys.executable, "-u", "-c", FAKE_SERVER], self.cwd,
            kwargs.pop("model", "gpt-6-astra"), kwargs.pop("effort", "low"),
            env=child_env, **kwargs,
        )

    def read_log(self) -> list[dict[str, object]]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]

    def wait_for_log(self, predicate, timeout: float = 1.0) -> list[dict[str, object]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            entries = self.read_log()
            if predicate(entries):
                return entries
            time.sleep(0.01)
        return self.read_log()

    def test_handshake_pagination_fresh_thread_turn_and_callback(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        with self.make_session(event_callback=lambda method, params: events.append((method, params))) as session:
            catalog = session.catalog()
            self.assertEqual(catalog["gpt-6-astra"], {"low", "high"})
            self.assertEqual(catalog["gpt-6-luna"], {"medium"})
            self.assertEqual(session.start(), "fresh-thread")
            self.assertEqual(session.start(), "fresh-thread")
            result = session.turn("review this", schema)

        self.assertEqual(result, {
            "text": "final answer", "thread_id": "fresh-thread", "turn_id": "turn-1",
            "usage": {"inputTokens": 12, "outputTokens": 4},
        })
        methods = [entry["method"] for entry in self.read_log() if "method" in entry]
        self.assertEqual(methods[:5], ["initialize", "initialized", "model/list", "model/list", "thread/start"])
        self.assertEqual(methods.count("initialize"), 1)
        self.assertEqual(methods.count("thread/start"), 1)
        thread = next(entry for entry in self.read_log() if entry.get("method") == "thread/start")
        self.assertEqual(thread["params"]["approvalPolicy"], "never")
        self.assertEqual(thread["params"]["sandbox"], "workspace-write")
        self.assertFalse(thread["params"]["config"]["features.apps"])
        turn = next(entry for entry in self.read_log() if entry.get("method") == "turn/start")
        self.assertEqual(turn["params"]["model"], "gpt-6-astra")
        self.assertEqual(turn["params"]["effort"], "low")
        self.assertEqual(turn["params"]["approvalPolicy"], "never")
        self.assertEqual(turn["params"]["outputSchema"], schema)
        self.assertEqual(turn["params"]["sandboxPolicy"]["type"], "workspaceWrite")
        self.assertEqual(turn["params"]["sandboxPolicy"]["writableRoots"], [str(self.cwd.resolve())])
        env_event = next(params for method, params in events if method == "fake/env")
        self.assertEqual(env_event, {"github": False, "gh": False, "deepseek": False, "openai": False})
        self.assertIn("item/agentMessage/delta", [method for method, _ in events])
        self.assertIn("turn/completed", [method for method, _ in events])

    def test_readonly_reviewer_uses_readonly_policy_and_never_inherits_coder_thread(self) -> None:
        with self.make_session(readonly=True) as session:
            self.assertEqual(session.start(), "fresh-thread")
            session.turn("review only")
        log = self.read_log()
        thread = next(entry for entry in log if entry.get("method") == "thread/start")
        turn = next(entry for entry in log if entry.get("method") == "turn/start")
        self.assertEqual(thread["params"]["sandbox"], "read-only")
        self.assertEqual(turn["params"]["sandboxPolicy"], {"type": "readOnly"})
        self.assertEqual(turn["params"]["threadId"], "fresh-thread")
        self.assertNotIn("thread/resume", [entry.get("method") for entry in log])
        self.assertNotIn("thread/fork", [entry.get("method") for entry in log])

    def test_denied_paths_use_named_permission_profile(self) -> None:
        denied = self.temp_path.parent / "acceptance-secret.with.dots"
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        with self.make_session(readonly=True, denied_paths=[denied]) as session:
            session.start()
            session.turn("read only", schema)

        log = self.read_log()
        initialize = next(entry for entry in log if entry.get("method") == "initialize")
        self.assertEqual(initialize["params"]["capabilities"], {"experimentalApi": True})
        thread = next(entry for entry in log if entry.get("method") == "thread/start")
        turn = next(entry for entry in log if entry.get("method") == "turn/start")
        profile = thread["params"]["permissions"]
        self.assertTrue(profile.startswith("symphony_acceptance_"))
        self.assertEqual(turn["params"]["permissions"], profile)
        self.assertNotIn("sandbox", thread["params"])
        self.assertNotIn("sandboxPolicy", turn["params"])
        self.assertEqual(turn["params"]["outputSchema"], schema)
        config = thread["params"]["config"]
        filesystem = config[f"permissions.{profile}.filesystem"]
        self.assertEqual(config[f"permissions.{profile}.extends"], ":workspace")
        self.assertEqual(config[f"permissions.{profile}.network.enabled"], False)
        self.assertEqual(filesystem[":root"], "deny")
        self.assertEqual(filesystem[":minimal"], "read")
        self.assertEqual(filesystem[":workspace_roots"], {".": "read", ".git": "read"})
        self.assertEqual(filesystem[os.path.normcase(str(denied.resolve()))], "deny")

    def test_coder_profile_keeps_workspace_writable_and_git_readonly(self) -> None:
        denied = self.temp_path.parent / "acceptance-secret"
        with self.make_session(denied_paths=[denied]) as session:
            session.start()
            session.turn("write within workspace")

        log = self.read_log()
        thread = next(entry for entry in log if entry.get("method") == "thread/start")
        turn = next(entry for entry in log if entry.get("method") == "turn/start")
        profile = thread["params"]["permissions"]
        self.assertNotIn("sandbox", thread["params"])
        self.assertNotIn("sandboxPolicy", turn["params"])
        self.assertEqual(turn["params"]["permissions"], profile)
        filesystem = thread["params"]["config"][f"permissions.{profile}.filesystem"]
        self.assertEqual(filesystem[":workspace_roots"], {".": "write", ".git": "read"})

    def test_codex_command_gets_process_scoped_profile_table(self) -> None:
        denied = self.temp_path.parent / "external secret.with spaces"
        adapter_command = [sys.executable, "adapter.py", "--", "/opt/codex app/codex", "app-server"]
        session = CodexSession(
            adapter_command, self.cwd, "gpt-6-astra", "high", readonly=True,
            denied_paths=[denied],
        )
        configured = session._configured_command()
        codex_index = configured.index("/opt/codex app/codex")
        self.assertEqual(configured[codex_index + 1], "-c")
        overrides = configured[codex_index + 1:configured.index("app-server", codex_index)]
        self.assertIn("default_permissions=" + json.dumps(session._permission_profile), overrides)
        filesystem_override = next(value for value in overrides
                                   if value.startswith(f"permissions.{session._permission_profile}.filesystem="))
        self.assertIn(json.dumps(os.path.normcase(str(denied.resolve()))), filesystem_override)
        self.assertIn(json.dumps(".git") + " = " + json.dumps("read"), filesystem_override)
        self.assertIn(json.dumps(str(Path("/opt/codex app/codex").resolve())), filesystem_override)
        self.assertTrue(session._profile_cli_active)
        self.assertFalse(any(key.startswith("permissions.") for key in session._thread_config()))

    def test_denied_paths_reject_workspace_or_ancestor(self) -> None:
        for denied in (self.cwd, self.cwd.parent):
            with self.assertRaises(ValueError):
                self.make_session(denied_paths=[denied])

    def test_unsupported_model_and_effort_fail_without_fallback(self) -> None:
        for kwargs, expected in (({"model": "gpt-unknown"}, "unsupported_model"),
                                 ({"effort": "max"}, "unsupported_reasoning_effort")):
            self.log_path.unlink(missing_ok=True)
            with self.make_session(**kwargs) as session:
                with self.assertRaises(AgentError) as caught:
                    session.start()
            self.assertEqual(caught.exception.reason, expected)
            self.assertNotIn("thread/start", [entry.get("method") for entry in self.read_log()])

    def test_repeated_catalog_cursor_fails_closed(self) -> None:
        with self.make_session("repeated_cursor") as session:
            with self.assertRaises(AgentError) as caught:
                session.catalog()
        self.assertEqual(caught.exception.reason, "repeated_model_catalog_cursor")

    def test_rpc_failure_and_eof_are_typed(self) -> None:
        with self.make_session("failed_turn") as session:
            with self.assertRaises(AgentError) as caught:
                session.turn("fail")
        self.assertEqual(caught.exception.reason, "app_server_turn_start_failed")
        self.assertIn("hidden details", caught.exception.diagnostic)
        self.assertNotIn("ghp_supersecretvalue", caught.exception.diagnostic)

        with self.make_session("eof_turn") as session:
            with self.assertRaises(AgentError) as caught:
                session.turn("exit")
        self.assertEqual(caught.exception.reason, "app_server_eof")
        self.assertTrue(caught.exception.transient)

    def test_approval_and_dynamic_tool_requests_are_refused(self) -> None:
        with self.make_session("approval") as session:
            with self.assertRaises(AgentError) as caught:
                session.turn("approval")
            self.assertEqual(caught.exception.reason, "approval_request_denied")
            entries = self.wait_for_log(lambda values: any(not item.get("method") for item in values))
            response = next(item for item in entries if item.get("id") == 700 and not item.get("method"))
            self.assertEqual(response["result"], {"decision": "decline"})

        self.log_path.unlink(missing_ok=True)
        with self.make_session("dynamic_tool") as session:
            with self.assertRaises(AgentError) as caught:
                session.turn("dynamic")
            self.assertEqual(caught.exception.reason, "external_tool_request_denied")
            entries = self.wait_for_log(lambda values: any(item.get("id") == 701 and "error" in item for item in values))
            response = next(item for item in entries if item.get("id") == 701)
            self.assertEqual(response["error"]["code"], -32000)

    def test_timeout_terminates_process_and_stderr_evidence_is_bounded_and_redacted(self) -> None:
        with self.make_session("timeout_turn", timeout_seconds=5) as session:
            session.start()
            # Keep catalog and thread startup out of this test's short deadline;
            # exercise cleanup for an in-flight turn specifically.
            session.timeout_seconds = 0.15
            with self.assertRaises(AgentError) as caught:
                session.turn("hang")
            self.assertTrue(caught.exception.transient)
            self.assertEqual(caught.exception.reason, "timeout")
            self.assertIsNotNone(session.child.poll())

        self.log_path.unlink(missing_ok=True)
        with self.make_session("stderr", model="gpt-unknown") as session:
            with self.assertRaises(AgentError) as caught:
                session.start()
        self.assertLessEqual(len(caught.exception.stderr_evidence), 2048)
        self.assertNotIn("secret-value-123", caught.exception.stderr_evidence)
        self.assertIn("[redacted]", caught.exception.stderr_evidence)

    @unittest.skipUnless(sys.platform.startswith("linux"),
                         "Linux parent-death guard is unavailable")
    def test_linux_parent_exit_terminates_app_server_process_group(self) -> None:
        pid_path = self.temp_path / "server.pid"
        grandchild_pid_path = self.temp_path / "grandchild.pid"
        server_marker = self.temp_path / "server.terminated"
        grandchild_marker = self.temp_path / "grandchild.terminated"
        grandchild = (
            "import os,signal,time; "
            "signal.signal(signal.SIGTERM, lambda *_: (open(os.environ['GRANDCHILD_MARKER'],'w').write('term'), os._exit(0))); "
            "open(os.environ['GRANDCHILD_PID'],'w').write(str(os.getpid())); "
            "time.sleep(60)"
        )
        server = (
            "import os,signal,subprocess,sys,time; "
            "signal.signal(signal.SIGTERM, lambda *_: (open(os.environ['SERVER_MARKER'],'w').write('term'), os._exit(0))); "
            "subprocess.Popen([sys.executable,'-c'," + repr(grandchild) + "]); "
            "open(os.environ['SERVER_PID'],'w').write(str(os.getpid())); "
            "time.sleep(60)"
        )
        helper = f'''\
import os, sys, time
sys.path.insert(0, {str(REPO)!r})
from scripts.symphony_acceptance.agents import CodexSession
session = CodexSession(
    [sys.executable, "-c", {server!r}], {str(self.cwd)!r},
    "gpt-6-astra", "low", env={{
        "SERVER_PID": {str(pid_path)!r}, "SERVER_MARKER": {str(server_marker)!r},
        "GRANDCHILD_PID": {str(grandchild_pid_path)!r},
        "GRANDCHILD_MARKER": {str(grandchild_marker)!r},
    }},
)
session._ensure_process()
deadline = time.time() + 3
while not (os.path.exists({str(pid_path)!r}) and os.path.exists({str(grandchild_pid_path)!r})) and time.time() < deadline:
    time.sleep(0.01)
os._exit(0)
'''
        parent = subprocess.Popen(
            [sys.executable, "-c", helper], cwd=REPO,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        parent.wait(timeout=5)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not (server_marker.exists() and grandchild_marker.exists()):
            time.sleep(0.02)
        self.assertTrue(pid_path.exists(), "fake app-server did not start")
        self.assertTrue(grandchild_pid_path.exists(), "fake child process did not start")
        self.assertTrue(server_marker.exists(), "parent death did not deliver SIGTERM to the app-server")
        self.assertTrue(grandchild_marker.exists(), "parent death did not terminate a same-group descendant")
        self.assertEqual(server_marker.read_text(encoding="utf-8"), "term")
        self.assertEqual(grandchild_marker.read_text(encoding="utf-8"), "term")

    @unittest.skipUnless(sys.platform.startswith("linux"),
                         "Linux process-group guardian is unavailable")
    def test_linux_normal_server_exit_terminates_orphaned_descendant(self) -> None:
        grandchild_pid_path = self.temp_path / "normal-exit-grandchild.pid"
        grandchild_marker = self.temp_path / "normal-exit-grandchild.terminated"
        started_marker = self.temp_path / "normal-exit-server.started"
        grandchild = (
            "import os,signal,time; "
            "signal.signal(signal.SIGTERM, lambda *_: (open(os.environ['GRANDCHILD_MARKER'],'w').write('term'), os._exit(0))); "
            "open(os.environ['GRANDCHILD_PID'],'w').write(str(os.getpid())); "
            "time.sleep(60)"
        )
        server = (
            "import os,subprocess,sys,time\n"
            "subprocess.Popen([sys.executable,'-c'," + repr(grandchild) + "])\n"
            "deadline=time.time()+2\n"
            "while not os.path.exists(os.environ['GRANDCHILD_PID']) and time.time()<deadline:\n"
            "    time.sleep(0.01)\n"
            "open(os.environ['SERVER_MARKER'],'w').write('started')\n"
        )
        helper = f'''\
import os, sys, time
sys.path.insert(0, {str(REPO)!r})
from scripts.symphony_acceptance.agents import CodexSession
session = CodexSession(
    [sys.executable, "-c", {server!r}], {str(self.cwd)!r},
    "gpt-6-astra", "low", env={{
        "GRANDCHILD_PID": {str(grandchild_pid_path)!r},
        "GRANDCHILD_MARKER": {str(grandchild_marker)!r},
        "SERVER_MARKER": {str(started_marker)!r},
    }},
)
session._ensure_process()
deadline = time.time() + 3
while not os.path.exists({str(started_marker)!r}) and time.time() < deadline:
    time.sleep(0.01)
while session.child.poll() is None and time.time() < deadline:
    time.sleep(0.01)
session.close()
'''
        parent = subprocess.Popen(
            [sys.executable, "-c", helper], cwd=REPO,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        parent.wait(timeout=5)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not grandchild_marker.exists():
            time.sleep(0.02)
        self.assertTrue(started_marker.exists(), "fake app-server did not start")
        self.assertTrue(grandchild_pid_path.exists(), "fake child process did not start")
        self.assertTrue(grandchild_marker.exists(),
                        "normal app-server exit left a same-group descendant running")
        self.assertEqual(grandchild_marker.read_text(encoding="utf-8"), "term")


if __name__ == "__main__":
    unittest.main()
