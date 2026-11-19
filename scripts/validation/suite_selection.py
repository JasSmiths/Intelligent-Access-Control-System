"""Choose focused tests and order the complete inventory without changing its membership."""
from collections import defaultdict
import json
from pathlib import Path
import re
import subprocess


def focused_targets(selectors, registered):
    """Group pytest node IDs by file. A whole-file selection covers all its nodes."""
    groups = defaultdict(list)
    for selector in selectors:
        path, sep, node = selector.partition("::")
        if path not in registered and not re.fullmatch(r"backend/tests/test_[a-zA-Z0-9_]+\.py", path):
            raise ValueError(f"Expected a registered database suite or backend unit test file: {path}")
        if sep and (not node or any(not part for part in node.split("::")) or "\n" in node):
            raise ValueError(f"Invalid pytest node ID: {selector}")
        if path not in groups[path] and selector not in groups[path]:
            if not sep:
                groups[path].clear()
            groups[path].append(selector)
    return dict(groups)


def changed_files(root, untracked):
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    changed = git("diff", "--name-only", "-z", "HEAD", "--")
    files = set(untracked)
    if changed.returncode == 0:
        files.update(changed.stdout.decode().split("\0"))
    files.discard("")
    if not files:
        previous = git("diff", "--name-only", "-z", "HEAD^1", "HEAD", "--")
        if previous.returncode == 0:
            files.update(filter(None, previous.stdout.decode().split("\0")))
    return sorted(files)


def recent_suites(root, evidence, current):
    """Read scheduling hints only. A newer passing result supersedes an older failure."""
    failed, focused, seen = set(), set(), set()
    candidates = sorted((p for p in evidence.glob("iacs-validation-*") if p != current
                         and p.is_dir() and not p.is_symlink()), key=lambda p: p.stat().st_mtime, reverse=True)
    for run in candidates[:8]:
        try:
            manifest = json.loads((run / "manifest.json").read_text())
            if manifest["repository"] != str(root):
                continue
            rows = json.loads((run / "persistence-suites.json").read_text())
            checks = json.loads((run / "checks.json").read_text())
            for row in rows:
                path = row["path"]
                status = checks.get(row["check"], {}).get("status")
                if path in seen or status not in ("passed", "failed"):
                    continue
                if status == "failed":
                    failed.add(path)
                if manifest["mode"] == "focused":
                    focused.add(path)
                # A selected method passing cannot clear an older whole-suite failure.
                if not row.get("targets") or row["targets"] == [path] or status == "failed":
                    seen.add(path)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return failed, focused


def prioritize(suites, changed, failed, focused):
    ignored = {"test", "tests", "persistence", "service", "services", "app", "backend", "frontend", "src", "py", "ts", "tsx"}
    def terms(path):
        return set(re.findall(r"[a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", path).lower())) - ignored
    topics = [terms("/".join(Path(path).parts[-2:])) for path in changed]
    def rank(entry):
        path, _ = entry
        words = terms(Path(path).stem)
        overlap = max((len(words & topic) for topic in topics), default=0)
        return (path not in failed, path not in changed, path not in focused, -overlap)
    return sorted(suites, key=rank)
