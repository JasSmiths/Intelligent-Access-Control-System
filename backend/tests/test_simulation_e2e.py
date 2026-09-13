import uuid
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI

import app.simulation.router as simulation_router_module
import app.simulation.scenarios as simulation_scenarios
from app.api.dependencies import current_user
from app.models import User
from app.models.enums import UserRole
from app.modules.lpr.base import PlateRead
from app.services.access.reads import (
    GATE_OBSERVATION_PAYLOAD_KEY,
    PRESERVE_GATE_OBSERVATION_PAYLOAD_KEY,
)
from app.services.access_events import AccessEventService
from app.simulation.scenarios import (
    SimulationRecorder,
    available_scenario_ids,
    recommended_fix_for_issue,
)


def user_with_role(role: UserRole) -> User:
    return User(
        id=uuid.uuid4(),
        username=f"{role.value}-{uuid.uuid4().hex[:8]}",
        first_name="Test",
        last_name="User",
        full_name="Test User",
        password_hash="not-used",
        role=role,
        is_active=True,
    )


def app_for_user(user: User) -> FastAPI:
    app = FastAPI()
    app.include_router(simulation_router_module.router, prefix="/api/v1/simulation")

    async def override_current_user() -> User:
        return user

    app.dependency_overrides[current_user] = override_current_user
    return app


def test_full_access_scenario_catalog_covers_requested_cases() -> None:
    assert available_scenario_ids() == (
        "known_arrival_closed_gate",
        "known_exit_open_gate",
        "rapid_same_vehicle_reads",
        "multiple_arrivals_under_two_minutes",
        "open_gate_convoy_arrivals",
        "multiple_exits_same_gate_open",
        "unknown_plate_denied",
    )


def test_wrong_direction_recommendation_names_convoy_fix() -> None:
    recommendation = recommended_fix_for_issue("wrong_direction")

    assert "open-gate convoy" in recommendation
    assert "AccessEvidenceResolver._resolve_direction" in recommendation


@pytest.mark.asyncio
async def test_simulation_gate_observation_hook_preserves_supplied_state() -> None:
    service = AccessEventService()
    captured_at = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    read = PlateRead(
        registration_number="SIM123",
        confidence=1.0,
        source="simulation_e2e",
        captured_at=captured_at,
        raw_payload={
            PRESERVE_GATE_OBSERVATION_PAYLOAD_KEY: True,
            GATE_OBSERVATION_PAYLOAD_KEY: {
                "state": "open",
                "observed_at": captured_at.isoformat(),
                "controller": "simulation",
            },
        },
    )

    result = await service._read_with_gate_observation(read)

    assert result is read


@pytest.mark.asyncio
async def test_full_access_endpoint_is_retired_before_runner(monkeypatch) -> None:
    calls = 0

    async def fail_runner(*_args, **_kwargs) -> None:
        nonlocal calls
        calls += 1
        raise AssertionError("The retired endpoint must not invoke the E2E simulation.")

    monkeypatch.setattr(simulation_scenarios, "run_full_access_flow", fail_runner)
    app = app_for_user(user_with_role(UserRole.STANDARD))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/simulation/e2e/full-access-flow", json={})

    assert response.status_code == 410
    assert response.json()["detail"] == (
        "Full access-flow simulation is available only through the isolated test harness."
    )
    assert calls == 0


@pytest.mark.asyncio
async def test_full_access_endpoint_is_retired_before_confirmation_or_runner(monkeypatch) -> None:
    runner_calls = 0
    confirmation_calls = 0

    async def fail_runner(*_args, **_kwargs) -> None:
        nonlocal runner_calls
        runner_calls += 1
        raise AssertionError("The retired endpoint must not invoke the E2E simulation.")

    async def fake_confirmation(_session, **_kwargs) -> None:
        nonlocal confirmation_calls
        confirmation_calls += 1
        raise AssertionError("The retired endpoint must not consume a confirmation token.")

    monkeypatch.setattr(simulation_scenarios, "run_full_access_flow", fail_runner)
    monkeypatch.setattr(simulation_router_module, "require_confirmed_action", fake_confirmation)
    app = app_for_user(user_with_role(UserRole.ADMIN))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/simulation/e2e/full-access-flow",
            json={
                "cleanup": False,
                "scenario_ids": ["unknown_plate_denied"],
                "include_debug": True,
                "confirmation_token": "server-token",
            },
        )

    assert response.status_code == 410
    assert response.json()["detail"] == (
        "Full access-flow simulation is available only through the isolated test harness."
    )
    assert runner_calls == 0
    assert confirmation_calls == 0


def test_recorder_counts_suppressed_reads() -> None:
    recorder = SimulationRecorder(
        realtime_events=[
            {"type": "plate_read.received", "payload": {}, "created_at": "2026-05-09T12:00:00+00:00"},
            {"type": "plate_read.suppressed", "payload": {}, "created_at": "2026-05-09T12:00:01+00:00"},
        ]
    )

    assert recorder.counts()["suppressed_reads"] == 1
