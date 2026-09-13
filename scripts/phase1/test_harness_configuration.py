"""Host-safe checks of required suites and command construction; no IACS or Docker.

All runner process/container checks are replaced before execute() is called.
Only temporary files, advisory locks and repository source text are inspected.
"""

from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import recovery_checks
from validate import FullRunBusyError, Harness, arguments, full_run_lock, run


ROOT = Path(__file__).resolve().parents[2]


class RecoveryProfileTests(unittest.TestCase):
    def test_container_user_matches_bind_owner_without_restoring_capabilities(self):
        harness = object.__new__(Harness)
        harness.prefix = 'identity-probe'
        harness.owned = []
        harness.write = lambda *args: None
        with patch('validate.os.getuid', return_value=1001), patch('validate.os.getgid', return_value=1002):
            command = harness.container('python-deps', 'inert-image', ['true'])
            self.assertEqual(command[command.index('--user') + 1], '1001:1002')
            self.assertEqual(command[command.index('--cap-drop') + 1], 'ALL')
            self.assertNotIn('--cap-add', command)
            postgres = harness.container('postgres', 'inert-image', ['true'])
            self.assertEqual(postgres[postgres.index('--user') + 1], '0')

    def test_every_phase1_test_has_one_explicit_execution_class(self):
        self.assertEqual(recovery_checks.inventory_errors(ROOT), [])

    def test_full_defaults_include_critical_recovery_and_the_known_simulation_path(self):
        args = arguments(['--mode', 'full', '--allow-downloads'])
        selected = dict(recovery_checks.persistence_checks(args))
        required = {
            'test_access_pipeline.py', 'test_alfred_approval_persistence.py',
            'test_confirmed_notifications.py', 'test_movement_admission.py',
            'test_visitor_reservation_recovery.py', 'test_automation_admission_order.py',
            'test_incoming_messages.py', 'test_whatsapp_inbox.py', 'test_feedback_recovery.py',
            'test_release_restore_rehearsal.py', 'test_recovery_hold_persistence.py',
            'test_delivery_schema_compatibility.py', 'test_access_device_command_journal.py',
        }
        self.assertTrue(required <= {Path(path).name for path in selected})
        self.assertEqual(selected['scripts/phase1/test_recovery_boundaries.py'], 'diagnostic')
        self.assertEqual(recovery_checks.schema_checks(args), ['scripts/phase1/test_schema_contract.py'])

    def test_explicit_selections_are_additive_and_duplicate_paths_run_once(self):
        default = arguments(['--allow-downloads'])
        args = arguments(['--allow-downloads', '--diagnostic', 'scripts/phase1/test_access_pipeline.py',
                          '--persistence-test', 'scripts/phase1/test_additional.py',
                          '--schema-check', 'scripts/phase1/test_schema_contract.py'])
        selected = recovery_checks.persistence_checks(args)
        self.assertEqual(len(selected), len(recovery_checks.persistence_checks(default)) + 1)
        self.assertEqual(len(selected), len({path for path, _ in selected}))
        self.assertIn(('scripts/phase1/test_additional.py', 'persistence'), selected)
        self.assertEqual(recovery_checks.schema_checks(args), recovery_checks.schema_checks(default))

    def test_full_snapshot_requires_new_helpers_host_checks_and_all_executed_files(self):
        args = arguments(['--allow-downloads', '--include', 'backend/app/synthetic_new.py'])
        selected = set(recovery_checks.required_sources(args))
        expected = set(recovery_checks.HOST_TESTS + recovery_checks.REQUIRED_HELPERS + recovery_checks.SCHEMA_CHECKS)
        expected.update(path for path, _ in recovery_checks.persistence_checks(args))
        self.assertTrue(expected <= selected)
        self.assertIn('backend/app/synthetic_new.py', selected)

    def test_snapshot_mode_does_not_require_runtime_suites(self):
        args = arguments(['--check-only', '--include', 'backend/app/synthetic_new.py'])
        self.assertEqual(recovery_checks.required_sources(args), ['backend/app/synthetic_new.py'])

    def test_unclassified_full_check_is_refused_before_snapshot_or_docker(self):
        args = arguments(['--allow-downloads'])
        with patch('validate.inventory_errors', return_value=['Unclassified phase1 test: synthetic.py']), \
             patch('validate.discover_sources') as discover, \
             patch.object(Harness, 'production_state') as production, patch('sys.stderr'):
            self.assertEqual(run(args), 2)
        discover.assert_not_called()
        production.assert_not_called()

    def test_suite_names_are_bounded_unique_and_cannot_select_a_foreign_database(self):
        identities = [recovery_checks.suite_identity('iacs-p1-' + 'a' * 12, index) for index in range(1, 1000)]
        self.assertEqual(len(set(identities)), 999)
        self.assertEqual(identities[0], ('suite-001', 'iacs_p1_aaaaaaaaaaaa_s001'))
        for prefix, index in [('iacs-postgres', 1), ('iacs-p1-' + 'a' * 12, 0),
                              ('iacs-p1-' + 'a' * 12, 1000), ('iacs-p1-../escape', 1)]:
            with self.subTest(prefix=prefix, index=index), self.assertRaises(ValueError):
                recovery_checks.suite_identity(prefix, index)

    def test_pytest_artifacts_and_temporary_files_stay_under_a_unique_results_child(self):
        command = recovery_checks.pytest_arguments('suite-001', ['/workspace/scripts/phase1/test_persistence.py'])
        self.assertIn('--basetemp=/results/suite-001/tmp', command)
        self.assertIn('--junitxml=/results/suite-001/junit.xml', command)
        self.assertIn('/workspace/scripts/phase1/test_persistence.py', command)
        for invalid in ('../escape', '', '/results', 'has space'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                recovery_checks.pytest_arguments(invalid)

    def test_ci_uses_default_full_inventory_and_uploads_nested_recovery_artifacts(self):
        workflow = (ROOT / '.github/workflows/backend-alfred.yml').read_text()
        step = workflow.split('name: Run the same isolated locked checks as local development', 1)[1]
        step = step.split('      - name:', 1)[0]
        self.assertIn('scripts/phase1/validate.py --mode full --allow-downloads', step)
        self.assertNotIn('--diagnostic', step)
        self.assertNotIn('--persistence-test', step)
        self.assertNotIn('--schema-check', step)
        self.assertIn('iacs-evidence/*/artifacts/**', workflow)


class FullRunLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'synthetic-full.lock'

    def test_competing_full_run_fails_without_waiting_or_interrupting_the_owner(self):
        with full_run_lock('full', lock_path=self.path):
            with self.assertRaisesRegex(FullRunBusyError, 'Another full isolated harness is active'):
                with full_run_lock('full', lock_path=self.path):
                    self.fail('A second full run acquired the same slot')
        with full_run_lock('full', lock_path=self.path):
            pass

    def test_exception_releases_the_owned_slot(self):
        with self.assertRaisesRegex(RuntimeError, 'synthetic'):
            with full_run_lock('full', lock_path=self.path):
                raise RuntimeError('synthetic')
        with full_run_lock('full', lock_path=self.path):
            pass

    def test_snapshot_and_db_free_do_not_create_or_take_the_full_slot(self):
        for mode in ('snapshot', 'db-free'):
            with full_run_lock(mode, lock_path=self.path):
                self.assertFalse(self.path.exists())

    def test_lock_refuses_a_symlink_without_changing_its_target(self):
        target = self.root / 'synthetic-target'
        target.write_text('unchanged')
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            with full_run_lock('full', lock_path=self.path):
                self.fail('Followed a lock symlink')
        self.assertEqual(target.read_text(), 'unchanged')


class IsolatedCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.run = self.root / 'evidence'
        self.run.mkdir()
        self.args = arguments(['--mode', 'full', '--reuse-dependencies', str(self.root / 'deps')])
        self.harness = Harness(self.args, self.run, self.source)
        self.images = {name: 'sha256:' + str(index) * 64 for index, name in enumerate(('backend', 'node', 'postgres', 'redis'), 1)}
        self.calls = []
        self.fail = {}
        self.raise_at = None

    def execute(self):
        def check(name, command, *args, **kwargs):
            if '-m' in command and 'pytest' in command:
                self.assertTrue((self.run / 'artifacts' / name).is_dir(),
                                'Pytest evidence parent must exist before execution')
            self.calls.append((name, command, kwargs))
            if name == self.raise_at:
                raise RuntimeError('synthetic suite process failure')
            code = self.fail.get(name, 0)
            self.harness.results[name] = code
            return code

        with ExitStack() as patches:
            patches.enter_context(patch('validate.image_references', return_value=self.images))
            patches.enter_context(patch.object(self.harness, 'image', side_effect=lambda image: image))
            patches.enter_context(patch.object(self.harness, 'dependencies', return_value=(self.root / 'python-deps', self.root / 'js-deps')))
            patches.enter_context(patch.object(self.harness, 'check', side_effect=check))
            restore = patches.enter_context(patch('database_restore.run_rehearsal',
                side_effect=lambda *args: self.calls.append(('database-restore', [], {}))))
            self.harness.execute()
        return restore

    def test_full_checks_require_schema_and_real_restore_before_any_test_clone(self):
        restore = self.execute()
        restore.assert_called_once()
        names = [name for name, _, _ in self.calls]
        self.assertLess(names.index('migration-check'), names.index('database-restore'))
        self.assertLess(names.index('database-restore'), names.index('suite-001-create'))
        self.assertIn('schema-check-1', names)
        arguments_ = restore.call_args.args
        self.assertIs(arguments_[0], self.harness)
        self.assertEqual(arguments_[1], self.images)
        self.assertEqual(arguments_[-1], 'container:' + self.harness.prefix + '-postgres')

    def test_every_file_has_a_clean_clone_redis_namespace_retained_temp_and_image_identity(self):
        self.execute()
        commands = {name: command for name, command, _ in self.calls}
        suites = recovery_checks.persistence_checks(self.args)
        databases = set()
        for index, (relative, _) in enumerate(suites, 1):
            name, database = recovery_checks.suite_identity(self.harness.prefix, index)
            command = commands[name]
            databases.add(database)
            self.assertIn('/workspace/' + relative, command)
            self.assertIn('IACS_DATABASE_URL=postgresql+asyncpg://phase1:synthetic-phase1-only@127.0.0.1:5432/' + database, command)
            self.assertIn(f'IACS_REDIS_URL=redis://127.0.0.1:6379/{index}', command)
            self.assertIn('IACS_RECOVERY_PROBES=synthetic-only', command)
            self.assertIn('IACS_IMAGE_IDENTITY=' + self.images['backend'], command)
            self.assertIn(f'--basetemp=/results/{name}/tmp', command)
            self.assertIn(f'--junitxml=/results/{name}/junit.xml', command)
            self.assertIn(f'{self.source}:/workspace:ro', command)
            self.assertNotIn('--publish', command)
            for suffix in ('-create', '-drop'):
                client = commands[name + suffix]
                self.assertIn(self.images['postgres'], client)
                self.assertIn('container:' + self.harness.prefix + '-postgres', client)
                self.assertEqual(client[-1], database)
                self.assertNotIn('--force', client)
            self.assertIn('--template=' + self.harness.prefix.replace('-', '_'), commands[name + '-create'])
        self.assertEqual(len(databases), len(suites))
        self.assertEqual(commands['redis-start'][-2:], ['--databases', str(len(suites) + 1)])

    def test_assertion_failure_keeps_cleanup_and_later_required_suites(self):
        self.fail['suite-001'] = 1
        self.execute()
        names = [name for name, _, _ in self.calls]
        self.assertLess(names.index('suite-001'), names.index('suite-001-drop'))
        self.assertLess(names.index('suite-001-drop'), names.index('suite-002-create'))
        self.assertEqual(self.harness.results['suite-001'], 1)

    def test_process_exception_still_drops_its_owned_suite_database(self):
        self.raise_at = 'suite-001'
        with self.assertRaisesRegex(RuntimeError, 'synthetic suite process failure'):
            self.execute()
        self.assertIn('suite-001-drop', [name for name, _, _ in self.calls])

    def test_failed_clone_is_not_run_or_dropped_and_does_not_hide_its_failure(self):
        self.fail['suite-001-create'] = 1
        self.execute()
        names = [name for name, _, _ in self.calls]
        self.assertNotIn('suite-001', names)
        self.assertNotIn('suite-001-drop', names)
        self.assertIn('suite-002', names)
        self.assertEqual(self.harness.results['suite-001-create'], 1)
        self.assertEqual(self.harness.checks['suite-001']['status'], 'not_attempted')

    def test_failed_migration_never_clones_or_runs_schema_dependent_pytest(self):
        self.fail['migration-up'] = 1
        restore = self.execute()
        restore.assert_not_called()
        self.assertFalse(any(name.startswith('suite-') for name, _, _ in self.calls))
        for index, _ in enumerate(recovery_checks.persistence_checks(self.args), 1):
            name, _ = recovery_checks.suite_identity(self.harness.prefix, index)
            self.assertEqual(self.harness.checks[name]['status'], 'not_attempted')


if __name__ == '__main__':
    unittest.main()
