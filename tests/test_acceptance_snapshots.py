import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from symphony_acceptance.core import PipelineError
from symphony_acceptance.snapshots import prepare_snapshot


def fixture_git(root, *args, check=True):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                            text=True, env=os.environ.copy(), timeout=30)
    if check and result.returncode:
        raise PipelineError('fixture_git_failed:' + args[0])
    return result


class AcceptanceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.seed = self.home / 'seed'
        self.seed.mkdir()
        fixture_git(self.seed, 'init', '-q', '-b', 'main')
        fixture_git(self.seed, 'config', 'user.name', 'snapshot test')
        fixture_git(self.seed, 'config', 'user.email', 'snapshot@example.test')
        (self.seed / 'notes.txt').write_text('old review content\n', encoding='utf-8')
        (self.seed / 'image.bin').write_bytes(b'\x00old\xff')
        fixture_git(self.seed, 'add', '.')
        fixture_git(self.seed, 'commit', '-qm', 'base')
        self.base_sha = fixture_git(self.seed, 'rev-parse', 'HEAD').stdout.strip()

        self.remote = self.home / 'origin.git'
        fixture_git(self.home, 'clone', '--bare', str(self.seed), str(self.remote))
        fixture_git(self.seed, 'remote', 'add', 'origin', str(self.remote))
        fixture_git(self.seed, 'switch', '-qc', 'codex/review')
        (self.seed / 'notes.txt').write_text('new review content\n', encoding='utf-8')
        (self.seed / 'image.bin').write_bytes(b'\x00new\xfe')
        fixture_git(self.seed, 'add', '.')
        fixture_git(self.seed, 'commit', '-qm', 'head')
        self.head_sha = fixture_git(self.seed, 'rev-parse', 'HEAD').stdout.strip()
        fixture_git(self.seed, 'push', '-q', 'origin', 'codex/review')

    def test_snapshot_has_base_blob_and_diff_without_lazy_fetch(self):
        root = self.home / 'reviews' / 'first'
        snapshot = prepare_snapshot(root=root, repo=str(self.remote), base_sha=self.base_sha,
                                    head_sha=self.head_sha, branch='codex/review', git=fixture_git)
        self.assertEqual(snapshot, root.resolve())
        self.assertEqual(fixture_git(root, 'rev-parse', 'HEAD').stdout.strip(), self.head_sha)

        no_lazy_fetch_env = os.environ.copy()
        no_lazy_fetch_env['GIT_NO_LAZY_FETCH'] = '1'
        old_blob = subprocess.run(['git', '-C', str(root), 'cat-file', 'blob',
                                   f'{self.base_sha}:notes.txt'], capture_output=True,
                                  env=no_lazy_fetch_env, timeout=30)
        self.assertEqual(old_blob.returncode, 0, old_blob.stderr.decode(errors='replace'))
        self.assertEqual(old_blob.stdout, b'old review content\n')

        diff = subprocess.run(['git', '-C', str(root), 'diff', self.base_sha, self.head_sha],
                              capture_output=True, text=True, env=no_lazy_fetch_env, timeout=30)
        self.assertEqual(diff.returncode, 0, diff.stderr)
        self.assertIn('-old review content', diff.stdout)
        self.assertIn('+new review content', diff.stdout)
        self.assertIn('Binary files', diff.stdout)

    def test_invalid_sha_fails_closed_before_creating_checkout(self):
        root = self.home / 'reviews' / 'invalid'
        with self.assertRaisesRegex(PipelineError, 'review_snapshot_invalid_head_sha'):
            prepare_snapshot(root=root, repo=str(self.remote), base_sha=self.base_sha,
                             head_sha='not-a-full-commit-sha', branch='codex/review',
                             git=fixture_git)
        self.assertFalse(root.exists())

    def test_unavailable_full_sha_fails_closed(self):
        root = self.home / 'reviews' / 'missing'
        with self.assertRaisesRegex(PipelineError, 'review_snapshot_head_commit_unavailable'):
            prepare_snapshot(root=root, repo=str(self.remote), base_sha=self.base_sha,
                             head_sha='f' * 40, branch='codex/review', git=fixture_git)
        # Failed attempts stay available for diagnosis and are never cleaned up.
        self.assertTrue(root.exists())

    def test_existing_review_workspace_is_never_overwritten(self):
        root = self.home / 'reviews' / 'existing'
        root.mkdir(parents=True)
        marker = root / 'evidence.txt'
        marker.write_text('keep', encoding='utf-8')
        with self.assertRaisesRegex(PipelineError, 'review_snapshot_path_exists'):
            prepare_snapshot(root=root, repo=str(self.remote), base_sha=self.base_sha,
                             head_sha=self.head_sha, branch='codex/review', git=fixture_git)
        self.assertEqual(marker.read_text(encoding='utf-8'), 'keep')


if __name__ == '__main__':
    unittest.main()
