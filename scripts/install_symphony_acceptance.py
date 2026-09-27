#!/usr/bin/env python3
"""Install an immutable controller bundle; activate only while workers are idle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4


def install(source, destination, activate=False):
    source, destination = Path(source).resolve(), Path(destination).expanduser().resolve()
    paths = [source / 'WORKFLOW.md', source / 'config' / 'symphony-blog.json',
             source / 'scripts' / 'symphony_codex_adapter.py',
             source / 'scripts' / 'symphony_deepseek_proxy.py',
             source / 'scripts' / 'start_symphony_acceptance.sh']
    paths += sorted((source / 'scripts' / 'symphony_acceptance').glob('*.py'))
    if not all(path.is_file() for path in paths):
        raise RuntimeError('installation_source_incomplete')
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(source).as_posix().encode())
        digest.update(path.read_bytes().replace(b'\r\n', b'\n'))
    version = digest.hexdigest()[:20]
    release = destination / 'releases' / version
    manifest = {'version': version, 'files': [path.relative_to(source).as_posix() for path in paths]}
    if not release.exists():
        staging = release.with_name(version + '.building-' + uuid4().hex)
        for path in paths:
            target = staging / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('wb') as stream:
                stream.write(path.read_bytes().replace(b'\r\n', b'\n'))
                stream.flush()
                os.fsync(stream.fileno())
        with (staging / 'manifest.json').open('w', encoding='utf-8') as stream:
            stream.write(json.dumps(manifest, indent=2) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        staging.rename(release)
    if json.loads((release / 'manifest.json').read_text(encoding='utf-8')) != manifest:
        raise RuntimeError('installed_manifest_mismatch')
    for path in paths:
        if (release / path.relative_to(source)).read_bytes() != path.read_bytes().replace(b'\r\n', b'\n'):
            raise RuntimeError('installed_bundle_corrupted')
    if activate:
        destination.mkdir(parents=True, exist_ok=True)
        pending = destination / ('current-' + str(os.getpid()))
        pending.symlink_to(release, target_is_directory=True)
        pending.replace(destination / 'current')
    return {'version': version, 'release': str(release), 'activated': activate}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--destination', default='~/.local/symphony-acceptance')
    parser.add_argument('--activate', action='store_true', help='Switch current only after checking idle workers and the ready queue')
    args = parser.parse_args()
    print(json.dumps(install(args.source, args.destination, args.activate), indent=2))
