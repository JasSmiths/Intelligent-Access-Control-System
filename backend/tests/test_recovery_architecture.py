"""Small enforceable guards; detailed persistence contracts prove runtime behavior."""

import importlib.util
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("iacs_architecture_guard", ROOT / "scripts/architecture/check_boundaries.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def test_no_new_cycles_side_effect_bypasses_or_presence_constructors():
    baseline = json.loads((ROOT / "scripts/architecture/baseline.json").read_text())
    assert guard.regressions(guard.inspect(ROOT), baseline) == []


def test_ratchet_detects_new_edges_and_writer_increases_but_allows_removal():
    baseline = {"python_cycle_edges": [["a", "b"], ["b", "a"]], "frontend_cycle_edges": [],
                "violations": {"legacy::presence_constructor": 1}}
    removed = {"python_cycle_edges": [], "frontend_cycle_edges": [], "violations": {}}
    assert guard.regressions(removed, baseline) == []
    changed = {**baseline, "python_cycle_edges": [*baseline["python_cycle_edges"], ["c", "a"]],
               "violations": {"legacy::presence_constructor": 2}}
    assert len(guard.regressions(changed, baseline)) == 2


def test_cycle_inventory_keeps_only_edges_within_actual_components():
    assert guard.cycle_edges({"a": {"b"}, "b": {"a", "c"}, "c": set()}) == [["a", "b"], ["b", "a"]]
