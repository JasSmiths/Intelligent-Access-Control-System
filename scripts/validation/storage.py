"""Disk admission and bounded accounting for local IACS validation resources."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import signal
from contextlib import contextmanager

GIB = 1024 ** 3
RESERVE = 30 * GIB
ADMISSION = 40 * GIB
BUDGET = 10 * GIB
DISPOSABLE = frozenset({'python-deps', 'js-deps', 'postgres', 'redis', 'runtime',
                        'frontend-cache', 'browser-cache', 'python-env', 'js-browsers', 'uv-cache'})


class StorageViolation(RuntimeError):
    """Allocation must stop; owned cleanup and evidence retention must continue."""


def existing_parent(path):
    path = Path(path)
    while not path.exists():
        path = path.parent
    return path


def disposable_bytes(roots):
    """Conservatively count all task bytes, including working data amid evidence."""
    total, seen = 0, set()
    deadline = time.monotonic() + 5
    def within_deadline():
        if time.monotonic() > deadline:
            raise StorageViolation('Storage accounting exceeded its five-second budget')
    def walk_error(error):
        if not isinstance(error, FileNotFoundError):
            raise StorageViolation('Storage traversal failed: ' + type(error).__name__)
    for root in roots:
        within_deadline()
        if not root.exists():
            continue
        if root.is_symlink():
            raise StorageViolation('Storage root is a symlink')
        for parent, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
            within_deadline()
            path = Path(parent)
            selected=[]
            for directory in dirs:
                within_deadline()
                if directory != '.git' and not (path/directory).is_symlink():selected.append(directory)
            dirs[:]=selected
            for name in files:
                within_deadline()
                item = path / name
                try:
                    stat = item.lstat()
                except FileNotFoundError:
                    continue
                identity = stat.st_dev, stat.st_ino
                if identity not in seen:
                    total += stat.st_blocks * 512
                    seen.add(identity)
    within_deadline()
    return total


def docker_free(image):
    """Probe the VM using a cached image, no mounts/network/application startup."""
    identity = subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],
                                       text=True,timeout=3).strip()
    if not identity.startswith('sha256:'):
        raise StorageViolation('No cached immutable probe image')
    name = 'iacs-storage-' + uuid.uuid4().hex
    command = ['docker', 'run', '--pull=never', '--rm', '--name', name, '--label', 'iacs.validation=' + name,
               '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--no-healthcheck',
               '--security-opt', 'no-new-privileges', '--memory', '64m', '--pids-limit', '32',
               '--entrypoint', '/bin/df', identity, '-Pk', '/']
    try:
        output = subprocess.check_output(command, text=True, timeout=5)
        return int(output.splitlines()[-1].split()[3]) * 1024
    finally:
        with draining_cleanup():
            # This exact random name belongs only to this inert probe.
            subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=3, check=False)


@contextmanager
def validation_interrupts():
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    previous={s:signal.signal(s,interrupt) for s in (signal.SIGINT,signal.SIGTERM)}
    try:
        yield
    finally:
        for signum,handler in previous.items():signal.signal(signum,handler)


@contextmanager
def draining_cleanup():
    previous={s:signal.signal(s,signal.SIG_IGN) for s in (signal.SIGINT,signal.SIGTERM)}
    try:
        yield
    finally:
        for signum,handler in previous.items():signal.signal(signum,handler)


class StorageGuard:
    def __init__(self, evidence, preparation=None, *, vm_probe=None):
        self.roots = [Path(evidence)]
        if preparation:
            self.roots.append(Path(preparation))
        self.vm_probe = vm_probe
        self.last = 0.0
        self.samples = []
        self.images = {}
        self.admitted = False
        self.cleaning = False
        self.initial_vm_free = None

    def check(self, *, force=False):
        if self.cleaning:
            return
        now = time.monotonic()
        if not force and self.admitted and now - self.last < 5:
            return
        try:
            files_occupied = disposable_bytes(self.roots)
            free = min(shutil.disk_usage(existing_parent(p)).free for p in self.roots)
            minimum = ADMISSION if force or not self.admitted else RESERVE
            if free < minimum:
                raise StorageViolation('Insufficient host disk reserve before Docker probe')
            vm_free = self.vm_probe() if self.vm_probe else None
            if self.vm_probe and (not isinstance(vm_free, int) or vm_free < 0):
                raise ValueError('Unknown Docker VM capacity')
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise StorageViolation('Storage accounting failed: ' + type(exc).__name__) from exc
        if vm_free is not None and self.initial_vm_free is None:
            self.initial_vm_free = vm_free
        vm_growth = max(0, self.initial_vm_free - vm_free) if vm_free is not None else 0
        # VM growth covers intermediate build layers/container writes as well as
        # completed images. Unrelated VM growth conservatively consumes headroom.
        occupied = files_occupied + max(sum(self.images.values()), vm_growth)
        sample = {'host_free_bytes': free, 'docker_vm_free_bytes': vm_free,
                  'docker_vm_growth_bytes_proxy': vm_growth,
                  'disposable_bytes': occupied, 'monotonic_seconds': now}
        self.samples = (self.samples + [sample])[-1200:]
        self.last = now
        minimum = ADMISSION if force or not self.admitted else RESERVE
        if free < minimum or (vm_free is not None and vm_free < minimum):
            raise StorageViolation('Insufficient disk reserve; stop allocation and drain owned resources')
        if occupied > BUDGET:
            raise StorageViolation('Task disposable storage exceeds 10 GiB across retained runs')
        self.admitted = True

    def record(self):
        return {'reserve_bytes': RESERVE, 'admission_bytes': ADMISSION, 'budget_bytes': BUDGET,
                'roots': list(map(str, self.roots)), 'samples': self.samples,
                'additional_image_bytes': self.images,
                'accounting': 'Conservative task-root allocation includes evidence; deletion eligibility is separately allowlisted.',
                'qualification': 'Polling and admission headroom are safeguards, not a filesystem allocation quota.'}


def remove_disposable(run, paths, container_mounts):
    """Only explicitly owned paths; unknown references or substituted paths refuse."""
    run = Path(run)
    removed = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            continue
        if path.is_symlink() or path.resolve() != path or path.parent != run or path.name not in DISPOSABLE:
            raise StorageViolation('Refusing unowned/substituted cleanup path')
        for source in container_mounts:
            mount = Path(source).resolve()
            if mount == path or mount in path.parents or path in mount.parents:
                raise StorageViolation('Disposable path is still referenced by a container')
        shutil.rmtree(path)
        removed.append(str(path))
    return removed


def all_container_mounts():
    ids = subprocess.check_output(['docker', 'ps', '-aq'], text=True, timeout=30).split()
    if not ids:
        return []
    output = subprocess.check_output(['docker', 'inspect', '--format', '{{json .Mounts}}', *ids],
                                     text=True, timeout=30)
    return [m['Source'] for line in output.splitlines() for m in json.loads(line) if m.get('Source')]
