"""Exercise the exact CI collector against complete, failed and partial trees."""
import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ci_evidence import collect, selected


class CIEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.validation = self.root / "validation"
        self.runtime = self.root / "runtime"
        self.run = self.validation / "iacs-validation-fixture"
        self.output = self.root / "selected"

    def put(self, path, data="synthetic"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data) if isinstance(data, (dict, list)) else data)

    def complete(self):
        for name in ["manifest.json", "checks.json", "source-integrity.json", "dependencies.json", "images.json",
                     "cleanup.json", "production-before.json", "production-after.json", "storage.json", "database-restore.json"]:
            self.put(self.run / name, {"synthetic": True})
        self.put(self.run / "results.json", {"backend-tests": 0, "database-restore": 0, "cleanup": 0,
            "source_integrity": 0, "production_unchanged": True})
        self.put(self.run / "checks.json", {"backend-tests": {"status": "passed", "exit_code": 0}})
        self.put(self.run / "persistence-suites.json", [{"evidence": "artifacts/suite-001"}])
        for name in ["artifacts/backend-tests/junit.xml", "artifacts/suite-001/junit.xml",
                     "artifacts/database-restore/verification.json", "artifacts/schema-contract-123456abcdef/report.json"]:
            self.put(self.run / name, "synthetic report")

    def collect(self):
        return collect([("validation", self.validation), ("runtime", self.runtime)], self.output)

    def test_clean_pass_retains_reports_without_scratch_or_allowed_looking_extensions(self):
        self.complete()
        useful = ["suite-001.log", "migration-up.log", "database-restore-verify.log", "artifacts/suite-001/query-efficiency.json",
                  "artifacts/schema-contract-123456abcdef/staged-20261014_0018.json"]
        forbidden = ["auth-secret.key", ".env", "tokens.json", "unrelated.log", "artifacts/unrelated.json",
            "artifacts/suite-001/tmp/auth-secret.key", "artifacts/suite-001/tmp/credentials.json",
            "artifacts/suite-001/tmp/unrelated.log", "artifacts/suite-001/tmp/nested/rehearsal.dump",
            "artifacts/database-restore/rehearsal.dump", "artifacts/database-restore/tokens.json",
            "artifacts/schema-contract-123456abcdef/unrelated.json", "artifacts/suite-001/photo.png",
            "artifacts/suite-001/tmp/postgres/PG_VERSION", "source/.env.example", "runtime/postgres/base/1",
            "node_modules/report.json", "artifacts/suite-001/tmp/cache/report.log"]
        for name in useful + forbidden:
            self.put(self.run / name)
        report = self.collect()
        self.assertEqual(report["errors"], [])
        kept = {item["path"].removeprefix("validation/iacs-validation-fixture/") for item in report["files"]}
        self.assertTrue(set(useful) <= kept)
        self.assertFalse(set(forbidden) & kept)
        self.assertEqual(report["runs"][0]["status"], "passed")
        self.assertTrue((self.run / "artifacts/database-restore/rehearsal.dump").exists(), "Collector must not delete owner scratch")

    def test_failing_run_keeps_actionable_log_and_junit_without_claiming_pass(self):
        self.put(self.run / "results.json", {"backend-tests": 1})
        self.put(self.run / "checks.json", {"backend-tests": {"status": "failed", "exit_code": 1}})
        self.put(self.run / "backend-tests.log", "synthetic assertion failure")
        self.put(self.run / "artifacts/backend-tests/junit.xml", "<testsuite failures='1'/>")
        report = self.collect()
        self.assertEqual(report["runs"][0]["status"], "partial_or_failed")
        self.assertEqual(report["errors"], [])
        self.assertEqual(len(report["files"]), 4)

    def test_interrupted_run_retains_partial_source_identity_and_failure_proof(self):
        self.put(self.run / "manifest.json", {"source_fingerprint": "synthetic-source"})
        self.put(self.run / "migration-up.log", "interrupted migration fixture")
        report = self.collect()
        self.assertEqual(report["runs"][0]["missing"], ["checks.json", "results.json"])
        self.assertEqual(len(report["files"]), 2)
        self.assertEqual(report["runs"][0]["status"], "partial_or_failed")

    def test_missing_successful_evidence_is_failure_but_other_reports_survive(self):
        self.complete()
        (self.run / "artifacts/suite-001/junit.xml").unlink()
        report = self.collect()
        self.assertTrue(report["errors"])
        self.assertIn("artifacts/suite-001/junit.xml", report["runs"][0]["missing"])
        self.assertTrue(report["files"])

    def test_selected_file_and_directory_symlink_escapes_are_rejected(self):
        self.complete()
        outside = self.root / "private.json"
        self.put(outside, "never copy")
        (self.run / "checks.json").unlink()
        (self.run / "checks.json").symlink_to(outside)
        (self.run / "artifacts/suite-002").symlink_to(self.root, target_is_directory=True)
        report = self.collect()
        self.assertTrue(any("symlink" in error for error in report["errors"]))
        self.assertFalse(any(item["path"].endswith("checks.json") for item in report["files"]))
        self.assertFalse(selected(Path("../checks.json")))

    def test_root_escape_destination_reuse_and_storage_guard_fail_closed(self):
        self.complete()
        with self.assertRaises(ValueError):
            collect([("validation", self.validation)], self.run / "selected")
        self.output.mkdir()
        with self.assertRaises(ValueError):
            self.collect()
        with self.assertRaisesRegex(RuntimeError, "storage"):
            collect([("validation", self.validation)], self.root / "new", check_storage=lambda: (_ for _ in ()).throw(RuntimeError("storage")))

    def test_runtime_has_only_explicit_pdf_proof_and_missing_runs_are_visible(self):
        run = self.runtime / "iacs-runtime-pdf-fixture"
        for name in ["checks.json", "result.json", "cleanup.json", "production-before.json", "production-after.json", "storage.json"]:
            self.put(run / name, {})
        self.put(run / "results.json", {"smoke": 0})
        self.put(run / "checks.json", {"build": {"command": ["inert"]}, "smoke": {"command": ["inert"]}})
        self.put(run / "result.json", {"status": "passed", "production_unchanged": True, "cleanup_results": {"cleanup": 0}})
        for name in ["build.log", "smoke.log", "artifacts/report.pdf", "artifacts/source-parity.json", "artifacts/photo.png", "artifacts/token.json"]:
            self.put(run / name)
        report = self.collect()
        self.assertEqual(report["errors"], [])
        self.assertTrue(any(item["path"].endswith("artifacts/report.pdf") for item in report["files"]))
        self.assertTrue(any(item["path"].endswith("build.log") for item in report["files"]))
        self.assertFalse(any(item["path"].endswith((".png", "token.json")) for item in report["files"]))
        self.assertEqual(report["runs"][0]["status"], "not_started")

    def test_runtime_smoke_success_does_not_override_later_failure_or_interruption(self):
        for final in [
            {"status": "failed", "production_unchanged": True, "cleanup_results": {"cleanup": 0}, "error": "Not a PDF"},
            {"status": "failed", "production_unchanged": True, "cleanup_results": {"cleanup": 1}},
            {"status": "failed", "production_unchanged": False, "cleanup_results": {"cleanup": 0}},
            {"status": "interrupted", "production_unchanged": True, "cleanup_results": {"cleanup": 0}},
        ]:
            with self.subTest(final=final), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                root = Path(temporary)
                run = root / "runtime/iacs-runtime-pdf-fixture"
                self.put(run / "results.json", {"smoke": 0})
                self.put(run / "checks.json", {"smoke": {"status": "passed", "command": ["inert"]}})
                self.put(run / "result.json", final)
                self.put(run / "smoke.log", "synthetic smoke accepted before final failure")
                report = collect([("runtime", root / "runtime")], root / "selected")
                self.assertEqual(report["runs"][0]["status"], "partial_or_failed")
                self.assertTrue(any(item["path"].endswith("result.json") for item in report["files"]))

    def test_successful_run_missing_attempted_command_log_fails_collection(self):
        self.complete()
        self.put(self.run / "checks.json", {"backend-tests": {"status": "passed", "command": ["inert"]}})
        report = self.collect()
        self.assertIn("backend-tests.log", report["runs"][0]["missing"])
        self.assertTrue(report["errors"])

    def test_workflow_invokes_this_collector_and_uploads_only_its_staged_directory(self):
        workflow = Path(__file__).resolve().parents[2] / ".github/workflows/project-checks.yml"
        content = workflow.read_text()
        self.assertIn("python3 scripts/validation/ci_evidence.py", content)
        self.assertIn('path: ${{ runner.temp }}/iacs-upload-evidence', content)
        self.assertNotIn("/artifacts/**", content)
        self.put(self.run / "manifest.json", {"source_fingerprint": "synthetic"})
        self.put(self.run / "migration-up.log", "synthetic interrupted run")
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("ci_evidence.py")),
            "--validation-root", str(self.validation), "--runtime-root", str(self.runtime),
            "--output", str(self.output)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(json.loads((self.output / "collection.json").read_text())["files"]), 2)

    @unittest.skipUnless(importlib.util.find_spec("yaml"), "PyYAML is available in the locked harness dependencies")
    def test_workflow_yaml_preserves_gates_and_failure_upload(self):
        import yaml
        workflow = Path(__file__).resolve().parents[2] / ".github/workflows/project-checks.yml"
        data = yaml.load(workflow.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(set(data["jobs"]), {"isolated-contracts", "dependency-audits", "image-builds"})
        self.assertEqual(data["permissions"], {"contents": "read"})
        job = data["jobs"]["isolated-contracts"]
        self.assertEqual(job["timeout-minutes"], "60")
        steps = job["steps"]
        selection = next(step for step in steps if "ci_evidence.py" in step.get("run", ""))
        upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
        self.assertEqual(selection["if"], "always()")
        self.assertEqual(upload["if"], "always()")
        self.assertEqual(upload["with"]["retention-days"], "14")
        self.assertEqual(upload["with"]["path"], "${{ runner.temp }}/iacs-upload-evidence")
        self.assertLess(steps.index(selection), steps.index(upload))
        self.assertTrue(any("validate.py --mode full --allow-downloads" in step.get("run", "") for step in steps))


if __name__ == "__main__":
    unittest.main()
