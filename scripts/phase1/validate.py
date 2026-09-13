#!/usr/bin/env python3
"""Sequential disposable checks; never use production Compose or exec wrappers."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid

from source_snapshot import changed_snapshot_sources, copy_sources, discover_sources, source_file, source_fingerprint
from recovery_checks import (
    HOST_TESTS, PERSISTENCE_TESTS, inventory_errors, persistence_checks, pytest_arguments,
    required_sources, schema_checks, suite_identity,
)

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION = ['iacs-backend', 'iacs-frontend', 'iacs-postgres', 'iacs-redis', 'iacs-updater']
LINT_COMMANDS = {'ruff': ['-m', 'ruff', 'check', '--no-cache', 'app/services/notification_runs.py', 'app/services/notification_dispatch.py', 'app/services/notification_rules.py', 'app/services/mutation_context.py', 'app/services/schedule_overrides.py', 'app/services/schedule_assignments.py', 'app/services/schedule_operations.py', 'app/ai/tool_inputs.py', 'app/ai/tools.py', 'app/ai/context.py', 'app/ai/tool_groups', 'app/services/alfred', 'app/services/chat.py', 'app/services/domain_events.py', 'app/services/access/decision.py', 'app/services/access/reads.py', 'app/services/access/evidence.py', 'app/services/access/execution.py', 'app/services/access/enrichment.py', 'app/services/access/payloads.py', 'app/services/access/hardware.py', 'app/services/access_events.py'], 'mypy': ['-m', 'mypy', '--cache-dir=/tmp/mypy', 'app/services/notification_runs.py', 'app/services/notification_dispatch.py', 'app/services/notification_rules.py', 'app/services/mutation_context.py', 'app/services/schedule_overrides.py', 'app/services/schedule_assignments.py', 'app/services/schedule_operations.py', 'app/ai/tool_inputs.py', 'app/ai/tools.py', 'app/ai/context.py', 'app/ai/tool_groups', 'app/services/alfred/memory.py', 'app/services/domain_events.py', 'app/services/access/decision.py', 'app/services/access/reads.py', 'app/services/access/evidence.py', 'app/services/access/execution.py', 'app/services/access/enrichment.py', 'app/services/access/payloads.py', 'app/services/access/hardware.py', 'app/services/access_events.py']}


# The scoped style/type baseline must not hide undefined runtime names elsewhere.
LINT_COMMANDS["undefined-names"] = ["-m", "ruff", "check", "--no-cache", "--select", "F821", "app"]
LINT_COMMANDS["ruff"] += ["app/services/actionable_notifications.py", "app/simulation/scenarios.py"]

def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--include', action='append', default=[], metavar='REPO_RELATIVE_FILE',
                        help='Require a safe new/ignored source or fixture file in the snapshot.')
    parser.add_argument('--evidence-root', type=Path,
                        default=Path.home() / 'Documents' / 'IACS Regression Baselines')
    parser.add_argument('--mode', choices=('snapshot', 'db-free', 'full'), default='full',
                        help='snapshot: host-only; db-free: no services/migrations; full: disposable DB.')
    parser.add_argument('--check-only', action='store_true', help='Alias for --mode snapshot; no Docker/imports/install.')
    parser.add_argument('--no-migrations', action='store_true', help='Alias for --mode db-free; no DB services or persistence checks.')
    parser.add_argument('--reuse-dependencies', type=Path, metavar='PRIOR_RUN',
                        help='Copy and verify python-deps and js-deps from an existing evidence directory.')
    parser.add_argument('--allow-downloads', action='store_true',
                        help='Allow image pulls and exact-lock dependency installation in manifest-only containers.')
    parser.add_argument('--diagnostic', action='append', default=[], metavar='REPO_RELATIVE_TEST',
                        help='Run an additional inert pytest diagnostic separately; failures remain failures.')
    parser.add_argument('--persistence-test', action='append', default=[], metavar='REPO_RELATIVE_TEST',
                        help='Add a pytest file; each file uses its own disposable database and Redis namespace.')
    parser.add_argument('--schema-check', action='append', default=[], metavar='REPO_RELATIVE_SCRIPT',
                        help='Run a schema-check CLI inside the disposable database namespace.')
    parser.add_argument('--database-restore', action='store_true',
                        help='Explicit spelling of the pg_dump/pg_restore rehearsal already required in full mode.')
    args = parser.parse_args(argv)
    if args.check_only and args.no_migrations:
        parser.error('--check-only and --no-migrations cannot be combined')
    if args.check_only:
        args.mode = 'snapshot'
    if args.no_migrations:
        args.mode = 'db-free'
    if args.mode != 'full' and (args.diagnostic or args.persistence_test or args.schema_check or args.database_restore):
        parser.error('Diagnostics, persistence tests and schema checks require --mode full')
    if args.mode != 'snapshot' and not (args.reuse_dependencies or args.allow_downloads):
        parser.error('Execution requires --reuse-dependencies or explicit --allow-downloads for exact locks')
    return args


class FullRunBusyError(RuntimeError):
    """Another full harness owns this host's bounded execution slot."""


