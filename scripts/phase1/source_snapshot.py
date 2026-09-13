"""Select and copy source files without including local credentials or runtime state."""

import hashlib
from pathlib import Path
import shutil
import subprocess
from collections.abc import Iterable


EXCLUDED_PARTS = {
    '.git', '.codex', '.agents', 'data', 'logs', 'node_modules', 'dist', 'build',
    '.venv', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
    'test-results', 'coverage', '.DS_Store', '.npmrc', '.pypirc', '.netrc',
}
EXCLUDED_SUFFIXES = {'.key', '.pem', '.p12', '.pfx', '.pyc', '.pyo',
                     '.db', '.sqlite', '.sqlite3', '.log', '.har', '.pcap'}
SOURCE_ROOTS = ('backend/', 'frontend/', 'scripts/', 'docs/', '.github/')


def excluded_path(relative: str) -> bool:
    path = Path(relative)
    return (any(part in EXCLUDED_PARTS or part.startswith('.env') for part in path.parts)
            or path.name.startswith('auth-secret.key')
            or path.suffix.lower() in EXCLUDED_SUFFIXES)


def discover_sources(root: Path, includes: Iterable[str] = ()) -> tuple[list[str], dict]:
    """Account for untracked first-party files; refuse ignored source and symlinks.

    Git inventories filenames only. Runtime directories are filtered before any
    file content is read. Ignored first-party files need deliberate --include;
    ignoring an application module must never silently turn a test green.
    """
    def git_paths(*arguments):
        result = subprocess.check_output(
            ['git', 'ls-files', '-z', *arguments], cwd=root,
        ).decode().split('\0')
        return {relative for relative in result if relative}

    explicit = {str(source_file(root, rel).relative_to(root.resolve())) for rel in includes}
    tracked = git_paths('--cached')
    untracked = git_paths('--others', '--exclude-standard')
    ignored = git_paths('--others', '--ignored', '--exclude-standard')
    relevant = lambda relative: relative.startswith(SOURCE_ROOTS) and not excluded_path(relative)
    blocked = sorted(relative for relative in ignored if relevant(relative) and relative not in explicit)
    if blocked:
        raise ValueError('Ignored first-party files require explicit --include or removal: ' + ', '.join(blocked))
    automatic = {relative for relative in untracked if relevant(relative)}
    # Unlike intentionally excluded/deleted tracked files, invalid new source is
    # an error. Validate the complete selection before copying any bytes.
    for relative in sorted(automatic | explicit):
        source_file(root, relative)
    selected = tracked | automatic | explicit
    return sorted(selected), {
        'automatic_untracked': sorted(automatic),
        'explicit_includes': sorted(explicit),
        'excluded': sorted(relative for relative in tracked | untracked if excluded_path(relative)),
        'untracked_outside_source_roots': sorted(untracked - automatic - explicit
                                               - {r for r in untracked if excluded_path(r)}),
        'deleted_tracked': sorted(relative for relative in tracked
                                  if not (root / relative).exists() and not (root / relative).is_symlink()),
    }


def source_fingerprint(hashes: dict[str, str]) -> str:
    """A stable identity including both filenames and exact copied file bytes."""
    digest = hashlib.sha256()
    for relative, file_hash in sorted(hashes.items()):
        digest.update(relative.encode() + b'\0' + file_hash.encode() + b'\n')
    return digest.hexdigest()


def changed_snapshot_sources(snapshot: Path, hashes: dict[str, str]) -> list[str]:
    """Verify original inputs after checks; new generated artifacts are not inputs."""
    changed = []
    for relative, expected in sorted(hashes.items()):
        try:
            path = source_file(snapshot, relative)
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, ValueError):
            actual = None
        if actual != expected:
            changed.append(relative)
    return changed


def source_file(root: Path, relative: str) -> Path:
    """Validate an explicit inclusion before reading or creating any resources."""
    path = Path(relative)
    if not relative or path.is_absolute() or '..' in path.parts:
        raise ValueError(f'Include must be a workspace-relative file: {relative!r}')
    if excluded_path(relative):
        raise ValueError(f'Excluded source path: {relative!r}')
    root = root.resolve()
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f'Symlinks cannot be included: {relative!r}')
    if not current.resolve().is_relative_to(root):
        raise ValueError(f'Include is outside the workspace: {relative!r}')
    if not current.is_file():
        raise ValueError(f'Include is not an existing file: {relative!r}')
    return current


def copy_sources(
    root: Path,
    destination: Path,
    paths: Iterable[str],
    includes: Iterable[str] = (),
) -> dict[str, str]:
    """Copy safe tracked files and required inclusions; hash the actual copied bytes."""
    root = root.resolve()
    required = {str(source_file(root, rel).relative_to(root)) for rel in includes}
    hashes = {}
    selected = []
    for relative in sorted(set(paths) | required):
        if excluded_path(relative):
            continue
        if not (root / relative).exists() and not (root / relative).is_symlink():
            if relative in required:
                raise ValueError(f'Include is not an existing file: {relative!r}')
            continue
        try:
            source = source_file(root, relative)
        except ValueError as exc:
            raise ValueError(f'Unsafe selected source: {relative!r}') from exc
        selected.append(source)
    for source in selected:
        target = destination / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        hashes[str(source.relative_to(root))] = hashlib.sha256(target.read_bytes()).hexdigest()
    return hashes
