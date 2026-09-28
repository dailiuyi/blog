"""Publish screenshot evidence on a dedicated branch, outside application code."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote

from .core import PipelineError
from .github import APIError


def publish_browser_evidence(github, state, report, branch='codex/acceptance-evidence'):
    if branch != 'codex/acceptance-evidence':
        raise PipelineError('invalid_evidence_branch')
    public = copy.deepcopy(report)
    files = []
    for shot in public.get('screenshots', []):
        path = Path(shot.pop('local_path'))
        data = path.read_bytes()
        if len(data) > 8 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != shot['sha256']:
            raise PipelineError('browser_evidence_changed')
        files.append((shot['file'], data))
    if (not files or len(files) > 75 or len({name for name, _ in files}) != len(files)
            or not re.fullmatch(r'[a-f0-9]{32}', state['run_id'])):
        raise PipelineError('invalid_evidence_batch')
    public['candidate_sha'] = state['head_sha']
    public['issue'] = state['issue']
    manifest = json.dumps(public, ensure_ascii=False, indent=2).encode('utf-8')
    batch = hashlib.sha256(manifest).hexdigest()[:16]
    prefix = f"issue-{int(state['issue'])}/{state['run_id']}-{batch}"
    files.append(('report.json', manifest))
    api = '/repos/' + github.repo
    entries = []
    for filename, data in files:
        if not re.fullmatch(r'(?:page-[0-9]+-[0-9]+(?:-step-[0-9]+)?\.png|report\.json)', filename):
            raise PipelineError('invalid_evidence_filename')
        blob = github.request('POST', api + '/git/blobs',
                              {'content': base64.b64encode(data).decode('ascii'), 'encoding': 'base64'})
        entries.append({'path': prefix + '/' + filename, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
    for attempt in range(3):
        head = github.branch_head(branch)
        tree = {'tree': entries}
        if head:
            commit = github.request('GET', api + '/git/commits/' + head)
            tree['base_tree'] = commit['tree']['sha']
        created_tree = github.request('POST', api + '/git/trees', tree)
        if head and created_tree['sha'] == commit['tree']['sha']:
            evidence_sha = head
            break
        created = github.request('POST', api + '/git/commits', {
            'message': f"Evidence for issue #{state['issue']} at {state['head_sha'][:12]}",
            'tree': created_tree['sha'], 'parents': [head] if head else []})
        evidence_sha = created['sha']
        try:
            if head:
                github.request('PATCH', api + '/git/refs/heads/' + quote(branch, safe='/'),
                               {'sha': evidence_sha, 'force': False})
            else:
                github.request('POST', api + '/git/refs', {'ref': 'refs/heads/' + branch, 'sha': evidence_sha})
            break
        except APIError:
            # Optimistic fast-forward publication: on concurrent updates rebuild
            # from the latest tree, never force or discard another run's evidence.
            if attempt == 2 or github.branch_head(branch) == head:
                raise
    raw = f'https://raw.githubusercontent.com/{github.repo}/{evidence_sha}/{prefix}/'
    for shot in report['screenshots']:
        shot['url'] = raw + shot['file']
    report['manifest_url'] = f'https://github.com/{github.repo}/blob/{evidence_sha}/{prefix}/report.json'
    report['evidence_commit'] = evidence_sha
    return report
