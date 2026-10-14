"""Small host-only fixtures; no Docker, network, database or large allocations."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import subprocess

from storage import StorageGuard, StorageViolation, disposable_bytes, remove_disposable, GIB, docker_free, validation_interrupts, draining_cleanup


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def guard(self):
        return StorageGuard(self.root, vm_probe=lambda: 100 * GIB)

    def test_admission_refuses_low_host_and_vm_capacity(self):
        for host, vm in [(39, 100), (100, 39)]:
            with self.subTest(host=host, vm=vm), patch('storage.shutil.disk_usage', return_value=SimpleNamespace(free=host * GIB)):
                guard = StorageGuard(self.root, vm_probe=lambda: vm * GIB)
                with self.assertRaises(StorageViolation):
                    guard.check()

    def test_budget_counts_prior_runs_not_only_current(self):
        for name in ['old', 'new']:
            folder = self.root / name / 'postgres'
            folder.mkdir(parents=True)
            (folder / 'synthetic').write_bytes(b'x')
        self.assertGreater(disposable_bytes([self.root]), 0)
        with patch('storage.disposable_bytes', return_value=11 * GIB), patch('storage.shutil.disk_usage', return_value=SimpleNamespace(free=100 * GIB)):
            with self.assertRaisesRegex(StorageViolation, 'across retained runs'):
                self.guard().check()

    def test_shared_inodes_count_once_and_generated_artifacts_are_budgeted(self):
        deps = self.root / 'python-deps'; deps.mkdir()
        (deps / 'file').write_bytes(b'x')
        source = self.root / 'source' / 'postgres'; source.mkdir(parents=True)
        (source / 'fixture').write_bytes(b'x')
        self.assertEqual(disposable_bytes([self.root]), disposable_bytes([self.root, self.root]))
        artifacts=self.root/'artifacts'/'suite'/'tmp';artifacts.mkdir(parents=True)
        (artifacts/'measurement.json').write_text('{}')
        expected=sum(p.stat().st_blocks*512 for p in [deps/'file',source/'fixture',artifacts/'measurement.json'])
        self.assertEqual(disposable_bytes([self.root]),expected)

    def test_host_refusal_happens_before_any_vm_operation(self):
        from unittest.mock import Mock
        probe=Mock()
        with patch('storage.shutil.disk_usage',return_value=SimpleNamespace(free=39*GIB)):
            with self.assertRaises(StorageViolation):StorageGuard(self.root,vm_probe=probe).check()
        probe.assert_not_called()

    def test_missing_image_never_launches_probe_and_cached_id_cannot_move(self):
        with patch('storage.subprocess.check_output',side_effect=subprocess.CalledProcessError(1,['inspect'])) as command, patch('storage.subprocess.run') as cleanup:
            with self.assertRaises(subprocess.CalledProcessError):docker_free('mutable-tag')
        self.assertEqual(command.call_args.args[0][:3],['docker','image','inspect']);cleanup.assert_not_called()
        with patch('storage.subprocess.check_output',side_effect=['sha256:synthetic','Filesystem 1024-blocks Used Available Capacity Mounted\nx 100 1 99 1% /\n']) as command,patch('storage.subprocess.run'):
            self.assertEqual(docker_free('mutable-tag'),99*1024)
        workload=command.call_args.args[0]
        self.assertIn('--pull=never',workload);self.assertIn('sha256:synthetic',workload);self.assertNotIn('mutable-tag',workload)

    def test_unreadable_and_slow_accounting_fail_closed(self):
        def denied(root,**kwargs):
            kwargs['onerror'](PermissionError('synthetic'))
            return iter([])
        with patch('storage.os.walk',side_effect=denied):
            with self.assertRaisesRegex(StorageViolation,'traversal'):disposable_bytes([self.root])
        with patch('storage.time.monotonic',side_effect=[0,6]):
            with self.assertRaisesRegex(StorageViolation,'five-second'):disposable_bytes([self.root])

    def test_one_directory_cannot_finish_after_accounting_deadline(self):
        (self.root/'one-file').write_text('synthetic')
        samples=iter([0,0,0,0,6])
        with patch('storage.time.monotonic',side_effect=lambda:next(samples,6)):
            with self.assertRaisesRegex(StorageViolation,'five-second'):disposable_bytes([self.root])

    def test_sigterm_and_repeated_interrupt_drain_before_lock_release(self):
        import signal
        from validate import validation_run_lock,ValidationRunBusyError
        events=[];lock=self.root/'slot.lock'
        with validation_run_lock('runtime',lock_path=lock),validation_interrupts():
            try:
                signal.raise_signal(signal.SIGTERM)
            except KeyboardInterrupt:events.append('interrupted')
            finally:
                with draining_cleanup():
                    signal.raise_signal(signal.SIGINT)
                    events.append('drained')
                    with self.assertRaises(ValidationRunBusyError):
                        with validation_run_lock('db-free',lock_path=lock):self.fail('Released before drain')
        with validation_run_lock('full',lock_path=lock):events.append('released')
        self.assertEqual(events,['interrupted','drained','released'])

    def test_unrelated_disk_growth_and_accounting_failure_abort(self):
        guard = self.guard()
        with patch('storage.shutil.disk_usage', return_value=SimpleNamespace(free=100 * GIB)):
            guard.check()
        guard.last = float('-inf')
        with patch('storage.shutil.disk_usage', return_value=SimpleNamespace(free=29 * GIB)):
            with self.assertRaises(StorageViolation):
                guard.check()
        guard.last = float('-inf')
        with patch('storage.shutil.disk_usage', side_effect=OSError('synthetic failure')):
            with self.assertRaisesRegex(StorageViolation, 'accounting failed'):
                self.guard().check()

    def test_intermediate_vm_growth_consumes_budget_before_final_image_exists(self):
        with patch('storage.shutil.disk_usage', return_value=SimpleNamespace(free=100 * GIB)):
            guard=StorageGuard(self.root,vm_probe=lambda:100*GIB);guard.check()
            guard.vm_probe=lambda:89*GIB;guard.last=float('-inf')
            with self.assertRaisesRegex(StorageViolation,'10 GiB'):guard.check()

    def test_unknown_vm_accounting_refuses_admission(self):
        with patch('storage.shutil.disk_usage', return_value=SimpleNamespace(free=100 * GIB)):
            with self.assertRaisesRegex(StorageViolation,'accounting failed'):
                StorageGuard(self.root,vm_probe=lambda:None).check()

    def test_cleanup_preserves_source_and_canonical_preparation(self):
        run = self.root / 'run'; run.mkdir()
        pg = run / 'postgres'; pg.mkdir(); (pg / 'file').write_text('synthetic')
        source = run / 'source'; source.mkdir()
        canonical = self.root / 'python-deps'; canonical.mkdir()
        self.assertEqual(remove_disposable(run, [pg], []), [str(pg)])
        self.assertTrue(source.exists()); self.assertTrue(canonical.exists())
        with self.assertRaises(StorageViolation):
            remove_disposable(run, [canonical], [])

    def test_mount_overlap_and_symlink_substitution_refuse_deletion(self):
        run = self.root / 'run'; run.mkdir()
        pg = run / 'postgres'; pg.mkdir()
        for mount in [run, pg, pg / 'child']:
            with self.subTest(mount=mount), self.assertRaises(StorageViolation):
                remove_disposable(run, [pg], [str(mount)])
        pg.rmdir(); pg.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(StorageViolation):
            remove_disposable(run, [pg], [])

    def test_reuse_does_not_copy_or_own_shared_dependencies(self):
        from validate import Harness, arguments
        previous = self.root / 'previous'; previous.mkdir()
        (previous / 'images.json').write_text('{"backend":"b","node":"n"}')
        run = self.root / 'run'; run.mkdir()
        harness = Harness(arguments(['--mode','db-free','--reuse-dependencies',str(previous)]), run, self.root)
        harness.check = lambda *a, **kw: 0
        with patch('validate.verify_dependency_manifests'), patch('validate.manifest_hashes', return_value={}), patch('validate.shutil.copytree', side_effect=AssertionError('Must not copy')):
            actual = harness.dependencies({'backend':'b','node':'n'})
        self.assertEqual(actual, (previous/'python-deps', previous/'js-deps'))
        self.assertEqual(harness.disposable, [])

    def test_storage_abort_kills_cli_and_drains_owned_containers(self):
        from validate import Harness, arguments
        from unittest.mock import Mock
        run = self.root / 'run'; run.mkdir()
        harness = Harness(arguments(['--check-only']),run,self.root)
        harness.storage = Mock(cleaning=False)
        harness.storage.check.side_effect = [None,None,StorageViolation('synthetic reserve loss')]
        harness.cleanup = Mock()
        with patch('validate.subprocess.Popen') as popen, patch('validate.os.killpg') as kill:
            process = popen.return_value; process.pid=12345
            process.wait.side_effect=[subprocess.TimeoutExpired('synthetic',1),0]
            with self.assertRaises(StorageViolation):harness.check('guard-abort',['synthetic'])
        kill.assert_called_once(); harness.cleanup.assert_called_once()
        self.assertEqual(harness.results['guard-abort'],125)
        self.assertEqual(harness.results['storage_abort'],1)

    def test_cleanup_logging_failure_does_not_skip_owned_file_cleanup(self):
        from validate import Harness, arguments
        run = self.root / 'run';run.mkdir()
        pg = run/'postgres';pg.mkdir()
        harness=Harness(arguments(['--check-only']),run,self.root)
        harness.disposable=[pg];harness.capture=lambda *a:''
        harness.write=lambda *a: (_ for _ in ()).throw(OSError('synthetic full disk'))
        with patch('validate.all_container_mounts',return_value=[]):harness.cleanup()
        self.assertFalse(pg.exists())
        self.assertEqual(harness.results['cleanup_evidence'],1)

    def test_unknown_mount_accounting_preserves_disposable_files(self):
        from validate import Harness, arguments
        run=self.root/'run';run.mkdir();pg=run/'postgres';pg.mkdir()
        harness=Harness(arguments(['--check-only']),run,self.root)
        harness.disposable=[pg];harness.capture=lambda *a:''
        with patch('validate.all_container_mounts',side_effect=OSError('synthetic inspection failure')):harness.cleanup()
        self.assertTrue(pg.exists());self.assertEqual(harness.results['cleanup'],1)


if __name__ == '__main__':
    unittest.main()
