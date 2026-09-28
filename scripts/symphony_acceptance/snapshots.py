"""Build complete, isolated checkouts for read-only acceptance review."""

import re
from pathlib import Path

from .core import PipelineError


_FULL_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def prepare_snapshot(*, root, repo, base_sha, head_sha, branch, git):
    """Create and verify a fresh checkout containing all objects needed for review.

    ``repo`` is a git clone source (for example an HTTPS URL or a local bare
    repository path); ``git`` is the controller's credential-filtering git
    callable. The caller chooses a unique ``root`` for each attempt. Existing
    roots are never reset or removed because they may contain evidence from an
    earlier review.
    """
    root = Path(root).resolve()
    if root.exists():
        raise PipelineError('review_snapshot_path_exists')
    if not isinstance(base_sha, str) or not _FULL_SHA.fullmatch(base_sha):
        raise PipelineError('review_snapshot_invalid_base_sha')
    if not isinstance(head_sha, str) or not _FULL_SHA.fullmatch(head_sha):
        raise PipelineError('review_snapshot_invalid_head_sha')
    if not isinstance(branch, str) or not branch:
        raise PipelineError('review_snapshot_invalid_branch')
    if not isinstance(repo, str) or not repo:
        raise PipelineError('review_snapshot_invalid_repository')

    root.parent.mkdir(parents=True, exist_ok=True)

    # Do not use --filter/--depth: the reviewer has no network access, so its
    # base and head trees must already have every required blob locally.
    git(root.parent, 'clone', '--no-checkout', '--no-tags', repo, str(root))
    git(root, 'config', '--local', 'core.autocrlf', 'input')
    git(root, 'check-ref-format', '--branch', branch)
    git(root, 'fetch', '--no-tags', 'origin',
        f'refs/heads/{branch}:refs/remotes/origin/{branch}')

    def commit_is_present(sha):
        result = git(root, 'cat-file', '-e', f'{sha}^{{commit}}', check=False)
        return result.returncode == 0

    def ensure_commit(sha, label):
        if not commit_is_present(sha):
            # The base may not be an ancestor of the review branch. Fetch its
            # exact immutable object when the server still advertises it.
            try:
                git(root, 'fetch', '--no-tags', 'origin', sha)
            except PipelineError as exc:
                raise PipelineError(f'review_snapshot_{label}_commit_unavailable') from exc
        if not commit_is_present(sha):
            raise PipelineError(f'review_snapshot_{label}_commit_unavailable')

    ensure_commit(base_sha, 'base')
    ensure_commit(head_sha, 'head')

    git(root, 'checkout', '--detach', head_sha)
    actual_head = git(root, 'rev-parse', 'HEAD').stdout.strip()
    if actual_head != head_sha:
        raise PipelineError('review_checkout_mismatch')

    # Running diff here forces Git to walk both commit trees and read every
    # changed blob before handing the checkout to an offline reviewer.
    git(root, 'diff', '--no-ext-diff', '--no-renames', base_sha, head_sha, '--')
    return root
