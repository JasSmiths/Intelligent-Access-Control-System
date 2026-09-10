#!/usr/bin/env python3
"""Sequential, disposable Phase 1 baseline. Never uses production Compose/exec."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
RESULT_ROOT = Path.home() / 'Documents' / 'IACS Regression Baselines'
RESULT_ROOT.mkdir(parents=True, exist_ok=True)
RUN = Path(tempfile.mkdtemp(prefix='iacs-phase1-', dir=RESULT_ROOT))
SNAP = RUN / 'source'
SNAP.mkdir()
PREFIX = 'iacs-p1-' + uuid.uuid4().hex[:12]
OWNED = []
RESULTS = {}
PRODUCTION = ['iacs-backend', 'iacs-frontend', 'iacs-postgres', 'iacs-redis', 'iacs-updater']


def capture(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def production_state():
    rows = json.loads(capture('docker', 'inspect', *PRODUCTION))
    return [{k: row[k] for k in ('Id', 'Name', 'Image')} | {
        'Mounts': sorted(row['Mounts'], key=lambda mount: mount['Destination']),
        'StartedAt': row['State']['StartedAt'], 'RestartCount': row['RestartCount'],
        'Running': row['State']['Running']} for row in rows]


def check(name, args, required=False, timeout=1200):
    print(name, flush=True)
    with (RUN / (name + '.log')).open('w') as log:
        try:
            result = subprocess.run(args, cwd=SNAP, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
    RESULTS[name] = code
    (RUN / 'results.json').write_text(json.dumps(RESULTS, indent=2))
    if code == 124:
        raise RuntimeError(f'{name} timed out; stopping this run and its containers')
    if required and code:
        raise RuntimeError(f'{name} failed ({code}); see retained log')
    return code


def image(ref):
    try:
        return capture('docker', 'image', 'inspect', ref, '--format', '{{.Id}}')
    except subprocess.CalledProcessError:
        check('pull-' + ref.split(':')[0].replace('/', '-'), ['docker', 'pull', ref], True)
        return capture('docker', 'image', 'inspect', ref, '--format', '{{.Id}}')


def container(name, img, command, *, mounts=(), env=None, network='none', work='/tmp', detached=False):
    cname = PREFIX + '-' + name
    OWNED.append(cname)
    (RUN / 'owned-containers.json').write_text(json.dumps(OWNED, indent=2))
    args = ['docker', 'run', '--name', cname, '--label', 'iacs.phase1=' + PREFIX,
            '--network', network, '--cpus', '1', '--memory', '1536m', '--pids-limit', '256',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--no-healthcheck',
            '--user', '0', '--workdir', work]
    # Postgres entrypoint needs these capabilities to initialize its private bind.
    if name == 'postgres':
        args += ['--cap-add', 'CHOWN', '--cap-add', 'DAC_OVERRIDE', '--cap-add', 'SETUID', '--cap-add', 'SETGID']
    for source, dest, mode in mounts:
        args += ['-v', f'{source}:{dest}:{mode}']
    for key, value in (env or {}).items():
        args += ['-e', f'{key}={value}']
    if detached:
        args += ['-d']
    args += ['--entrypoint', command[0], img, *command[1:]]
    return args


def cleanup():
    failures = []
    for name in reversed(OWNED):
        found = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True)
        if found.returncode:
            continue
        row = json.loads(found.stdout)[0]
        if row['Config']['Labels'].get('iacs.phase1') != PREFIX:
            failures.append(name)
            continue
        result = subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
        if result.returncode:
            failures.append(name)
    remaining = capture('docker', 'ps', '-aq', '--filter', 'label=iacs.phase1=' + PREFIX)
    (RUN / 'cleanup.json').write_text(json.dumps({'failures': failures, 'remaining': remaining}, indent=2))
    if failures or remaining:
        RESULTS['cleanup'] = 1


def interrupted(signum, frame):
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, interrupted)
print('Retained results: ' + str(RUN), flush=True)
before = production_state()
for row in before:
    for mount in row['Mounts']:
        if mount['Type'] == 'bind' and RUN.resolve().is_relative_to(Path(mount['Source']).resolve()):
            raise RuntimeError('Snapshot overlaps production bind mount')
(RUN / 'production-before.json').write_text(json.dumps(before, indent=2))
try:
    # Copy only version-controlled source + deliberate Phase 1 additions. Never walk data/logs.
    paths = capture('git', 'ls-files', '-z').split('\0')
    paths += ['.github/workflows/v2-p06-privacy-review.yml']
    paths += [str(p.relative_to(ROOT)) for p in (ROOT / 'scripts/phase1').rglob('*') if p.is_file()]
    paths += [str(p.relative_to(ROOT)) for p in (ROOT / 'docs/validation').glob('phase1*')]
    hashes = {}
    for rel in sorted(set(paths)):
        p = ROOT / rel
        if not p.is_file():
            continue
        if p.is_symlink() or any(part in {'data', 'logs', 'node_modules', 'dist', '.venv', '__pycache__'} for part in p.relative_to(ROOT).parts) or p.name.startswith('.env'):
            continue
        target = SNAP / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    manifest = {'commit': capture('git', 'rev-parse', 'HEAD'), 'status': capture('git', 'status', '--short'),
                'diff_stat': capture('git', 'diff', '--stat'), 'sha256': hashes}
    (RUN / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    backend = image(os.environ.get('PHASE1_BACKEND_IMAGE', 'intelligentaccesssystem-backend:latest'))
    node = image('node:22-alpine@sha256:968df39aedcea65eeb078fb336ed7191baf48f972b4479711397108be0966920')
    pg = image('pgvector/pgvector:pg16@sha256:00ba258a66dac104fd5171074a0084462a64a1369d8513f3d0a634e2f24d15bc')
    redis = image('redis:7-alpine@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99')
    (RUN / 'images.json').write_text(json.dumps(dict(backend=backend, node=node, postgres=pg, redis=redis), indent=2))
    deps = RUN / 'python-deps'
    deps.mkdir()
    for name in ('pyproject.toml', 'uv.lock'):
        shutil.copy2(SNAP / 'backend' / name, deps / name)
    # Network-enabled commands see only dependency manifests, never workspace/config/runtime mounts.
    check('python-dependencies', container('python-deps', backend,
        ['uv', 'sync', '--locked', '--extra', 'dev', '--no-install-project', '--python', '/usr/local/bin/python'],
        mounts=[(deps, '/deps', 'rw')], env={'UV_PROJECT_ENVIRONMENT': '/deps/.venv'}, network='bridge', work='/deps'), True)
    jsdeps = RUN / 'js-deps'
    jsdeps.mkdir()
    for name in ('package.json', 'package-lock.json'):
        shutil.copy2(SNAP / 'frontend' / name, jsdeps / name)
    check('frontend-dependencies', container('js-deps', node, ['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'],
        mounts=[(jsdeps, '/deps', 'rw')], network='bridge', work='/deps'), True)
    for name in ('postgres', 'redis', 'runtime', 'logs'):
        (RUN / name).mkdir()
    check('postgres-start', container('postgres', pg, ['docker-entrypoint.sh', 'postgres'],
        mounts=[(RUN / 'postgres', '/var/lib/postgresql/data', 'rw')],
        env={'POSTGRES_USER': 'phase1', 'POSTGRES_PASSWORD': 'synthetic-phase1-only', 'POSTGRES_DB': PREFIX.replace('-', '_')}, detached=True), True)
    namespace = 'container:' + PREFIX + '-postgres'
    check('redis-start', container('redis', redis, ['redis-server', '--save', '', '--appendonly', 'no'],
        mounts=[(RUN / 'redis', '/data', 'rw')], network=namespace, detached=True), True)
    env = {'IACS_ENVIRONMENT': 'testing',
           'IACS_DATABASE_URL': 'postgresql+asyncpg://phase1:synthetic-phase1-only@127.0.0.1:5432/' + PREFIX.replace('-', '_'),
           'IACS_REDIS_URL': 'redis://127.0.0.1:6379/0', 'IACS_AUTO_CREATE_SCHEMA': 'false',
           'IACS_SEED_DEMO_DATA': 'false', 'IACS_AUTH_SECRET_KEY': 'phase1-synthetic-auth-root-never-production',
           'IACS_DATA_DIR': '/isolated/runtime', 'IACS_LOG_DIR': '/isolated/logs', 'IACS_WORKSPACE_DIR': '/workspace',
           'PYTHONPATH': '/workspace/backend', 'PYTHONDONTWRITEBYTECODE': '1', 'HOME': '/tmp'}
    mounts = [(SNAP, '/workspace', 'ro'), (deps, '/deps', 'ro'), (RUN / 'runtime', '/isolated/runtime', 'rw'),
              (RUN / 'logs', '/isolated/logs', 'rw'), (RUN, '/results', 'rw')]
    def backend_check(name, command, required=False):
        return check(name, container(name, backend, ['/deps/.venv/bin/python', *command],
            mounts=mounts, env=env, network=namespace, work='/workspace/backend'), required)
    backend_check('isolation', ['/workspace/scripts/phase1/preflight.py'], True)
    check('isolation-inspect', ['docker', 'inspect', PREFIX + '-postgres', PREFIX + '-redis', PREFIX + '-isolation'], True)
    backend_check('compile', ['-c', "import pathlib; files=list(pathlib.Path('app').rglob('*.py'))+list(pathlib.Path('tests').rglob('*.py')); [compile(p.read_bytes(),str(p),'exec') for p in files]; print(len(files),'files compiled')"])
    migrated = backend_check('migration-up', ['-m', 'alembic', 'upgrade', 'head']) == 0
    backend_check('migration-current', ['-m', 'alembic', 'current'])
    if migrated:
        backend_check('migration-check', ['-m', 'alembic', 'check'])
    backend_check('backend-tests', ['-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--junitxml=/results/backend.xml'])
    if migrated:
        backend_check('persistence-tests', ['-m', 'pytest', '-q', '-p', 'no:cacheprovider', '/workspace/scripts/phase1/test_persistence.py', '--junitxml=/results/persistence.xml'])
    backend_check('ruff', ['-m', 'ruff', 'check', '--no-cache', 'app/ai/tool_groups', 'app/services/alfred', 'app/services/chat.py', 'app/services/domain_events.py'])
    backend_check('mypy', ['-m', 'mypy', '--cache-dir=/tmp/mypy', 'app/ai/tool_groups', 'app/services/alfred/memory.py', 'app/services/domain_events.py'])
    for name, cmd in [('frontend-tests', ['npm', 'test', '--', '--maxWorkers=1']), ('frontend-build', ['npm', 'run', 'build'])]:
        check(name, container(name, node, cmd, mounts=[(SNAP / 'frontend', '/frontend', 'rw'),
            (jsdeps / 'node_modules', '/frontend/node_modules', 'rw')], work='/frontend'))
    # Compose parsing only, against snapshot, with a clean environment; never start it.
    check('compose-config', ['env', '-i', 'PATH=' + os.environ['PATH'], 'HOME=' + str(RUN), shutil.which('docker-compose') or str(Path.home() / '.docker/cli-plugins/docker-compose'), '-f', str(SNAP / 'docker-compose.yml'), 'config', '--quiet'])
except (Exception, KeyboardInterrupt) as exc:
    RESULTS['harness_error'] = str(exc)
    print(type(exc).__name__ + ': ' + str(exc), flush=True)
finally:
    cleanup()
    after = production_state()
    (RUN / 'production-after.json').write_text(json.dumps(after, indent=2))
    RESULTS['production_unchanged'] = before == after
    (RUN / 'results.json').write_text(json.dumps(RESULTS, indent=2))
    print(json.dumps(RESULTS, indent=2), flush=True)
    print('Retained results: ' + str(RUN), flush=True)
raise SystemExit(0 if RESULTS.get('production_unchanged') and all(v == 0 for k, v in RESULTS.items() if k != 'production_unchanged') else 1)
