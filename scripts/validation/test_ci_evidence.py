"""Exercise the exact CI collector against complete, failed and partial trees."""
import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ci_evidence import collect, selected
from recovery_checks import HOST_CHECKS


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
        self.put(self.run / "manifest.json", {"database_restore": True})
        self.put(self.run / "results.json", {"backend-tests": 0, "database-restore": 0, "cleanup": 0,
            "source_integrity": 0, "production_unchanged": True})
        self.put(self.run / "checks.json", {"backend-tests": {"status": "passed", "exit_code": 0}})
        self.put(self.run / "persistence-suites.json", [{"evidence": "artifacts/suite-001"}])
        for name in ["artifacts/backend-tests/junit.xml", "artifacts/suite-001/junit.xml",
                     "artifacts/database-restore/verification.json", "artifacts/schema-contract-123456abcdef/report.json"]:
            self.put(self.run / name, "synthetic report")

    def collect(self):
        return collect([("validation", self.validation), ("runtime", self.runtime)], self.output)

    def test_unrequested_restore_needs_skip_evidence_without_restore_reports(self):
        self.complete()
        self.put(self.run / "manifest.json", {"database_restore": False})
        results = json.loads((self.run / "results.json").read_text())
        del results["database-restore"]
        self.put(self.run / "results.json", results)
        self.put(self.run / "checks.json", {"database-restore": {
            "status": "not_attempted", "reason": "Not requested; opt in with --database-restore"}})
        for name in ("database-restore.json", "artifacts/database-restore/verification.json"):
            (self.run / name).unlink()
        report = self.collect()
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["runs"][0]["status"], "passed")
        self.assertEqual(report["runs"][0]["missing"], [])

    def test_requested_restore_still_requires_success_and_verification(self):
        self.complete()
        (self.run / "artifacts/database-restore/verification.json").unlink()
        report = self.collect()
        self.assertTrue(report["errors"])
        self.assertIn("artifacts/database-restore/verification.json", report["runs"][0]["missing"])

    def test_requested_restore_cannot_pass_without_its_result(self):
        self.complete()
        results = json.loads((self.run / "results.json").read_text())
        del results["database-restore"]
        self.put(self.run / "results.json", results)
        report = self.collect()
        self.assertTrue(any("has no successful result" in error for error in report["errors"]))

    def test_unrequested_restore_needs_an_explicit_not_attempted_check(self):
        self.complete()
        self.put(self.run / "manifest.json", {"database_restore": False})
        results = json.loads((self.run / "results.json").read_text())
        del results["database-restore"]
        self.put(self.run / "results.json", results)
        report = self.collect()
        self.assertTrue(any("must be recorded as not attempted" in error for error in report["errors"]))

    def test_missing_restore_selection_cannot_claim_success(self):
        self.complete()
        self.put(self.run / "manifest.json", {})
        report = self.collect()
        self.assertTrue(any("Boolean database_restore selection" in error for error in report["errors"]))

    def test_unrequested_restore_cannot_claim_it_was_executed(self):
        self.complete()
        self.put(self.run / "manifest.json", {"database_restore": False})
        report = self.collect()
        self.assertTrue(any("must be recorded as not attempted" in error for error in report["errors"]))

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

    def test_fresh_dependency_preparation_retains_each_attempted_log(self):
        self.complete()
        commands = ["python-dependencies", "frontend-dependencies", "pull-node", "pull-pgvector-pgvector", "pull-redis"]
        self.put(self.run / "checks.json", {name: {"status": "passed", "command": ["inert"]} for name in commands})
        for name in commands:
            self.put(self.run / (name + ".log"), "synthetic locked dependency preparation")
        for name in ["pull-unrelated.log", "python-dependencies-secret.log", "artifacts/python-dependencies.log"]:
            self.put(self.run / name, "not a named producer")
        report = self.collect()
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["runs"][0]["missing"], [])
        kept = {item["path"].removeprefix("validation/iacs-validation-fixture/") for item in report["files"]}
        self.assertTrue({name + ".log" for name in commands} <= kept)
        self.assertNotIn("pull-unrelated.log", kept)
        self.assertNotIn("python-dependencies-secret.log", kept)
        self.assertNotIn("artifacts/python-dependencies.log", kept)

    def test_every_named_host_check_retains_its_log_without_nearby_scratch(self):
        self.complete()
        self.put(self.run / "checks.json", {
            name: {"status": "passed", "command": ["python", path]}
            for name, path in HOST_CHECKS.items()
        })
        for name in HOST_CHECKS:
            self.put(self.run / (name + ".log"), "synthetic host check")
            self.put(self.run / (name + "-secret.log"), "not a named producer")
        report = self.collect()
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["runs"][0]["missing"], [])
        kept = {item["path"].removeprefix("validation/iacs-validation-fixture/") for item in report["files"]}
        self.assertTrue({name + ".log" for name in HOST_CHECKS} <= kept)
        self.assertFalse(any(path.endswith("-secret.log") for path in kept))

    def test_reused_dependencies_require_verification_logs_without_fresh_preparation(self):
        self.complete()
        commands = ["python-lock-check", "frontend-lock-check"]
        self.put(self.run / "checks.json", {name: {"status": "passed", "command": ["inert"]} for name in commands})
        for name in commands:
            self.put(self.run / (name + ".log"), "synthetic reused lock verification")
        report = self.collect()
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["runs"][0]["missing"], [])

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
        restore_input = data["on"]["workflow_dispatch"]["inputs"]["database_restore"]
        self.assertEqual(restore_input["type"], "boolean")
        self.assertEqual(restore_input["default"], "false")
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

    @unittest.skipUnless(importlib.util.find_spec("yaml"), "PyYAML is available in the locked harness dependencies")
    def test_workflow_shell_only_enables_restore_for_an_explicit_manual_request(self):
        import yaml
        workflow = Path(__file__).resolve().parents[2] / ".github/workflows/project-checks.yml"
        data = yaml.load(workflow.read_text(), Loader=yaml.BaseLoader)
        steps = data["jobs"]["isolated-contracts"]["steps"]
        validation = next(step for step in steps if "validate.py --mode full" in step.get("run", ""))
        self.assertEqual(validation["env"], {
            "VALIDATION_EVENT": "${{ github.event_name }}",
            "DATABASE_RESTORE_REQUESTED": "${{ inputs.database_restore }}"})
        script = 'python3() { printf "%s\\n" "$@"; }\n' + validation["run"].replace("${{ runner.temp }}", "/synthetic")
        for event, requested in (("push", "true"), ("pull_request", "true"),
                                 ("workflow_dispatch", "false"), ("workflow_dispatch", ""),
                                 ("workflow_dispatch", "true")):
            with self.subTest(event=event, requested=requested):
                result = subprocess.run(["/bin/bash", "-euo", "pipefail", "-c", script],
                    env={"VALIDATION_EVENT": event, "DATABASE_RESTORE_REQUESTED": requested},
                    capture_output=True, text=True, check=True)
                arguments = result.stdout.splitlines()
                self.assertIn("--mode", arguments)
                self.assertIn("full", arguments)
                self.assertEqual("--database-restore" in arguments,
                    event == "workflow_dispatch" and requested == "true")


if __name__ == "__main__":
    unittest.main()
