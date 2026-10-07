"""Scanner algorithm regressions; the isolated harness owns the repository scan."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("iacs_architecture_guard", ROOT / "scripts/architecture/check_boundaries.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


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


def test_owner_guard_distinguishes_runtime_business_imports_and_route_writes(tmp_path):
    fixtures = {
        "backend/app/modules/vendor.py": """
from typing import TYPE_CHECKING
from app.services.settings import get_runtime_config
if TYPE_CHECKING:
    from app.services.contract import ReadContract
from app.services.domain import mutate
""",
        "backend/app/services/domain.py": "from app.api.v1.directory import router\n",
        "backend/app/api/v1/directory.py": """
async def create(session, person):
    session.add(person)
    await session.flush()
    await session.commit()
""",
    }
    for relative, source in fixtures.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    inventory = guard.inspect(tmp_path)
    assert inventory["violations"] == {
        "backend/app/api/v1/directory.py::domain_transaction_in_route": 3,
        "backend/app/modules/vendor.py::business_service_import::app.services.domain": 1,
        "backend/app/modules/vendor.py::business_service_import::app.services.domain.mutate": 1,
        "backend/app/services/domain.py::api_import::app.api.v1.directory": 1,
        "backend/app/services/domain.py::api_import::app.api.v1.directory.router": 1,
    }
