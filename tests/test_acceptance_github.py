import base64
import hashlib
import json
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from urllib.parse import unquote, urlsplit

from scripts.symphony_acceptance.github import APIError, GitHub, parse_command


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.decode("utf-8").strip()


def git_sha(kind: str, payload: bytes) -> str:
    return hashlib.sha1(f"{kind} {len(payload)}\0".encode() + payload).hexdigest()


class FakeGitHub:
    def __init__(self, base_sha: str, base_tree: str):
        self.commits = {base_sha: {"tree": base_tree, "parents": []}}
        self.trees = {base_tree: []}
        self.branches = {"main": base_sha}
        self.pulls = []
        self.comments_by_issue = {}
        self.next_comment_id = 1
        self.labels = {3: ["symphony:ready", "bug"]}
        self.check_runs = []
        self.statuses = []
        self.calls = []
        self.next_pr = 10
        self.tamper_pull_head = False

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        parsed = urlsplit(path)
        route = parsed.path
        query = parsed.query
        prefix = "/repos/o/r"

        if route == "/user":
            return {"login": "app"}

        if route == "/graphql":
            field = "convertPullRequestToDraft" if "convertPullRequestToDraft" in body["query"] else "markPullRequestReadyForReview"
            desired = field == "convertPullRequestToDraft"
            node_id = body["variables"]["id"]
            for pull in self.pulls:
                if pull.get("node_id") == node_id:
                    pull["draft"] = desired
            return {"data": {field: {"pullRequest": {"isDraft": desired}}}}
        if route.startswith(prefix + "/git/ref/heads/"):
            branch = unquote(route.split(prefix + "/git/ref/heads/", 1)[1])
            return self._ref(branch)
        if route.startswith(prefix + "/git/refs/heads/") and method == "PATCH":
            branch = unquote(route.split(prefix + "/git/refs/heads/", 1)[1])
            commit = self.commits[body["sha"]]
            if self.branches.get(branch) not in commit["parents"]:
                raise APIError("not fast-forward", status=422)
            self.branches[branch] = body["sha"]
            return self._ref(branch)
        if route == prefix + "/git/refs" and method == "POST":
            branch = body["ref"].removeprefix("refs/heads/")
            if branch in self.branches:
                raise APIError("already exists", status=422)
            self.branches[branch] = body["sha"]
            return self._ref(branch)
        if route.startswith(prefix + "/git/commits/") and method == "GET":
            sha = route.rsplit("/", 1)[1]
            commit = self.commits.get(sha)
            if not commit:
                raise APIError("missing commit", status=404)
            return self._commit_response(sha, commit)
        if route == prefix + "/git/commits" and method == "POST":
            data = json.dumps(body, sort_keys=True).encode()
            sha = hashlib.sha1(data).hexdigest()
            self.commits[sha] = {"tree": body["tree"], "parents": list(body["parents"])}
            return {"sha": sha}
        if route == prefix + "/git/blobs" and method == "POST":
            content = base64.b64decode(body["content"])
            return {"sha": git_sha("blob", content)}
        if route == prefix + "/git/trees" and method == "POST":
            if not body["tree"]:
                return {"sha": body["base_tree"]}
            serial = json.dumps([body["base_tree"], body["tree"]], sort_keys=True).encode()
            sha = hashlib.sha1(serial).hexdigest()
            self.trees[sha] = body["tree"]
            return {"sha": sha}
        if route.startswith(prefix + "/compare/"):
            compare = route.split(prefix + "/compare/", 1)[1]
            base_sha, head_sha = compare.split("...", 1)
            return {"status": "ahead" if base_sha in self._ancestors(head_sha) else "diverged"}
        if route == prefix + "/pulls" and method == "GET":
            owner_branch = None
            base = None
            for pair in query.split("&"):
                key, _, value = pair.partition("=")
                if key == "head":
                    owner_branch = unquote(value).split(":", 1)[-1]
                elif key == "base":
                    base = unquote(value)
            matches = [pull for pull in self.pulls if pull["head"]["ref"] == owner_branch and pull["base"]["ref"] == base]
            for pull in matches:
                pull["head"]["sha"] = self.branches[pull["head"]["ref"]]
            return matches
        if route == prefix + "/pulls" and method == "POST":
            pull = {
                "number": self.next_pr,
                "html_url": f"https://github.com/o/r/pull/{self.next_pr}",
                "head": {"ref": body["head"], "sha": "f" * 40 if self.tamper_pull_head else self.branches[body["head"]]},
                "base": {"ref": body["base"]},
                "draft": body.get("draft", False),
                "node_id": f"PR_{self.next_pr}",
            }
            self.next_pr += 1
            self.pulls.append(pull)
            return pull
        if route.startswith(prefix + "/issues/") and route.endswith("/comments") and method == "GET":
            issue = int(route.split("/")[-2])
            page = int(dict(pair.split("=", 1) for pair in query.split("&"))["page"])
            start = (page - 1) * 100
            return self.comments_by_issue.get(issue, [])[start:start + 100]
        if route.startswith(prefix + "/issues/") and route.endswith("/comments") and method == "POST":
            issue = int(route.split("/")[-2])
            comment = {"id": self.next_comment_id, "body": body["body"], "user": {"login": "app", "type": "Bot"}}
            self.next_comment_id += 1
            self.comments_by_issue.setdefault(issue, []).append(comment)
            return comment
        if route.startswith(prefix + "/issues/comments/") and method == "PATCH":
            comment_id = int(route.rsplit("/", 1)[1])
            for comments in self.comments_by_issue.values():
                for comment in comments:
                    if comment["id"] == comment_id:
                        comment["body"] = body["body"]
                        return comment
        if route.startswith(prefix + "/issues/") and method == "GET":
            issue = int(route.rsplit("/", 1)[1])
            return {"number": issue, "labels": [{"name": label} for label in self.labels.get(issue, [])]}
        if route.startswith(prefix + "/issues/") and route.endswith("/labels") and method == "POST":
            issue = int(route.split("/")[-2])
            self.labels[issue] = list(dict.fromkeys(self.labels.get(issue, []) + body["labels"]))
            return [{"name": label} for label in self.labels[issue]]
        if "/issues/" in route and "/labels/" in route and method == "DELETE":
            issue = int(route.split("/issues/", 1)[1].split("/", 1)[0])
            label = unquote(route.rsplit("/", 1)[1])
            self.labels[issue] = [item for item in self.labels.get(issue, []) if item != label]
            return {}
        if route.startswith(prefix + "/commits/") and route.endswith("/check-runs"):
            page = int(dict(pair.split("=", 1) for pair in query.split("&"))["page"])
            start = (page - 1) * 100
            return {"check_runs": self.check_runs[start:start + 100]}
        if route.startswith(prefix + "/commits/") and route.endswith("/statuses"):
            page = int(dict(pair.split("=", 1) for pair in query.split("&"))["page"])
            start = (page - 1) * 100
            return self.statuses[start:start + 100]
        if route.startswith(prefix + "/statuses/") and method == "POST":
            return {"sha": route.rsplit("/", 1)[1], **body}
        raise AssertionError(f"unexpected fake GitHub request: {method} {path}")

    def _ref(self, branch):
        sha = self.branches.get(branch)
        if sha is None:
            raise APIError("missing ref", status=404)
        return {"ref": f"refs/heads/{branch}", "object": {"sha": sha}}

    def _commit_response(self, sha, commit):
        return {"sha": sha, "tree": {"sha": commit["tree"]}, "parents": [{"sha": parent} for parent in commit["parents"]]}

    def _ancestors(self, sha):
        seen = set()
        stack = [sha]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(self.commits.get(current, {}).get("parents", []))
        return seen


class GitHubAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        run_git(self.root, "init", "-b", "main")
        run_git(self.root, "config", "user.name", "Acceptance Test")
        run_git(self.root, "config", "user.email", "acceptance@example.invalid")
        (self.root / "keep.txt").write_text("before\n", encoding="utf-8")
        (self.root / "keep.txt").chmod(0o755)
        (self.root / "remove.txt").write_text("remove\n", encoding="utf-8")
        run_git(self.root, "add", "keep.txt", "remove.txt")
        run_git(self.root, "update-index", "--chmod=+x", "keep.txt")
        run_git(self.root, "commit", "-m", "base")
        self.base_sha = run_git(self.root, "rev-parse", "HEAD")
        self.base_tree = run_git(self.root, "rev-parse", "HEAD^{tree}")
        self.fake = FakeGitHub(self.base_sha, self.base_tree)
        self.client = GitHub("o/r", transport=self.fake)

    def tearDown(self):
        self.temp.cleanup()

    def change_worktree(self):
        (self.root / "keep.txt").write_text("after\n", encoding="utf-8")
        (self.root / "remove.txt").unlink()
        (self.root / "added.bin").write_bytes(b"\x00\xffbinary\n")

    def test_publish_bases_tree_on_supplied_sha_handles_delete_binary_and_is_idempotent(self):
        self.change_worktree()
        result = self.client.publish(
            self.root, self.base_sha, "symphony/issue-3", None,
            "Acceptance", "Automated acceptance", 3,
        )
        self.assertEqual(result["base_sha"], self.base_sha)
        self.assertEqual(result["number"], 10)
        self.assertEqual(len(self.fake.commits), 2)
        candidate_sha = result["head_sha"]
        tree_sha = self.fake.commits[candidate_sha]["tree"]
        entries = {entry["path"]: entry for entry in self.fake.trees[tree_sha]}
        self.assertIsNone(entries["remove.txt"]["sha"])
        self.assertEqual(entries["added.bin"]["mode"], "100644")
        self.assertEqual(entries["keep.txt"]["mode"], "100755")
        blob_request = next(body for method, path, body in self.fake.calls if path.endswith("/git/blobs") and body["encoding"] == "base64" and base64.b64decode(body["content"]) == b"\x00\xffbinary\n")
        self.assertEqual(base64.b64decode(blob_request["content"]), b"\x00\xffbinary\n")

        # Simulate a parent aligning local HEAD to the published candidate.
        # The helper still derives the desired tree from base_sha.
        run_git(self.root, "add", "-A")
        run_git(self.root, "commit", "-m", "local acceptance snapshot")
        again = self.client.publish(
            self.root, self.base_sha, "symphony/issue-3", candidate_sha,
            "Acceptance", "Updated body", 3,
        )
        self.assertEqual(again["head_sha"], candidate_sha)
        self.assertEqual(again["number"], 10)
        self.assertEqual(len(self.fake.commits), 2)
        self.assertEqual(len(self.fake.pulls), 1)

    def test_publish_retry_reconciles_remote_commit_after_timeout(self):
        self.change_worktree()
        base_transport = self.fake
        timed_out = {"value": False}

        def transport(method, path, body):
            result = base_transport(method, path, body)
            if method == "POST" and path.endswith("/git/refs") and not timed_out["value"]:
                timed_out["value"] = True
                raise TimeoutError("response was lost")
            return result

        client = GitHub("o/r", transport=transport)
        with self.assertRaises(APIError) as raised:
            client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3)
        self.assertTrue(raised.exception.transient)
        head = self.fake.branches["symphony/issue-3"]
        retried = client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3)
        self.assertEqual(retried["head_sha"], head)
        self.assertEqual(len(self.fake.commits), 2)
        self.assertEqual(len(self.fake.pulls), 1)

    def test_publish_rejects_an_unrecognized_external_branch_advance(self):
        self.fake.branches["symphony/issue-3"] = self.base_sha
        self.change_worktree()
        external_sha = "e" * 40
        self.fake.commits[external_sha] = {"tree": "f" * 40, "parents": [self.base_sha]}
        self.fake.branches["symphony/issue-3"] = external_sha
        with self.assertRaisesRegex(APIError, "changed outside"):
            self.client.publish(self.root, self.base_sha, "symphony/issue-3", self.base_sha, "T", "B", 3)
        self.assertEqual(len(self.fake.pulls), 0)

    def test_publish_uses_configured_base_branch_and_checks_pr_head(self):
        self.change_worktree()
        self.fake.branches["release"] = self.base_sha
        client = GitHub("o/r", transport=self.fake, base_branch="release")
        result = client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3)
        self.assertEqual(self.fake.pulls[0]["base"]["ref"], "release")
        self.assertEqual(result["head_sha"], self.fake.branches["symphony/issue-3"])

        other_fake = FakeGitHub(self.base_sha, self.base_tree)
        other_fake.tamper_pull_head = True
        other = GitHub("o/r", transport=other_fake, base_branch="release")
        other_fake.branches["release"] = self.base_sha
        with self.assertRaisesRegex(APIError, "head does not match"):
            other.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3)

    def test_base_advance_is_added_as_second_parent(self):
        self.fake.branches["symphony/issue-3"] = self.base_sha
        self.change_worktree()
        first = self.client.publish(
            self.root, self.base_sha, "symphony/issue-3", self.base_sha, "T", "B", 3,
        )
        run_git(self.root, "add", "-A")
        run_git(self.root, "commit", "-m", "align local candidate")
        local_candidate = run_git(self.root, "rev-parse", "HEAD")
        local_candidate_tree = run_git(self.root, "rev-parse", "HEAD^{tree}")
        self.fake.commits[local_candidate] = {"tree": local_candidate_tree, "parents": [self.base_sha]}
        (self.root / "main-only.txt").write_text("new base content\n", encoding="utf-8")
        run_git(self.root, "add", "main-only.txt")
        run_git(self.root, "commit", "-m", "advance main")
        base2 = run_git(self.root, "rev-parse", "HEAD")
        base2_tree = run_git(self.root, "rev-parse", "HEAD^{tree}")
        self.fake.commits[base2] = {"tree": base2_tree, "parents": [local_candidate]}
        self.fake.branches["main"] = base2

        result = self.client.publish(
            self.root, base2, "symphony/issue-3", first["head_sha"], "T", "B", 3,
        )
        parents = self.fake.commits[result["head_sha"]]["parents"]
        self.assertEqual(parents, [first["head_sha"], base2])
        self.assertEqual(result["number"], first["number"])

    def test_comments_paginate_and_summary_and_terminal_notify_are_deduplicated(self):
        self.fake.comments_by_issue[3] = [
            {"id": i + 1, "body": f"user comment {i}", "user": {"login": "human", "type": "User"}}
            for i in range(101)
        ]
        self.fake.next_comment_id = 102
        comments = self.client.comments(3)
        self.assertEqual(len(comments), 101)
        comment_id = self.client.summary(3, "first summary")
        self.assertEqual(comment_id, 102)
        self.assertEqual(self.client.summary(3, "revised summary"), comment_id)
        self.assertIn("revised summary", self.fake.comments_by_issue[3][-1]["body"])
        notify_id = self.client.notify(3, "finished", "run-42")
        self.assertEqual(self.client.notify(3, "ignored duplicate", "run-42"), notify_id)
        self.assertEqual(len(self.fake.comments_by_issue[3]), 103)

    def test_set_labels_only_changes_managed_labels_and_is_idempotent(self):
        labels = self.client.set_labels(3, ["symphony:done"], ["symphony:ready"])
        self.assertEqual(labels, ["bug", "symphony:done"])
        calls = len(self.fake.calls)
        self.assertEqual(self.client.set_labels(3, ["symphony:done"], ["symphony:ready"]), labels)
        new_label_requests = [call for call in self.fake.calls[calls:] if call[1].endswith("/labels") or "/labels/" in call[1]]
        self.assertEqual(new_label_requests, [])
        with self.assertRaises(ValueError):
            self.client.set_labels(3, ["bug"], [])

    def test_ci_requires_exact_named_success_and_ignores_own_status(self):
        sha = self.base_sha
        self.fake.check_runs = [
            {"name": "Build", "head_sha": sha, "status": "completed", "conclusion": "success", "completed_at": "2026-09-27T01:00:00Z"},
            {"name": "Build", "head_sha": "0" * 40, "status": "completed", "conclusion": "failure", "completed_at": "2026-09-27T02:00:00Z"},
        ]
        self.fake.statuses = [
            {"context": "symphony/acceptance", "sha": sha, "state": "failure", "created_at": "2026-09-27T03:00:00Z"},
            {"context": "Lint", "sha": sha, "state": "success", "created_at": "2026-09-27T03:00:00Z"},
        ]
        result = self.client.ci(sha, required=["Build", "Lint", "Package"])
        self.assertEqual(result["state"], "pending")
        self.assertNotIn("symphony/acceptance", [check["name"] for check in result["checks"]])
        self.assertNotIn("0" * 40, [check["sha"] for check in result["checks"]])
        self.fake.check_runs[0]["conclusion"] = "failure"
        self.assertEqual(self.client.ci(sha, required=["Build"])["state"], "failure")

    def test_optional_deploy_skipped_is_not_ci_but_required_skipped_cannot_pass(self):
        sha = self.base_sha
        self.fake.check_runs = [
            {"name": "Build", "head_sha": sha, "status": "completed", "conclusion": "success"},
            {"name": "Deploy", "head_sha": sha, "status": "completed", "conclusion": "skipped"},
        ]
        self.assertEqual(self.client.ci(sha, required=["Build"])["state"], "success")
        self.assertEqual(self.client.ci(sha, required=["Build", "Deploy"])["state"], "pending")

    def test_ci_rerun_pending_invalidates_old_success_and_extra_failure_blocks(self):
        sha = self.base_sha
        self.fake.check_runs = [
            {"name": "Build", "head_sha": sha, "status": "completed", "conclusion": "success", "completed_at": "2026-09-27T01:00:00Z"},
            {"name": "Build", "head_sha": sha, "status": "in_progress", "conclusion": None, "started_at": "2026-09-27T02:00:00Z"},
        ]
        self.assertEqual(self.client.ci(sha, required=["Build"])["state"], "pending")
        self.fake.check_runs.pop()
        self.fake.statuses = [{"context": "Security", "sha": sha, "state": "failure", "created_at": "2026-09-27T03:00:00Z"}]
        self.assertEqual(self.client.ci(sha, required=["Build"])["state"], "failure")

    def test_foreign_comment_marker_cannot_capture_summary_or_suppress_notification(self):
        foreign = {"id": 999, "body": "<!-- symphony:acceptance -->\n\nspoofed", "user": {"login": "stranger"}}
        self.fake.comments_by_issue[3] = [foreign]
        created = self.client.summary(3, "real progress")
        self.assertNotEqual(created, 999)
        self.assertEqual(foreign["body"], "<!-- symphony:acceptance -->\n\nspoofed")
        self.assertEqual(self.client.summary(3, "updated progress"), created)
        key = "a-cycle:ready"
        digest = hashlib.sha256(key.encode()).hexdigest()[:24]
        self.fake.comments_by_issue[3].append({"id": 1000, "body": f"<!-- symphony:acceptance:notify:{digest} -->", "user": {"login": "stranger"}})
        self.assertNotEqual(self.client.notify(3, "passed", key), 1000)

    def test_draft_uses_graphql_and_is_safe_when_already_in_requested_state(self):
        pull = {"number": 7, "draft": False, "node_id": "PR_7"}
        self.fake.pulls.append(pull)

        def lookup_pr(number):
            return next(item for item in self.fake.pulls if item["number"] == number)

        client = self.client
        original = client.pr
        client.pr = lookup_pr
        self.assertTrue(client.draft(7, True))
        self.assertFalse(client.draft(7, False))
        self.assertEqual(sum(path == "/graphql" for _, path, _ in self.fake.calls), 2)
        client.pr = original

    def test_command_parser_rejects_bots_quotes_code_and_untrusted_users(self):
        allowed = {"oil"}
        valid = {"id": 91, "user": {"login": "oil", "type": "User"}, "body": "/symphony rework Please fix the failing Build check"}
        self.assertEqual(parse_command(valid, allowed), {
            "action": "rework", "reason": "Please fix the failing Build check", "id": 91,
        })
        resume = {**valid, "body": "Some prose\n/symphony resume\nThanks"}
        self.assertEqual(parse_command(resume, allowed), {"action": "resume", "reason": None, "id": 91})
        for body in (
            "> /symphony resume",
            "> quoted text\n/symphony resume",
            "```text\n/symphony resume\n```",
            "Read /symphony resume later",
            "/symphony rework   ",
            "/symphony resume now",
            "  /symphony resume",
        ):
            self.assertIsNone(parse_command({**valid, "body": body}, allowed), body)
        self.assertIsNone(parse_command({**valid, "user": {"login": "oil", "type": "Bot"}}, allowed))
        self.assertIsNone(parse_command({**valid, "user": {"login": "stranger", "type": "User"}}, allowed))

    def test_http_transient_classification_is_limited_to_network_429_and_5xx(self):
        def failing(status):
            def transport(method, path, body):
                raise urllib.error.HTTPError(path, status, "error", {}, None)
            return GitHub("o/r", transport=transport)

        for status, expected in ((429, True), (503, True), (403, False)):
            with self.subTest(status=status), self.assertRaises(APIError) as raised:
                failing(status).request("GET", "/repos/o/r")
            self.assertEqual(raised.exception.transient, expected)


if __name__ == "__main__":
    unittest.main()
