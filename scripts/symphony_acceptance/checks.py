"""Trusted, sandboxed check and preparation command execution."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .core import PipelineError, assert_scope, fingerprint
from .agents import _LINUX_GUARDIAN


_MAX_CHECK_ATTEMPTS = 3
_TRANSIENT_ERRNO = re.compile(r"\b(?:ECONNRESET|EAI_AGAIN|ETIMEDOUT)\b", re.IGNORECASE)
_TRANSIENT_HTTP = re.compile(
    r"\b(?:HTTP(?:/\d(?:\.\d)?)?|status(?:\s+code)?|response(?:\s+code)?)[^\r\n]{0,24}\b(?:502|503|504)\b"
    r"|\b(?:502\s+Bad Gateway|503\s+Service Unavailable|504\s+Gateway Timeout)\b",
    re.IGNORECASE,
)


def _within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_log_name(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")[:80]
    return value or "check"


def _evidence_dir(root: Path, evidence_dir: str | os.PathLike[str]) -> Path:
    repo = root.resolve()
    directory = Path(os.path.expanduser(os.path.expandvars(os.fspath(evidence_dir)))).resolve()
    if _within(directory, repo):
        raise PipelineError("evidence_directory_inside_workspace")
    directory.mkdir(parents=True, exist_ok=True)
    directory = directory.resolve()
    if _within(directory, repo):
        raise PipelineError("evidence_directory_inside_workspace")
    return directory


def _workspace_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    # Build/test commands receive neither GitHub credentials nor provider keys.
    secret_markers = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "PRIVATE_KEY", "CREDENTIAL")
    provider_prefixes = (
        "GH_", "GITHUB_", "DEEPSEEK_", "OPENAI_", "ANTHROPIC_", "GEMINI_",
        "GOOGLE_", "MISTRAL_", "COHERE_", "XAI_", "OPENROUTER_", "AZURE_OPENAI_",
    )
    for key in list(env):
        upper = key.upper()
        if upper.startswith(provider_prefixes) or any(marker in upper for marker in secret_markers):
            env.pop(key, None)

    local = root / ".local"
    paths = {
        "npm_config_cache": local / "npm-cache",
        "GOCACHE": local / "go-cache",
        "GOPATH": local / "go-path",
        "TMPDIR": local / "tmp",
    }
    for key, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    # Build checks are acceptance evidence, not product telemetry runs. Keep
    # tools such as Astro from trying to create user-global config outside the
    # sandboxed workspace (which is intentionally denied).
    env["ASTRO_TELEMETRY_DISABLED"] = "1"
    if os.name == "nt":
        env["TEMP"] = str(paths["TMPDIR"])
        env["TMP"] = str(paths["TMPDIR"])
    return env


def _spec_command(spec: Any) -> tuple[list[str], Path, float]:
    if not isinstance(spec, dict):
        raise PipelineError("command_spec_invalid")
    command = spec.get("command")
    if (not isinstance(command, list) or not command or
            not all(isinstance(part, str) and part and "\x00" not in part for part in command)):
        raise PipelineError("command_argv_invalid")
    cwd = spec.get("cwd", ".")
    if not isinstance(cwd, str) or "\x00" in cwd:
        raise PipelineError("command_cwd_invalid")
    timeout = spec.get("timeout_seconds", 900)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > 86400:
        raise PipelineError("command_timeout_invalid")
    return list(command), Path(cwd), float(timeout)


def _actual_command(command: list[str], config: dict[str, Any], root: Path, cwd: Path) -> list[str]:
    if "sandbox_command" not in config:
        if "sandbox_profile" in config or config.get("denied_read_paths"):
            raise PipelineError("sandbox_command_missing")
        return command
    prefix = config.get("sandbox_command")
    if (not isinstance(prefix, list) or not prefix or
            not all(isinstance(arg, str) and arg and "\x00" not in arg for arg in prefix)):
        raise PipelineError("sandbox_command_invalid")
    rendered = [arg.replace("{root}", str(root)).replace("{cwd}", str(cwd)) for arg in prefix]
    profile = config.get("sandbox_profile")
    denied_paths = config.get("denied_read_paths", [])
    if not isinstance(denied_paths, list):
        raise PipelineError("denied_read_paths_invalid")
    if "sandbox_profile" in config or denied_paths:
        if not isinstance(profile, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", profile):
            raise PipelineError("sandbox_profile_invalid")
        command_name = Path(rendered[0]).name.lower()
        if command_name not in {"codex", "codex.exe"} or len(rendered) < 2 or rendered[1] != "sandbox":
            raise PipelineError("sandbox_command_invalid")
        separator = [index for index, arg in enumerate(rendered) if arg == "--"]
        if len(separator) != 1:
            raise PipelineError("sandbox_command_invalid")
        selected_profiles = []
        index = 0
        while index < separator[0]:
            arg = rendered[index]
            if arg in {"-P", "--profile"}:
                if index + 1 >= separator[0]:
                    raise PipelineError("sandbox_profile_invalid")
                selected_profiles.append(rendered[index + 1])
                index += 2
                continue
            if arg.startswith("--profile="):
                selected_profiles.append(arg.split("=", 1)[1])
            elif arg.startswith("-P") and arg != "-P":
                selected_profiles.append(arg[2:])
            index += 1
        if selected_profiles != [profile]:
            raise PipelineError("sandbox_profile_mismatch")

        normalized = []
        for item in denied_paths:
            if not isinstance(item, str) or not item or "\x00" in item:
                raise PipelineError("denied_read_path_invalid")
            expanded = os.path.expanduser(os.path.expandvars(item))
            path = Path(expanded)
            if not path.is_absolute():
                raise PipelineError("denied_read_path_must_be_absolute")
            try:
                absolute = path.resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise PipelineError("denied_read_path_invalid") from exc
            if str(absolute) == absolute.anchor:
                raise PipelineError("denied_read_path_too_broad")
            if _within(root, absolute) or _within(absolute, root):
                raise PipelineError("denied_read_path_overlaps_workspace")
            normalized.append(str(absolute))
        if normalized:
            entries = ",".join(
                f"{json.dumps(path, ensure_ascii=False)}=\"deny\""
                for path in dict.fromkeys(normalized)
            )
            # Keep path keys inside a TOML inline table. Codex's dotted override
            # parser treats a quoted dotted-key component as part of the path.
            setting = f"permissions.{profile}.filesystem={{{entries}}}"
            additions = ["-c", setting]
            rendered[separator[0]:separator[0]] = additions
    return rendered + command


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            kwargs: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                                      "timeout": 8, "check": False}
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], **kwargs)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    except OSError:
        try:
            process.kill()
        except OSError:
            pass


def _run_attempt(
    name: str,
    spec: dict[str, Any],
    root: Path,
    config: dict[str, Any],
    evidence_dir: Path,
    log_prefix: str,
) -> dict[str, Any]:
    configured_command: list[str] = []
    actual: list[str] = []
    log_path = evidence_dir / f"{log_prefix}-{_safe_log_name(name)}.log"
    result: dict[str, Any] = {
        "name": name,
        "command": actual,
        "exit_code": None,
        "log": str(log_path),
    }
    try:
        configured_command, cwd_suffix, timeout = _spec_command(spec)
        cwd = (root / cwd_suffix).resolve()
        if not _within(cwd, root.resolve()) or not cwd.is_dir():
            raise PipelineError("command_cwd_invalid")
        actual = _actual_command(configured_command, config, root.resolve(), cwd)
        result["command"] = actual
    except PipelineError as exc:
        result["error"] = exc.reason
        return {**result, "status": "blocked", "reason": exc.reason}

    env = _workspace_env(root.resolve())
    creationflags = 0
    popen_args: dict[str, Any] = {}
    launch_command = actual
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if creationflags:
            popen_args["creationflags"] = creationflags
        popen_args["creationflags"] = popen_args.get("creationflags", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        popen_args["start_new_session"] = True
        if sys.platform.startswith("linux"):
            # Share the agent guardian: commands get a separate process group,
            # reaped even when their direct process exits before descendants.
            # Avoid Python preexec_fn inside the threaded controller.
            launch_command = [sys.executable, "-c", _LINUX_GUARDIAN, "--", *actual]
            setpriv = shutil.which("setpriv")
            if setpriv:
                launch_command = [setpriv, "--pdeathsig", "SIGTERM", "--", *launch_command]
        else:
            launch_command = actual

    timed_out = False
    reader_error: list[BaseException] = []
    try:
        with log_path.open("wb") as log:
            log.write(("$ " + " ".join(actual) + "\n").encode("utf-8", errors="replace"))
            log.flush()
            process = subprocess.Popen(
                launch_command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                **popen_args,
            )

            def copy_output() -> None:
                try:
                    assert process.stdout is not None
                    while True:
                        chunk = process.stdout.read(65536)
                        if not chunk:
                            break
                        log.write(chunk)
                except BaseException as exc:  # surfaced after process completion
                    reader_error.append(exc)

            reader = threading.Thread(target=copy_output, name="acceptance-check-log", daemon=True)
            reader.start()
            try:
                exit_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(process)
                try:
                    exit_code = process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    exit_code = process.wait(timeout=5)
            reader.join(timeout=8)
            if reader.is_alive() and process.stdout is not None:
                process.stdout.close()
                reader.join(timeout=2)
            if process.stdout is not None and not process.stdout.closed:
                process.stdout.close()
            log.flush()
            os.fsync(log.fileno())
            result["exit_code"] = exit_code
    except FileNotFoundError:
        result.update(status="blocked", reason="check_tool_missing", error="check_tool_missing")
        return result
    except PermissionError:
        result.update(status="blocked", reason="check_launch_blocked", error="check_launch_blocked")
        return result
    except OSError:
        result.update(status="blocked", reason="check_launch_failed", error="check_launch_failed")
        return result

    if timed_out:
        result.update(status="blocked", reason="check_timeout", error="check_timeout")
    elif reader_error:
        result.update(status="blocked", reason="check_log_failed", error="check_log_failed")
    elif result["exit_code"] != 0:
        try:
            with log_path.open("rb") as stream:
                diagnostic = stream.read(64 * 1024).decode("utf-8", errors="replace").lower()
        except OSError:
            diagnostic = ""
        missing_tool = any(marker in diagnostic for marker in (
            "command not found", "executable file not found",
            "is not recognized as an internal or external command",
        ))
        # Missing application files/imports are code failures, not missing tools.
        missing_tool = missing_tool or (
            ("subprocess.py" in diagnostic or result["exit_code"] == 127) and
            any(marker in diagnostic for marker in ("no such file or directory", "[winerror 2]", "[errno 2]")))
        result.update(status="blocked" if missing_tool else "failed",
                      reason="check_tool_missing" if missing_tool else "check_failed")
    else:
        result.update(status="passed")
    return result


def _transient_network_failure(result: dict[str, Any]) -> bool:
    """Match only explicit network errno and transient HTTP status diagnostics."""
    if result.get("status") != "failed":
        return False
    log_path = result.get("log")
    if not isinstance(log_path, str):
        return False
    try:
        path = Path(log_path)
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 256 * 1024))
            diagnostic = stream.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    return bool(_TRANSIENT_ERRNO.search(diagnostic) or _TRANSIENT_HTTP.search(diagnostic))


def _run_command(
    name: str,
    spec: dict[str, Any],
    root: Path,
    config: dict[str, Any],
    evidence_dir: Path,
    log_prefix: str,
) -> dict[str, Any]:
    """Run a command with at most two retries for explicit transient networking failures."""
    try:
        source_before = fingerprint(root)
    except PipelineError as exc:
        return {
            "name": name,
            "command": [],
            "exit_code": None,
            "log": "",
            "attempts": [],
            "status": "blocked",
            "reason": exc.reason,
            "error": exc.reason,
        }

    attempts: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for attempt_number in range(1, _MAX_CHECK_ATTEMPTS + 1):
        attempt_prefix = f"{log_prefix}-attempt-{attempt_number:02d}"
        last = _run_attempt(name, spec, root, config, evidence_dir, attempt_prefix)
        transient = _transient_network_failure(last)
        attempt = {
            "attempt": attempt_number,
            "status": last.get("status"),
            "reason": last.get("reason", ""),
            "exit_code": last.get("exit_code"),
            "log": last.get("log", ""),
            "transient_network_failure": transient,
        }
        attempts.append(attempt)
        last["attempts"] = list(attempts)

        if not transient:
            return last
        if attempt_number == _MAX_CHECK_ATTEMPTS:
            last.update(
                status="blocked",
                reason="check_transient_network_exhausted",
                error="check_transient_network_exhausted",
                transient=True,
            )
            return last

        try:
            source_after = fingerprint(root)
        except PipelineError as exc:
            last.update(status="blocked", reason=exc.reason, error=exc.reason)
            return last
        if source_after != source_before:
            last.update(
                status="blocked",
                reason="source_changed_between_check_attempts",
                error="source_changed_between_check_attempts",
            )
            return last
        # Short bounded backoff gives transient services time to recover without
        # adding a separate retry budget to the controller's repair counter.
        time.sleep(0.5 * attempt_number)

    return last


def _assert_artifacts(root: Path, spec: dict[str, Any]) -> list[str]:
    artifacts = spec.get("artifacts", [])
    if not isinstance(artifacts, list) or not all(isinstance(item, str) and item for item in artifacts):
        raise PipelineError("check_artifacts_invalid")
    missing = []
    for item in artifacts:
        candidate = Path(item)
        if candidate.is_absolute() or "\\" in item or any(part in {"", ".", ".."} for part in item.split("/")):
            raise PipelineError("check_artifact_path_invalid")
        path = (root / candidate).resolve()
        if not _within(path, root.resolve()):
            raise PipelineError("check_artifact_escape")
        if not path.is_file():
            missing.append(item)
    return missing


def _comparison_base(root: Path, config: dict[str, Any]) -> str:
    explicit = config.get("acceptance_base_sha") or config.get("base_sha")
    if isinstance(explicit, str) and explicit:
        return explicit
    branch = config.get("base_branch")
    if isinstance(branch, str) and re.fullmatch(r"[A-Za-z0-9._/-]+", branch) and ".." not in branch.split("/"):
        for candidate in (f"origin/{branch}", branch):
            try:
                subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", candidate],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               check=True, timeout=15)
                return candidate
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass
    return "HEAD"


def _blocked_result(reason: str, before: str = "", after: str = "", results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        "fingerprint_before": before,
        "fingerprint_after": after,
        "checks": results or [],
    }


def _run_many(
    specs: list[dict[str, Any]], root: Path, config: dict[str, Any],
    evidence_dir: Path, prefix: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, spec in enumerate(specs, 1):
        name = str(spec.get("name") or f"{prefix}-{index}") if isinstance(spec, dict) else f"{prefix}-{index}"
        result = _run_command(name, spec, root, config, evidence_dir, f"{prefix}-{index:02d}")
        results.append(result)
        if result["status"] == "blocked":
            error = PipelineError(result["reason"])
            error.checks = results
            raise error
        if result["status"] != "passed":
            error = PipelineError("prepare_failed")
            error.checks = results
            raise error
    return results


def prepare_checks(root: str | os.PathLike[str], config: dict[str, Any], evidence_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Run optional trusted preparation commands once for a source snapshot."""
    repo = Path(root).resolve()
    try:
        directory = _evidence_dir(repo, evidence_dir)
        specs = config.get("prepare", [])
        if not isinstance(specs, list):
            raise PipelineError("prepare_specs_invalid")
        results = _run_many(specs, repo, config, directory, "prepare")
        return {"status": "passed", "checks": results}
    except PipelineError:
        # Preserve per-attempt logs and summaries for callers that persist
        # preparation failures as blocked evidence.
        raise


