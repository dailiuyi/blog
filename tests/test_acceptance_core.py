from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from symphony_acceptance.checks import _actual_command, prepare_checks, run_checks
from symphony_acceptance.core import (
    PipelineError,
    StateStore,
    assert_scope,
    changed_files,
    criteria,
    extract_plan,
    fingerprint,
    load_config,
    plan_hash,
    validate_plan,
    validate_review,
)
from symphony_acceptance.core import _safe_changed_path


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


def make_repo(root: Path, *, second_repo: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.name", "Acceptance Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / ".gitignore").write_text(".local/\ndist/\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "page.md").write_text("# hello\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "checker.py").write_text("print('trusted')\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "initial")
    return root


def profile_config(**overrides):
    config = {
        "repository": "dailiuyi/blog",
        "base_branch": "main",
        "required_checks": ["portable"],
        "conditional_checks": [],
        "protected_paths": ["scripts/checker.py", "config/**"],
        "checks": {
            "portable": {
                "command": [sys.executable, "-c", "print('check passed')"],
                "cwd": ".",
                "timeout_seconds": 5,
            }
        },
    }
    config.update(overrides)
    return config


def valid_plan(**overrides):
    plan = {
        "title": "Update a page",
        "acceptance": "The page renders correctly.",
        "allowedPaths": ["src/**"],
        "checks": ["portable"],
    }
    plan.update(overrides)
    return plan


class CoreTests(unittest.TestCase):
    def test_load_config_resolves_roots_expands_command_and_supports_second_repo(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            config_dir = base / "settings"
            config_dir.mkdir()
            env_root = base / "from-env"
            payload = {
                "repository": "some-owner/another-repo",
                "workspace_root": "../workspace",
                "control_root": "${ACCEPTANCE_TEST_ROOT}/state",
                "denied_read_paths": ["${ACCEPTANCE_TEST_ROOT}/private"],
                "checks": {"portable": {"command": ["${ACCEPTANCE_TEST_BIN}", "--version"]}},
            }
            config_file = config_dir / "config.json"
            config_file.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.dict(os.environ, {
                "ACCEPTANCE_TEST_ROOT": str(env_root),
                "ACCEPTANCE_TEST_BIN": sys.executable,
            }):
                config = load_config(config_file)
            self.assertEqual(config["repository"], "some-owner/another-repo")
            self.assertEqual(Path(config["workspace_root"]), (config_dir / "../workspace").resolve())
            self.assertEqual(Path(config["control_root"]), (env_root / "state").resolve())
            self.assertEqual(Path(config["denied_read_paths"][0]).resolve(), (env_root / "private").resolve())
            self.assertEqual(config["checks"]["portable"]["command"][0], sys.executable)

    def test_load_config_rejects_invalid_repo_and_nested_control_root(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            file = base / "config.json"
            common = {"workspace_root": "workspace", "control_root": "state"}
            file.write_text(json.dumps({**common, "repository": "owner/name/extra"}), encoding="utf-8")
            with self.assertRaisesRegex(PipelineError, "invalid_repository"):
                load_config(file)
            file.write_text(json.dumps({
                "repository": "owner/repo", "workspace_root": "workspace",
                "control_root": "workspace/.local/state",
            }), encoding="utf-8")
            with self.assertRaisesRegex(PipelineError, "control_root_inside_workspace"):
                load_config(file)

    def test_plan_comment_validation_hash_and_criteria(self):
        config = profile_config(
            checks={"portable": {}, "motion": {}},
            conditional_checks=[{"paths": ["src/motion.ts"], "checks": ["motion"]}],
        )
        raw = valid_plan(
            acceptance="First criterion.\n\nSecond criterion.",
            allowedPaths=["src/**"], checks=["portable", "motion"],
        )
        comment = "Context\n<!-- workflow-plan\n" + json.dumps(raw) + "\n-->\n"
        parsed = extract_plan(comment)
        normalized = validate_plan(parsed, config)
        self.assertEqual(criteria(normalized), ["First criterion.", "Second criterion."])
        self.assertEqual(len(plan_hash(normalized)), 64)
        self.assertEqual(plan_hash(normalized), plan_hash(dict(reversed(list(normalized.items())))))
        self.assertEqual(criteria(valid_plan()), ["The page renders correctly."])
        with self.assertRaisesRegex(PipelineError, "workflow_plan_comment_count_invalid"):
            extract_plan(comment + comment)
        with self.assertRaisesRegex(PipelineError, "workflow_plan_json_invalid"):
            extract_plan("<!-- workflow-plan\nnot-json\n-->")
        with self.assertRaisesRegex(PipelineError, "required_check_missing"):
            validate_plan({**raw, "checks": ["portable"]}, config)

    def test_state_store_durable_paths_evidence_and_nonblocking_process_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "control", "owner/another-repo", 27)
            self.assertTrue(store.home.is_relative_to(store.root))
            self.assertNotIn("..", store.home.parts)
            state = {"phase": "checking", "nested": {"safe": True}}
            store.save(state)
            self.assertEqual(store.load(), state)
            evidence = Path(store.evidence("../../escape.json", {"result": "passed"}))
            self.assertTrue(evidence.is_relative_to(store.home / "evidence"))
            self.assertEqual(json.loads(evidence.read_text(encoding="utf-8")), {"result": "passed"})
            store.event("check", check="portable")
            event = json.loads(store.events_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(event["event"], "check")

            child_code = (
                "from symphony_acceptance.core import StateStore, PipelineError; import sys; "
                "s=StateStore(sys.argv[1], 'owner/another-repo', 27); "
                "\ntry:\n with s.lock(): print('acquired')\n"
                "except PipelineError as e: print(e.reason)"
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = str(REPO_ROOT / "scripts") + os.pathsep + env.get("PYTHONPATH", "")
            with store.lock():
                result = subprocess.run(
                    [sys.executable, "-c", child_code, str(store.root)], env=env,
                    text=True, capture_output=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "state_locked")
            with store.lock():
                self.assertIsNotNone(store.load())

    def test_git_helpers_normalize_crlf_and_capture_modifications_deletions_untracked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = make_repo(Path(temp) / "repo")
            git(root, "config", "core.autocrlf", "true")
            original = fingerprint(root)
            (root / "src" / "page.md").write_bytes(b"# hello\r\n")
            self.assertEqual(fingerprint(root), original)
            (root / "src" / "page.md").write_text("# changed\n", encoding="utf-8")
            (root / "src" / "deleted.md").write_text("remove me\n", encoding="utf-8")
            git(root, "add", "src/deleted.md")
            git(root, "commit", "-qm", "add for delete")
            before_delete = fingerprint(root)
            (root / "src" / "deleted.md").unlink()
            unstaged_delete = fingerprint(root)
            git(root, "add", "-u")
            staged_delete = fingerprint(root)
            self.assertNotEqual(before_delete, unstaged_delete)
            self.assertEqual(unstaged_delete, staged_delete)
            (root / "src" / "untracked.md").write_text("new\n", encoding="utf-8")
            changed = changed_files(root)
            self.assertEqual(changed, ["src/deleted.md", "src/page.md", "src/untracked.md"])

    def test_scope_allows_plan_paths_and_rejects_protected_escape_and_special_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = make_repo(Path(temp) / "repo")
            config = profile_config()
            plan = valid_plan()
            (root / "src" / "page.md").write_text("# changed\n", encoding="utf-8")
            self.assertEqual(assert_scope(root, plan, config), ["src/page.md"])
            (root / "scripts" / "checker.py").write_text("print('tampered')\n", encoding="utf-8")
            with self.assertRaisesRegex(PipelineError, "protected_path_changed"):
                assert_scope(root, plan, config)

        with tempfile.TemporaryDirectory() as temp:
            root = make_repo(Path(temp) / "repo")
            (root / "docs").mkdir()
            (root / "docs" / "outside.md").write_text("outside\n", encoding="utf-8")
            with self.assertRaisesRegex(PipelineError, "changed_path_outside_plan"):
                assert_scope(root, valid_plan(), profile_config())

        if hasattr(os, "symlink"):
            with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
                root = make_repo(Path(temp) / "repo")
                try:
                    os.symlink(str(Path(outside) / "secret.txt"), root / "src" / "escape.txt")
                except (OSError, NotImplementedError):
                    repo = root.resolve()
                    with mock.patch("symphony_acceptance.core.Path.lstat",
                                    return_value=SimpleNamespace(st_mode=stat.S_IFLNK)), \
                         mock.patch("symphony_acceptance.core.Path.resolve",
                                    return_value=Path(outside)):
                        self.assertFalse(_safe_changed_path(repo, "src/escape.txt"))
                    with mock.patch("symphony_acceptance.core.Path.lstat",
                                    return_value=SimpleNamespace(st_mode=stat.S_IFIFO)):
                        self.assertFalse(_safe_changed_path(repo, "src/special-file"))
                else:
                    with self.assertRaisesRegex(PipelineError, "unsafe_changed_file"):
                        assert_scope(root, valid_plan(), profile_config())

    def test_check_runner_rejects_committed_checker_changes_against_trusted_base(self):
        with tempfile.TemporaryDirectory() as temp:
            root = make_repo(Path(temp) / "repo")
            base = git(root, "rev-parse", "HEAD")
            git(root, "update-ref", "refs/remotes/origin/main", base)
            (root / "scripts" / "checker.py").write_text("print('edited checker')\n", encoding="utf-8")
            git(root, "add", "scripts/checker.py")
            git(root, "commit", "-qm", "tamper with checker")
            config = profile_config()
            with tempfile.TemporaryDirectory() as external:
                result = run_checks(root, validate_plan(valid_plan(), config), config, Path(external) / "logs")
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason"], "protected_path_changed")

    def test_review_requires_exact_binding_and_complete_evidence(self):
        plan = {"acceptance": "A\nB"}
        binding = {"head_sha": "a" * 40, "base_sha": "b" * 40, "plan_hash": "c" * 64}
        report = {
            **binding,
            "verdict": "pass",
            "criteria": [
                {"criterion": "A", "status": "met", "evidence": "Observed A."},
                {"criterion": "B", "status": "met", "evidence": "Observed B."},
            ],
            "findings": [],
            "summary": "Both criteria are met.",
        }
        self.assertEqual(validate_review(report, binding, plan)["verdict"], "pass")
        with self.assertRaisesRegex(PipelineError, "review_binding_mismatch"):
            validate_review({**report, "head_sha": "d" * 40}, binding, plan)
        with self.assertRaisesRegex(PipelineError, "review_criteria_coverage_invalid"):
            validate_review({**report, "criteria": report["criteria"][:1]}, binding, plan)
        with self.assertRaisesRegex(PipelineError, "review_criterion_evidence_missing"):
            validate_review({**report, "criteria": [{**report["criteria"][0], "evidence": " "}, report["criteria"][1]]}, binding, plan)
        unmet = [{**report["criteria"][0], "status": "unmet"}, report["criteria"][1]]
        with self.assertRaisesRegex(PipelineError, "review_pass_conflicts_with_evidence"):
            validate_review({**report, "criteria": unmet}, binding, plan)
        finding = {"blocking": True, "path": "src/page.md", "line": 4, "evidence": "Broken link.", "required_fix": "Fix link."}
        with self.assertRaisesRegex(PipelineError, "review_recapture_request_missing"):
            validate_review({**report, "verdict": "recapture"}, binding, plan)
        with self.assertRaisesRegex(PipelineError, "review_rework_request_missing"):
            validate_review({**report, "verdict": "rework", "findings": [{**finding, "blocking": False}]}, binding, plan)
        self.assertEqual(validate_review({**report, "verdict": "recapture", "findings": [finding]}, binding, plan)['verdict'], 'recapture')
        with self.assertRaisesRegex(PipelineError, "review_pass_conflicts_with_evidence"):
            validate_review({**report, "findings": [finding]}, binding, plan)
        with self.assertRaisesRegex(PipelineError, "review_fields_invalid"):
            validate_review({**report, "agent_email": "claimed@example.invalid"}, binding, plan)

    def test_check_runner_pass_failure_block_timeout_mutation_and_sandbox(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = make_repo(base / "repo")
            evidence = base / "evidence"
            plan = validate_plan(valid_plan(), profile_config())

            passed = run_checks(root, plan, profile_config(), evidence / "pass")
            self.assertEqual(passed["status"], "passed", (
                passed,
                Path(passed["checks"][0]["log"]).read_text(encoding="utf-8", errors="replace")
                if passed.get("checks") else "no check log",
            ))
            self.assertEqual(passed["checks"][0]["exit_code"], 0)
            self.assertTrue(Path(passed["checks"][0]["log"]).is_file())

            fail_config = profile_config(checks={"portable": {
                "command": [sys.executable, "-c", "raise SystemExit(7)"], "cwd": ".", "timeout_seconds": 5,
            }})
            failed = run_checks(root, validate_plan(valid_plan(), fail_config), fail_config, evidence / "fail")
            self.assertEqual(failed["status"], "failed")

            missing_config = profile_config(checks={"portable": {
                "command": [str(base / "missing-command"), "--version"], "cwd": ".", "timeout_seconds": 5,
            }})
            missing = run_checks(root, validate_plan(valid_plan(), missing_config), missing_config, evidence / "missing")
            self.assertEqual(missing["status"], "blocked")
            self.assertEqual(missing["reason"], "check_tool_missing")

            timeout_config = profile_config(checks={"portable": {
                "command": [sys.executable, "-c", "import time; time.sleep(2)"], "cwd": ".", "timeout_seconds": 0.1,
            }})
            timed_out = run_checks(root, validate_plan(valid_plan(), timeout_config), timeout_config, evidence / "timeout")
            self.assertEqual(timed_out["status"], "blocked")
            self.assertEqual(timed_out["reason"], "check_timeout")

            child_marker = base / "child-survived-timeout.txt"
            child_source = (
                "import pathlib,time; time.sleep(0.7); pathlib.Path(" + repr(str(child_marker)) + ").write_text('alive')"
            )
            parent_source = (
                "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child_source) + "]); time.sleep(5)"
            )
            tree_config = profile_config(checks={"portable": {
                "command": [sys.executable, "-c", parent_source], "cwd": ".", "timeout_seconds": 0.15,
            }})
            tree_result = run_checks(root, validate_plan(valid_plan(), tree_config), tree_config, evidence / "tree-timeout")
            self.assertEqual(tree_result["status"], "blocked")
            if os.name != "nt":
                time.sleep(0.85)
                self.assertFalse(child_marker.exists(), "timed out check left a child process running")

            mutate_config = profile_config(checks={"portable": {
                "command": [sys.executable, "-c", "from pathlib import Path; Path('src/page.md').write_text('tampered')"],
                "cwd": ".", "timeout_seconds": 5,
            }})
            mutated = run_checks(root, validate_plan(valid_plan(), mutate_config), mutate_config, evidence / "mutate")
            self.assertEqual(mutated["status"], "blocked")
            self.assertEqual(mutated["reason"], "source_changed_during_checks")

            wrapper = base / "sandbox_wrapper.py"
            wrapper.write_text(
                "import subprocess,sys\n"
                "root=sys.argv[1]\n"
                "cwd=sys.argv[2]\n"
                "print('sandbox-root=' + root)\n"
                "print('sandbox-cwd=' + cwd)\n"
                "raise SystemExit(subprocess.run(sys.argv[3:], cwd=cwd).returncode)\n",
                encoding="utf-8",
            )
            (root / "stats").mkdir()
            sandbox_config = profile_config(
                sandbox_command=[sys.executable, str(wrapper), "{root}", "{cwd}"],
                checks={"portable": {
                    "command": [sys.executable, "-c", "import os; print(os.getcwd()); print(bool(os.getenv('GITHUB_TOKEN') or os.getenv('DEEPSEEK_API_KEY') or os.getenv('CUSTOM_TOKEN') or os.getenv('SYMPHONY_ACCEPTANCE_GITHUB_TOKEN'))); print(os.getenv('ASTRO_TELEMETRY_DISABLED'))"],
                    "cwd": "stats", "timeout_seconds": 5,
                }},
            )
            with mock.patch.dict(os.environ, {
                "GITHUB_TOKEN": "test-gh-secret", "DEEPSEEK_API_KEY": "test-provider-secret", "CUSTOM_TOKEN": "test-token",
                "SYMPHONY_ACCEPTANCE_GITHUB_TOKEN": "test-controller-token",
            }):
                sandboxed = run_checks(root, validate_plan(valid_plan(), sandbox_config), sandbox_config, evidence / "sandbox")
            self.assertEqual(sandboxed["status"], "passed")
            self.assertEqual(sandboxed["checks"][0]["command"][:4], [
                sys.executable, str(wrapper), str(root.resolve()), str((root / "stats").resolve()),
            ])
            log = Path(sandboxed["checks"][0]["log"]).read_text(encoding="utf-8")
            self.assertIn("sandbox-root=" + str(root.resolve()), log)
            self.assertIn("sandbox-cwd=" + str((root / "stats").resolve()), log)
            self.assertIn(str((root / "stats").resolve()), log)
            self.assertNotIn("test-gh-secret", log)
            self.assertNotIn("True", log)
            self.assertIn("\n1\n", log)

            inner_missing_config = profile_config(
                sandbox_command=[sys.executable, str(wrapper), "{root}", "{cwd}"],
                checks={"portable": {"command": ["tool-missing-inside-sandbox"], "cwd": ".", "timeout_seconds": 5}},
            )
            inner_missing = run_checks(
                root, validate_plan(valid_plan(), inner_missing_config), inner_missing_config, evidence / "inner-missing"
            )
            self.assertEqual(inner_missing["status"], "blocked", (
                inner_missing,
                Path(inner_missing["checks"][0]["log"]).read_text(encoding="utf-8", errors="replace"),
            ))
            self.assertEqual(inner_missing["reason"], "check_tool_missing")

    def test_prepare_checks_uses_sandbox_and_missing_configured_sandbox_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = make_repo(base / "repo")
            evidence = base / "evidence"
            config = profile_config(prepare=[{
                "command": [sys.executable, "-c", "print('prepared')"], "cwd": ".", "timeout_seconds": 5,
            }])
            result = prepare_checks(root, config, evidence / "prepare")
            self.assertEqual(result["status"], "passed")
            self.assertTrue(Path(result["checks"][0]["log"]).is_file())

            blocked_config = profile_config(
                sandbox_command=[],
                prepare=[{"command": [sys.executable, "-c", "pass"], "cwd": ".", "timeout_seconds": 5}],
            )
            with self.assertRaisesRegex(PipelineError, "sandbox_command_invalid"):
                prepare_checks(root, blocked_config, evidence / "blocked")

    def test_missing_application_file_is_repairable_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = make_repo(base / 'repo')
            config = profile_config(checks={'portable': {
                'command': [sys.executable, '-c', "from pathlib import Path; Path('missing-application.json').read_text()"],
                'cwd': '.', 'timeout_seconds': 5,
            }})
            result = run_checks(root, valid_plan(), config, base / 'evidence')
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['checks'][0]['reason'], 'check_failed')

    def test_transient_network_retries_are_bounded_and_keep_attempt_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = make_repo(base / "repo")
            evidence = base / "evidence"
            counter = evidence / "pass-counter.txt"
            transient_once = (
                "from pathlib import Path; import sys; p=Path(" + repr(str(counter)) + "); "
                "n=int(p.read_text()) if p.exists() else 0; p.parent.mkdir(parents=True,exist_ok=True); "
                "p.write_text(str(n+1)); "
                "print('npm ERR! code ECONNRESET' if n == 0 else 'check passed'); "
                "sys.exit(1 if n == 0 else 0)"
            )
            config = profile_config(checks={"portable": {
                "command": [sys.executable, "-c", transient_once], "cwd": ".", "timeout_seconds": 5,
            }})
            passed = run_checks(root, valid_plan(), config, evidence / "transient-pass")
            self.assertEqual(passed["status"], "passed")
            attempts = passed["checks"][0]["attempts"]
            self.assertEqual(len(attempts), 2)
            self.assertTrue(attempts[0]["transient_network_failure"])
            self.assertFalse(attempts[1]["transient_network_failure"])
            self.assertEqual(len({attempt["log"] for attempt in attempts}), 2)
            self.assertTrue(all(Path(attempt["log"]).is_file() for attempt in attempts))

            always_transient = (
                "import sys; print('HTTP/1.1 503 Service Unavailable'); sys.exit(1)"
            )
            exhausted_config = profile_config(checks={"portable": {
                "command": [sys.executable, "-c", always_transient], "cwd": ".", "timeout_seconds": 5,
            }})
            exhausted = run_checks(root, valid_plan(), exhausted_config, evidence / "transient-exhausted")
            self.assertEqual(exhausted["status"], "blocked")
            self.assertEqual(exhausted["reason"], "check_transient_network_exhausted")
            self.assertEqual(len(exhausted["checks"][0]["attempts"]), 3)

            assertion_config = profile_config(checks={"portable": {
                "command": [sys.executable, "-c", "print('AssertionError: expected output was missing'); raise SystemExit(1)"],
                "cwd": ".", "timeout_seconds": 5,
            }})
            assertion = run_checks(root, valid_plan(), assertion_config, evidence / "assertion")
            self.assertEqual(assertion["status"], "failed")
            self.assertEqual(len(assertion["checks"][0]["attempts"]), 1)

            changed_on_transient = (
                "from pathlib import Path; Path('src/page.md').write_text('changed'); "
                "print('network error ECONNRESET'); raise SystemExit(1)"
            )
            changed_config = profile_config(checks={"portable": {
                "command": [sys.executable, "-c", changed_on_transient], "cwd": ".", "timeout_seconds": 5,
            }})
            changed = run_checks(root, valid_plan(), changed_config, evidence / "source-changed")
            self.assertEqual(changed["status"], "blocked")
            self.assertEqual(changed["reason"], "source_changed_during_checks")
            self.assertEqual(len(changed["checks"][0]["attempts"]), 1)

    def test_prepare_retries_transient_network_and_attaches_exhausted_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = make_repo(base / "repo")
            evidence = base / "evidence"
            counter = evidence / "prepare-counter.txt"
            command = (
                "from pathlib import Path; import sys; p=Path(" + repr(str(counter)) + "); "
                "n=int(p.read_text()) if p.exists() else 0; p.parent.mkdir(parents=True,exist_ok=True); "
                "p.write_text(str(n+1)); print('ETIMEDOUT' if n == 0 else 'prepared'); "
                "sys.exit(1 if n == 0 else 0)"
            )
            config = profile_config(prepare=[{
                "command": [sys.executable, "-c", command], "cwd": ".", "timeout_seconds": 5,
            }])
            prepared = prepare_checks(root, config, evidence / "prepare-success")
            self.assertEqual(prepared["status"], "passed")
            self.assertEqual(len(prepared["checks"][0]["attempts"]), 2)

            failure = profile_config(prepare=[{
                "command": [sys.executable, "-c", "print('connect EAI_AGAIN'); raise SystemExit(1)"],
                "cwd": ".", "timeout_seconds": 5,
            }])
            with self.assertRaisesRegex(PipelineError, "check_transient_network_exhausted") as caught:
                prepare_checks(root, failure, evidence / "prepare-failure")
            self.assertEqual(len(caught.exception.checks[0]["attempts"]), 3)

    def test_sandbox_denied_read_paths_are_quoted_in_selected_codex_profile(self):
        with tempfile.TemporaryDirectory(prefix="acceptance private ") as temp:
            root = Path(temp) / "workspace"
            root.mkdir()
            denied = Path(temp) / 'external "private" folder' / "cred.json"
            prefix = [
                "codex", "sandbox", "-P", "acceptance-check", "-C", "{root}", "--", "env", "-C", "{cwd}",
            ]
            config = {
                "sandbox_command": prefix,
                "sandbox_profile": "acceptance-check",
                "denied_read_paths": [str(denied)],
            }
            command = ["python3", "-c", "pass"]
            actual = _actual_command(command, config, root.resolve(), root.resolve())
            separator = actual.index("--")
            setting = actual[actual.index("-c") + 1]
            self.assertLess(actual.index("-c"), separator)
            self.assertTrue(setting.startswith("permissions.acceptance-check.filesystem={"))
            self.assertIn(f'{json.dumps(str(denied.resolve()))}="deny"', setting)
            self.assertTrue(setting.endswith("}"))
            self.assertEqual(actual[separator + len(["--", "env", "-C", str(root.resolve())]):], command)

            wrong_profile = {**config, "sandbox_profile": "other"}
            with self.assertRaisesRegex(PipelineError, "sandbox_profile_mismatch"):
                _actual_command(command, wrong_profile, root.resolve(), root.resolve())
            relative_path = {**config, "denied_read_paths": ["relative/secret"]}
            with self.assertRaisesRegex(PipelineError, "denied_read_path_must_be_absolute"):
                _actual_command(command, relative_path, root.resolve(), root.resolve())
            malformed_profile = {**config, "sandbox_profile": "invalid.profile"}
            with self.assertRaisesRegex(PipelineError, "sandbox_profile_invalid"):
                _actual_command(command, malformed_profile, root.resolve(), root.resolve())
            custom_wrapper = {**config, "sandbox_command": [sys.executable, "wrapper.py", "{root}"]}
            with self.assertRaisesRegex(PipelineError, "sandbox_command_invalid"):
                _actual_command(command, custom_wrapper, root.resolve(), root.resolve())

    @unittest.skipUnless(os.name == "posix", "Linux parent-death behavior is platform-specific")
    def test_linux_parent_death_kills_check_process_group(self):
        with tempfile.TemporaryDirectory(prefix="acceptance-pdeath-") as temp:
            base = Path(temp)
            root = make_repo(base / "repo")
            external = base / "external"
            external.mkdir()
            command_started = external / "command-started"
            child_started = external / "child-started"
            child_marker = external / "child-survived-parent"
            child_code = (
                "from pathlib import Path; import time; Path(" + repr(str(child_started)) + ").write_text('started'); "
                "time.sleep(2); Path(" + repr(str(child_marker)) + ").write_text('survived')"
            )
            command_code = (
                "from pathlib import Path; import subprocess,sys,time; Path(" + repr(str(command_started)) + ").write_text('started'); "
                "subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "]); time.sleep(3)"
            )
            config = profile_config(checks={"long": {
                "command": [sys.executable, "-c", command_code], "cwd": ".", "timeout_seconds": 20,
            }}, required_checks=[])
            plan = {"allowedPaths": ["src/**"], "checks": ["long"]}
            runner_code = (
                "import json,sys; sys.path.insert(0,sys.argv[1]); "
                "from symphony_acceptance.checks import run_checks; "
                "run_checks(sys.argv[2],json.loads(sys.argv[4]),json.loads(sys.argv[5]),sys.argv[3])"
            )
            child_env = dict(os.environ)
            scripts_path = str(REPO_ROOT / "scripts")
            child_env["PYTHONPATH"] = scripts_path + os.pathsep + child_env.get("PYTHONPATH", "")
            owner = subprocess.Popen([
                sys.executable, "-c", runner_code, scripts_path, str(root), str(external / "logs"),
                json.dumps(plan), json.dumps(config),
            ], env=child_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not child_started.exists() and owner.poll() is None:
                time.sleep(0.05)
            self.assertTrue(child_started.exists(), "check child did not start")
            owner.kill()
            owner.wait(timeout=5)
            time.sleep(2.3)
            self.assertFalse(child_marker.exists(), "check descendant survived controller process death")

    @unittest.skipUnless(os.environ.get("SYMPHONY_ACCEPTANCE_REAL_SANDBOX") == "1",
                         "set SYMPHONY_ACCEPTANCE_REAL_SANDBOX=1 for Codex sandbox integration")
    def test_real_codex_sandbox_denies_sentinel_read_and_external_write(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("real Codex sandbox preflight runs under Linux/WSL")
        with tempfile.TemporaryDirectory(prefix="acceptance-sandbox-") as temp:
            base = Path(temp)
            root = make_repo(base / "workspace")
            external = base / "outside"
            external.mkdir()
            sentinel = external / "harmless-sentinel.txt"
            sentinel.write_text("temporary, non-secret sentinel\n", encoding="utf-8")
            output = external / "must-not-be-created.txt"
            source = (
                "from pathlib import Path; import sys; sentinel=Path(sys.argv[1]); output=Path(sys.argv[2]); "
                "\ntry: sentinel.read_text()\nexcept PermissionError: pass\nelse: print('sentinel-read-allowed'); sys.exit(41)"
                "\ntry: output.write_text('external write')\nexcept PermissionError: pass\nelse: print('external-write-allowed'); sys.exit(42)"
                "\nprint('read-and-write-denied')"
            )
            config = profile_config(
                sandbox_profile="acceptance-check",
                # Deny the containing external area: Codex permissions apply to
                # the named subtree, so this proves both read and write isolation.
                denied_read_paths=[str(external)],
                sandbox_command=[
                    "codex", "sandbox", "-P", "acceptance-check",
                    "-c", 'permissions.acceptance-check.extends=":workspace"',
                    "-c", "permissions.acceptance-check.network.enabled=true",
                    "-C", "{root}", "--", "env", "-C", "{cwd}",
                ],
                checks={"sandbox": {
                    "command": [sys.executable, "-c", source, str(sentinel), str(output)],
                    "cwd": ".", "timeout_seconds": 20,
                }},
                required_checks=[],
                protected_paths=[],
            )
            result = run_checks(root, {"allowedPaths": ["src/**"], "checks": ["sandbox"]},
                                config, external / "evidence")
            self.assertEqual(result["status"], "passed", (
                result,
                Path(result["checks"][0]["log"]).read_text(encoding="utf-8", errors="replace")
                if result.get("checks") else "no sandbox log",
            ))
            log = Path(result["checks"][0]["log"]).read_text(encoding="utf-8", errors="replace")
            self.assertIn("read-and-write-denied", log)
            self.assertFalse(output.exists())


    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux process guardian")
    def test_completed_check_reaps_background_descendant(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = make_repo(base / "repo")
            started = base / "child-started"
            stopped = base / "child-stopped"
            child = (
                "import os,signal,time; from pathlib import Path; "
                "signal.signal(signal.SIGTERM, lambda *_: (Path(" + repr(str(stopped)) +
                ").write_text('stopped'), os._exit(0))); "
                "Path(" + repr(str(started)) + ").write_text('started'); time.sleep(60)"
            )
            parent = (
                "import subprocess,sys,time; from pathlib import Path\n"
                "subprocess.Popen([sys.executable,'-c'," + repr(child) + "])\n"
                "deadline=time.monotonic()+5\n"
                "while not Path(" + repr(str(started)) + ").exists() and time.monotonic()<deadline: time.sleep(0.01)\n"
            )
            config = profile_config(checks={"portable": {
                "command": [sys.executable, "-c", parent], "cwd": ".", "timeout_seconds": 10,
            }})
            result = run_checks(root, validate_plan(valid_plan(), config), config, base / "evidence")
            self.assertEqual(result["status"], "passed", result)
            self.assertTrue(started.exists())
            self.assertTrue(stopped.exists(), "completed check left a background process alive")


if __name__ == "__main__":
    unittest.main()
