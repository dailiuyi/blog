import base64
import hashlib
import json
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

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
        self.issues = []
        self.events_by_issue = {}
        self.last_edited_response = {
            "data": {"repository": {"issue": {"lastEditedAt": None}}},
        }
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
            if "lastEditedAt" in body["query"]:
                return self.last_edited_response
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
        if route == prefix + "/issues" and method == "GET":
            parameters = parse_qs(query)
            page = int(parameters["page"][0])
            per_page = int(parameters["per_page"][0])
            start = (page - 1) * per_page
            return self.issues[start:start + per_page]
        if route.startswith(prefix + "/issues/") and route.endswith("/events") and method == "GET":
            issue = int(route.split("/")[-2])
            parameters = parse_qs(query)
            page = int(parameters["page"][0])
            per_page = int(parameters["per_page"][0])
            start = (page - 1) * per_page
            return self.events_by_issue.get(issue, [])[start:start + per_page]
        if route.startswith(prefix + "/pulls/") and method == "GET":
            number = int(route.rsplit("/", 1)[1])
            pull = next(item for item in self.pulls if item["number"] == number)
            if not self.tamper_pull_head:
                pull["head"]["sha"] = self.branches[pull["head"]["ref"]]
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
        journal = []
        with self.assertRaises(APIError) as raised:
            client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3,
                           on_candidate=journal.append)
        self.assertTrue(raised.exception.transient)
        head = self.fake.branches["symphony/issue-3"]
        self.assertEqual(journal, [head])
        retried = client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3,
                                 candidate_sha=journal[-1], on_candidate=journal.append)
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

    def test_publish_refreshes_exact_pr_when_list_head_lags_branch_update(self):
        self.change_worktree()
        first = self.client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3)
        (self.root / "keep.txt").write_text("second candidate\n", encoding="utf-8")

        def transport(method, path, body):
            result = self.fake(method, path, body)
            if method == "GET" and path.startswith("/repos/o/r/pulls?"):
                result = json.loads(json.dumps(result))
                result[0]["head"]["sha"] = first["head_sha"]
            return result

        client = GitHub("o/r", transport=transport)
        result = client.publish(self.root, self.base_sha, "symphony/issue-3", first["head_sha"], "T", "B", 3)
        self.assertEqual(result["head_sha"], self.fake.branches["symphony/issue-3"])
        self.assertNotEqual(result["head_sha"], first["head_sha"])
        self.assertEqual(sum(method == "GET" and path == "/repos/o/r/pulls/10"
                             for method, path, _ in self.fake.calls), 1)

    def test_publish_pr_head_lag_is_transient_and_retry_reuses_published_commit(self):
        self.change_worktree()
        first = self.client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3)
        (self.root / "keep.txt").write_text("second candidate\n", encoding="utf-8")
        lagging = True

        def transport(method, path, body):
            result = self.fake(method, path, body)
            if lagging and method == "GET" and path.startswith("/repos/o/r/pulls"):
                result = json.loads(json.dumps(result))
                pull = result[0] if isinstance(result, list) else result
                pull["head"]["sha"] = first["head_sha"]
            return result

        client = GitHub("o/r", transport=transport)
        journal = []
        with self.assertRaisesRegex(APIError, "head does not match") as raised:
            client.publish(self.root, self.base_sha, "symphony/issue-3", first["head_sha"], "T", "B", 3,
                           on_candidate=journal.append)
        self.assertTrue(raised.exception.transient)
        published = self.fake.branches["symphony/issue-3"]
        commits_before = len(self.fake.commits)
        lagging = False
        result = client.publish(self.root, self.base_sha, "symphony/issue-3", first["head_sha"], "T", "B", 3,
                                candidate_sha=journal[-1], on_candidate=journal.append)
        self.assertEqual(result["head_sha"], published)
        self.assertEqual(len(self.fake.commits), commits_before)
        self.assertEqual(len(self.fake.pulls), 1)

    def test_publish_pr_head_mismatch_is_fatal_after_foreign_branch_move(self):
        self.change_worktree()
        self.fake.tamper_pull_head = True
        foreign_sha = "e" * 40

        def transport(method, path, body):
            result = self.fake(method, path, body)
            if method == "GET" and path == "/repos/o/r/pulls/10":
                self.fake.branches["symphony/issue-3"] = foreign_sha
            return result

        client = GitHub("o/r", transport=transport)
        with self.assertRaisesRegex(APIError, "head does not match") as raised:
            client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3)
        self.assertFalse(raised.exception.transient)
        self.assertEqual(self.fake.branches["symphony/issue-3"], foreign_sha)

    def test_matches_publication_recognizes_own_candidate_without_mutating_refs_or_prs(self):
        self.change_worktree()
        first = self.client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3)
        (self.root / "keep.txt").write_text("second candidate\n", encoding="utf-8")
        published = self.client.publish(self.root, self.base_sha, "symphony/issue-3", first["head_sha"], "T", "B", 3)
        calls_before = len(self.fake.calls)
        self.assertTrue(self.client.matches_publication(
            self.root, self.base_sha, "symphony/issue-3", first["head_sha"],
            candidate_sha=published["head_sha"],
        ))
        self.assertEqual(self.fake.branches["symphony/issue-3"], published["head_sha"])
        mutations = [(method, path) for method, path, _ in self.fake.calls[calls_before:] if method != "GET"]
        self.assertTrue(mutations)
        self.assertTrue(all(method == "POST" and path in {
            "/repos/o/r/git/blobs", "/repos/o/r/git/trees",
        } for method, path in mutations))

    def test_matches_publication_rejects_foreign_tree_or_parent_and_changed_source(self):
        self.change_worktree()
        result = self.client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3)
        candidate = self.fake.commits[result["head_sha"]]
        original = {"tree": candidate["tree"], "parents": list(candidate["parents"])}
        self.assertTrue(self.client.matches_publication(self.root, self.base_sha, "symphony/issue-3", None,
                                                        candidate_sha=result["head_sha"]))
        for tree, parents in (("f" * 40, original["parents"]),
                              (original["tree"], ["e" * 40]),
                              (original["tree"], [self.base_sha, "e" * 40])):
            with self.subTest(tree=tree, parents=parents):
                candidate.update(tree=tree, parents=parents)
                self.assertFalse(self.client.matches_publication(self.root, self.base_sha, "symphony/issue-3", None,
                                                                 candidate_sha=result["head_sha"]))
        candidate.update(original)
        (self.root / "keep.txt").write_text("unchecked source\n", encoding="utf-8")
        self.assertFalse(self.client.matches_publication(self.root, self.base_sha, "symphony/issue-3", None,
                                                         candidate_sha=result["head_sha"]))

    def test_matches_publication_rejects_missing_or_unchanged_branch(self):
        self.change_worktree()
        self.assertFalse(self.client.matches_publication(self.root, self.base_sha, "missing", None,
                                                         candidate_sha="e" * 40))
        self.assertFalse(self.client.matches_publication(self.root, self.base_sha, "main", self.base_sha,
                                                         candidate_sha=self.base_sha))
        self.assertTrue(all(method == "GET" for method, _, _ in self.fake.calls))

    def test_candidate_lookup_preserves_api_errors_except_missing_commit(self):
        for status, transient in ((503, True), (403, False), (404, False)):
            with self.subTest(status=status):
                error = APIError("candidate lookup failed", transient=transient, status=status)

                def transport(method, path, body):
                    self.assertEqual((method, path), ("GET", "/repos/o/r/git/commits/" + "e" * 40))
                    raise error

                client = GitHub("o/r", transport=transport)
                if status == 404:
                    self.assertFalse(client._matches_candidate("e" * 40, "f" * 40, None, self.base_sha))
                else:
                    with self.assertRaises(APIError) as raised:
                        client._matches_candidate("e" * 40, "f" * 40, None, self.base_sha)
                    self.assertIs(raised.exception, error)

    def test_candidate_callback_precedes_both_ref_create_and_update(self):
        self.change_worktree()
        journal = []
        ref_operations = []

        def transport(method, path, body):
            if method in {"POST", "PATCH"} and "/git/refs" in path:
                self.assertEqual(journal[-1], body["sha"])
                ref_operations.append(method)
            return self.fake(method, path, body)

        client = GitHub("o/r", transport=transport)
        first = client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3,
                               on_candidate=journal.append)
        (self.root / "keep.txt").write_text("second candidate\n", encoding="utf-8")
        second = client.publish(self.root, self.base_sha, "symphony/issue-3", first["head_sha"], "T", "B", 3,
                                on_candidate=journal.append)
        self.assertEqual(journal, [first["head_sha"], second["head_sha"]])
        self.assertEqual(ref_operations, ["POST", "PATCH"])

    def test_candidate_callback_failure_prevents_any_ref_or_pr_mutation(self):
        self.change_worktree()

        def failed_save(sha):
            self.assertIn(sha, self.fake.commits)
            raise RuntimeError("journal save failed")

        for expected in (None, self.base_sha):
            with self.subTest(expected=expected):
                if expected:
                    self.fake.branches["symphony/issue-3"] = expected
                before_branches = dict(self.fake.branches)
                calls_before = len(self.fake.calls)
                with self.assertRaisesRegex(RuntimeError, "journal save failed"):
                    self.client.publish(self.root, self.base_sha, "symphony/issue-3", expected, "T", "B", 3,
                                        on_candidate=failed_save)
                self.assertEqual(self.fake.branches, before_branches)
                self.assertFalse(any(method in {"POST", "PATCH"} and ("/git/refs" in path or "/pulls" in path)
                                     for method, path, _ in self.fake.calls[calls_before:]))

    def test_journaled_candidate_is_reused_after_ref_write_never_reached_server(self):
        self.change_worktree()
        journal = []
        fail_once = True

        def transport(method, path, body):
            nonlocal fail_once
            if method == "POST" and path.endswith("/git/refs") and fail_once:
                fail_once = False
                raise TimeoutError("request never reached server")
            return self.fake(method, path, body)

        client = GitHub("o/r", transport=transport)
        with self.assertRaises(APIError):
            client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3,
                           on_candidate=journal.append)
        self.assertNotIn("symphony/issue-3", self.fake.branches)
        candidate_sha = journal[-1]
        commits_before = len(self.fake.commits)
        result = client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3,
                                candidate_sha=candidate_sha, on_candidate=journal.append)
        self.assertEqual(result["head_sha"], candidate_sha)
        self.assertEqual(len(self.fake.commits), commits_before)

    def test_advanced_head_requires_journal_even_when_tree_and_parents_match(self):
        self.change_worktree()
        result = self.client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3)
        calls_before = len(self.fake.calls)
        self.assertFalse(self.client.matches_publication(self.root, self.base_sha, "symphony/issue-3", None))
        self.assertEqual(len(self.fake.calls), calls_before)
        with self.assertRaisesRegex(APIError, "changed outside"):
            self.client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3)

        foreign_sha = "e" * 40
        self.fake.commits[foreign_sha] = dict(self.fake.commits[result["head_sha"]])
        self.fake.branches["symphony/issue-3"] = foreign_sha
        self.assertFalse(self.client.matches_publication(self.root, self.base_sha, "symphony/issue-3", None,
                                                         candidate_sha=result["head_sha"]))
        with self.assertRaisesRegex(APIError, "changed outside"):
            self.client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3,
                                candidate_sha=result["head_sha"])
        self.assertEqual(self.fake.branches["symphony/issue-3"], foreign_sha)

    def test_journaled_candidate_cannot_publish_changed_worktree(self):
        self.change_worktree()
        journal = []

        def stop_after_journal(sha):
            journal.append(sha)
            raise RuntimeError("stopped before ref write")

        with self.assertRaises(RuntimeError):
            self.client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3,
                                on_candidate=stop_after_journal)
        (self.root / "keep.txt").write_text("unreviewed change\n", encoding="utf-8")
        with self.assertRaisesRegex(APIError, "recorded publication candidate does not match"):
            self.client.publish(self.root, self.base_sha, "symphony/issue-3", None, "T", "B", 3,
                                candidate_sha=journal[-1])
        self.assertNotIn("symphony/issue-3", self.fake.branches)

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

    def test_issues_with_label_encodes_query_paginates_and_excludes_pull_requests(self):
        label = "symphony:ready / review"
        self.fake.issues = [
            {"number": i + 1, "title": f"issue {i + 1}"}
            for i in range(100)
        ]
        self.fake.issues[24]["pull_request"] = {"url": "https://api.github.com/repos/o/r/pulls/25"}
        self.fake.issues.append({"number": 101, "title": "issue 101"})

        issues = self.client.issues_with_label(label)

        self.assertEqual(len(issues), 100)
        self.assertEqual(issues[-1]["number"], 101)
        self.assertTrue(all("pull_request" not in issue for issue in issues))
        requests = [
            path for method, path, _ in self.fake.calls
            if method == "GET" and urlsplit(path).path == "/repos/o/r/issues"
        ]
        self.assertEqual(len(requests), 2)
        for page, path in enumerate(requests, start=1):
            parsed = urlsplit(path)
            self.assertEqual(parsed.query.split("&")[0], "state=open")
            self.assertIn("labels=symphony%3Aready%20%2F%20review", parsed.query)
            parameters = parse_qs(parsed.query)
            self.assertEqual(parameters["labels"], [label])
            self.assertEqual(parameters["per_page"], ["100"])
            self.assertEqual(parameters["page"], [str(page)])

    def test_issue_events_paginate_and_preserve_raw_actor_and_label(self):
        self.fake.events_by_issue[3] = [
            {
                "id": i + 1,
                "event": "labeled" if i == 0 else "unlabeled",
                "actor": {"id": i + 100, "login": f"actor-{i}"},
                "label": {"id": i + 200, "name": "symphony:ready"},
                "created_at": f"2026-09-28T00:{i % 60:02d}:00Z",
            }
            for i in range(101)
        ]

        events = self.client.issue_events(3)

        self.assertEqual(len(events), 101)
        self.assertEqual(events[0], self.fake.events_by_issue[3][0])
        self.assertEqual(events[0]["event"], "labeled")
        self.assertEqual(events[0]["actor"], {"id": 100, "login": "actor-0"})
        self.assertEqual(events[0]["label"], {"id": 200, "name": "symphony:ready"})
        self.assertEqual(events[-1]["id"], 101)
        requests = [
            path for method, path, _ in self.fake.calls
            if method == "GET" and urlsplit(path).path == "/repos/o/r/issues/3/events"
        ]
        self.assertEqual(len(requests), 2)
        self.assertEqual([parse_qs(urlsplit(path).query)["page"][0] for path in requests], ["1", "2"])
        self.assertTrue(all(parse_qs(urlsplit(path).query)["per_page"] == ["100"] for path in requests))

    def test_issue_last_edited_at_returns_timestamp_or_null_from_graphql(self):
        timestamp = "2026-09-28T03:04:05Z"
        self.fake.last_edited_response = {
            "data": {"repository": {"issue": {"lastEditedAt": timestamp}}},
        }

        self.assertEqual(self.client.issue_last_edited_at(3), timestamp)
        method, path, body = self.fake.calls[-1]
        self.assertEqual((method, path), ("POST", "/graphql"))
        self.assertIn("lastEditedAt", body["query"])
        self.assertEqual(body["variables"], {"owner": "o", "name": "r", "number": 3})

        self.fake.last_edited_response = {
            "data": {"repository": {"issue": {"lastEditedAt": None}}},
        }
        self.assertIsNone(self.client.issue_last_edited_at(3))

    def test_issue_last_edited_at_fails_closed_on_errors_or_invalid_data(self):
        invalid_responses = [
            {"errors": [{"message": "denied"}], "data": {"repository": {"issue": {"lastEditedAt": None}}}},
            {},
            {"data": {"repository": None}},
            {"data": {"repository": {"issue": {}}}},
            {"data": {"repository": {"issue": {"lastEditedAt": 42}}}},
            {"data": {"repository": {"issue": {"lastEditedAt": "not a timestamp"}}}},
            {"data": {"repository": {"issue": {"lastEditedAt": "2026-09-28T03:04:05"}}}},
        ]
        for response in invalid_responses:
            with self.subTest(response=response):
                self.fake.last_edited_response = response
                with self.assertRaises(APIError):
                    self.client.issue_last_edited_at(3)

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
