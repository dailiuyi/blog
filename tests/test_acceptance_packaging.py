"""Packaging and stdio-bridge acceptance tests; all state lives in temp dirs."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import quote_plus


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import install_symphony_acceptance as installer
from symphony_acceptance.bridge import Bridge


def bundle_paths(source: Path) -> list[Path]:
    paths = [
        source / "AGENTS.md",
        source / "WORKFLOW.md",
        source / "docs" / "README.md",
        source / "docs" / "DECISIONS.md",
        source / "docs" / "SYMPHONY_ACCEPTANCE.md",
        source / "docs" / "symphony-acceptance-pilot.md",
        source / "config" / "symphony-blog.json",
        source / "scripts" / "symphony_codex_adapter.py",
        source / "scripts" / "symphony_deepseek_proxy.py",
        source / "scripts" / "start_symphony_acceptance.sh",
    ]
    return (paths + sorted((source / "scripts" / "symphony_acceptance").glob("*.py"))
            + sorted((source / "scripts" / "symphony_acceptance").glob("*.cjs")))


def copy_bundle_source(destination: Path) -> Path:
    """Make a complete installer fixture from the checked-out source tree."""
    source = destination / "source"
    for original in bundle_paths(ROOT):
        target = source / original.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    return source


def route_text(issue: str = "GH-42") -> str:
    return "\n".join((
        "[SYMPHONY_ROUTING_V1]",
        "issue=" + quote_plus(issue),
        "label=" + quote_plus("symphony:model:gpt-6-astra"),
        "label=" + quote_plus("symphony:effort:high"),
        "[/SYMPHONY_ROUTING_V1]",
        "Run the acceptance plan.",
    ))


class InstallerPackagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = copy_bundle_source(self.root)
        self.destination = self.root / "installed"

    def test_bundle_normalizes_line_endings_and_reinstall_is_idempotent(self) -> None:
        for path in bundle_paths(self.source):
            original = path.read_bytes().replace(b"\r\n", b"\n")
            path.write_bytes(original.replace(b"\n", b"\r\n"))

        first = installer.install(self.source, self.destination)
        first_manifest = json.loads((Path(first["release"]) / "manifest.json").read_text(encoding="utf-8"))
        for relative in first_manifest["files"]:
            bundled = (Path(first["release"]) / relative).read_bytes()
            self.assertNotIn(b"\r\n", bundled, relative)

        second = installer.install(self.source, self.destination)
        releases = list((self.destination / "releases").iterdir())
        self.assertEqual(second["version"], first["version"])
        self.assertEqual(Path(second["release"]), Path(first["release"]))
        self.assertEqual(releases, [Path(first["release"])])
        self.assertEqual(list(self.destination.glob("releases/*.building-*")), [])

    def test_incomplete_source_cannot_activate_or_replace_current(self) -> None:
        valid = installer.install(self.source, self.destination)

        (self.source / "scripts" / "start_symphony_acceptance.sh").unlink()
        with self.assertRaisesRegex(RuntimeError, "installation_source_incomplete"):
            installer.install(self.source, self.destination, activate=True)

        self.assertFalse((self.destination / "current").exists())
        self.assertTrue(Path(valid["release"]).is_dir())

    def test_decision_documents_travel_with_release_and_change_its_version(self) -> None:
        first = installer.install(self.source, self.destination)
        release = Path(first["release"])
        for relative in ("AGENTS.md", "docs/README.md", "docs/DECISIONS.md",
                         "docs/SYMPHONY_ACCEPTANCE.md", "docs/symphony-acceptance-pilot.md"):
            self.assertEqual((release / relative).read_bytes(),
                             (self.source / relative).read_bytes().replace(b"\r\n", b"\n"))

        decisions = self.source / "docs" / "DECISIONS.md"
        decisions.write_bytes(decisions.read_bytes() + b"\nNew accepted decision.\n")
        second = installer.install(self.source, self.destination)
        self.assertNotEqual(first["version"], second["version"])
        self.assertNotEqual((release / "docs/DECISIONS.md").read_bytes(),
                            (Path(second["release"]) / "docs/DECISIONS.md").read_bytes())

        decisions.unlink()
        with self.assertRaisesRegex(RuntimeError, "installation_source_incomplete"):
            installer.install(self.source, self.destination, activate=True)
        self.assertFalse((self.destination / "current").exists())

    def test_corrupted_unactivated_release_cannot_become_current(self) -> None:
        valid = installer.install(self.source, self.destination)

        variant = self.root / "variant"
        shutil.copytree(self.source, variant)
        workflow = variant / "WORKFLOW.md"
        workflow.write_bytes(workflow.read_bytes() + b"\n# new bundle version\n")
        candidate = installer.install(variant, self.destination)
        candidate_workflow = Path(candidate["release"]) / "WORKFLOW.md"
        candidate_workflow.write_bytes(b"corrupted release contents\n")

        with self.assertRaisesRegex(RuntimeError, "installed_bundle_corrupted"):
            installer.install(variant, self.destination, activate=True)

        current = self.destination / "current"
        self.assertFalse(current.exists())
        self.assertFalse(current.is_symlink())
        self.assertTrue(Path(valid["release"]).is_dir())

    def test_incomplete_release_cannot_become_current(self) -> None:
        valid = installer.install(self.source, self.destination)
        variant = self.root / "variant"
        shutil.copytree(self.source, variant)
        workflow = variant / "WORKFLOW.md"
        workflow.write_bytes(workflow.read_bytes() + b"\n# incomplete candidate\n")
        candidate = installer.install(variant, self.destination)
        packaged_adapter = Path(candidate["release"]) / "scripts" / "symphony_codex_adapter.py"
        packaged_adapter.unlink()

        with self.assertRaises(OSError):
            installer.install(variant, self.destination, activate=True)

        current = self.destination / "current"
        self.assertFalse(current.exists())
        self.assertFalse(current.is_symlink())
        self.assertTrue(Path(valid["release"]).is_dir())

    @unittest.skipUnless(os.name == "posix", "atomic symlink activation is a Linux/POSIX contract")
    def test_linux_activation_switches_current_symlink_and_keeps_prior_release(self) -> None:
        first = installer.install(self.source, self.destination, activate=True)
        current = self.destination / "current"
        first_target = current.resolve()

        variant = self.root / "variant"
        shutil.copytree(self.source, variant)
        config = variant / "config" / "symphony-blog.json"
        config.write_bytes(config.read_bytes() + b"\n")
        second = installer.install(variant, self.destination, activate=True)

        self.assertNotEqual(first["version"], second["version"])
        self.assertTrue(current.is_symlink())
        self.assertEqual(current.resolve(), Path(second["release"]))
        self.assertTrue(first_target.is_dir(), "switching current must retain the old immutable release")
        self.assertEqual(list(self.destination.glob("current-*")), [])


class BridgeProtocolTests(unittest.TestCase):
    def test_lost_supervisor_stdin_exits_active_controller(self):
        with mock.patch.object(sys, 'stdin', io.StringIO('')):
            bridge = Bridge({}, output=io.StringIO())
            bridge.worker = object()
            with mock.patch('symphony_acceptance.bridge.os._exit', side_effect=SystemExit(1)) as terminate:
                with self.assertRaises(SystemExit):
                    bridge.run()
                terminate.assert_called_once_with(1)

    def test_initialize_thread_turn_dispatches_once_and_emits_completion_json(self) -> None:
        calls: list[tuple[dict, int, str]] = []

        class FakeController:
            def __init__(self, config, progress=None):
                self.progress = progress
                self.config = config

            def run(self, issue, cwd):
                calls.append((self.config, issue, cwd))
                self.progress("fake check completed")
                return {"phase": "ready", "reason": "acceptance checks passed"}

        config = {"repository": "example/blog"}
        request_lines = [
            {"id": 1, "method": "initialize", "params": {}},
            {"id": 2, "method": "thread/start", "params": {"cwd": "/workspace/GH-42"}},
            {"id": 3, "method": "turn/start", "params": {
                "threadId": "ignored-by-single-process-bridge",
                "input": [{"type": "text", "text": route_text()}],
                "model": "gpt-6-astra", "effort": "high",
            }},
            {"id": 4, "method": "turn/start", "params": {
                "input": [{"type": "text", "text": route_text("43")}],
            }},
        ]
        output = io.StringIO()
        bridge = Bridge(
            config,
            source=io.StringIO("".join(json.dumps(item) + "\n" for item in request_lines)),
            output=output,
            controller_factory=FakeController,
        )

        self.assertEqual(bridge.run(), 0)
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        responses = {message["id"]: message for message in messages if "id" in message}

        self.assertEqual(responses[1]["result"]["userAgent"], "symphony-acceptance/1.0")
        self.assertEqual(responses[1]["result"]["platformFamily"], "unix")
        thread = responses[2]["result"]["thread"]
        self.assertEqual(thread["turns"], [])
        self.assertEqual(responses[3]["result"]["turn"]["status"], "inProgress")
        self.assertEqual(responses[4]["error"]["message"], "one_dispatch_per_process")
        self.assertEqual(calls, [(config, 42, "/workspace/GH-42")])

        notifications = [message for message in messages if "method" in message]
        methods = [message["method"] for message in notifications]
        self.assertIn("turn/started", methods)
        self.assertIn("item/agentMessage/delta", methods)
        completed_item = next(message for message in notifications if message["method"] == "item/completed")
        self.assertEqual(completed_item["params"]["item"]["type"], "agentMessage")
        self.assertIn("自动验收任务 #42：ready。acceptance checks passed",
                      completed_item["params"]["item"]["text"])
        completed_turn = next(message for message in notifications if message["method"] == "turn/completed")
        self.assertEqual(completed_turn["params"]["turn"]["status"], "completed")
        self.assertIsNone(completed_turn["params"]["turn"]["error"])
        self.assertEqual(completed_turn["params"]["turn"]["items"], [completed_item["params"]["item"]])


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux service supervisor')
class ServiceSupervisorTests(unittest.TestCase):
    def test_watcher_failure_stops_runner_and_clears_own_pid_files(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bindir = home / '.local' / 'symphony-bin'
            bindir.mkdir(parents=True)
            watcher = bindir / 'python3'
            watcher.write_text('#!/bin/sh\nsleep 0.3\nexit 7\n')
            watcher.chmod(0o755)
            runner = home / 'fake-runner'
            runner.write_text('#!/bin/sh\necho $$ > "$HOME/observed-runner.pid"\nexec sleep 30\n')
            runner.chmod(0o755)
            # Run the normalized installed script, as the real WSL service does.
            bundle = installer.install(ROOT, home / 'bundle')
            launcher = Path(bundle['release']) / 'scripts' / 'start_symphony_acceptance.sh'
            result = subprocess.run(['bash', str(launcher)],
                                    input='harmless-test-token\n', text=True, capture_output=True, timeout=10,
                                    env=dict(os.environ, HOME=str(home), SYMPHONY_BINARY=str(runner)))
            self.assertEqual(result.returncode, 7, result.stderr)
            pid = int((home / 'observed-runner.pid').read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            control = home / '.local' / 'symphony-acceptance'
            self.assertFalse((control / 'runner.pid').exists())
            self.assertFalse((control / 'watch.pid').exists())


if __name__ == "__main__":
    unittest.main()