@contextmanager
def full_run_lock(mode, *, lock_path=None):
    """Fail clearly instead of overlapping full runs or interrupting another run."""
    if mode != 'full':
        yield
        return
    path = lock_path or Path(tempfile.gettempdir()) / f'iacs-phase1-full-{os.getuid()}.lock'
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FullRunBusyError(f'Another full isolated harness is active on this host ({path}); retry after it finishes.') from exc
        yield
    finally:
        os.close(descriptor)


def manifest_hashes(snapshot):
    paths = ['backend/pyproject.toml', 'backend/uv.lock', 'frontend/package.json', 'frontend/package-lock.json']
    return {relative: hashlib.sha256((snapshot / relative).read_bytes()).hexdigest() for relative in paths}


def verify_dependency_manifests(snapshot, previous):
    """Refuse drift before importing or executing a reused dependency environment."""
    expected = manifest_hashes(snapshot)
    for relative, digest in expected.items():
        folder = 'python-deps' if relative.startswith('backend/') else 'js-deps'
        candidate = previous / folder / Path(relative).name
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError(f'Missing/non-regular dependency manifest: {candidate}')
        if hashlib.sha256(candidate.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Reused dependency manifest differs from snapshot: {relative}')
    for relative in ('python-deps/.venv', 'js-deps/node_modules'):
        if not (previous / relative).is_dir() or (previous / relative).is_symlink():
            raise ValueError(f'Missing/non-directory dependency environment: {relative}')
    return expected


def image_references(snapshot):
    """Read only the literal pinned image declarations; do not evaluate Compose."""
    dockerfile = (snapshot / 'frontend/Dockerfile').read_text()
    compose = (snapshot / 'docker-compose.yml').read_text()
    patterns = {
        'node': (dockerfile, r'^FROM\s+(node:[^\s]+@sha256:[0-9a-f]{64})(?:\s|$)'),
        'postgres': (compose, r'^\s+image:\s+(pgvector/pgvector:[^\s]+@sha256:[0-9a-f]{64})\s*$'),
        'redis': (compose, r'^\s+image:\s+(redis:[^\s]+@sha256:[0-9a-f]{64})\s*$'),
    }
    refs = {}
    for name, (content, pattern) in patterns.items():
        matches = re.findall(pattern, content, re.MULTILINE)
        if len(matches) != 1:
            raise ValueError(f'Expected one literal pinned {name} image in snapshot')
        refs[name] = matches[0]
    refs['backend'] = os.environ.get('PHASE1_BACKEND_IMAGE', 'intelligentaccesssystem-backend:latest')
    return refs


def validate_resource_paths(evidence, reuse, production):
    """Check proposed paths before creating directories or copying source/deps."""
    for row in production['containers']:
        for mount in row['Mounts']:
            if mount['Type'] != 'bind':
                continue
            source = Path(mount['Source']).resolve()
            for candidate in (evidence, reuse):
                if candidate is not None:
                    candidate = candidate.expanduser().resolve()
                    if candidate.is_relative_to(source) or source.is_relative_to(candidate):
                        raise ValueError('Evidence/dependency directory overlaps production bind mount')


class Harness:
    def __init__(self, args, run, snapshot):
        self.args, self.run, self.snapshot = args, run, snapshot
        self.prefix = 'iacs-p1-' + uuid.uuid4().hex[:12]
        self.owned, self.results, self.checks = [], {}, {}

    def write(self, name, data):
        (self.run / name).write_text(json.dumps(data, indent=2) + '\n')

    def capture(self, *command):
        return subprocess.check_output(command, cwd=self.snapshot, text=True, timeout=60).strip()

    def production_state(self):
        names = set(self.capture('docker', 'ps', '-a', '--format', '{{.Names}}').splitlines())
        present = sorted(names.intersection(PRODUCTION))
        rows = json.loads(self.capture('docker', 'inspect', *present)) if present else []
        return {'missing': sorted(set(PRODUCTION) - names), 'containers': [
            {k: row[k] for k in ('Id', 'Name', 'Image')} | {
                'Mounts': sorted(row['Mounts'], key=lambda mount: mount['Destination']),
                'StartedAt': row['State']['StartedAt'], 'RestartCount': row['RestartCount'],
                'Running': row['State']['Running']} for row in rows]}

    def check(self, name, command, required=False, timeout=1200, classification='static'):
        print(name, flush=True)
        self.checks[name] = {'command': command, 'cwd': str(self.snapshot),
                             'classification': classification, 'status': 'running'}
        self.write('checks.json', self.checks)
        code = 1
        with (self.run / (name + '.log')).open('w') as log:
            process = subprocess.Popen(command, cwd=self.snapshot, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                code = 124
                raise
            finally:
                self.results[name] = code
                self.checks[name]['status'] = 'passed' if code == 0 else 'failed'
                self.checks[name]['exit_code'] = code
                self.write('checks.json', self.checks)
                self.write('results.json', self.results)
        if required and code:
            raise RuntimeError(f'{name} failed ({code}); see retained log')
        return code

    def skip(self, name, reason):
        self.checks[name] = {'status': 'not_attempted', 'reason': reason}
        self.write('checks.json', self.checks)

    def image(self, ref):
        try:
            return self.capture('docker', 'image', 'inspect', ref, '--format', '{{.Id}}')
        except subprocess.CalledProcessError:
            if not self.args.allow_downloads:
                raise RuntimeError(f'Image unavailable locally; downloads not authorized: {ref}') from None
            name = 'pull-' + ref.split(':')[0].replace('/', '-')
            self.check(name, ['docker', 'pull', ref], True, classification='dependency-setup')
            return self.capture('docker', 'image', 'inspect', ref, '--format', '{{.Id}}')

    def container(self, name, img, command, *, mounts=(), env=None, network='none', work='/tmp', detached=False):
        cname = self.prefix + '-' + name
        self.owned.append(cname)
        self.write('owned-containers.json', self.owned)
        command_line = ['docker', 'run', '--name', cname, '--label', 'iacs.phase1=' + self.prefix,
                        '--network', network, '--cpus', '1', '--memory', '1536m', '--pids-limit', '256',
                        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--no-healthcheck',
                        '--user', '0', '--workdir', work]
        if name == 'postgres':
            command_line += ['--cap-add', 'CHOWN', '--cap-add', 'DAC_OVERRIDE', '--cap-add', 'SETUID', '--cap-add', 'SETGID']
        for source, dest, mode in mounts:
            command_line += ['-v', f'{source}:{dest}:{mode}']
        for key, value in (env or {}).items():
            command_line += ['-e', f'{key}={value}']
        if detached:
            command_line += ['-d']
        return command_line + ['--entrypoint', command[0], img, *command[1:]]

    def cleanup(self):
        failures = []
        for name in reversed(self.owned):
            try:
                found = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True, timeout=30)
                if found.returncode:
                    continue
                row = json.loads(found.stdout)[0]
                if (row['Config'].get('Labels') or {}).get('iacs.phase1') != self.prefix:
                    failures.append(name)
                    continue
                result = subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)
                if result.returncode:
                    failures.append(name)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                failures.append(f'{name}: {type(exc).__name__}')
        try:
            remaining = self.capture('docker', 'ps', '-aq', '--filter', 'label=iacs.phase1=' + self.prefix)
        except (OSError, subprocess.SubprocessError):
            remaining = 'inspection_failed'
        self.write('cleanup.json', {'failures': failures, 'remaining': remaining})
        self.results['cleanup'] = int(bool(failures or remaining))

    def dependencies(self, images):
        python_deps, js_deps = self.run / 'python-deps', self.run / 'js-deps'
        if self.args.reuse_dependencies:
            previous = self.args.reuse_dependencies.resolve()
            verify_dependency_manifests(self.snapshot, previous)
            previous_images = json.loads((previous / 'images.json').read_text())
            for name in ('backend', 'node'):
                if previous_images.get(name) != images[name]:
                    raise ValueError(f'Reused {name} image identity does not match this run')
            # Tests may write dependency caches; never mutate the prior evidence.
            for folder in ('python-deps', 'js-deps'):
                shutil.copytree(previous / folder, self.run / folder, symlinks=True)
        else:
            for directory, source, names in (
                (python_deps, 'backend', ('pyproject.toml', 'uv.lock')),
                (js_deps, 'frontend', ('package.json', 'package-lock.json')),
            ):
                directory.mkdir()
                for name in names:
                    shutil.copy2(self.snapshot / source / name, directory / name)
            self.check('python-dependencies', self.container('python-deps', images['backend'],
                ['uv', 'sync', '--locked', '--extra', 'dev', '--no-install-project', '--python', '/usr/local/bin/python'],
                mounts=[(python_deps, '/deps', 'rw')], env={'UV_PROJECT_ENVIRONMENT': '/deps/.venv'},
                network='bridge', work='/deps'), True, classification='dependency-setup')
            self.check('frontend-dependencies', self.container('js-deps', images['node'],
                ['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'],
                mounts=[(js_deps, '/deps', 'rw')], network='bridge', work='/deps'),
                True, classification='dependency-setup')
        self.check('python-lock-check', self.container('python-lock-check', images['backend'],
            ['uv', 'sync', '--locked', '--offline', '--check', '--extra', 'dev', '--no-install-project', '--python', '/usr/local/bin/python'],
            mounts=[(python_deps, '/deps', 'ro')], env={'UV_PROJECT_ENVIRONMENT': '/deps/.venv', 'UV_CACHE_DIR': '/tmp/uv-cache'}, work='/deps'),
            True, classification='dependency-verification')
        python_identity = "import importlib.metadata as m,json,platform,sys; print(json.dumps({'python':sys.version,'platform':platform.platform(),'packages':sorted([(d.metadata['Name'],d.version) for d in m.distributions()])},indent=2))"
        self.check('python-identity', self.container('python-identity', images['backend'],
            ['/deps/.venv/bin/python', '-c', python_identity], mounts=[(python_deps, '/deps', 'ro')]),
            True, classification='dependency-verification')
        self.check('uv-identity', self.container('uv-identity', images['backend'], ['uv', '--version']),
            True, classification='dependency-verification')
        self.check('frontend-lock-check', self.container('frontend-lock-check', images['node'],
            ['node', '-e', NODE_LOCK_CHECK], mounts=[(js_deps, '/deps', 'ro')], work='/deps'),
            True, classification='dependency-verification')
        self.check('frontend-tree-check', self.container('frontend-tree-check', images['node'],
            ['npm', 'ls', '--all', '--json'], mounts=[(js_deps, '/deps', 'ro')], work='/deps'),
            True, classification='dependency-verification')
        self.check('npm-identity', self.container('npm-identity', images['node'], ['npm', '--version']),
            True, classification='dependency-verification')
        self.write('dependencies.json', {'manifests': manifest_hashes(self.snapshot),
                   'reused_from': str(self.args.reuse_dependencies) if self.args.reuse_dependencies else None,
                   'verification': 'exact copied manifests; offline uv sync --check; installed npm lock versions and tree',
                   'identities': ['python-identity.log', 'uv-identity.log', 'frontend-lock-check.log', 'npm-identity.log']})
        return python_deps, js_deps

    def execute(self):
        references = image_references(self.snapshot)
        required = ('backend', 'node', 'postgres', 'redis') if self.args.mode == 'full' else ('backend', 'node')
        images = {name: self.image(references[name]) for name in required}
        self.write('images.json', images)
        self.write('image-references.json', references)
        deps, jsdeps = self.dependencies(images)
        for folder in ('runtime', 'logs', 'artifacts'):
            (self.run / folder).mkdir()
        database = self.prefix.replace('-', '_')
        env = {'IACS_ENVIRONMENT': 'testing', 'IACS_PHASE1_MODE': 'db-free',
               'IACS_DATABASE_URL': 'postgresql+asyncpg://phase1:synthetic-phase1-only@127.0.0.1:1/' + database,
               'IACS_REDIS_URL': 'redis://127.0.0.1:1/0', 'IACS_AUTO_CREATE_SCHEMA': 'false',
               'IACS_SEED_DEMO_DATA': 'false', 'IACS_AUTH_SECRET_KEY': 'phase1-synthetic-auth-root-never-production',
               'IACS_DATA_DIR': '/isolated/runtime', 'IACS_LOG_DIR': '/isolated/logs', 'IACS_WORKSPACE_DIR': '/workspace',
               'IACS_IMAGE_IDENTITY': images['backend'],
               'PYTHONPATH': '/workspace/backend', 'PYTHONDONTWRITEBYTECODE': '1', 'HOME': '/tmp'}
        mounts = [(self.snapshot, '/workspace', 'ro'), (deps, '/deps', 'ro'),
                  (self.run / 'runtime', '/isolated/runtime', 'rw'), (self.run / 'logs', '/isolated/logs', 'rw'),
                  (self.run / 'artifacts', '/results', 'rw')]
        def backend_check(name, command, required=False, *, persistent=False, classification='db-free', extra_env=None):
            # Pytest creates basetemp itself, but requires its parent to exist.
            if command[:2] == ['-m', 'pytest']:
                (self.run / 'artifacts' / name).mkdir()
            return self.check(name, self.container(name, images['backend'], ['/deps/.venv/bin/python', *command],
                mounts=mounts, env=(persistence_env if persistent else env) | (extra_env or {}),
                network=namespace if persistent else 'none', work='/workspace/backend'),
                required, classification=classification)
        backend_check('isolation-db-free', ['/workspace/scripts/phase1/preflight.py'], True)
        for index, relative in enumerate(HOST_TESTS):
            backend_check('snapshot-tests' if index == 0 else 'harness-configuration-tests', ['/workspace/' + relative], True)
        backend_check('architecture-boundaries', ['/workspace/scripts/architecture/check_boundaries.py'],
                      classification='static')
        backend_check('compile', ['-c', "import pathlib; files=list(pathlib.Path('app').rglob('*.py'))+list(pathlib.Path('tests').rglob('*.py')); [compile(p.read_bytes(),str(p),'exec') for p in files]; print(len(files),'files compiled')"])
        backend_check('backend-tests', pytest_arguments('backend-tests'))
        for name, command in LINT_COMMANDS.items():
            backend_check(name, command)
        for name, command in [('frontend-tests', ['npm', 'test', '--', '--maxWorkers=1']), ('frontend-build', ['npm', 'run', 'build'])]:
            self.check(name, self.container(name, images['node'], command,
                mounts=[(self.snapshot / 'frontend', '/frontend', 'rw'), (jsdeps / 'node_modules', '/frontend/node_modules', 'rw')],
                work='/frontend'), classification='db-free')
        plugin = Path.home() / '.docker/cli-plugins/docker-compose'
        compose = ([shutil.which('docker-compose')] if shutil.which('docker-compose') else
                   [str(plugin)] if plugin.is_file() else ['docker', 'compose'])
        self.check('compose-config', ['env', '-i', 'PATH=' + os.environ['PATH'], 'HOME=' + str(self.run),
            *compose, '-f', str(self.snapshot / 'docker-compose.yml'), 'config', '--quiet'])
        if self.args.mode != 'full':
            for name in ('migration-up', 'migration-current', 'migration-check', 'persistence-tests', 'schema-checks', 'diagnostics', 'database-restore'):
                self.skip(name, 'db-free mode prohibits services, migrations and schema-dependent execution')
            return
        suites = persistence_checks(self.args)
        if len(suites) > 999:
            raise ValueError('The bounded full harness supports at most 999 isolated pytest suites')
        for folder in ('postgres', 'redis'):
            (self.run / folder).mkdir()
        self.check('postgres-start', self.container('postgres', images['postgres'], ['docker-entrypoint.sh', 'postgres'],
            mounts=[(self.run / 'postgres', '/var/lib/postgresql/data', 'rw')],
            env={'POSTGRES_USER': 'phase1', 'POSTGRES_PASSWORD': 'synthetic-phase1-only', 'POSTGRES_DB': database},
            detached=True), True, classification='persistence-setup')
        namespace = 'container:' + self.prefix + '-postgres'
        self.check('redis-start', self.container('redis', images['redis'],
            ['redis-server', '--save', '', '--appendonly', 'no', '--databases', str(len(suites) + 1)],
            mounts=[(self.run / 'redis', '/data', 'rw')], network=namespace, detached=True), True, classification='persistence-setup')
        persistence_env = env | {'IACS_PHASE1_MODE': 'persistence',
            'IACS_DATABASE_URL': env['IACS_DATABASE_URL'].replace(':1/', ':5432/'),
            'IACS_REDIS_URL': 'redis://127.0.0.1:6379/0'}
        backend_check('isolation-persistence', ['/workspace/scripts/phase1/preflight.py'], True, persistent=True, classification='persistence-setup')
        self.check('isolation-inspect', ['docker', 'inspect', self.prefix + '-postgres', self.prefix + '-redis',
            self.prefix + '-isolation-persistence'], True, classification='persistence-setup')
        migrated = backend_check('migration-up', ['-m', 'alembic', 'upgrade', 'head'], persistent=True, classification='schema') == 0
        backend_check('migration-current', ['-m', 'alembic', 'current'], persistent=True, classification='schema')
        if migrated:
            backend_check('migration-check', ['-m', 'alembic', 'check'], persistent=True, classification='schema')
            from database_restore import run_rehearsal
            # The migrated template has no application connections or test rows.
            # The rehearsal owns its two extra databases and never mutates it.
            run_rehearsal(self, images, persistence_env, mounts, namespace)
        else:
            for name in ('migration-check', 'persistence-tests', 'diagnostics', 'database-restore'):
                self.skip(name, 'migration-up failed; no schema-dependent tests attempted')
        for index, relative in enumerate(schema_checks(self.args), 1):
            backend_check(f'schema-check-{index}', ['/workspace/' + relative], persistent=True, classification='schema',
                          extra_env={'IACS_SCHEMA_CONTRACT': 'synthetic-only'})
        suite_records = []
        for index, (relative, classification) in enumerate(suites, 1):
            name, suite_database = suite_identity(self.prefix, index)
            suite_records.append({'check': name, 'path': relative, 'classification': classification,
                'template_database': database, 'database': suite_database, 'redis_database': index,
                'evidence': f'artifacts/{name}'})
            self.write('persistence-suites.json', suite_records)
            if not migrated:
                self.skip(name, 'migration-up failed; no schema-dependent tests attempted')
                continue

            def database_client(suffix, command):
                return self.check(name + suffix, self.container(name + suffix, images['postgres'],
                    command, env={'PGPASSWORD': 'synthetic-phase1-only'}, network=namespace),
                    timeout=180, classification='persistence-setup')

            created = database_client('-create', ['createdb', '-h', '127.0.0.1', '-U', 'phase1',
                                                 '--template=' + database, suite_database]) == 0
            if not created:
                self.skip(name, 'disposable suite database could not be created')
                continue
            try:
                backend_check(name, pytest_arguments(name, ['/workspace/' + relative]),
                    persistent=True, classification=classification, extra_env={
                        'IACS_DATABASE_URL': persistence_env['IACS_DATABASE_URL'].rsplit('/', 1)[0] + '/' + suite_database,
                        'IACS_REDIS_URL': f'redis://127.0.0.1:6379/{index}',
                        'IACS_RECOVERY_PROBES': 'synthetic-only',
                    })
            finally:
                # Never use --force or drop the template. If an unexpected
                # connection prevents cleanup, retain the failure and let the
                # outer owner remove only this run's PostgreSQL container.
                database_client('-drop', ['dropdb', '-h', '127.0.0.1', '-U', 'phase1', suite_database])


