"""Trusted core primitives for the Symphony acceptance controller."""

from __future__ import annotations

import base64
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class PipelineError(RuntimeError):
    """A safe, stable reason suitable for state and user-facing summaries."""

    def __init__(self, reason: str):
        candidate = str(reason)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", candidate):
            candidate = "pipeline_error"
        self.reason = candidate
        super().__init__(candidate)


REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "rework", "recapture", "blocked"]},
        "head_sha": {"type": "string"},
        "base_sha": {"type": "string"},
        "plan_hash": {"type": "string"},
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "criterion": {"type": "string"},
                    "status": {"type": "string", "enum": ["met", "unmet", "blocked"]},
                    "evidence": {"type": "string"},
                },
                "required": ["criterion", "status", "evidence"],
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "blocking": {"type": "boolean"},
                    "path": {"type": "string"},
                    "line": {"type": "integer"},
                    "evidence": {"type": "string"},
                    "required_fix": {"type": "string"},
                },
                "required": ["blocking", "path", "line", "evidence", "required_fix"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["verdict", "head_sha", "base_sha", "plan_hash", "criteria", "findings", "summary"],
}

_PLAN_KEYS = {"title", "acceptance", "allowedPaths", "checks"}
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PLAN_MARKER = re.compile(r"<!--[ \t]*workflow-plan(?=\s)(.*?)-->", re.DOTALL)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expanduser(os.path.expandvars(value))
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    return value


def _repository_name(config: dict[str, Any]) -> str:
    value = config.get("repository", config.get("repo"))
    if isinstance(value, dict):
        owner, name = value.get("owner"), value.get("name")
        value = f"{owner}/{name}" if isinstance(owner, str) and isinstance(name, str) else ""
    if not isinstance(value, str) or not _REPO_RE.fullmatch(value):
        raise PipelineError("invalid_repository")
    owner, name = value.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise PipelineError("invalid_repository")
    config["repository"] = value
    return value


