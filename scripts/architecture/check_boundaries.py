"""Static first-party dependency and side-effect ratchet; never imports IACS.

The baseline permits existing violations only. It is not a claim that dynamic
registration, SQL strings or all ORM writes have been proved safe.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("baseline.json")


def runtime_nodes(node):
    if isinstance(node, ast.If) and (
        isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
        or isinstance(node.test, ast.Attribute) and node.test.attr == "TYPE_CHECKING"
    ):
        for child in node.orelse:
            yield from runtime_nodes(child)
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from runtime_nodes(child)


def cycle_edges(graph: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    indices, low, stack, active = {}, {}, [], set()
    edges = []

    def visit(node):
        nonlocal index
        indices[node] = low[node] = index
        index += 1
        stack.append(node)
        active.add(node)
        for target in sorted(graph[node]):
            if target not in indices:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in active:
                low[node] = min(low[node], indices[target])
        if low[node] == indices[node]:
            component = set()
            while stack:
                member = stack.pop()
                active.remove(member)
                component.add(member)
                if member == node:
                    break
            if len(component) > 1:
                edges.extend([member, target] for member in component for target in graph[member] if target in component)

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return sorted(edges)


def inspect(root: Path) -> dict:
    app = root / "backend/app"
    paths = sorted(app.rglob("*.py"))
    if len(paths) > 5000:
        raise ValueError("Unexpected source inventory; review traversal scope")
    modules = {"app." + str(p.relative_to(app).with_suffix("")).replace("/", "."): p for p in paths}
    modules = {name.removesuffix(".__init__"): p for name, p in modules.items()}
    graph = {name: set() for name in modules}
    violations = Counter()
    for name, path in modules.items():
        relative = path.relative_to(root).as_posix()
        package = name if path.name == "__init__.py" else name.rsplit(".", 1)[0]
        for node in runtime_nodes(ast.parse(path.read_text())):
            targets = []
            if isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parent = package.split(".")[:len(package.split(".")) - node.level + 1]
                    base = ".".join([*parent, *([base] if base else [])])
                targets.extend([base, *(base + "." + alias.name for alias in node.names)])
            for target in targets:
                if target in modules and target != name:
                    graph[name].add(target)
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == "open_gate" and relative != "backend/app/services/gate_commands.py":
                    violations[relative + "::gate_provider_call"] += 1
                if (node.func.attr == "command_cover" and relative != "backend/app/services/access_devices.py"
                        and not relative.startswith("backend/app/modules/access_devices/")):
                    violations[relative + "::device_provider_call"] += 1
            if (isinstance(node.func, ast.Name) and node.func.id == "Presence"
                    and relative != "backend/app/services/movement/presence.py"):
                violations[relative + "::presence_constructor"] += 1
    frontend = root / "frontend/src"
    files = sorted([*frontend.rglob("*.ts"), *frontend.rglob("*.tsx")])
    files = [p for p in files if ".test." not in p.name and not p.name.endswith(".d.ts")]
    imports = re.compile(r'(?:import|export)\s+(?!type\b)(?:[^;]*?\s+from\s+)?["\']([^"\']+)["\']')
    front_graph = {p.relative_to(root).as_posix(): set() for p in files}
    for path in files:
        origin = path.relative_to(root).as_posix()
        for specifier in imports.findall(path.read_text()):
            if not specifier.startswith("."):
                continue
            base = path.parent / specifier
            for candidate in (base, base.with_suffix(".ts"), base.with_suffix(".tsx"), base / "index.ts", base / "index.tsx"):
                resolved = candidate.resolve()
                if not resolved.is_relative_to(root.resolve()):
                    continue
                target = resolved.relative_to(root.resolve()).as_posix()
                if target in front_graph:
                    front_graph[origin].add(target)
                    if (origin.startswith("frontend/src/ui/") and
                            target.startswith(("frontend/src/views/", "frontend/src/features/", "frontend/src/app/"))):
                        violations[origin + "::feature_import::" + target] += 1
                    break
    return {"python_cycle_edges": cycle_edges(graph), "frontend_cycle_edges": cycle_edges(front_graph),
            "violations": dict(sorted(violations.items())), "python_modules": len(modules),
            "frontend_modules": len(files)}


def regressions(actual: dict, baseline: dict) -> list[str]:
    failures = []
    for key in ("python_cycle_edges", "frontend_cycle_edges"):
        previous = {tuple(edge) for edge in baseline[key]}
        failures.extend(key + ": " + " -> ".join(edge) for edge in actual[key] if tuple(edge) not in previous)
    for key, count in actual["violations"].items():
        if count > baseline["violations"].get(key, 0):
            failures.append(f"{key}: {count} exceeds baseline {baseline['violations'].get(key, 0)}")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--inventory", action="store_true")
    args = parser.parse_args()
    actual = inspect(args.root)
    if args.inventory:
        print(json.dumps(actual, indent=2, sort_keys=True))
        return 0
    failures = regressions(actual, json.loads(BASELINE.read_text()))
    for failure in failures:
        print(failure)
    print("FAIL" if failures else "PASS", "dependency/side-effect ratchet",
          f"({actual['python_modules']} Python / {actual['frontend_modules']} frontend modules)")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
