"""Exact query boundary and pure ranking regressions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.dialects import postgresql

from app.services import access_events
from app.services.access.plate_matching import match_candidates


def test_exact_candidate_priority_beats_fuzzy_score_and_normalizes_historical_plates():
    match = match_candidates(("AB10CDE", "PL000001"), ["PL000001", "ab-10 cde", "AB10CDF"], .78)
    assert match["exact"] is True
    assert match["registration_number"] == "AB-10CDE"
    assert match["detected_registration_number"] == "AB10CDE"


async def test_exact_query_does_not_materialize_directory(monkeypatch):
    queries = []
    async def scalars(query):
        compiled = query.compile(dialect=postgresql.dialect())
        assert list(compiled.params.values()) == [["AB10CDE"]]
        queries.append(str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})))
        return SimpleNamespace(all=lambda: ["ab-10 cde"])
    session = SimpleNamespace(scalars=scalars)
    class Context:
        async def __aenter__(self): return session
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(access_events, "AsyncSessionLocal", Context)
    assert await access_events.AccessEventService()._matching_vehicle_registrations(("AB10CDE",)) == ["ab-10 cde"]
    assert len(queries) == 1
    assert "regexp_replace" in queries[0] and "is_active IS true" in queries[0]


async def test_missing_exact_match_performs_one_fuzzy_directory_read(monkeypatch):
    results = iter([[], ["AB10CDF", "PL000001"]])
    scalars = AsyncMock(side_effect=lambda *_: SimpleNamespace(all=lambda: next(results)))
    class Context:
        async def __aenter__(self): return SimpleNamespace(scalars=scalars)
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(access_events, "AsyncSessionLocal", Context)
    assert await access_events.AccessEventService()._matching_vehicle_registrations(("AB10CDE",)) == ["AB10CDF", "PL000001"]
    assert scalars.await_count == 2
