"""Safe bridge telemetry tests; all app-server events are synthetic."""
from __future__ import annotations

import io
import json
import sys
import unittest


ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from symphony_acceptance.bridge import Bridge
from symphony_acceptance.telemetry import Telemetry


class TelemetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 100.0
        self.messages: list[tuple[str, dict]] = []
        self.telemetry = Telemetry(
            "outer-thread", "outer-turn",
            lambda method, params: self.messages.append((method, params)),
            clock=lambda: self.now,
        )

    def event(self, role: str, session: str, thread: str, total: dict,
              *, last: dict | None = None, context: int | None = None) -> dict:
        usage = {"total": total, "last": last or total}
        if context is not None:
            usage["modelContextWindow"] = context
        return {
            "role": role,
            "sessionKey": session,
            "threadId": thread,
            "turnId": "private-inner-turn",
            "tokenUsage": usage,
        }

    def test_aggregates_distinct_sessions_and_deduplicates_snapshots_and_final(self) -> None:
        coder = self.event("coder", "coder-session-1", "private-coder-thread", {
            "inputTokens": 10, "outputTokens": 2, "totalTokens": 12,
        }, context=100_000)
        self.telemetry.event_callback("thread/tokenUsage/updated", coder)
        self.telemetry.event_callback("thread/tokenUsage/updated", coder)
        self.telemetry.event_callback("turn/completed", {
            "role": "coder", "sessionKey": "coder-session-1",
            "threadId": "private-coder-thread",
            "turn": {"id": "private-inner-turn", "tokenUsage": {
                "inputTokens": 10, "outputTokens": 2, "totalTokens": 12,
            }},
        })

        self.telemetry.event_callback("thread/tokenUsage/updated", self.event(
            "coder", "coder-session-1", "private-coder-thread", {
                "inputTokens": 18, "outputTokens": 6, "totalTokens": 24,
            }, context=200_000,
        ))
        self.telemetry.event_callback("thread/tokenUsage/updated", self.event(
            "reviewer", "reviewer-session-1", "private-review-thread", {
                "inputTokens": 5, "outputTokens": 5, "totalTokens": 10,
            }, context=100_000,
        ))

        self.assertEqual(len(self.messages), 3)
        method, params = self.messages[-1]
        self.assertEqual(method, "thread/tokenUsage/updated")
        self.assertEqual(params, {
            "threadId": "outer-thread",
            "turnId": "outer-turn",
            "tokenUsage": {
                "total": {"inputTokens": 23, "outputTokens": 11, "totalTokens": 34},
                "last": {"inputTokens": 5, "outputTokens": 5, "totalTokens": 10},
                "modelContextWindow": 100_000,
            },
        })
        serialized = json.dumps(params)
        self.assertNotIn("private-coder-thread", serialized)
        self.assertNotIn("reviewer-session-1", serialized)
        self.assertNotIn("private-inner-turn", serialized)

    def test_streaming_delta_refreshes_activity_without_exposing_content(self) -> None:
        self.telemetry.progress("#42 coding; repairs=0")
        self.now += 20
        self.telemetry.event_callback("item/agentMessage/delta", {
            "role": "reviewer",
            "sessionKey": "review-session",
            "threadId": "private-review-thread",
            "delta": "private source text ghp_supersecretvalue",
        })
        self.now += 3

        heartbeat = self.telemetry.heartbeat_message()
        self.assertIn("阶段 coding", heartbeat)
        self.assertIn("最近活动 3 秒前", heartbeat)
        self.assertIn("reviewer item/agentMessage/delta", heartbeat)
        self.assertNotIn("private source text", heartbeat)
        self.assertNotIn("ghp_supersecretvalue", heartbeat)
        self.assertEqual(self.messages, [])

    def test_ignores_message_bodies_and_heartbeat_keeps_phase_and_activity_age(self) -> None:
        secret = "ghp_supersecretvalue"
        self.telemetry.event_callback("item/agentMessage/delta", {
            "role": "coder", "delta": "source code and " + secret,
        })
        self.telemetry.progress("#42 reviewing; repairs=1")
        self.now += 27
        heartbeat = self.telemetry.heartbeat_message()

        self.assertEqual(self.messages, [])
        self.assertIn("阶段 reviewing", heartbeat)
        self.assertIn("最近活动 27 秒前", heartbeat)
        self.assertNotIn(secret, heartbeat)
        self.assertNotIn("source code", heartbeat)

    def test_never_merges_usage_without_session_or_thread_identity(self) -> None:
        self.telemetry.event_callback("thread/tokenUsage/updated", {
            "role": "reviewer",
            "tokenUsage": {"total": {"inputTokens": 9, "outputTokens": 2}},
        })
        self.assertEqual(self.messages, [])

    def test_omits_unknown_context_window_even_if_another_session_reported_one(self) -> None:
        self.telemetry.event_callback("thread/tokenUsage/updated", self.event(
            "coder", "coder-session", "coder-thread", {
                "inputTokens": 9, "outputTokens": 2, "totalTokens": 11,
            }, context=100_000,
        ))
        self.telemetry.event_callback("thread/tokenUsage/updated", self.event(
            "reviewer", "reviewer-session", "reviewer-thread", {
                "inputTokens": 4, "outputTokens": 1, "totalTokens": 5,
            },
        ))
        self.assertNotIn("modelContextWindow", self.messages[-1][1]["tokenUsage"])


class BridgeTerminalStateTests(unittest.TestCase):
    def test_terminal_block_is_failed_and_reason_is_visible_without_credentials(self) -> None:
        class FakeController:
            def __init__(self, config, progress=None, event_callback=None):
                self.event_callback = event_callback

            def run(self, issue, cwd):
                self.event_callback("thread/tokenUsage/updated", {
                    "role": "coder", "sessionKey": "fake-session",
                    "threadId": "fake-internal-thread",
                    "tokenUsage": {"total": {"inputTokens": 4, "outputTokens": 1}},
                })
                return {"phase": "blocked", "reason": "coder_blocked ghp_supersecretvalue"}

        output = io.StringIO()
        bridge = Bridge({}, source=io.StringIO(), output=output, controller_factory=FakeController)
        bridge.turn_id = "outer-turn"
        bridge.telemetry = Telemetry(bridge.thread_id, bridge.turn_id, bridge.notify)
        bridge.execute(42)
        notifications = [json.loads(line) for line in output.getvalue().splitlines()]
        final_turn = next(message["params"]["turn"] for message in notifications
                          if message.get("method") == "turn/completed")
        final_item = next(message["params"]["item"] for message in notifications
                          if message.get("method") == "item/completed")

        self.assertEqual(final_turn["status"], "failed")
        self.assertIn("coder_blocked", final_turn["error"]["message"])
        self.assertIn("coder_blocked", final_item["text"])
        self.assertNotIn("ghp_supersecretvalue", output.getvalue())
        self.assertIn("thread/tokenUsage/updated", [message.get("method") for message in notifications])


if __name__ == "__main__":
    unittest.main()
