"""Stage only named, synthetic validation reports for CI artifact upload."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

from storage import StorageGuard
from validate import validation_run_lock
from recovery_checks import HOST_CHECKS


REPORTS = frozenset((
    "manifest.json", "checks.json", "results.json", "source-integrity.json", "images.json",
    "image-references.json", "dependencies.json", "persistence-suites.json", "database-restore.json",
    "production-before.json", "production-after.json", "owned-containers.json", "cleanup.json",
    "disposable-owned.json", "disposable-cleanup.json", "storage.json", "result.json",
))
LOGS = frozenset((
    "python-dependencies", "frontend-dependencies", "pull-node", "pull-pgvector-pgvector", "pull-redis",
    "python-lock-check", "frontend-lock-check", "frontend-tree-check", "python-identity", "uv-identity",
    "npm-identity", "isolation-db-free", "isolation-persistence", "isolation-inspect",
    *HOST_CHECKS, "architecture-boundaries", "compile",
    "workflow-contracts", "backend-tests", "frontend-tests", "frontend-build", "compose-config", "ruff", "mypy",
    "typed-contracts", "postgres-start", "redis-start", "migration-up", "migration-current", "migration-check",
    "test-names", "database-restore-client-version", "database-restore-source-create", "database-restore-prepare",
    "database-restore-dump", "database-restore-target-create", "database-restore-load", "database-restore-verify",
    "database-restore-drop-0", "database-restore-drop-1", "build", "smoke",
))
SCHEMA_REPORTS = frozenset((
    "report", "baseline-ddl-export", "current-baseline-ddl", "pre-recovery-baseline-ddl", "pre-recovery-ddl-export",
    "direct-vs-downgraded-pre-recovery-diff", "downgraded-pre-recovery", "fresh-head", "fresh-upgrade",
    "fresh-vs-staged-head-diff", "guard-retained-prior-head", "historical-initial-create", "historical-initial-head",
    "historical-initial-revision-stability-diff", "historical-initial-source-schema", "historical-initial-upgrade",
    "historical-initial-vs-fresh-head-diff", "historical-pre_recovery-create", "historical-pre_recovery-head",
    "historical-pre_recovery-revision-stability-diff", "historical-pre_recovery-source-schema",
    "historical-pre_recovery-upgrade", "historical-pre_recovery-vs-fresh-head-diff", "prior-head",
    "prior-roundtrip-create", "prior-vs-roundtrip-prior-head-diff", "roundtrip-downgrade", "roundtrip-prior-head",
    "roundtrip-upgrade", "sentinel-baseline", "sentinel-upgrade", "staged-vs-prior-head-diff",
    *(f"{guard}-downgrade-guard{suffix}" for guard in ("discovery", "recipient-binding", "recovery", "retirement")
      for suffix in ("", "-before", "-after", "-prepare")),
))


def restore_reports(manifest, checks, results):
    """Require proof of the explicit selection and any requested rehearsal."""
    if not isinstance(manifest, dict) or type(manifest.get("database_restore")) is not bool:
        raise ValueError("Successful validation requires a Boolean database_restore selection in manifest.json")
    if manifest["database_restore"]:
        if results.get("database-restore") != 0:
            raise ValueError("Requested database restore rehearsal has no successful result")
        return {"database-restore.json", "artifacts/database-restore/verification.json"}
    check = checks.get("database-restore")
    if not isinstance(check, dict) or check.get("status") != "not_attempted" or "database-restore" in results:
        raise ValueError("Unrequested database restore rehearsal must be recorded as not attempted")
    return set()


def selected(relative, *, runtime=False):
    """Finite producer locations, never recursive extension-based scratch matching."""
    parts = relative.parts
    if runtime and len(parts) == 2 and parts[0] == "artifacts":
        return parts[1] in {"report.pdf", "source-parity.json", "expected-source.json"}
    if len(parts) == 1:
        return (relative.name in REPORTS or relative.suffix == ".log" and
            (relative.stem in LOGS or re.fullmatch(r"suite-\d{3}(?:-create|-drop)?|schema-check-\d+", relative.stem)))
    if len(parts) != 3 or parts[0] != "artifacts":
        return False
    directory, name = parts[1:]
    if re.fullmatch(r"suite-\d{3}|backend-tests", directory):
        return name in {"junit.xml", "query-efficiency.json"}
    if directory == "database-restore":
        return name in {"before.json", "after.json", "raw-schema-diff.json", "verification.json"}
    if re.fullmatch(r"schema-contract-[0-9a-f]{12}", directory):
        return relative.suffix in {".json", ".log"} and (relative.stem in SCHEMA_REPORTS or
            re.fullmatch(r"staged-\d{8}_\d{4}", relative.stem))
    return False


def safe_file(run, relative):
    path = run / relative
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Evidence path escape")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("Evidence symlink refused: " + str(relative))
        if parent == run:
            break
    if not path.resolve().is_relative_to(run.resolve()) or not path.is_file():
        raise ValueError("Evidence must be a regular file: " + str(relative))
    return path


def collect(roots, output, *, check_storage=lambda: None):
    """Preserve partial reports; missing proof from a claimed pass fails collection."""
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError("Evidence destination must be new")
    for _, root in roots:
        if root.is_symlink() or output.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(output.resolve()):
            raise ValueError("Evidence roots and destination must be separate, without symlinks")
    check_storage()
    output.mkdir(parents=True)
    record = {"files": [], "runs": [], "errors": [], "policy": "Named reports only; no raw state or recursive scratch selection."}
    for category, root in roots:
        if not root.exists():
            record["runs"].append({"category": category, "status": "not_started"})
            continue
        for run in sorted(root.iterdir()):
            prefix = r"iacs-validation-[\w-]+" if category == "validation" else r"iacs-runtime-pdf-[\w-]+"
            if not re.fullmatch(prefix, run.name):
                continue
            if run.is_symlink() or not run.is_dir():
                record["errors"].append("Run symlink/non-directory refused: " + run.name)
                continue
            # Only inspect the three known levels. Test-owned tmp, caches and media are never traversed.
            candidates = list(run.iterdir())
            artifacts = run / "artifacts"
            if artifacts.is_symlink():
                record["errors"].append("Artifact directory symlink refused: " + run.name)
            elif artifacts.is_dir():
                for directory in artifacts.iterdir():
                    if directory.is_symlink():
                        record["errors"].append("Artifact child symlink refused: " + run.name + "/" + directory.name)
                    elif directory.is_dir():
                        candidates.extend(directory.iterdir())
                    else:
                        candidates.append(directory)
            names = set()
            for candidate in sorted(candidates):
                relative = candidate.relative_to(run)
                if not selected(relative, runtime=category == "runtime"):
                    continue
                try:
                    source = safe_file(run, relative)
                    target = output / category / run.name / relative
                    check_storage()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    names.add(relative.as_posix())
                    record["files"].append({"path": target.relative_to(output).as_posix(), "bytes": target.stat().st_size,
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
                except (ValueError, OSError) as error:
                    record["errors"].append(str(error))
            required = {"checks.json", "results.json"}
            try:
                staged = output / category / run.name
                results = json.loads((staged / "results.json").read_text()) if "results.json" in names else {}
                checks = json.loads((staged / "checks.json").read_text()) if "checks.json" in names else {}
                if not isinstance(results, dict) or not isinstance(checks, dict):
                    raise ValueError("Result/check summaries must be objects")
                required |= {name + ".log" for name, check in checks.items() if "command" in check}
                # validate.run's final Boolean and run_smoke's final record are
                # authoritative; the runtime's last check result is provisional.
                if category == "validation":
                    passed = results.get("production_unchanged") is True and all(
                        type(value) is int and value == 0 for key, value in results.items() if key != "production_unchanged")
                else:
                    required.add("result.json")
                    final = json.loads((staged / "result.json").read_text()) if "result.json" in names else {}
                    if not isinstance(final, dict) or not isinstance(final.get("cleanup_results", {}), dict):
                        raise ValueError("Runtime final/cleanup summaries must be objects")
                    cleanup = final.get("cleanup_results", {})
                    passed = (final.get("status") == "passed" and final.get("production_unchanged") is True
                        and cleanup.get("cleanup") == 0 and all(value == 0 for value in cleanup.values()))
                    if final.get("status") == "passed" and not passed:
                        record["errors"].append("Runtime claimed success without final preservation/cleanup proof: " + run.name)
                if passed:
                    required |= {"cleanup.json", "production-before.json", "production-after.json", "storage.json"}
                    if category == "validation":
                        required |= {"manifest.json", "source-integrity.json", "dependencies.json", "images.json",
                            "persistence-suites.json", "artifacts/backend-tests/junit.xml"}
                        manifest = json.loads((staged / "manifest.json").read_text())
                        required |= restore_reports(manifest, checks, results)
                        suites = json.loads((output / category / run.name / "persistence-suites.json").read_text())
                        required |= {item["evidence"] + "/junit.xml" for item in suites}
                        schemas = [name for name in names if re.fullmatch(r"artifacts/schema-contract-[0-9a-f]{12}/report.json", name)]
                        if not schemas:
                            required.add("artifacts/schema-contract-<run>/report.json")
                    else:
                        required |= {"result.json", "smoke.log", "artifacts/report.pdf"}
                missing = sorted(required - names)
                record["runs"].append({"category": category, "run": run.name, "status": "passed" if passed else "partial_or_failed",
                    "missing": missing})
                if passed and missing:
                    record["errors"].append("Successful run missing required evidence: " + run.name + ": " + ", ".join(missing))
            except (ValueError, OSError, KeyError, TypeError) as error:
                record["errors"].append("Invalid evidence summary: " + run.name + ": " + str(error))
    if not record["files"]:
        record["errors"].append("No validation evidence was produced")
    record["retained_bytes"] = sum(item["bytes"] for item in record["files"])
    (output / "collection.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-root", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with validation_run_lock("ci-evidence"):
        guard = StorageGuard(args.output)
        guard.roots.extend([args.validation_root, args.runtime_root])
        guard.check(force=True)
        record = collect([("validation", args.validation_root), ("runtime", args.runtime_root)], args.output,
            check_storage=guard.check)
        guard.check(force=True)
        record["storage"] = guard.record()
        (args.output / "collection.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"Retained {len(record['files'])} files, {record['retained_bytes']} bytes; {len(record['errors'])} collection errors")
    return int(bool(record["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
