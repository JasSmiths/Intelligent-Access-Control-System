"""Publish a compact full-run identity in Actions and verify it from a clean checkout."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from ci_evidence import restore_reports
from recovery_checks import DIAGNOSTIC_TESTS, HOST_CHECKS, PERSISTENCE_TESTS, SCHEMA_CHECKS
from source_snapshot import discover_sources, excluded_path, source_file, source_fingerprint

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = "project-checks.yml"
UPLOAD_STEP = "Publish verified full-run receipt"


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def checkout_identity(root):
    if git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("Checkout has local changes. Use local full validation for this source.")
    paths, _ = discover_sources(root)
    hashes = {path: hashlib.sha256(source_file(root, path).read_bytes()).hexdigest()
              for path in paths if not excluded_path(path)}
    return git(root, "rev-parse", "HEAD"), git(root, "rev-parse", "HEAD^{tree}"), source_fingerprint(hashes)


def receipt_name(identity, attempt):
    commit, tree, fingerprint = identity
    return f"iacs-full-v1-{commit}-{tree}-{fingerprint}-{attempt}"


def validate_run(run, identity):
    manifest, checks, results, suites = [
        json.loads((run / (name + ".json")).read_text())
        for name in ("manifest", "checks", "results", "persistence-suites")]
    commit, _, fingerprint = identity
    if (manifest["mode"] != "full" or manifest["status"] or manifest["commit"] != commit
            or manifest["source_fingerprint"] != fingerprint or source_fingerprint(manifest["sha256"]) != fingerprint):
        raise ValueError("Full harness source differs from the clean checkout")
    if (results.get("production_unchanged") is not True or results.get("source_integrity") != 0
            or results.get("cleanup") != 0 or any(type(v) is not int or v != 0
                for k, v in results.items() if k != "production_unchanged")):
        raise ValueError("Full harness did not finish cleanly")
    required = set(HOST_CHECKS) | {"architecture-boundaries", "compile", "workflow-contracts", "backend-tests",
        "ruff", "test-names", "mypy", "typed-contracts", "frontend-build", "frontend-tests", "compose-config",
        "isolation-db-free", "isolation-persistence", "isolation-inspect", "migration-up", "migration-current",
        "migration-check", "python-lock-check", "frontend-lock-check", "frontend-tree-check"}
    required.update(f"schema-check-{i}" for i in range(1, len(SCHEMA_CHECKS) + 1))
    if {r["path"] for r in suites} != set(PERSISTENCE_TESTS + DIAGNOSTIC_TESTS):
        raise ValueError("Full suite inventory differs")
    for row in suites:
        if row.get("targets") != [row["path"]]:
            raise ValueError("Full suite was filtered")
        required.update([row["check"], row["check"] + "-create", row["check"] + "-drop"])
    if any(checks.get(key, {}).get("status") != "passed" or results.get(key) != 0 for key in required):
        raise ValueError("Required full checks did not all pass")
    restore_reports(manifest, checks, results)
    if any(value["status"] != "passed" for key, value in checks.items() if key != "database-restore"):
        raise ValueError("Incomplete CI checks")


def origin_repository(root):
    url = git(root, "remote", "get-url", "origin").removesuffix(".git")
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+)", url)
    if not match:
        raise ValueError("Expected a GitHub origin remote")
    return match[1]


def eligible_run(run, repo, workflow_id):
    return (run["status"] == "completed" and run["conclusion"] == "success"
            and run["workflow_id"] == workflow_id and run["path"] == ".github/workflows/" + WORKFLOW
            and run["event"] in ("push", "pull_request", "workflow_dispatch")
            and run["repository"]["full_name"].casefold() == repo.casefold()
            and (run.get("head_repository") or {}).get("full_name", "").casefold() == repo.casefold())


def matches_receipt(name, identity, run, parents):
    commit, tree, fingerprint = identity
    match = re.fullmatch(r"iacs-full-v1-([0-9a-f]{40})-([0-9a-f]{40})-([0-9a-f]{64})-([0-9]+)", name)
    if not match or match.groups()[1:] != (tree, fingerprint, str(run["run_attempt"])):
        return False
    tested = match[1]
    if run["head_sha"] == commit:
        # A PR validates GitHub's merge checkout. Both complete trees and all
        # harness inputs must match to reuse that result for its contributor head.
        return tested == commit or run["event"] == "pull_request"
    return (run["event"] == "pull_request" and tested == commit and len(parents) == 2
            and parents[1] == run["head_sha"])


def verify(root, run_id=None):
    identity = checkout_identity(root)
    repo = origin_repository(root)
    def api(path):
        return json.loads(subprocess.check_output(["gh", "api", "--hostname", "github.com",
            f"repos/{repo}/actions/{path}"], timeout=35))
    workflow = api("workflows/" + WORKFLOW)
    parents = git(root, "show", "-s", "--format=%P", "HEAD").split()
    if run_id:
        runs = [api(f"runs/{run_id}")]
    else:
        # Ordinary branch heads and an explicitly checked-out PR merge revision.
        revisions = [identity[0]] + (parents[1:] if len(parents) == 2 else [])
        runs = [run for sha in revisions for run in api(f"workflows/{WORKFLOW}/runs?"
                f"head_sha={sha}&status=success&per_page=10")["workflow_runs"]]
    for run in runs:
        if not eligible_run(run, repo, workflow["id"]):
            continue
        prefix = f"runs/{run['id']}"
        artifacts = api(prefix + "/artifacts?per_page=100")["artifacts"]
        if not any(not artifact["expired"] and matches_receipt(artifact["name"], identity, run, parents)
                   for artifact in artifacts):
            continue
        jobs = api(prefix + f"/attempts/{run['run_attempt']}/jobs?per_page=100")["jobs"]
        if any(job["name"] == "isolated-contracts" and job["conclusion"] == "success"
               and any(step["name"] == UPLOAD_STEP and step["conclusion"] == "success"
                       for step in job["steps"]) for job in jobs):
            if checkout_identity(root) != identity:
                raise ValueError("Checkout changed during verification")
            return f"https://github.com/{repo}/actions/runs/{run['id']}/attempts/{run['run_attempt']}"
    raise ValueError("No successful matching full-run receipt found. Use local full validation.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("verify", help="Look up a green Project Checks run for this exact checkout")
    check.add_argument("--run", type=int, help="Check a specific GitHub Actions run")
    publish = commands.add_parser("name", help="CI producer: validate the full result and print the artifact name")
    publish.add_argument("--root", type=Path, required=True)
    publish.add_argument("--attempt", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            print(verify(ROOT, args.run))
        else:
            runs = list(args.root.glob("iacs-validation-*"))
            if len(runs) != 1 or args.attempt < 1:
                raise ValueError("Expected one full CI run and a positive attempt number")
            identity = checkout_identity(ROOT)
            validate_run(runs[0], identity)
            print(receipt_name(identity, args.attempt))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"CI evidence unavailable: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
