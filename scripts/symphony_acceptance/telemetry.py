"""Privacy-preserving app-server telemetry for the Symphony acceptance bridge.

Internal agent events may contain prompts, source text, tool requests, and
credentials. This module only reads allow-listed lifecycle metadata and token
counts, then emits notifications using the bridge's outer thread and turn IDs.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable


_COUNT_NAMES = {
    "inputTokens": ("inputTokens", "input_tokens"),
    "outputTokens": ("outputTokens", "output_tokens"),
    "totalTokens": ("totalTokens", "total_tokens"),
}
_PHASES = {
    "queued", "coding", "checking", "publishing", "reviewing",
    "waiting_ci", "ready", "blocked", "closed",
}
_KNOWN_EVENTS = {
    "thread/started", "turn/started", "turn/completed",
    "item/started", "item/completed", "item/agentMessage/delta",
    "thread/tokenUsage/updated",
}
_ROLE_NAMES = {"coder", "reviewer"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _pick(mapping: Any, names: tuple[str, ...]) -> int | None:
    if not isinstance(mapping, dict):
        return None
    for name in names:
        value = _nonnegative_int(mapping.get(name))
        if value is not None:
            return value
    return None


def _counts(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    counts = {key: _pick(value, aliases) for key, aliases in _COUNT_NAMES.items()}
    if counts["inputTokens"] is None and counts["outputTokens"] is None and counts["totalTokens"] is None:
        return None
    counts["inputTokens"] = counts["inputTokens"] or 0
    counts["outputTokens"] = counts["outputTokens"] or 0
    if counts["totalTokens"] is None:
        counts["totalTokens"] = counts["inputTokens"] + counts["outputTokens"]
    return counts


def _first_dict(*values: Any) -> dict[str, Any] | None:
    return next((value for value in values if isinstance(value, dict)), None)


def _extract_usage(method: str, params: dict[str, Any]) -> tuple[dict[str, int], dict[str, int], int | None] | None:
    """Read only counters from known app-server usage shapes."""
    if method == "turn/completed":
        turn = params.get("turn")
        usage = _first_dict(turn.get("tokenUsage"), turn.get("usage")) if isinstance(turn, dict) else None
    else:
        usage = _first_dict(params.get("tokenUsage"), params.get("usage"))

    # Some relays wrap the same absolute counters in an event payload.
    if usage is None:
        msg = params.get("msg")
        payload = msg.get("payload") if isinstance(msg, dict) else None
        info = payload.get("info") if isinstance(payload, dict) else None
        if isinstance(info, dict):
            usage = _first_dict(info.get("total_token_usage"), info.get("totalTokenUsage"))
    if usage is None:
        return None

    total = _counts(usage.get("total")) or _counts(usage)
    if total is None:
        return None
    last = _counts(usage.get("last")) or _counts(usage) or dict(total)
    context_window = _nonnegative_int(
        usage.get("modelContextWindow", usage.get("model_context_window"))
    )
    return total, last, context_window


class Telemetry:
    """Aggregate isolated agent token totals and emit safe outer notifications."""

    def __init__(self, thread_id: str, turn_id: str,
                 notify: Callable[[str, dict[str, Any]], None],
                 *, clock: Callable[[], float] = time.monotonic):
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.notify = notify
        self.clock = clock
        self._lock = threading.Lock()
        self.phase = "starting"
        self._last_activity = clock()
        self._last_activity_label = "controller started"
        self._sessions: dict[str, dict[str, Any]] = {}
        self._last_emitted: dict[str, Any] | None = None

    @staticmethod
    def _safe_role(value: Any) -> str:
        return value if isinstance(value, str) and value in _ROLE_NAMES else "agent"

    def _session_key(self, params: dict[str, Any], role: str) -> str | None:
        session_key = params.get("sessionKey")
        thread_id = params.get("threadId")
        if isinstance(session_key, str) and _SAFE_ID.fullmatch(session_key):
            return "session:" + session_key
        if isinstance(thread_id, str) and _SAFE_ID.fullmatch(thread_id):
            return "thread:" + role + ":" + thread_id
        # Do not merge unidentified agents into a synthetic global session.
        return None

    def _record_activity(self, method: str, params: dict[str, Any], role: str) -> None:
        item = params.get("item")
        item_type = item.get("type") if isinstance(item, dict) else None
        label = method
        if isinstance(item_type, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,39}", item_type):
            label += ":" + item_type
        self._last_activity = self.clock()
        self._last_activity_label = role + " " + label

    def progress(self, message: str) -> None:
        """Record controller progress while keeping only a recognized phase."""
        if not isinstance(message, str):
            return
        phase = None
        for candidate in _PHASES:
            if re.search(r"(?<![A-Za-z0-9_])" + re.escape(candidate) + r"(?![A-Za-z0-9_])", message):
                phase = candidate
                break
        with self._lock:
            if phase:
                self.phase = phase
            self._last_activity = self.clock()
            self._last_activity_label = "controller progress"

    def heartbeat_message(self) -> str:
        """Return a status line without claiming that an agent is streaming."""
        with self._lock:
            phase = self.phase if self.phase in _PHASES else "starting"
            label = self._last_activity_label
            elapsed = max(0, int(self.clock() - self._last_activity))
        return f"自动验收心跳：阶段 {phase}；最近活动 {elapsed} 秒前（{label}）。"

    def event_callback(self, method: str, params: dict[str, Any]) -> None:
        """Consume one internal app-server event without forwarding its payload."""
        if not isinstance(method, str) or method not in _KNOWN_EVENTS or not isinstance(params, dict):
            return

        role = self._safe_role(params.get("role"))
        try:
            with self._lock:
                self._record_activity(method, params, role)
                extracted = _extract_usage(method, params)
                if extracted is None:
                    return
                session_key = self._session_key(params, role)
                if session_key is None:
                    return
                total, last, context_window = extracted
                session = self._sessions.setdefault(session_key, {
                    "role": role,
                    "total": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
                    "last": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
                    "modelContextWindow": None,
                })
                # App-server usage updates are cumulative snapshots. max() makes
                # repeated snapshots and the final turn result idempotent.
                for name in session["total"]:
                    session["total"][name] = max(session["total"][name], total[name])
                session["last"] = last
                if context_window is not None:
                    session["modelContextWindow"] = context_window

                aggregate_total = {
                    name: sum(entry["total"][name] for entry in self._sessions.values())
                    for name in ("inputTokens", "outputTokens", "totalTokens")
                }
                aggregate_last = dict(session["last"])
                usage = {
                    "total": aggregate_total,
                    "last": aggregate_last,
                }
                # Context windows belong to the model behind this session.
                # Never combine coder/reviewer values or select the larger one.
                if session["modelContextWindow"] is not None:
                    usage["modelContextWindow"] = session["modelContextWindow"]
                snapshot = {
                    "threadId": self.thread_id,
                    "turnId": self.turn_id,
                    "tokenUsage": usage,
                }
                if snapshot == self._last_emitted:
                    return
                self._last_emitted = snapshot
        except Exception:
            # Invalid/partial app-server telemetry must not fail the acceptance run.
            return

        try:
            self.notify("thread/tokenUsage/updated", snapshot)
        except Exception:
            # Telemetry is best-effort; the controller remains authoritative.
            return
