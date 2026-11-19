"""Exact query boundary and pure ranking regressions."""

from difflib import SequenceMatcher
from itertools import product
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.dialects import postgresql

from app.services import access_events
from app.services.access.plate_matching import fuzzy_registration_pattern, match_candidates
from app.services.movement.sessions import normalize_registration_number


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
    session = SimpleNamespace(scalars=scalars, close=AsyncMock())
    monkeypatch.setattr(access_events, "AsyncSessionLocal", lambda: session)
    match = await access_events.AccessEventService()._known_vehicle_match(("AB10CDE",), .78)
    assert match["registration_number"] == "AB-10CDE" and match["exact"]
    assert len(queries) == 1
    session.close.assert_awaited_once()
    assert "regexp_replace" in queries[0] and "is_active IS true" in queries[0]


async def test_missing_exact_match_streams_filtered_registrations_across_batches(monkeypatch):
    scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
    async def batches(size):
        assert size == 128
        yield ["ab-10cdf"]
        yield ["AB10CDZ"]
    rows = SimpleNamespace(partitions=batches, close=AsyncMock())
    stream = AsyncMock(return_value=rows)
    session = SimpleNamespace(scalars=scalars, stream_scalars=stream, close=AsyncMock())
    monkeypatch.setattr(access_events, "AsyncSessionLocal", lambda: session)
    match = await access_events.AccessEventService()._known_vehicle_match(("AB10CDE",), .78)
    assert match["registration_number"] == "AB10CDZ"
    assert "~" in str(stream.await_args.args[0])
    assert scalars.await_count == stream.await_count == 1
    rows.close.assert_awaited_once()
    session.close.assert_awaited_once()


def test_overlap_filter_and_streamed_ranking_preserve_exhaustive_scores_and_ties():
    registrations = ["".join(chars) for size in range(1, 5) for chars in product("A0Z", repeat=size)]
    registrations += ["a-0 z", "A..Z", "Å0Z", "AAA-000", "a/00-z", "", "--"]
    for candidates in (("ZZZ",), ("A000Z", "Z0A", "00Z"), tuple(registrations[:15])):
        for threshold in (0., .1, .5, 2/3, .78, 6/7, 1.):
            allowed = re.compile(fuzzy_registration_pattern(candidates, threshold))
            for value in registrations:
                normalized = normalize_registration_number(value)
                if normalized and any(SequenceMatcher(None, plate, normalized).ratio() >= threshold for plate in candidates):
                    assert allowed.search(value), (value, candidates, threshold)
            for stored in (registrations, registrations[::-1], registrations[-7:]):
                ranked = []
                for index, candidate in enumerate(candidates):
                    for value in stored:
                        normalized = normalize_registration_number(value)
                        if not normalized:
                            continue
                        score = SequenceMatcher(None, candidate, normalized).ratio()
                        if score >= threshold:
                            lookup = value.strip().upper().replace(" ", "") or normalized
                            ranked.append(((candidate == normalized, score, -index, lookup), normalized))
                expected = max(ranked, key=lambda item: item[0]) if ranked else None
                eligible = [value for value in stored if allowed.search(value)]
                best = None
                for i in range(0, len(eligible), 5):
                    best = match_candidates(candidates, eligible[i:i+5], threshold, best)
                if expected is None:
                    assert best is None
                else:
                    (exact, score, order, lookup), normalized = expected
                    assert best == {"exact": exact, "similarity": score, "threshold": threshold,
                        "detected_registration_number": candidates[-order],
                        "normalized_registration_number": normalized, "registration_number": lookup}
