"""Host-safe snapshot tests: temporary files only, with no IACS or Docker imports."""

import hashlib
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from source_snapshot import changed_snapshot_sources, copy_sources, discover_sources, source_file, source_fingerprint
from validate import Harness, arguments, image_references, validate_resource_paths, verify_dependency_manifests


class SourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'workspace'
        self.root.mkdir()
        self.destination = Path(self.temp.name) / 'snapshot'

    def make_file(self, relative, content='synthetic source\n'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_new_files_are_copied_and_hashed_alongside_tracked_files(self):
        self.make_file('tracked.py')
        added = self.make_file('backend/app/ai/context.py', 'new source\n')
        fixture = self.make_file('backend/tests/fixtures/catalog.json', '[]\n')
        hashes = copy_sources(self.root, self.destination, ['tracked.py', 'deleted.py'],
                              ['backend/app/ai/context.py', 'backend/tests/fixtures/catalog.json'])
        self.assertEqual(set(hashes), {'tracked.py', 'backend/app/ai/context.py',
                                      'backend/tests/fixtures/catalog.json'})
        for source in (added, fixture):
            relative = str(source.relative_to(self.root))
            self.assertEqual((self.destination / relative).read_bytes(), source.read_bytes())
            self.assertEqual(hashes[relative], hashlib.sha256(source.read_bytes()).hexdigest())

    def test_rejects_missing_directory_absolute_and_parent_paths(self):
        self.make_file('valid.py')
        for relative in ('', '.', 'missing.py', str(self.root / 'valid.py'),
                         '../outside.py', 'folder/../valid.py'):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                source_file(self.root, relative)

    def test_excluded_files_are_refused_explicitly_and_omitted_when_tracked(self):
        excluded = ['.env', '.env.local', '.git/config', '.codex/config.toml',
                    '.agents/settings.json', 'data/state.db', 'logs/output.log',
                    'frontend/node_modules/module.js', 'frontend/dist/index.html',
                    '.venv/bin/python', '__pycache__/module.pyc', '.pytest_cache/state',
                    'auth-secret.key', 'auth-secret.key.previous', 'credentials.pem',
                    'build/generated.py', 'nested/.env/token']
        for relative in excluded:
            self.make_file(relative)
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                copy_sources(self.root, self.destination, [], [relative])
        self.assertEqual(copy_sources(self.root, self.destination, excluded), {})

    def test_rejects_leaf_parent_and_broken_symlinks(self):
        self.make_file('real/file.py')
        (self.root / 'leaf.py').symlink_to(self.root / 'real/file.py')
        (self.root / 'linked').symlink_to(self.root / 'real', target_is_directory=True)
        (self.root / 'broken.py').symlink_to(self.root / 'missing.py')
        for relative in ('leaf.py', 'linked/file.py', 'broken.py'):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                source_file(self.root, relative)

    def test_invalid_inclusion_fails_before_any_source_is_copied(self):
        self.make_file('valid.py')
        with self.assertRaises(ValueError):
            copy_sources(self.root, self.destination, ['valid.py'], ['missing.py'])
        self.assertFalse(self.destination.exists())

    def inventory(self, tracked=(), untracked=(), ignored=(), includes=()):
        outputs = [('\0'.join(paths) + '\0').encode() for paths in (tracked, untracked, ignored)]
        with patch('source_snapshot.subprocess.check_output', side_effect=outputs):
            return discover_sources(self.root, includes)

    def test_auto_includes_untracked_application_fixture_and_probe(self):
        paths = ['backend/app/new.py', 'frontend/src/New.tsx',
                 'backend/tests/contracts/fixture.json', 'scripts/phase1/test_schema_contract.py']
        for relative in paths:
            self.make_file(relative)
        selected, inventory = self.inventory(untracked=paths + ['scratch.txt', 'frontend/node_modules/module.js'])
        self.assertEqual(selected, sorted(paths))
        self.assertEqual(inventory['automatic_untracked'], sorted(paths))
        self.assertEqual(inventory['untracked_outside_source_roots'], ['scratch.txt'])
        self.assertEqual(inventory['excluded'], ['frontend/node_modules/module.js'])

    def test_ignored_source_fails_closed_unless_explicitly_included(self):
        self.make_file('backend/app/ignored.py')
        with self.assertRaisesRegex(ValueError, 'Ignored first-party'):
            self.inventory(ignored=['backend/app/ignored.py'])
        selected, _ = self.inventory(ignored=['backend/app/ignored.py'], includes=['backend/app/ignored.py'])
        self.assertIn('backend/app/ignored.py', selected)

    def test_runtime_and_generated_files_do_not_block_discovery(self):
        selected, _ = self.inventory(ignored=['frontend/node_modules/library/index.js',
                                             'backend/.env', 'frontend/test-results/run.json', 'docs/.DS_Store'])
        self.assertEqual(selected, [])

    def test_new_source_symlink_refuses_complete_inventory(self):
        source = self.make_file('real.py')
        (self.root / 'backend/app').mkdir(parents=True)
        (self.root / 'backend/app/link.py').symlink_to(source)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.inventory(untracked=['backend/app/link.py'])

    def test_tracked_symlink_is_not_silently_omitted(self):
        source = self.make_file('real.py')
        (self.root / 'link.py').symlink_to(source)
        with self.assertRaisesRegex(ValueError, 'Unsafe selected source'):
            copy_sources(self.root, self.destination, ['real.py', 'link.py'])
        self.assertFalse(self.destination.exists())

    def test_fingerprint_is_order_independent_and_includes_paths_and_content(self):
        self.assertEqual(source_fingerprint({'a': '1', 'b': '2'}), source_fingerprint({'b': '2', 'a': '1'}))
        self.assertNotEqual(source_fingerprint({'a': '1'}), source_fingerprint({'b': '1'}))
        self.assertNotEqual(source_fingerprint({'a': '1'}), source_fingerprint({'a': '2'}))

    def test_postcheck_integrity_detects_input_changes_but_allows_new_build_outputs(self):
        self.make_file('frontend/src/App.tsx')
        hashes = copy_sources(self.root, self.destination, ['frontend/src/App.tsx'])
        (self.destination / 'frontend/dist').mkdir()
        (self.destination / 'frontend/dist/index.html').write_text('generated')
        self.assertEqual(changed_snapshot_sources(self.destination, hashes), [])
        (self.destination / 'frontend/src/App.tsx').write_text('changed input')
        self.assertEqual(changed_snapshot_sources(self.destination, hashes), ['frontend/src/App.tsx'])
        (self.destination / 'frontend/src/App.tsx').unlink()
        self.assertEqual(changed_snapshot_sources(self.destination, hashes), ['frontend/src/App.tsx'])


class HarnessSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshot = self.root / 'source'
        self.snapshot.mkdir()

    def test_snapshot_and_no_migration_modes_are_explicit(self):
        self.assertEqual(arguments(['--check-only']).mode, 'snapshot')
        self.assertEqual(arguments(['--no-migrations', '--reuse-dependencies', '/temporary/deps']).mode, 'db-free')

    def test_runtime_modes_require_dependency_authorization(self):
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            arguments([])
        self.assertEqual(arguments(['--allow-downloads']).mode, 'full')

    def test_db_free_mode_rejects_schema_dependent_requests(self):
        for flag in ('--diagnostic', '--persistence-test', '--schema-check'):
            with self.subTest(flag=flag), patch('sys.stderr'), self.assertRaises(SystemExit):
                arguments(['--mode', 'db-free', '--reuse-dependencies', '/temporary/deps', flag, 'scripts/phase1/test_probe.py'])

    def test_conflicting_safe_mode_aliases_are_rejected(self):
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            arguments(['--check-only', '--no-migrations'])

    def test_evidence_and_reuse_paths_cannot_overlap_production_binds(self):
        production = {'containers': [{'Mounts': [{'Type': 'bind', 'Source': str(self.root / 'live')}]}]}
        validate_resource_paths(self.root / 'evidence', None, production)
        for evidence, reuse in ((self.root / 'live/results', None),
                                (self.root, None), (self.root / 'evidence', self.root / 'live/deps')):
            with self.subTest(evidence=evidence, reuse=reuse), self.assertRaisesRegex(ValueError, 'overlaps'):
                validate_resource_paths(evidence, reuse, production)

    def test_reuse_checks_both_manifests_and_locks(self):
        previous = self.root / 'previous'
        mapping = {'backend': ('python-deps', ('pyproject.toml', 'uv.lock')),
                   'frontend': ('js-deps', ('package.json', 'package-lock.json'))}
        for source, (folder, names) in mapping.items():
            (self.snapshot / source).mkdir()
            (previous / folder).mkdir(parents=True)
            for name in names:
                (self.snapshot / source / name).write_text('synthetic lock')
                (previous / folder / name).write_text('synthetic lock')
        (previous / 'python-deps/.venv').mkdir()
        (previous / 'js-deps/node_modules').mkdir()
        self.assertEqual(len(verify_dependency_manifests(self.snapshot, previous)), 4)
        (previous / 'js-deps/package-lock.json').write_text('different lock')
        with self.assertRaisesRegex(ValueError, 'differs'):
            verify_dependency_manifests(self.snapshot, previous)

    def test_image_references_come_from_assessed_source(self):
        (self.snapshot / 'frontend').mkdir()
        digest = 'a' * 64
        (self.snapshot / 'frontend/Dockerfile').write_text(f'FROM node:22-alpine@sha256:{digest} AS build\n')
        (self.snapshot / 'docker-compose.yml').write_text(
            f'  postgres:\n    image: pgvector/pgvector:pg16@sha256:{digest}\n'
            f'  redis:\n    image: redis:7-alpine@sha256:{digest}\n')
        refs = image_references(self.snapshot)
        self.assertTrue(refs['node'].endswith(digest))
        (self.snapshot / 'frontend/Dockerfile').write_text('FROM node:latest\n')
        with self.assertRaisesRegex(ValueError, 'pinned node'):
            image_references(self.snapshot)

    def test_db_free_execution_never_starts_services_or_runs_migrations(self):
        args = arguments(['--mode', 'db-free', '--reuse-dependencies', '/temporary/deps'])
        harness = Harness(args, self.root, self.snapshot)
        commands = []
        def record(name, command, *args, **kwargs):
            commands.append((name, command))
            return 0
        with patch('validate.image_references', return_value={'backend': 'backend', 'node': 'node'}), \
             patch.object(harness, 'image', side_effect=lambda ref: ref), \
             patch.object(harness, 'dependencies', return_value=(self.root / 'python-deps', self.root / 'js-deps')), \
             patch.object(harness, 'check', side_effect=record):
            harness.execute()
        names = [name for name, _ in commands]
        self.assertIn('backend-tests', names)
        self.assertNotIn('postgres-start', names)
        self.assertNotIn('migration-up', names)
        self.assertNotIn('persistence-tests', names)
        for _, command in commands:
            self.assertNotIn('bridge', command)
            self.assertNotIn('scripts/backend-pytest', command)
        backend = next(command for name, command in commands if name == 'backend-tests')
        self.assertIn('IACS_PHASE1_MODE=db-free', backend)
        self.assertIn('none', backend)
        self.assertIn(f'{self.root}/artifacts:/results:rw', backend)
        self.assertNotIn(f'{self.root}:/results:rw', backend)
        self.assertIn(f'{self.snapshot}:/workspace:ro', backend)
        self.assertEqual(harness.checks['persistence-tests']['status'], 'not_attempted')

    def test_timeout_kills_and_waits_for_owned_host_process_group(self):
        args = arguments(['--check-only'])
        harness = Harness(args, self.root, self.snapshot)
        with patch('validate.subprocess.Popen') as popen, patch('validate.os.killpg') as kill:
            process = popen.return_value
            process.pid = 12345
            process.wait.side_effect = [subprocess.TimeoutExpired('synthetic', 1), 0]
            with self.assertRaises(subprocess.TimeoutExpired):
                harness.check('timeout', ['synthetic'], timeout=1)
        kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)
        self.assertEqual(harness.results['timeout'], 124)

    def test_cleanup_refuses_foreign_labels_and_still_checks_remaining(self):
        harness = Harness(arguments(['--check-only']), self.root, self.snapshot)
        harness.owned = ['owned-name']
        found = argparse.Namespace(returncode=0, stdout=json.dumps([{'Config': {'Labels': {'iacs.phase1': 'other-run'}}}]))
        with patch('validate.subprocess.run', return_value=found) as run, patch.object(harness, 'capture', return_value=''):
            harness.cleanup()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(harness.results['cleanup'], 1)


if __name__ == '__main__':
    unittest.main()
