import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from app.models import Person
from app.services.directory.errors import DirectoryOperationError
from app.services.directory.reads import _page_query, decode_cursor, encode_cursor
from app.services.directory.representation import latest_snapshot_query


def test_cursor_round_trip_and_query_scope():
    item_id = uuid.uuid4()
    cursor = encode_cursor("ash smith", item_id, "active-people")
    assert decode_cursor(cursor, "active-people") == ("ash smith", item_id)
    with pytest.raises(DirectoryOperationError, match="cursor"):
        decode_cursor(cursor, "all-people")


@pytest.mark.parametrize("cursor", ["", "%%%%", "e30", "WzEsMixudWxsXQ", "W10"])
def test_cursor_rejects_malformed_values(cursor):
    with pytest.raises(DirectoryOperationError, match="cursor"):
        decode_cursor(cursor, "people")


def test_page_orders_equal_names_by_id_and_fetches_only_one_extra_row():
    item_id = uuid.uuid4()
    cursor = encode_cursor("ash smith", item_id, "people")
    query = _page_query(
        select(Person), func.lower(Person.display_name), Person, cursor, "people", 50
    )
    compiled = query.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "ORDER BY lower(people.display_name), people.id" in sql
    assert "(lower(people.display_name), people.id) >" in sql
    assert 51 in compiled.params.values()
    assert "OFFSET" not in sql


def test_latest_snapshot_uses_one_indexable_lookup_per_requested_plate():
    compiled = latest_snapshot_query(["ASH01", "ZOE02"]).compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "JOIN LATERAL" in sql
    assert "snapshot_path IS NOT NULL" in sql
    assert "snapshot_bytes IS NOT NULL" in sql
    assert "ORDER BY access_events.occurred_at DESC" in sql
    assert 1 in compiled.params.values()
    assert "row_number" not in sql


@pytest.mark.asyncio
async def test_missing_person_keeps_not_found_before_confirmation(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from app.api.v1 import directory
    from app.schemas.directory import UpdatePersonRequest

    confirmation = AsyncMock(
        side_effect=AssertionError("Missing records must not consume confirmation")
    )
    monkeypatch.setattr(directory, "require_confirmed_action", confirmation)
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as failure:
        await directory.update_person(
            person_id=uuid.uuid4(),
            request=UpdatePersonRequest(notes="Synthetic"),
            user=SimpleNamespace(),
            session=session,
        )
    assert failure.value.status_code == 404
    assert failure.value.detail == "Person not found"
    confirmation.assert_not_awaited()


@pytest.mark.parametrize("item_id", [123, {}, [], None])
def test_cursor_rejects_non_string_uuid(item_id):
    import base64
    import json

    cursor = base64.urlsafe_b64encode(json.dumps(["ash", item_id, "people"]).encode()).decode()
    with pytest.raises(DirectoryOperationError, match="cursor"):
        decode_cursor(cursor, "people")