def run_checks(
    root: str | os.PathLike[str],
    plan: dict[str, Any],
    config: dict[str, Any],
    evidence_dir: str | os.PathLike[str],
) -> dict[str, Any]:
    """Run configured check profiles, preserving logs and source identity evidence."""
    repo = Path(root).resolve()
    results: list[dict[str, Any]] = []
    before = ""
    after = ""
    try:
        directory = _evidence_dir(repo, evidence_dir)
        before = fingerprint(repo)
        assert_scope(repo, plan, config, _comparison_base(repo, config))
    except PipelineError as exc:
        return _blocked_result(exc.reason, before, after, results)

    profiles = config.get("checks")
    names = plan.get("checks") if isinstance(plan, dict) else None
    if not isinstance(profiles, dict) or not isinstance(names, list) or not names:
        return _blocked_result("check_profiles_invalid", before, before, results)

    ordinary_failure = False
    for name in names:
        if not isinstance(name, str) or name not in profiles:
            return _blocked_result("check_profile_missing", before, after or before, results)
        spec = profiles[name]
        result = _run_command(name, spec, repo, config, directory, "check")
        try:
            if result.get("status") == "passed":
                missing = _assert_artifacts(repo, spec)
                if missing:
                    result.update(status="failed", reason="check_artifact_missing", artifacts_missing=missing)
        except PipelineError as exc:
            result.update(status="blocked", reason=exc.reason, error=exc.reason)
        results.append(result)

        try:
            after = fingerprint(repo)
        except PipelineError as exc:
            return _blocked_result(exc.reason, before, after, results)
        if after != before:
            return _blocked_result("source_changed_during_checks", before, after, results)
        if result["status"] == "blocked":
            return _blocked_result(result.get("reason", "check_environment_blocked"), before, after, results)
        if result["status"] != "passed":
            ordinary_failure = True

    if ordinary_failure:
        return {"status": "failed", "reason": "check_failed", "fingerprint_before": before,
                "fingerprint_after": after, "checks": results}
    return {"status": "passed", "reason": "", "fingerprint_before": before,
            "fingerprint_after": after, "checks": results}
