"""Narrow GitHub API surface used by the Symphony acceptance controller.

The controller owns credential lookup.  This module accepts an explicit token
or a transport, so tests and callers never need to expose ambient credentials.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable


class APIError(RuntimeError):
    """A safe-to-report GitHub or local publication error."""

    def __init__(self, reason: str, transient: bool = False, status: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.transient = transient
        self.status = status


Transport = Callable[[str, str, dict[str, Any] | None], dict[str, Any] | list[Any]]


class GitHub:
    API_ROOT = "https://api.github.com"
    API_VERSION = "2022-11-28"
    ACCEPTANCE_CONTEXT = "symphony/acceptance"
    PAGE_SIZE = 100

    def __init__(
        self,
        repo: str,
        token: str | None = None,
        transport: Transport | None = None,
        base_branch: str = "main",
    ):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("repo must be an owner/name pair")
        self.repo = repo
        self.token = token
        self.transport = transport
        self.base_branch = self._validate_branch(base_branch)
        self._repo_api = f"/repos/{repo}"
        self._actor_login: str | None = None

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        """Perform one GitHub request and normalize failures without leaking secrets."""
        method = method.upper()
        if not path.startswith("/"):
            raise ValueError("GitHub API paths must start with '/'")
        try:
            if self.transport is not None:
                return self.transport(method, path, body)
            return self._urllib_request(method, path, body)
        except APIError:
            raise
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            transient = status == 429 or 500 <= status <= 599
            raise APIError(
                f"GitHub API returned HTTP {status} for {method} {path}",
                transient=transient,
                status=status,
            ) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
            # The exception text may contain a URL or request detail.  Keep the
            # durable error surface bounded and independent of credentials.
            kind = type(getattr(exc, "reason", exc)).__name__[:60]
            raise APIError(f"GitHub API network error ({kind})", transient=True) from None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise APIError(f"GitHub API returned invalid JSON ({type(exc).__name__})") from None

    def _urllib_request(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.API_VERSION,
            "User-Agent": "symphony-acceptance",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.API_ROOT + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
        if not payload:
            return {}
        return json.loads(payload.decode("utf-8"))

    def issue(self, number: int) -> dict[str, Any]:
        return self.request("GET", f"{self._repo_api}/issues/{self._number(number)}")

    def pr(self, number: int) -> dict[str, Any]:
        return self.request("GET", f"{self._repo_api}/pulls/{self._number(number)}")

    def base(self, branch: str = "main") -> str:
        sha = self.branch_head(branch)
        if sha is None:
            raise APIError(f"GitHub branch {branch!r} does not exist")
        return sha

    def branch_head(self, branch: str) -> str | None:
        path = f"{self._repo_api}/git/ref/heads/{self._branch_path(branch)}"
        try:
            response = self.request("GET", path)
        except APIError as exc:
            if exc.status == 404:
                return None
            raise
        if not isinstance(response, dict):
            raise APIError("GitHub returned an invalid branch reference")
        target = response.get("object", {}).get("sha")
        return target if isinstance(target, str) and target else None

    def comments(self, issue_number: int) -> list[dict[str, Any]]:
        path = f"{self._repo_api}/issues/{self._number(issue_number)}/comments"
        result: list[dict[str, Any]] = []
        page = 1
        while True:
            response = self.request("GET", f"{path}?per_page={self.PAGE_SIZE}&page={page}")
            items = self._page_items(response, None)
            result.extend(item for item in items if isinstance(item, dict))
            if len(items) < self.PAGE_SIZE:
                return result
            page += 1

    def set_labels(self, issue: int, add: list[str], remove: list[str]) -> list[str]:
        add_labels = self._managed_labels(add)
        remove_labels = self._managed_labels(remove)
        current_issue = self.issue(issue)
        current = {
            label.get("name") if isinstance(label, dict) else label
            for label in current_issue.get("labels", [])
        }
        current = {str(label) for label in current if label}

        to_add = [label for label in add_labels if label not in current]
        to_remove = [label for label in remove_labels if label in current and label not in add_labels]
        if to_add:
            self.request("POST", f"{self._repo_api}/issues/{self._number(issue)}/labels", {"labels": to_add})
        for label in to_remove:
            encoded = urllib.parse.quote(label, safe="")
            self.request("DELETE", f"{self._repo_api}/issues/{self._number(issue)}/labels/{encoded}")
        return sorted((current | set(to_add)) - set(to_remove))

    def status(self, sha: str, state: str, description: str, target_url: str | None = None) -> dict[str, Any]:
        if state not in {"error", "failure", "pending", "success"}:
            raise ValueError("state must be error, failure, pending, or success")
        body: dict[str, Any] = {
            "state": state,
            "context": self.ACCEPTANCE_CONTEXT,
            "description": str(description)[:140],
        }
        if target_url:
            body["target_url"] = target_url
        return self.request("POST", f"{self._repo_api}/statuses/{self._sha(sha)}", body)

    def draft(self, pr_number: int, draft: bool) -> bool:
        pull = self.pr(pr_number)
        if bool(pull.get("draft")) == bool(draft):
            return bool(draft)
        node_id = pull.get("node_id")
        if not node_id:
            raise APIError("GitHub pull request has no GraphQL node id")
        if draft:
            field = "convertPullRequestToDraft"
            mutation = (
                "mutation($id: ID!) { convertPullRequestToDraft(input: {pullRequestId: $id}) "
                "{ pullRequest { isDraft } } }"
            )
        else:
            field = "markPullRequestReadyForReview"
            mutation = (
                "mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id}) "
                "{ pullRequest { isDraft } } }"
            )
        result = self.request("POST", "/graphql", {"query": mutation, "variables": {"id": node_id}})
        if not isinstance(result, dict) or result.get("errors"):
            raise APIError("GitHub GraphQL draft update failed")
        try:
            return bool(result["data"][field]["pullRequest"]["isDraft"])
        except (KeyError, TypeError):
            raise APIError("GitHub GraphQL draft update returned an invalid response") from None

    def summary(
        self,
        issue_number: int,
        body: str,
        marker: str = "<!-- symphony:acceptance -->",
    ) -> int:
        return self._controlled_comment(issue_number, body, marker, update=True)

    def notify(self, issue_number: int, body: str, key: str) -> int:
        key = str(key).strip()
        if not key:
            raise ValueError("notification key must not be empty")
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        marker = f"<!-- symphony:acceptance:notify:{digest} -->"
        return self._controlled_comment(issue_number, body, marker, update=False)

    def ci(self, head_sha: str, required: list[str] | None = None) -> dict[str, Any]:
        sha = self._sha(head_sha)
        required_names = list(dict.fromkeys(required if required is not None else ["Build"]))
        if not required_names or any(not isinstance(name, str) or not name.strip() for name in required_names):
            raise ValueError("required check names must be nonempty strings")

        runs = self._get_pages(
            f"{self._repo_api}/commits/{sha}/check-runs",
            "check_runs",
        )
        statuses = self._get_pages(f"{self._repo_api}/commits/{sha}/statuses", None)
        evidence: list[dict[str, Any]] = []
        for run in runs:
            if run.get("head_sha") != sha:
                continue
            name = str(run.get("name", ""))
            if name == self.ACCEPTANCE_CONTEXT:
                continue
            status, conclusion = str(run.get("status", "")), run.get("conclusion")
            # Conditional jobs such as production deployment do not run on PRs.
            # A configured required check may never use skip as a passing result.
            if status == "completed" and conclusion == "skipped" and name not in required_names:
                continue
            if status == "completed" and conclusion == "success":
                state = "success"
            elif status == "completed" and conclusion in {
                "failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"
            }:
                state = "failure"
            else:
                state = "pending"
            evidence.append(
                {
                    "name": name,
                    "state": state,
                    "sha": sha,
                    "source": "check_run",
                    "updated_at": run.get("completed_at") or run.get("started_at") or run.get("updated_at") or run.get("created_at") or "",
                }
            )
        for status in statuses:
            if status.get("sha") not in (None, sha):
                continue
            context = str(status.get("context", ""))
            if context == self.ACCEPTANCE_CONTEXT:
                continue
            state_value = str(status.get("state", "")).lower()
            state = "success" if state_value == "success" else "failure" if state_value in {"failure", "error"} else "pending"
            evidence.append(
                {
                    "name": context,
                    "state": state,
                    "sha": sha,
                    "source": "commit_status",
                    "updated_at": status.get("updated_at") or status.get("created_at") or "",
                }
            )

        per_check: list[dict[str, str]] = []
        # Required names must exist; every other reported CI check must also pass.
        for name in dict.fromkeys(required_names + [item["name"] for item in evidence]):
            matches = [item for item in evidence if item["name"] == name]
            if not matches:
                state = "pending"
            else:
                latest_time = max(item["updated_at"] for item in matches)
                latest = [item for item in matches if item["updated_at"] == latest_time]
                latest_states = {item["state"] for item in latest}
                if latest_states == {"success"}:
                    state = "success"
                elif "failure" in latest_states:
                    state = "failure"
                else:
                    state = "pending"
            per_check.append({"name": name, "state": state})

        overall = "failure" if any(check["state"] == "failure" for check in per_check) else (
            "success" if all(check["state"] == "success" for check in per_check) else "pending"
        )
        return {
            "state": overall,
            "checks": [
                {key: value for key, value in item.items() if key != "updated_at"}
                for item in evidence
            ],
        }

    def publish(
        self,
        root: str | os.PathLike[str],
        base_sha: str,
        branch: str,
        expected_head: str | None,
        title: str,
        body: str,
        issue_number: int,
    ) -> dict[str, Any]:
        """Publish the base-relative worktree snapshot with guarded branch updates.

        The desired tree is assembled from ``base_sha`` plus every tracked
        worktree change and untracked non-ignored file.  This remains correct
        when the caller has already checked out or synchronized an earlier
        candidate as local HEAD.
        """
        base_sha = self._sha(base_sha)
        expected_head = self._sha(expected_head) if expected_head else None
        branch = self._validate_branch(branch)
        root_path = Path(root).resolve()
        if not root_path.is_dir():
            raise APIError("publication root is not a directory")

        base_commit = self.request("GET", f"{self._repo_api}/git/commits/{base_sha}")
        try:
            base_tree = base_commit["tree"]["sha"]
        except (KeyError, TypeError):
            raise APIError("GitHub returned an invalid base commit") from None
        tree_entries = self._snapshot_tree(root_path, base_sha)
        desired_tree = self.request("POST", f"{self._repo_api}/git/trees", {
            "base_tree": base_tree,
            "tree": tree_entries,
        })
        try:
            desired_tree_sha = self._sha(desired_tree["sha"])
        except (KeyError, TypeError):
            raise APIError("GitHub returned an invalid candidate tree") from None

        remote_head = self.branch_head(branch)
        if remote_head != expected_head:
            if remote_head and self._matches_candidate(remote_head, desired_tree_sha, expected_head, base_sha):
                published_head = remote_head
            else:
                raise APIError("acceptance branch changed outside the recorded publication")
        else:
            desired_parents = self._parents(base_sha, expected_head)
            if expected_head:
                current_commit = self.request("GET", f"{self._repo_api}/git/commits/{expected_head}")
                current_tree = current_commit.get("tree", {}).get("sha") if isinstance(current_commit, dict) else None
                base_is_ancestor = self._base_is_ancestor(base_sha, expected_head)
                if current_tree == desired_tree_sha and base_is_ancestor:
                    published_head = expected_head
                else:
                    published_head = self._create_commit_and_update(
                        branch, expected_head, base_sha, desired_tree_sha, desired_parents, issue_number
                    )
            else:
                base_tree_sha = base_tree
                if desired_tree_sha == base_tree_sha:
                    raise APIError("candidate contains no changes from the base")
                published_head = self._create_commit_and_update(
                    branch, None, base_sha, desired_tree_sha, desired_parents, issue_number
                )

        pull = self._find_pull(branch, self.base_branch)
        if pull is None and desired_tree_sha == base_tree:
            raise APIError("candidate contains no changes from the base")
        if pull is None:
            pull = self.request("POST", f"{self._repo_api}/pulls", {
                "title": title,
                "body": body,
                "head": branch,
                "base": self.base_branch,
                "draft": True,
            })
        try:
            number = int(pull["number"])
            url = str(pull.get("html_url") or "")
        except (KeyError, TypeError, ValueError):
            raise APIError("GitHub returned an invalid pull request") from None
        actual_head = pull.get("head", {}).get("sha") if isinstance(pull.get("head"), dict) else None
        if actual_head != published_head:
            raise APIError("GitHub pull request head does not match the published candidate")
        return {
            "number": number,
            "head_sha": actual_head,
            "base_sha": base_sha,
            "url": url,
            "branch": branch,
        }

    def _snapshot_tree(self, root: Path, base_sha: str) -> list[dict[str, Any]]:
        raw = self._git(root, "diff", "--raw", "-z", "--no-renames", "--no-ext-diff", base_sha, "--")
        changes = self._parse_raw_diff(raw)
        untracked_raw = self._git(root, "ls-files", "--others", "--exclude-standard", "-z")
        untracked = [part for part in untracked_raw.split(b"\0") if part]
        paths: dict[str, tuple[str, str] | None] = {}
        for encoded_path, modes in changes.items():
            path = self._decode_path(encoded_path)
            paths[path] = modes
        for encoded_path in untracked:
            path = self._decode_path(encoded_path)
            paths.setdefault(path, None)

        entries: list[dict[str, Any]] = []
        for path in sorted(paths):
            file_path = self._safe_worktree_path(root, path)
            modes = paths[path]
            old_mode, new_mode = modes if modes else (None, None)
            if old_mode == "120000" or new_mode == "120000" or (file_path.exists() and file_path.is_symlink()):
                raise APIError(f"symlink changes are not supported: {path}")
            if new_mode == "160000" or old_mode == "160000":
                raise APIError(f"submodule changes are not supported: {path}")
            if not file_path.exists():
                if old_mode is None:
                    # A race or an untracked file removed after inventory is not
                    # a deletion from the base snapshot.
                    continue
                entries.append({"path": path, "mode": old_mode, "type": "blob", "sha": None})
                continue
            if not file_path.is_file():
                raise APIError(f"publication path is not a regular file: {path}")
            mode = new_mode or old_mode
            if mode not in {"100644", "100755"}:
                mode = "100755" if file_path.stat().st_mode & stat.S_IXUSR else "100644"
            content = file_path.read_bytes()
            blob = self.request("POST", f"{self._repo_api}/git/blobs", {
                "content": base64.b64encode(content).decode("ascii"),
                "encoding": "base64",
            })
            try:
                blob_sha = self._sha(blob["sha"])
            except (KeyError, TypeError):
                raise APIError("GitHub returned an invalid blob") from None
            entries.append({"path": path, "mode": mode, "type": "blob", "sha": blob_sha})
        return entries

    def _create_commit_and_update(
        self,
        branch: str,
        expected_head: str | None,
        base_sha: str,
        tree_sha: str,
        parents: list[str],
        issue_number: int,
    ) -> str:
        # Recheck immediately before constructing the branch update.  GitHub's
        # non-force ref update remains the final race guard.
        if self.branch_head(branch) != expected_head:
            current = self.branch_head(branch)
            if current and self._matches_candidate(current, tree_sha, expected_head, base_sha):
                return current
            raise APIError("acceptance branch changed before publication")
        commit = self.request("POST", f"{self._repo_api}/git/commits", {
            "message": f"Symphony acceptance for issue #{self._number(issue_number)}\n\nCandidate tree {tree_sha}",
            "tree": tree_sha,
            "parents": parents,
        })
        try:
            commit_sha = self._sha(commit["sha"])
        except (KeyError, TypeError):
            raise APIError("GitHub returned an invalid commit") from None
        current = self.branch_head(branch)
        if current != expected_head:
            if current and self._matches_candidate(current, tree_sha, expected_head, base_sha):
                return current
            raise APIError("acceptance branch changed during publication")

        if expected_head is None:
            ref = self.request("POST", f"{self._repo_api}/git/refs", {
                "ref": f"refs/heads/{branch}",
                "sha": commit_sha,
            })
        else:
            ref = self.request("PATCH", f"{self._repo_api}/git/refs/heads/{self._branch_path(branch)}", {
                "sha": commit_sha,
                "force": False,
            })
        try:
            updated = self._sha(ref["object"]["sha"])
        except (KeyError, TypeError):
            raise APIError("GitHub returned an invalid updated branch reference") from None
        return updated

    def _matches_candidate(
        self,
        head_sha: str,
        tree_sha: str,
        expected_head: str | None,
        base_sha: str,
    ) -> bool:
        try:
            commit = self.request("GET", f"{self._repo_api}/git/commits/{self._sha(head_sha)}")
        except APIError:
            return False
        if not isinstance(commit, dict) or commit.get("tree", {}).get("sha") != tree_sha:
            return False
        raw_parents = commit.get("parents", [])
        actual_parents = [parent.get("sha") for parent in raw_parents if isinstance(parent, dict)]
        if expected_head is None:
            expected_parents = [base_sha]
        else:
            expected_parents = [expected_head]
            if not self._base_is_ancestor(base_sha, expected_head):
                expected_parents.append(base_sha)
        return actual_parents == expected_parents

    def _parents(self, base_sha: str, expected_head: str | None) -> list[str]:
        if expected_head is None:
            return [base_sha]
        parents = [expected_head]
        if not self._base_is_ancestor(base_sha, expected_head):
            parents.append(base_sha)
        return parents

    def _base_is_ancestor(self, base_sha: str, head_sha: str) -> bool:
        if base_sha == head_sha:
            return True
        comparison = self.request(
            "GET",
            f"{self._repo_api}/compare/{self._sha(base_sha)}...{self._sha(head_sha)}",
        )
        return isinstance(comparison, dict) and comparison.get("status") in {"ahead", "identical"}

    def _find_pull(self, branch: str, base_branch: str) -> dict[str, Any] | None:
        owner = self.repo.split("/", 1)[0]
        query_head = urllib.parse.quote(f"{owner}:{branch}", safe="")
        query_base = urllib.parse.quote(base_branch, safe="")
        path = f"{self._repo_api}/pulls?state=all&head={query_head}&base={query_base}"
        for pull in self._get_pages(path, None):
            if (
                isinstance(pull, dict)
                and pull.get("head", {}).get("ref") == branch
                and pull.get("base", {}).get("ref") == base_branch
            ):
                return pull
        return None

    def _get_pages(self, path: str, key: str | None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        page = 1
        separator = "&" if "?" in path else "?"
        while True:
            response = self.request("GET", f"{path}{separator}per_page={self.PAGE_SIZE}&page={page}")
            items = self._page_items(response, key)
            result.extend(item for item in items if isinstance(item, dict))
            if len(items) < self.PAGE_SIZE:
                return result
            page += 1

    @staticmethod
    def _page_items(response: Any, key: str | None) -> list[Any]:
        if isinstance(response, list):
            return response
        if isinstance(response, dict):
            if key is None:
                raise APIError("GitHub returned an invalid list response")
            items = response.get(key)
            if isinstance(items, list):
                return items
        raise APIError("GitHub returned an invalid list response")

    def _controlled_comment(self, issue_number: int, body: str, marker: str, update: bool) -> int:
        if not marker or "\n" in marker or "\r" in marker:
            raise ValueError("comment marker must be one nonempty line")
        content = str(body)
        marked_body = content if content.splitlines()[:1] == [marker] else f"{marker}\n\n{content}"
        if self._actor_login is None:
            actor = self.request("GET", "/user")
            login = actor.get("login") if isinstance(actor, dict) else None
            if not isinstance(login, str) or not login:
                raise APIError("GitHub authenticated comment author unavailable")
            self._actor_login = login.casefold()
        controlled = [
            comment for comment in self.comments(issue_number)
            if self._first_line(comment.get("body", "")) == marker and isinstance(comment.get("id"), int)
            and str((comment.get("user") or {}).get("login", "")).casefold() == self._actor_login
        ]
        if controlled:
            canonical = min(controlled, key=lambda comment: int(comment["id"]))
            comment_id = int(canonical["id"])
            if update and canonical.get("body") != marked_body:
                self.request("PATCH", f"{self._repo_api}/issues/comments/{comment_id}", {"body": marked_body})
            return comment_id
        created = self.request("POST", f"{self._repo_api}/issues/{self._number(issue_number)}/comments", {"body": marked_body})
        try:
            return int(created["id"])
        except (KeyError, TypeError, ValueError):
            raise APIError("GitHub returned an invalid issue comment") from None

    @staticmethod
    def _first_line(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        lines = value.splitlines()
        return lines[0].strip() if lines else ""

    @staticmethod
    def _managed_labels(labels: Iterable[str]) -> list[str]:
        normalized = list(dict.fromkeys(str(label).strip() for label in labels))
        if any(not label.startswith("symphony:") for label in normalized):
            raise ValueError("only symphony: labels are managed by this client")
        if any(not label or any(ord(char) < 32 for char in label) for label in normalized):
            raise ValueError("label names must be nonempty printable strings")
        return normalized

    @staticmethod
    def _number(value: int) -> int:
        number = int(value)
        if number < 1:
            raise ValueError("GitHub issue and PR numbers must be positive")
        return number

    @staticmethod
    def _sha(value: Any) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", value):
            raise ValueError("expected a 40-character Git SHA")
        return value.lower()

    @staticmethod
    def _branch_path(branch: str) -> str:
        return urllib.parse.quote(GitHub._validate_branch(branch), safe="/")

    @staticmethod
    def _validate_branch(branch: str) -> str:
        if not isinstance(branch, str) or not branch or branch.startswith("/") or branch.endswith("/"):
            raise ValueError("invalid branch name")
        if any(part in {"", ".", ".."} for part in branch.split("/")) or any(ord(c) < 32 for c in branch):
            raise ValueError("invalid branch name")
        if "\\" in branch or "?" in branch or "#" in branch or ".." in branch:
            raise ValueError("invalid branch name")
        return branch

    @staticmethod
    def _decode_path(path: bytes) -> str:
        try:
            decoded = path.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise APIError("publication path is not UTF-8") from None
        pure = Path(decoded)
        if not decoded or pure.is_absolute() or "\\" in decoded or any(part in {"", ".", ".."} for part in decoded.split("/")):
            raise APIError("publication contains an unsafe file path")
        return decoded

    @staticmethod
    def _safe_worktree_path(root: Path, relative: str) -> Path:
        result = root.joinpath(*relative.split("/"))
        try:
            result.resolve(strict=False).relative_to(root)
        except ValueError:
            raise APIError(f"publication path escapes its root: {relative}") from None
        current = root
        for part in relative.split("/"):
            current = current / part
            if current.is_symlink():
                raise APIError(f"symlink changes are not supported: {relative}")
        return result

    @staticmethod
    def _parse_raw_diff(raw: bytes) -> dict[bytes, tuple[str | None, str | None]]:
        parts = raw.split(b"\0")
        result: dict[bytes, tuple[str | None, str | None]] = {}
        index = 0
        try:
            while index < len(parts) and parts[index]:
                header = parts[index].decode("ascii")
                index += 1
                raw_path = parts[index]
                index += 1
                fields = header.split()
                if len(fields) < 5 or not fields[0].startswith(":"):
                    raise ValueError("malformed raw diff")
                old_mode = fields[0][1:]
                new_mode = fields[1]
                result[raw_path] = (
                    old_mode if old_mode != "000000" else None,
                    new_mode if new_mode != "000000" else None,
                )
        except (IndexError, UnicodeDecodeError, ValueError):
            raise APIError("local Git returned an invalid diff") from None
        return result

    @staticmethod
    def _git(root: Path, *args: str) -> bytes:
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), *args],
                env={k: v for k, v in os.environ.items()
                     if k not in {"GITHUB_TOKEN", "GH_TOKEN", "SYMPHONY_ACCEPTANCE_GITHUB_TOKEN", "DEEPSEEK_API_KEY"}},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as exc:
            raise APIError(f"could not run local Git ({type(exc).__name__})") from None
        if completed.returncode != 0:
            raise APIError(f"local Git command failed with exit code {completed.returncode}")
        return completed.stdout


def parse_command(comment: dict[str, Any], allowed_users: Iterable[str]) -> dict[str, Any] | None:
    """Parse a narrow user-authored command from a GitHub issue comment.

    Commands must occupy a top-level Markdown line. Quoted text, fenced code,
    HTML comments, malformed forms, bot-authored comments, and users outside
    the explicit allowlist are ignored.
    """
    user = comment.get("user") if isinstance(comment, dict) else None
    if not isinstance(user, dict):
        return None
    login = user.get("login")
    if not isinstance(login, str) or str(user.get("type", "")).casefold() == "bot":
        return None
    names = [allowed_users] if isinstance(allowed_users, str) else allowed_users
    allowed = {str(name).casefold() for name in names if isinstance(name, str)}
    if login.casefold() not in allowed:
        return None
    identifier = comment.get("id")
    try:
        comment_id = int(identifier)
    except (TypeError, ValueError):
        return None
    if comment_id < 1:
        return None
    body = comment.get("body")
    if not isinstance(body, str):
        return None

    found: list[tuple[str, str | None]] = []
    fence_char: str | None = None
    fence_length = 0
    in_html_comment = False
    in_quote = False
    for line in body.splitlines():
        if in_quote:
            if line.startswith(">") or line.strip():
                continue
            in_quote = False
        if re.match(r"^ {0,3}>", line):
            in_quote = True
            continue
        if in_html_comment:
            if "-->" in line:
                in_html_comment = False
            continue
        if "<!--" in line:
            if "-->" not in line.split("<!--", 1)[1]:
                in_html_comment = True
            continue

        fence = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence_char is not None:
            closing = re.match(r"^ {0,3}(`{3,}|~{3,})[ \t]*$", line)
            if closing and closing.group(1)[0] == fence_char and len(closing.group(1)) >= fence_length:
                fence_char = None
                fence_length = 0
            continue
        if fence:
            fence_char = fence.group(1)[0]
            fence_length = len(fence.group(1))
            continue
        if line == "/symphony resume":
            found.append(("resume", None))
            continue
        match = re.fullmatch(r"/symphony rework[ \t]+(.+?)\s*", line)
        if match:
            reason = match.group(1).strip()
            if reason:
                found.append(("rework", reason))

    if len(found) != 1:
        return None
    action, reason = found[0]
    return {"action": action, "reason": reason, "id": comment_id}
