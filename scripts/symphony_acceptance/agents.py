"""Small, fail-closed Codex app-server client for independent agents.

Each CodexSession owns one fresh app-server process and one fresh thread. It
does not resume or fork threads, so a reviewer can never inherit a coder's
conversation history.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


_MAX_STDOUT_LINE = 16 * 1024 * 1024
_STDERR_TAIL_BYTES = 8192
_ERROR_EVIDENCE_CHARS = 2048
_EOF = object()
_LINUX_GUARDIAN = r'''\
import os, signal, subprocess, sys, time

expected_parent = os.getppid()
child = None
stopping = False

def terminate_child_group():
    if child is None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except OSError:
        return
    # Give adapters a short chance to close sockets and child processes, then
    # remove every remaining member even if the direct child has exited.
    time.sleep(0.35)
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except OSError:
        pass

def parent_died(signum, _frame):
    global stopping
    if stopping:
        return
    stopping = True
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if child is not None:
        terminate_child_group()
    if child is not None:
        try:
            child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
    os._exit(128 + signum)

signal.signal(signal.SIGTERM, parent_died)
# Block SIGTERM until Popen returns and `child` is assigned. Otherwise a
# parent exit in the fork/exec window could leave an untracked orphan.
old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
try:
    child = subprocess.Popen(
        sys.argv[2:], start_new_session=True,
        preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, old_mask),
    )
finally:
    signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)

if os.getppid() != expected_parent:
    parent_died(signal.SIGTERM, None)

while child.poll() is None:
    if os.getppid() != expected_parent:
        parent_died(signal.SIGTERM, None)
    time.sleep(0.05)
# A normally exiting app-server can leave adapter descendants behind too.
# Reap its complete session group before this guardian exits.
terminate_child_group()
sys.exit(child.returncode)
'''


def _toml_string(value: str) -> str:
    """Encode a string as a TOML basic string (JSON strings are compatible)."""
    return json.dumps(value, ensure_ascii=False)


def _toml_value(value: Any) -> str:
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return "{" + ", ".join(
            _toml_string(str(key)) + " = " + _toml_value(nested)
            for key, nested in value.items()
        ) + "}"
    raise TypeError("unsupported TOML permission profile value")


class AgentError(RuntimeError):
    """Safe, machine-readable failure from an agent process or protocol."""

    def __init__(self, reason: str, transient: bool = False,
                 stderr_evidence: str = "", diagnostic: str = "") -> None:
        self.reason = reason
        self.transient = transient
        self.stderr_evidence = stderr_evidence
        self.diagnostic = diagnostic
        super().__init__(reason)


class _StderrTail:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tail = bytearray()

    def append(self, data: bytes) -> None:
        with self._lock:
            self._tail.extend(data)
            if len(self._tail) > _STDERR_TAIL_BYTES:
                del self._tail[:-_STDERR_TAIL_BYTES]

    def text(self, secret_values: list[str]) -> str:
        with self._lock:
            value = bytes(self._tail).decode("utf-8", errors="replace")
        value = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [redacted]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{6,}\b", "[redacted-provider-token]", value)
        value = re.sub(r"\bgh[pousr]_[A-Za-z0-9_]{8,}\b", "[redacted-github-token]", value)
        for secret in sorted((item for item in secret_values if len(item) >= 4),
                             key=len, reverse=True):
            value = value.replace(secret, "[redacted]")
        value = " ".join(value.split())
        if len(value) > _ERROR_EVIDENCE_CHARS:
            value = "…" + value[-(_ERROR_EVIDENCE_CHARS - 1):]
        return value


class CodexSession:
    """Run one isolated Codex app-server thread over JSON-RPC stdio.

    A coder gets workspaceWrite; a reviewer gets readOnly. Use this as a
    context manager so even successful sessions reap the full subprocess tree.
    """

    def __init__(self, command: list[str], cwd: str | os.PathLike[str],
                 model: str, effort: str, readonly: bool = False,
                 event_callback: Callable[[str, dict[str, Any]], None] | None = None,
                 timeout_seconds: float = 1800, env: dict[str, str] | None = None,
                 denied_paths: list[str | os.PathLike[str]] | None = None) -> None:
        if not isinstance(command, list) or not command or not all(
                isinstance(part, str) and part for part in command):
            raise ValueError("command must be a non-empty list of strings")
        if not isinstance(model, str) or not model or not isinstance(effort, str) or not effort:
            raise ValueError("model and effort must be non-empty strings")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.command = list(command)
        self.cwd = Path(cwd).resolve()
        self.model = model
        self.effort = effort
        self.readonly = bool(readonly)
        self.event_callback = event_callback
        self.timeout_seconds = float(timeout_seconds)
        self.env = dict(env) if env is not None else None
        self.denied_paths = self._normalize_denied_paths(denied_paths)
        self._permission_profile = (
            "symphony_acceptance_" + uuid4().hex if self.denied_paths else None
        )
        self._profile_cli_active = False

        self.child: subprocess.Popen[bytes] | None = None
        self.thread_id: str | None = None
        self._initialized = False
        self._catalog: dict[str, set[str]] | None = None
        self._incoming: queue.Queue[bytes | object | tuple[str, str]] = queue.Queue()
        self._stderr = _StderrTail()
        self._reader_threads: list[threading.Thread] = []
        self._closed = False
        self._turn_active = False
        self._fatal_reason: str | None = None
        self._last_usage: dict[str, Any] | None = None
        self._secrets: list[str] = []

    def _normalize_denied_paths(
            self, paths: list[str | os.PathLike[str]] | None) -> list[str]:
        if paths is None:
            return []
        if isinstance(paths, (str, bytes)) or not isinstance(paths, (list, tuple)):
            raise ValueError("denied_paths must be a list of absolute paths")
        normalized: set[str] = set()
        for value in paths:
            if not isinstance(value, (str, os.PathLike)):
                raise ValueError("denied_paths must contain filesystem paths")
            path = Path(value).expanduser()
            if not path.is_absolute():
                raise ValueError("denied_paths must contain absolute paths")
            resolved = path.resolve(strict=False)
            if resolved == self.cwd or resolved in self.cwd.parents:
                raise ValueError("denied_paths cannot contain the workspace or an ancestor")
            normalized.add(os.path.normcase(str(resolved)))
        return sorted(normalized)

    def _profile_cli_overrides(self) -> list[str]:
        assert self._permission_profile is not None
        filesystem: dict[str, Any] = {
            ":root": "deny",
            ":minimal": "read",
            ":workspace_roots": {
                ".": "read" if self.readonly else "write",
                ".git": "read",
            },
        }
        filesystem.update({path: "read" for path in self._codex_runtime_paths()})
        filesystem.update({path: "deny" for path in self.denied_paths})
        profile = self._permission_profile
        # CLI config overrides are process-local and preserve CODEX_HOME for
        # app-server authentication. Encoding the filesystem table as one
        # TOML inline table preserves absolute-path keys (including dots).
        return [
            "-c", "default_permissions=" + _toml_string(profile),
            "-c", f"permissions.{profile}.extends=" + _toml_string(":workspace"),
            "-c", f"permissions.{profile}.filesystem=" + _toml_value(filesystem),
            "-c", f"permissions.{profile}.network.enabled=false",
        ]

    def _codex_runtime_paths(self) -> list[str]:
        """Allow only the Codex CLI entry point needed by its sandbox launcher."""
        search_path = (self.env or {}).get("PATH", os.environ.get("PATH", ""))
        allowed: set[str] = set()
        for index, token in enumerate(self.command[:-1]):
            basename = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
            is_launcher = basename in {"codex", "codex.exe"}
            is_codex_binary = bool(re.fullmatch(r"codex-linux-[0-9]+(?:\.[0-9]+)*", basename))
            if not (is_launcher or is_codex_binary) or self.command[index + 1] != "app-server":
                continue
            candidate = Path(token).expanduser()
            if not candidate.is_absolute():
                if "/" in token or "\\" in token:
                    candidate = self.cwd / candidate
                else:
                    located = shutil.which(token, path=search_path)
                    if not located:
                        continue
                    candidate = Path(located)
            runtime_candidates = [candidate]
            if is_launcher and candidate.is_file():
                try:
                    runtime_candidates.extend(
                        child for child in candidate.parent.iterdir()
                        if re.fullmatch(r"codex-linux-[0-9]+(?:\.[0-9]+)*", child.name.lower())
                        and child.is_file()
                    )
                except OSError:
                    pass
            for runtime_path in runtime_candidates:
                allowed.add(os.path.normcase(str(runtime_path.absolute())))
                allowed.add(os.path.normcase(str(runtime_path.resolve(strict=False))))
        return sorted(allowed)

    def _configured_command(self) -> list[str]:
        command = list(self.command)
        if not self._permission_profile:
            return command
        for index, token in enumerate(command[:-1]):
            basename = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
            if (basename in {"codex", "codex.exe"}
                    or re.fullmatch(r"codex-linux-[0-9]+(?:\.[0-9]+)*", basename)) \
                    and command[index + 1] == "app-server":
                command[index + 1:index + 1] = self._profile_cli_overrides()
                self._profile_cli_active = True
                return command
        return command

    def _spawn_command(self) -> list[str]:
        command = self._configured_command()
        # A guardian catches parent-death SIGTERM and relays it to the complete
        # process group, which includes adapter/app-server/proxy children. The
        # parent-pid poll closes the race if the parent exits before setpriv
        # installs PR_SET_PDEATHSIG. Avoid unsafe preexec_fn in threaded use.
        if sys.platform.startswith("linux"):
            setpriv = shutil.which("setpriv")
            command = [sys.executable, "-c", _LINUX_GUARDIAN, "--", *command]
            if setpriv:
                return [setpriv, "--pdeathsig", "SIGTERM", "--", *command]
            return command
        return command

    def __enter__(self) -> "CodexSession":
        self._ensure_process()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def _error(self, reason: str, transient: bool = False,
               diagnostic: str = "") -> AgentError:
        diagnostic_tail = _StderrTail()
        diagnostic_tail.append(diagnostic.encode("utf-8", errors="replace"))
        return AgentError(reason, transient, self._stderr.text(self._secrets),
                          diagnostic_tail.text(self._secrets))

    @staticmethod
    def _rpc_diagnostic(error: Any) -> str:
        if not isinstance(error, dict):
            return ""
        code = error.get("code")
        text = error.get("message")
        detail = (str(code) + ": " if code is not None else "")
        if isinstance(text, str):
            detail += text[:1024].strip()
        return detail

    def _child_environment(self) -> dict[str, str]:
        result = os.environ.copy()
        if self.env is not None:
            result.update(self.env)
        for name in list(result):
            upper = name.upper()
            if any(marker in upper for marker in ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL")):
                value = result.pop(name, None)
                if value:
                    self._secrets.append(str(value))
        return result

    def _ensure_process(self) -> None:
        if self.child is not None:
            if self.child.poll() is not None:
                raise self._error("app_server_exited", transient=True)
            return
        if self._closed:
            raise self._error("session_closed")
        child_env = self._child_environment()
        kwargs: dict[str, Any] = {
            "args": self._spawn_command(),
            "cwd": str(self.cwd),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": child_env,
            "bufsize": 0,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        try:
            self.child = subprocess.Popen(**kwargs)
        except (OSError, ValueError) as exc:
            raise self._error("app_server_start_failed") from exc
        assert self.child.stdout is not None and self.child.stderr is not None
        out_reader = threading.Thread(target=self._read_stdout, daemon=True,
                                      name="codex-app-server-stdout")
        err_reader = threading.Thread(target=self._read_stderr, daemon=True,
                                      name="codex-app-server-stderr")
        self._reader_threads = [out_reader, err_reader]
        for reader in self._reader_threads:
            reader.start()

    def _read_stdout(self) -> None:
        assert self.child is not None and self.child.stdout is not None
        try:
            while True:
                line = self.child.stdout.readline(_MAX_STDOUT_LINE + 1)
                if not line:
                    break
                if len(line) > _MAX_STDOUT_LINE:
                    self._incoming.put(("protocol", "stdout_line_too_large"))
                    return
                self._incoming.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self._incoming.put(_EOF)

    def _read_stderr(self) -> None:
        assert self.child is not None and self.child.stderr is not None
        try:
            while True:
                data = self.child.stderr.read(4096)
                if not data:
                    return
                self._stderr.append(data)
        except (OSError, ValueError):
            return

    def _send(self, payload: dict[str, Any]) -> None:
        self._ensure_process()
        assert self.child is not None and self.child.stdin is not None
        try:
            raw = (json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
            self.child.stdin.write(raw)
            self.child.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise self._error("app_server_write_failed", transient=True) from exc

    def _next_message(self, deadline: float) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            error = self._error("timeout", transient=True)
            self.close()
            raise error
        try:
            raw = self._incoming.get(timeout=remaining)
        except queue.Empty as exc:
            error = self._error("timeout", transient=True)
            self.close()
            raise error from exc
        if raw is _EOF:
            error = self._error("app_server_eof", transient=True)
            self.close()
            raise error
        if isinstance(raw, tuple):
            raise self._error(raw[1])
        assert isinstance(raw, bytes)
        try:
            message = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise self._error("invalid_protocol_json") from exc
        if not isinstance(message, dict):
            raise self._error("invalid_protocol_message")
        return message

    def _deny_server_request(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params")
        if not isinstance(method, str) or request_id is None:
            raise self._error("invalid_server_request")

        if method.endswith("requestApproval") and method != "item/permissions/requestApproval":
            self._send({"id": request_id, "result": {"decision": "decline"}})
            reason = "approval_request_denied"
        elif method == "item/permissions/requestApproval":
            self._send({"id": request_id, "result": {"permissions": []}})
            reason = "approval_request_denied"
        elif method == "mcpServer/elicitation/request":
            self._send({"id": request_id, "result": {"action": "decline", "content": None}})
            reason = "external_request_denied"
        else:
            self._send({"id": request_id, "error": {
                "code": -32000, "message": "Client declined this server request"}})
            reason = "external_tool_request_denied" if method in (
                "item/tool/call", "mcpServer/tool/call") else "server_request_denied"
        raise self._error(reason)

    def _check_external_item(self, method: str, params: dict[str, Any]) -> None:
        if method not in ("item/started", "item/completed"):
            return
        item = params.get("item")
        if not isinstance(item, dict):
            return
        kind = item.get("type")
        if kind in {"mcpToolCall", "dynamicToolCall", "collabToolCall", "webSearch", "imageView"}:
            raise self._error("external_tool_blocked")

    def _notification(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if not isinstance(method, str) or "id" in message:
            if "id" in message:
                self._deny_server_request(message)
            raise self._error("invalid_protocol_notification")
        params = message.get("params", {})
        if not isinstance(params, dict):
            raise self._error("invalid_protocol_notification")
        self._check_external_item(method, params)
        if method == "thread/tokenUsage/updated":
            value = params.get("tokenUsage", params.get("usage"))
            if isinstance(value, dict):
                self._last_usage = value
        if self.event_callback is not None:
            try:
                self.event_callback(method, params)
            except Exception as exc:
                raise self._error("event_callback_failed") from exc

    def _request(self, method: str, params: dict[str, Any], deadline: float) -> dict[str, Any]:
        request_id = "agent-" + uuid4().hex
        self._send({"id": request_id, "method": method, "params": params})
        while True:
            message = self._next_message(deadline)
            if "method" in message:
                self._notification(message)
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise self._error(f"app_server_{method.replace('/', '_')}_failed",
                                  diagnostic=self._rpc_diagnostic(message.get("error")))
            result = message.get("result")
            if not isinstance(result, dict):
                raise self._error("invalid_protocol_response")
            return result

    def _ensure_initialized(self, deadline: float) -> None:
        self._ensure_process()
        if self._initialized:
            return
        init_params: dict[str, Any] = {
            "clientInfo": {"name": "symphony_acceptance", "title": "Symphony Acceptance", "version": "1.0.0"},
        }
        if self._permission_profile:
            init_params["capabilities"] = {"experimentalApi": True}
        self._request("initialize", init_params, deadline)
        self._send({"method": "initialized", "params": {}})
        self._initialized = True

    def catalog(self) -> dict[str, set[str]]:
        """Read and cache the runtime model/effort catalog, including pages."""
        if self._catalog is not None:
            return {name: set(efforts) for name, efforts in self._catalog.items()}
        deadline = time.monotonic() + self.timeout_seconds
        self._ensure_initialized(deadline)
        result_catalog: dict[str, set[str]] = {}
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params: dict[str, Any] = {"limit": 100, "includeHidden": False}
            if cursor:
                params["cursor"] = cursor
            result = self._request("model/list", params, deadline)
            data = result.get("data")
            if not isinstance(data, list):
                raise self._error("invalid_model_catalog")
            for entry in data:
                if not isinstance(entry, dict):
                    raise self._error("invalid_model_catalog")
                name = entry.get("model")
                efforts = entry.get("supportedReasoningEfforts")
                if not isinstance(name, str) or not isinstance(efforts, list):
                    raise self._error("invalid_model_catalog")
                supported: set[str] = set()
                for item in efforts:
                    if not isinstance(item, dict) or not isinstance(item.get("reasoningEffort"), str):
                        raise self._error("invalid_model_catalog")
                    supported.add(item["reasoningEffort"])
                result_catalog[name] = supported
            next_cursor = result.get("nextCursor")
            if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor):
                raise self._error("invalid_model_catalog_cursor")
            if not next_cursor:
                self._catalog = result_catalog
                return {name: set(efforts) for name, efforts in result_catalog.items()}
            if next_cursor in seen_cursors:
                raise self._error("repeated_model_catalog_cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    def _thread_config(self) -> dict[str, Any]:
        # Use app-server config overrides, not persistent user config writes.
        # Unsupported feature switches are ignored by older runtimes; approval
        # and sandbox fields below still enforce the primary access boundary.
        config: dict[str, Any] = {
            "features.apps": False,
            "features.plugins": False,
            "features.rmcp_client": False,
            "features.skills": False,
            "features.multi_agent": False,
            "features.web_search_request": False,
            "shell_environment_policy.ignore_default_excludes": False,
            "shell_environment_policy.exclude": [
                "*KEY*", "*TOKEN*", "*SECRET*", "*PASSWORD*", "*CREDENTIAL*",
                "CODEX_HOME",
            ],
        }
        if self._permission_profile and not self._profile_cli_active:
            filesystem: dict[str, Any] = {
                # Keep common runtime/tool binaries readable while denying
                # everything else outside the current workspace. More specific
                # explicit deny rules remain effective inside broader grants.
                ":root": "deny",
                ":minimal": "read",
                ":workspace_roots": {
                    ".": "read" if self.readonly else "write",
                    ".git": "read",
                },
            }
            filesystem.update({path: "read" for path in self._codex_runtime_paths()})
            filesystem.update({path: "deny" for path in self.denied_paths})
            config.update({
                f"permissions.{self._permission_profile}.extends": ":workspace",
                f"permissions.{self._permission_profile}.filesystem": filesystem,
                f"permissions.{self._permission_profile}.network.enabled": False,
            })
        return config

    def start(self) -> str:
        """Initialize, validate routing against runtime capabilities, start fresh thread."""
        if self._fatal_reason:
            raise self._error(self._fatal_reason)
        if self.thread_id:
            return self.thread_id
        self._ensure_process()
        self.catalog()
        # DeepSeek routes are validated by the existing Symphony routing adapter
        # after it receives the first-turn routing envelope.
        if self.model != "deepseek-flash":
            supported = (self._catalog or {}).get(self.model)
            if supported is None:
                raise self._error("unsupported_model")
            if self.effort not in supported:
                raise self._error("unsupported_reasoning_effort")

        deadline = time.monotonic() + self.timeout_seconds
        # Named permission profiles are opt-in behind experimentalApi and
        # mutually exclusive with the older sandbox fields.
        thread_params: dict[str, Any] = {
            "model": self.model,
            "cwd": str(self.cwd),
            "approvalPolicy": "never",
            "config": self._thread_config(),
        }
        if self._permission_profile:
            thread_params["permissions"] = self._permission_profile
        else:
            # Codex CLI 0.154 uses a hyphenated SandboxMode on thread/start.
            thread_params["sandbox"] = "read-only" if self.readonly else "workspace-write"
        result = self._request("thread/start", thread_params, deadline)
        thread = result.get("thread")
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise self._error("invalid_thread_start_response")
        self.thread_id = thread_id
        return thread_id

    @staticmethod
    def _turn_items(turn: dict[str, Any]) -> list[dict[str, Any]]:
        items = turn.get("items")
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    def turn(self, prompt: str, schema: dict[str, Any] | None = None,
             *, images: list[str] | None = None) -> dict[str, Any]:
        """Run one turn and return only its completed final agent message."""
        if not isinstance(prompt, str):
            raise ValueError("prompt must be a string")
        if schema is not None and not isinstance(schema, dict):
            raise ValueError("schema must be a JSON schema object")
        image_inputs = []
        for image in images or []:
            path = Path(image).resolve()
            if not path.is_relative_to(self.cwd.resolve()) or not path.is_file() or path.suffix.lower() != '.png':
                raise ValueError('review image must be a PNG inside its workspace')
            image_inputs.append({'type': 'localImage', 'path': str(path)})
        if schema is not None:
            # Some provider routes do not enforce outputSchema. Keep the wire
            # contract and tell the model the final-answer contract explicitly.
            # Append it so the routing envelope remains the first input lines.
            prompt += (
                "\n\nFinal response format: return exactly one JSON object matching the following JSON Schema. "
                "Do not include Markdown, code fences, headings, or text outside that object. "
                "Put any completion summary or blocker explanation in the schema's string fields. "
                "This format requirement does not authorize additional actions or checks.\n"
                + json.dumps(schema, ensure_ascii=False)
            )
        thread_id = self.start()
        if self._turn_active:
            raise self._error("concurrent_turn_not_supported")
        self._turn_active = True
        self._last_usage = None
        deadline = time.monotonic() + self.timeout_seconds
        final_messages: list[dict[str, Any]] = []
        completed_turn: dict[str, Any] | None = None
        try:
            params: dict[str, Any] = {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}] + image_inputs,
                "cwd": str(self.cwd),
                "approvalPolicy": "never",
                "model": self.model,
                "effort": self.effort,
            }
            if self._permission_profile:
                params["permissions"] = self._permission_profile
            else:
                params["sandboxPolicy"] = ({"type": "readOnly"} if self.readonly else {
                    "type": "workspaceWrite",
                    "writableRoots": [str(self.cwd)],
                    "networkAccess": False,
                })
            if schema is not None:
                params["outputSchema"] = schema

            request_id = "agent-" + uuid4().hex
            self._send({"id": request_id, "method": "turn/start", "params": params})
            start_result: dict[str, Any] | None = None
            while start_result is None:
                message = self._next_message(deadline)
                if "method" in message:
                    method = message.get("method")
                    self._notification(message)
                    if method == "item/completed":
                        item = message.get("params", {}).get("item", {})
                        if isinstance(item, dict) and item.get("type") == "agentMessage":
                            final_messages.append(item)
                    elif method == "turn/completed":
                        turn = message.get("params", {}).get("turn", {})
                        if isinstance(turn, dict):
                            completed_turn = turn
                            final_messages.extend(
                                item for item in self._turn_items(turn)
                                if item.get("type") == "agentMessage")
                    continue
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise self._error("app_server_turn_start_failed",
                                      diagnostic=self._rpc_diagnostic(message.get("error")))
                start_result = message.get("result")
                if not isinstance(start_result, dict):
                    raise self._error("invalid_turn_start_response")

            start_turn = start_result.get("turn")
            turn_id = start_turn.get("id") if isinstance(start_turn, dict) else None
            if not isinstance(turn_id, str) or not turn_id:
                raise self._error("invalid_turn_start_response")
            if completed_turn is not None and completed_turn.get("id") != turn_id:
                raise self._error("turn_completion_id_mismatch")

            while completed_turn is None:
                message = self._next_message(deadline)
                if "method" not in message:
                    # This adapter owns one in-flight request at a time. A
                    # stray response cannot satisfy or alter the active turn.
                    continue
                method = message.get("method")
                self._notification(message)
                params = message.get("params", {})
                if method == "item/completed":
                    item = params.get("item", {})
                    if isinstance(item, dict) and item.get("type") == "agentMessage":
                        final_messages.append(item)
                elif method == "turn/completed":
                    turn = params.get("turn", {})
                    if not isinstance(turn, dict):
                        raise self._error("invalid_turn_completion")
                    completed_turn = turn
                    final_messages.extend(
                        item for item in self._turn_items(turn)
                        if item.get("type") == "agentMessage")
                    break

            if completed_turn.get("id") != turn_id:
                raise self._error("turn_completion_id_mismatch")
            if completed_turn.get("status") != "completed":
                raise self._error(
                    "turn_" + str(completed_turn.get("status", "failed")),
                    diagnostic=self._rpc_diagnostic(completed_turn.get("error")),
                )

            # item/completed is authoritative. Prefer the explicitly final
            # answer phase; never return a streamed delta or commentary item.
            final_phase = [item for item in final_messages
                           if item.get("phase") == "final_answer"]
            eligible = final_phase or [item for item in final_messages
                                       if item.get("phase") != "commentary"]
            text = next((item["text"] for item in reversed(eligible)
                         if isinstance(item.get("text"), str)), None)
            if text is None:
                raise self._error("missing_final_agent_message")
            usage = completed_turn.get("tokenUsage")
            if not isinstance(usage, dict):
                usage = self._last_usage
            result: dict[str, Any] = {
                "text": text,
                "thread_id": thread_id,
                "turn_id": turn_id,
            }
            if isinstance(usage, dict):
                result["usage"] = usage
            return result
        except AgentError as exc:
            if exc.reason in {"approval_request_denied", "external_request_denied",
                              "external_tool_request_denied", "server_request_denied"}:
                self._fatal_reason = exc.reason
            if exc.reason in {"timeout", "app_server_eof", "external_tool_blocked"}:
                self.close()
            raise
        finally:
            self._turn_active = False

    def close(self) -> None:
        """Close stdin and terminate/reap the full server process tree."""
        if self._closed:
            return
        self._closed = True
        child = self.child
        if child is None:
            return
        try:
            if child.stdin is not None:
                child.stdin.close()
        except OSError:
            pass
        if child.poll() is None:
            if os.name == "nt":
                try:
                    subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   check=False, timeout=5)
                except (OSError, subprocess.SubprocessError):
                    pass
                if child.poll() is None:
                    try:
                        child.terminate()
                    except OSError:
                        pass
            else:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        child.terminate()
                    except OSError:
                        pass
            try:
                child.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
                else:
                    try:
                        child.kill()
                    except OSError:
                        pass
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        for reader in self._reader_threads:
            reader.join(timeout=0.2)
        for stream in (child.stdout, child.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