NODE_LOCK_CHECK = r"""
const fs = require('fs');
const lock = JSON.parse(fs.readFileSync('package-lock.json', 'utf8'));
const differences = [], installed = [];
for (const [location, expected] of Object.entries(lock.packages)) {
  if (!location || !location.startsWith('node_modules/')) continue;
  const manifest = location + '/package.json';
  if (!fs.existsSync(manifest)) {
    if (!expected.optional) differences.push(location + ': missing');
    continue;
  }
  const actual = JSON.parse(fs.readFileSync(manifest, 'utf8'));
  installed.push([location, actual.version]);
  if (actual.version !== expected.version) differences.push(location + ': installed=' + actual.version + ' locked=' + expected.version);
}
console.log(JSON.stringify({node: process.version, platform: process.platform, arch: process.arch, installed, differences}, null, 2));
if (differences.length) process.exit(1);
"""


def run(args):
    required = required_sources(args)
    before = None
    try:
        if args.mode == 'full':
            errors = inventory_errors(ROOT)
            if errors:
                raise ValueError('; '.join(errors))
        paths, inventory = discover_sources(ROOT, required)
        # All explicit execution targets must be source files before any resource is created.
        for relative in required:
            source_file(ROOT, relative)
        evidence = args.evidence_root.expanduser().resolve()
        if evidence.is_relative_to(ROOT.resolve()) or ROOT.resolve().is_relative_to(evidence):
            raise ValueError('Evidence root must be separate from the assessed repository')
        if args.mode != 'snapshot':
            # Probe from the existing source directory: do not create anything
            # under a proposed evidence path until its mounts are checked.
            before = Harness(args, evidence, ROOT).production_state()
            validate_resource_paths(evidence, args.reuse_dependencies, before)
        evidence.mkdir(parents=True, exist_ok=True)
        run = Path(tempfile.mkdtemp(prefix='iacs-phase1-', dir=evidence))
        snapshot = run / 'source'
        hashes = copy_sources(ROOT, snapshot, paths, required)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'Preflight/snapshot refused: {exc}', file=sys.stderr)
        return 2
    harness = Harness(args, run, snapshot)
    print('Retained results: ' + str(run), flush=True)
    harness.write('manifest.json', {
        'repository': str(ROOT), 'branch': harness.capture('git', '-C', str(ROOT), 'branch', '--show-current'),
        'commit': harness.capture('git', '-C', str(ROOT), 'rev-parse', 'HEAD'),
        'status': harness.capture('git', '-C', str(ROOT), 'status', '--short'),
        'diff_stat': harness.capture('git', '-C', str(ROOT), 'diff', '--stat'),
        'mode': args.mode, 'inventory': inventory, 'sha256': hashes,
        'source_fingerprint': source_fingerprint(hashes), 'dependency_manifests': manifest_hashes(snapshot),
        'host_python': sys.version, 'host_platform': platform.platform(), 'argv': sys.argv,
    })
    if args.mode == 'snapshot':
        harness.write('checks.json', {'snapshot': {'status': 'passed', 'classification': 'host-static'},
            'runtime': {'status': 'not_attempted', 'reason': 'snapshot mode does not use Docker, install, import IACS or run tests'}})
        harness.write('results.json', {'snapshot': 0})
        print(f'Snapshot complete: {len(hashes)} files; fingerprint {source_fingerprint(hashes)}', flush=True)
        return 0
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        harness.write('production-before.json', before)
        harness.execute()
    except (Exception, KeyboardInterrupt) as exc:
        harness.results['harness_error'] = f'{type(exc).__name__}: {exc}'
        print(harness.results['harness_error'], flush=True)
    finally:
        # A second interrupt must not interrupt owned-resource cleanup.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        old_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            harness.cleanup()
            changed_sources = changed_snapshot_sources(snapshot, hashes)
            harness.write('source-integrity.json', {'changed_inputs': changed_sources,
                                                   'new_build_artifacts': 'not part of assessed input manifest'})
            harness.results['source_integrity'] = int(bool(changed_sources))
            if before is not None:
                try:
                    after = harness.production_state()
                    harness.write('production-after.json', after)
                    harness.results['production_unchanged'] = before == after
                except (OSError, subprocess.SubprocessError, ValueError) as exc:
                    harness.results['production_unchanged'] = False
                    harness.results['production_comparison_error'] = type(exc).__name__
            else:
                harness.results['production_unchanged'] = False
            harness.write('results.json', harness.results)
        finally:
            signal.signal(signal.SIGTERM, previous_handler)
            signal.signal(signal.SIGINT, old_int)
        print(json.dumps(harness.results, indent=2), flush=True)
        print('Retained results: ' + str(run), flush=True)
    return 0 if harness.results.get('production_unchanged') and all(
        value == 0 for key, value in harness.results.items() if key != 'production_unchanged') else 1


def main(argv=None):
    args = arguments(argv)
    try:
        with full_run_lock(args.mode):
            return run(args)
    except (FullRunBusyError, OSError) as exc:
        print(f'Full harness refused: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