def load_config(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load trusted JSON config, expanding variables and resolving configured roots."""
    config_file = Path(os.path.expanduser(os.path.expandvars(os.fspath(path)))).resolve()
    try:
        raw = json.loads(config_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PipelineError("config_missing") from exc
    except (OSError, UnicodeError) as exc:
        raise PipelineError("config_unreadable") from exc
    except json.JSONDecodeError as exc:
        raise PipelineError("config_invalid_json") from exc
    if not isinstance(raw, dict):
        raise PipelineError("config_must_be_object")
    config = _expand(raw)
    _repository_name(config)

    # These roots are paths owned by the host config. Command cwd/artifact paths
    # remain relative to the workspace and are deliberately not rewritten here.
    for key in ("workspace_root", "control_root", "evidence_root", "config_path", "checker_path"):
        value = config.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise PipelineError("invalid_config_path")
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = config_file.parent / candidate
        config[key] = str(candidate.resolve())
    if "workspace_root" not in config or "control_root" not in config:
        raise PipelineError("config_roots_required")
    workspace = Path(config["workspace_root"]).resolve()
    control = Path(config["control_root"]).resolve()
    if _within(control, workspace):
        raise PipelineError("control_root_inside_workspace")
    config["config_file"] = str(config_file)
    return config


def _valid_pattern(pattern: Any) -> bool:
    if not isinstance(pattern, str) or not pattern or pattern.strip() != pattern:
        return False
    if "\\" in pattern or pattern.startswith("/") or re.match(r"^[A-Za-z]:", pattern):
        return False
    if "\x00" in pattern or any(part in {"", ".", ".."} for part in pattern.split("/")):
        return False
    return True


def _matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path.replace("\\", "/"), pattern.replace("\\", "/"))


def _conditional_required(allowed_paths: list[str], conditionals: Any) -> list[str]:
    if not isinstance(conditionals, list):
        raise PipelineError("invalid_conditional_checks")
    required: list[str] = []
    for entry in conditionals:
        if not isinstance(entry, dict):
            raise PipelineError("invalid_conditional_checks")
        paths, names = entry.get("paths"), entry.get("checks")
        if (not isinstance(paths, list) or not paths or
                not all(_valid_pattern(path) for path in paths) or
                not isinstance(names, list) or not names or
                not all(isinstance(name, str) and name for name in names)):
            raise PipelineError("invalid_conditional_checks")
        if any(_matches(path, allowed) for path in paths for allowed in allowed_paths):
            for name in names:
                if name not in required:
                    required.append(name)
    return required


def validate_plan(plan: Any, config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict) or set(plan) != _PLAN_KEYS:
        raise PipelineError("plan_fields_invalid")
    title, acceptance = plan.get("title"), plan.get("acceptance")
    if (not isinstance(title, str) or not title.strip() or len(title) > 6000 or
            not isinstance(acceptance, str) or not acceptance.strip() or len(acceptance) > 6000):
        raise PipelineError("plan_text_invalid")
    allowed_paths = plan.get("allowedPaths")
    if (not isinstance(allowed_paths, list) or not allowed_paths or
            not all(_valid_pattern(pattern) for pattern in allowed_paths) or
            len(set(allowed_paths)) != len(allowed_paths)):
        raise PipelineError("plan_paths_invalid")
    names = plan.get("checks")
    if not isinstance(names, list) or not all(isinstance(name, str) and name for name in names):
        raise PipelineError("plan_checks_invalid")
    if len(set(names)) != len(names):
        raise PipelineError("plan_checks_duplicate")
    configured = config.get("checks")
    if not isinstance(configured, dict) or not configured:
        raise PipelineError("check_profiles_missing")
    if any(not isinstance(spec, dict) for spec in configured.values()):
        raise PipelineError("check_profiles_invalid")
    unknown = set(names) - set(configured)
    if unknown:
        raise PipelineError("unknown_check_profile")
    required = config.get("required_checks", [])
    if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
        raise PipelineError("required_checks_invalid")
    required = list(dict.fromkeys(required + _conditional_required(allowed_paths, config.get("conditional_checks", []))))
    if any(name not in configured for name in required):
        raise PipelineError("required_check_profile_missing")
    if not set(required).issubset(names):
        raise PipelineError("required_check_missing")
    # Config order makes equivalent issue plans hash identically.
    ordered = [name for name in configured if name in names]
    return {
        "title": title.strip(),
        "acceptance": acceptance.strip(),
        "allowedPaths": list(allowed_paths),
        "checks": ordered,
    }


def extract_plan(body: str) -> dict[str, Any]:
    if not isinstance(body, str):
        raise PipelineError("workflow_plan_body_invalid")
    marker_count = len(re.findall(r"<!--[ \t]*workflow-plan\b", body))
    matches = list(_PLAN_MARKER.finditer(body))
    if marker_count != 1 or len(matches) != 1:
        raise PipelineError("workflow_plan_comment_count_invalid")
    try:
        plan = json.loads(matches[0].group(1).strip())
    except json.JSONDecodeError as exc:
        raise PipelineError("workflow_plan_json_invalid") from exc
    if not isinstance(plan, dict):
        raise PipelineError("workflow_plan_shape_invalid")
    return plan


def plan_hash(plan: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PipelineError("plan_hash_invalid") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def criteria(plan: dict[str, Any]) -> list[str]:
    acceptance = plan.get("acceptance") if isinstance(plan, dict) else None
    if not isinstance(acceptance, str):
        raise PipelineError("plan_acceptance_invalid")
    return [line.strip() for line in acceptance.splitlines() if line.strip()]


def validate_review(report: Any, binding: dict[str, str], plan: dict[str, Any]) -> dict[str, Any]:
    required = set(REVIEW_SCHEMA["required"])
    if not isinstance(report, dict) or set(report) != required:
        raise PipelineError("review_fields_invalid")
    if report.get("verdict") not in {"pass", "rework", "recapture", "blocked"}:
        raise PipelineError("review_verdict_invalid")
    for key in ("head_sha", "base_sha", "plan_hash"):
        expected = binding.get(key)
        if not isinstance(expected, str) or not expected or report.get(key) != expected:
            raise PipelineError("review_binding_mismatch")
    summary = report.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise PipelineError("review_summary_missing")

    expected_criteria = criteria(plan)
    actual = report.get("criteria")
    if not isinstance(actual, list) or len(actual) != len(expected_criteria):
        raise PipelineError("review_criteria_coverage_invalid")
    seen: dict[str, dict[str, Any]] = {}
    for item in actual:
        if not isinstance(item, dict) or set(item) != {"criterion", "status", "evidence"}:
            raise PipelineError("review_criterion_invalid")
        criterion = item.get("criterion")
        if not isinstance(criterion, str) or criterion in seen:
            raise PipelineError("review_criteria_coverage_invalid")
        if item.get("status") not in {"met", "unmet", "blocked"}:
            raise PipelineError("review_criterion_status_invalid")
        evidence = item.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            raise PipelineError("review_criterion_evidence_missing")
        seen[criterion] = item
    if set(seen) != set(expected_criteria):
        raise PipelineError("review_criteria_coverage_invalid")

    findings = report.get("findings")
    if not isinstance(findings, list):
        raise PipelineError("review_findings_invalid")
    normalized_findings = []
    has_blocking = False
    for item in findings:
        if not isinstance(item, dict) or set(item) != {"blocking", "path", "line", "evidence", "required_fix"}:
            raise PipelineError("review_finding_invalid")
        if not isinstance(item.get("blocking"), bool):
            raise PipelineError("review_finding_invalid")
        if not isinstance(item.get("path"), str):
            raise PipelineError("review_finding_invalid")
        if isinstance(item.get("line"), bool) or not isinstance(item.get("line"), int) or item["line"] < 1:
            raise PipelineError("review_finding_invalid")
        evidence = item.get("evidence")
        fix = item.get("required_fix")
        if not isinstance(evidence, str) or not isinstance(fix, str):
            raise PipelineError("review_finding_invalid")
        if item["blocking"] and (not evidence.strip() or not fix.strip()):
            raise PipelineError("review_blocking_evidence_missing")
        if evidence.strip() == "":
            raise PipelineError("review_finding_evidence_missing")
        has_blocking = has_blocking or item["blocking"]
        normalized_findings.append(dict(item))

    if report["verdict"] == "pass" and (
        has_blocking or any(item["status"] != "met" for item in seen.values())
    ):
        raise PipelineError("review_pass_conflicts_with_evidence")
    if report["verdict"] == "recapture" and not has_blocking:
        raise PipelineError("review_recapture_request_missing")
    if report["verdict"] == "rework" and not has_blocking:
        raise PipelineError("review_rework_request_missing")
    return {
        "verdict": report["verdict"],
        "head_sha": report["head_sha"],
        "base_sha": report["base_sha"],
        "plan_hash": report["plan_hash"],
        "criteria": [dict(item) for item in actual],
        "findings": normalized_findings,
        "summary": summary.strip(),
    }


def _write_durable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        if os.name != "nt":
            try:
                dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


class StateStore:
    """Atomic durable state/evidence rooted outside task workspaces."""

    def __init__(self, control_root: str | os.PathLike[str], repo: str, issue: int):
        _repository_name({"repository": repo})
        if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
            raise PipelineError("invalid_issue_number")
        repo_component = base64.urlsafe_b64encode(repo.encode("utf-8")).decode("ascii").rstrip("=")
        self.root = Path(os.path.expanduser(os.path.expandvars(os.fspath(control_root)))).resolve()
        self.home = self.root / ("repo-" + repo_component) / str(issue)
        if not _within(self.home.resolve(), self.root):
            raise PipelineError("state_path_invalid")
        self.state_path = self.home / "state.json"
        self.events_path = self.home / "events.jsonl"

    def load(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PipelineError("state_unreadable") from exc
        if not isinstance(data, dict):
            raise PipelineError("state_shape_invalid")
        return data

    def save(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise PipelineError("state_shape_invalid")
        try:
            encoded = (json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise PipelineError("state_serialization_failed") from exc
        _write_durable(self.state_path, encoded)

    def event(self, event: str, **fields: Any) -> None:
        if not isinstance(event, str) or not event.strip():
            raise PipelineError("event_name_invalid")
        payload = {**fields, "event": event, "at": _now()}
        try:
            line = (json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise PipelineError("event_serialization_failed") from exc
        self.home.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.events_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)

    def evidence(self, name: str, payload: Any) -> str:
        if not isinstance(name, str) or not name:
            raise PipelineError("evidence_name_invalid")
        clean = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:72] or "evidence"
        suffix = hashlib.sha256(name.encode("utf-8", errors="replace")).hexdigest()[:12]
        extension = Path(clean).suffix
        stem = clean[:-len(extension)] if extension else clean
        path = self.home / "evidence" / f"{stem}-{suffix}{extension}"
        evidence_root = path.parent
        evidence_root.mkdir(parents=True, exist_ok=True)
        if not _within(evidence_root.resolve(), self.home.resolve()):
            raise PipelineError("evidence_path_invalid")
        if isinstance(payload, bytes):
            data = payload
        elif isinstance(payload, str):
            data = payload.encode("utf-8")
        else:
            try:
                data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise PipelineError("evidence_serialization_failed") from exc
        _write_durable(path, data)
        return str(path)

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        self.home.mkdir(parents=True, exist_ok=True)
        lock_path = self.home / "lock"
        stream = open(lock_path, "a+b")
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                except OSError as exc:
                    raise PipelineError("state_locked") from exc
            else:
                import fcntl

                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError as exc:
                    raise PipelineError("state_locked") from exc
            yield
        finally:
            if acquired:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()


def _git(root: Path, args: list[str], *, input_data: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            env={k: v for k, v in os.environ.items()
                 if k not in {"GITHUB_TOKEN", "GH_TOKEN", "SYMPHONY_ACCEPTANCE_GITHUB_TOKEN", "DEEPSEEK_API_KEY"}},
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise PipelineError("git_unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise PipelineError("git_timeout") from exc
    if result.returncode != 0:
        raise PipelineError("git_operation_failed")
    return result


def _names_from_nul(data: bytes) -> list[str]:
    return [os.fsdecode(item) for item in data.split(b"\0") if item]


def _listed_files(root: Path) -> list[str]:
    output = _git(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"]).stdout
    deleted = _git(root, ["diff", "--no-renames", "--name-only", "--diff-filter=D", "-z", "HEAD", "--"]).stdout
    return sorted(set(_names_from_nul(output)) | set(_names_from_nul(deleted)))


def fingerprint(root: str | os.PathLike[str]) -> str:
    """Hash Git-normalized tracked and untracked source, including deletions."""
    repo = Path(root).resolve()
    names = _listed_files(repo)
    regular: list[str] = []
    other: dict[str, str] = {}
    modes: dict[str, str] = {}
    for name in names:
        path = repo.joinpath(*name.split("/"))
        try:
            info = path.lstat()
        except FileNotFoundError:
            other[name] = "deleted"
            continue
        if stat.S_ISLNK(info.st_mode):
            other[name] = "120000:" + os.readlink(path)
        elif stat.S_ISREG(info.st_mode):
            regular.append(name)
            executable = os.name != "nt" and bool(info.st_mode & stat.S_IXUSR)
            modes[name] = "100755" if executable else "100644"
        else:
            other[name] = "unsafe-special-mode"

    blobs: dict[str, str] = {}
    for offset in range(0, len(regular), 64):
        batch = regular[offset:offset + 64]
        args = ["hash-object", "--", *batch]
        result = _git(repo, args).stdout.decode("ascii", errors="strict").splitlines()
        if len(result) != len(batch) or any(not re.fullmatch(r"[0-9a-f]{40,64}", value) for value in result):
            raise PipelineError("git_hash_failed")
        blobs.update(zip(batch, result))

    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update((modes.get(name, "") + blobs.get(name, other.get(name, ""))).encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
    return digest.hexdigest()


def changed_files(root: str | os.PathLike[str], base: str = "HEAD") -> list[str]:
    repo = Path(root).resolve()
    if not isinstance(base, str) or not base or "\x00" in base:
        raise PipelineError("comparison_base_invalid")
    diff = _git(repo, ["diff", "--no-renames", "--name-only", "-z", base, "--"]).stdout
    untracked = _git(repo, ["ls-files", "-z", "--others", "--exclude-standard"]).stdout
    names = set(_names_from_nul(diff)) | set(_names_from_nul(untracked))
    return sorted(name.replace("\\", "/") for name in names)


def _configured_protected(config: dict[str, Any]) -> list[str]:
    protected = config.get("protected_paths", [])
    if not isinstance(protected, list) or not all(_valid_pattern(value) for value in protected):
        raise PipelineError("protected_paths_invalid")
    for key in ("config_path", "checker_path"):
        value = config.get(key)
        if isinstance(value, str):
            root = Path(config.get("workspace_root", ".")).resolve()
            candidate = Path(value)
            try:
                relative = candidate.resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            if _valid_pattern(relative):
                protected.append(relative)
    checker_paths = config.get("checker_paths", [])
    if not isinstance(checker_paths, list) or not all(_valid_pattern(value) for value in checker_paths):
        raise PipelineError("checker_paths_invalid")
    return list(dict.fromkeys(protected + checker_paths))


def _safe_changed_path(repo: Path, name: str) -> bool:
    path = repo.joinpath(*name.split("/"))
    try:
        info = path.lstat()
    except FileNotFoundError:
        return True
    if stat.S_ISLNK(info.st_mode):
        try:
            resolved = path.resolve(strict=False)
        except (OSError, RuntimeError):
            return False
        return _within(resolved, repo)
    if not stat.S_ISREG(info.st_mode):
        return False
    if info.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
        return False
    try:
        return _within(path.resolve(strict=True), repo)
    except (OSError, RuntimeError):
        return False


def assert_scope(
    root: str | os.PathLike[str],
    plan: dict[str, Any],
    config: dict[str, Any],
    base: str = "HEAD",
) -> list[str]:
    repo = Path(root).resolve()
    patterns = plan.get("allowedPaths") if isinstance(plan, dict) else None
    if not isinstance(patterns, list) or not patterns or not all(_valid_pattern(pattern) for pattern in patterns):
        raise PipelineError("plan_paths_invalid")
    protected = _configured_protected(config)
    changed = changed_files(repo, base)
    for name in changed:
        if not _safe_changed_path(repo, name):
            raise PipelineError("unsafe_changed_file")
        if any(_matches(name, pattern) for pattern in protected):
            raise PipelineError("protected_path_changed")
        if not any(_matches(name, pattern) for pattern in patterns):
            raise PipelineError("changed_path_outside_plan")
    return changed
