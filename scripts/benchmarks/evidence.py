"""Retained synthetic measurements with source, tool and lock identities."""

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys

from scripts.validation.source_snapshot import EXCLUDED_PARTS, SOURCE_ROOTS, discover_sources, excluded_path, source_file, source_fingerprint


def evidence_identity():
    root = Path(__file__).resolve().parents[2]
    manifests = ('backend/pyproject.toml', 'backend/uv.lock',
                 'frontend/package.json', 'frontend/package-lock.json')
    identity = os.environ.get('IACS_SOURCE_IDENTITY')
    kind = 'harness-snapshot'
    if identity is None:
        hashes = {}
        if (root / '.git').exists():
            selected, _inventory = discover_sources(root)
            for relative in selected:
                if not excluded_path(relative) and (root / relative).is_file():
                    hashes[relative] = hashlib.sha256(source_file(root, relative).read_bytes()).hexdigest()
            kind = 'standalone-git-source-inventory'
        else:
            # A copied no-Git source tree uses the same safe source roots and an
            # explicit root-file allowlist. Never inspect arbitrary local files.
            root_files = {'AGENTS.md', 'docker-compose.yml', 'docker-compose.override.yml',
                          'docker-compose.preview.yml', '.github/workflows/project-checks.yml'}
            for directory, subdirectories, files in os.walk(root):
                subdirectories[:] = [name for name in subdirectories if name not in EXCLUDED_PARTS
                                      and (Path(directory) != root or name + '/' in SOURCE_ROOTS)]
                for name in files:
                    path = Path(directory) / name
                    relative = path.relative_to(root).as_posix()
                    if (relative.startswith(SOURCE_ROOTS) or relative in root_files) and not excluded_path(relative) and not path.is_symlink():
                        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            kind = 'standalone-allowlisted-source-tree'
        identity = source_fingerprint(hashes)
    return {
        'source_fingerprint': identity,
        'source_identity_kind': kind,
        'backend_image': os.environ.get('IACS_IMAGE_IDENTITY'),
        'dependency_manifests': {relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
                                 for relative in manifests},
        'python': sys.version, 'platform': platform.platform(),
        'packages': {name: importlib.metadata.version(name) for name in ('sqlalchemy', 'asyncpg', 'pydantic')},
        'identity_limits': 'Standalone fingerprints are labeled separately from harness inventory fingerprints. Standalone containers must supply IACS_IMAGE_IDENTITY; null means unrecorded, never inferred.',
    }


def write_evidence(path, measurements):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'identity': evidence_identity(), **measurements}, indent=2, sort_keys=True) + '\n')
