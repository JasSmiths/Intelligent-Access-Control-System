from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1 import events
from app.models import Anomaly
from app.models.enums import AnomalySeverity, AnomalyType


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _ChangingHistorySession:
    async def execute(self, _query):
        return _Rows([SimpleNamespace(id=uuid4(), group_key="group:unauthorized_plate:2026-09-28:TEST",
                                      stamp=datetime.now(UTC))])

    async def scalars(self, _query):
        return _Rows([])


class _ChangingDetailSession(_ChangingHistorySession):
    async def scalar(self, _query):
        return Anomaly(id=uuid4(), anomaly_type=AnomalyType.UNAUTHORIZED_PLATE,
                       severity=AnomalySeverity.WARNING, message="Synthetic alert",
                       context={"registration_number": "TEST"}, created_at=datetime.now(UTC))


@pytest.mark.asyncio
async def test_alert_group_resolved_between_summary_and_members_returns_refreshable_conflict(monkeypatch):
    async def site_config():
        return SimpleNamespace(site_timezone="Europe/London")

    monkeypatch.setattr(events, "get_runtime_config", site_config)
    with pytest.raises(HTTPException) as outcome:
        await events.alerts_history(status_filter="open", severity=None, type_filter=None, q=None,
                                    from_=None, to=None, limit=50, cursor=None, _=object(),
                                    session=_ChangingHistorySession())
    assert outcome.value.status_code == 409
    assert "Refresh" in outcome.value.detail


@pytest.mark.asyncio
async def test_direct_alert_group_resolved_between_row_and_members_returns_conflict(monkeypatch):
    async def site_config():
        return SimpleNamespace(site_timezone="Europe/London")

    monkeypatch.setattr(events, "get_runtime_config", site_config)
    with pytest.raises(HTTPException) as outcome:
        await events.alert_detail(uuid4(), object(), _ChangingDetailSession())
    assert outcome.value.status_code == 409
    assert "Refresh" in outcome.value.detail
