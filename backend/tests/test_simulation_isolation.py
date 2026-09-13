"""Simulator full-flow isolation is an application boundary, not a test toggle."""

from dataclasses import replace

import pytest

import app.simulation.scenarios as scenarios


def _isolated_snapshot(
    *,
    platform: str = "linux",
    network_interfaces: frozenset[str] = frozenset({"lo"}),
    docker_socket_present: bool = False,
    runtime_credential_source_present: bool = False,
    environment: dict[str, str] | None = None,
) -> scenarios._FullAccessFlowIsolationSnapshot:
    return scenarios._FullAccessFlowIsolationSnapshot(
        platform=platform,
        network_interfaces=network_interfaces,
        docker_socket_present=docker_socket_present,
        runtime_credential_source_present=runtime_credential_source_present,
        environment=environment
        or {
            "IACS_ENVIRONMENT": "testing",
            "IACS_PHASE1_MODE": "persistence",
            "IACS_RECOVERY_PROBES": "synthetic-only",
            "IACS_AUTO_CREATE_SCHEMA": "false",
            "IACS_SEED_DEMO_DATA": "false",
            "IACS_AUTH_SECRET_KEY": "phase1-synthetic-auth-root-never-production",
            "IACS_DATA_DIR": "/isolated/runtime",
            "IACS_LOG_DIR": "/isolated/logs",
            "IACS_WORKSPACE_DIR": "/workspace",
            "IACS_DATABASE_URL": (
                "postgresql+asyncpg://phase1:synthetic-phase1-only@127.0.0.1:5432/"
                "iacs_p1_simulation"
            ),
            "IACS_REDIS_URL": "redis://127.0.0.1:6379/0",
        },
    )


def test_full_access_flow_isolation_requires_all_harness_boundaries() -> None:
    snapshot = _isolated_snapshot()

    assert scenarios._full_access_flow_isolation_error(snapshot) is None
    assert scenarios._full_access_flow_isolation_error(replace(snapshot, platform="darwin"))
    assert scenarios._full_access_flow_isolation_error(
        replace(snapshot, network_interfaces=frozenset({"lo", "eth0"}))
    )
    assert scenarios._full_access_flow_isolation_error(
        replace(snapshot, docker_socket_present=True)
    )
    assert scenarios._full_access_flow_isolation_error(
        replace(snapshot, runtime_credential_source_present=True)
    )
    assert scenarios._full_access_flow_isolation_error(
        replace(
            snapshot,
            environment={
                **snapshot.environment,
                "IACS_DISCORD_BOT_TOKEN": "synthetic-but-disallowed",
            },
        )
    )
    assert scenarios._full_access_flow_isolation_error(
        replace(
            snapshot,
            environment={
                **snapshot.environment,
                "IACS_DATABASE_URL": (
                    "postgresql+asyncpg://phase1:synthetic-phase1-only@127.0.0.1:5432/iacs"
                ),
            },
        )
    )


def test_full_access_flow_capability_is_issued_and_revalidated_only_after_proof(monkeypatch) -> None:
    valid = _isolated_snapshot()
    invalid = replace(valid, network_interfaces=frozenset({"lo", "eth0"}))
    snapshots = iter((valid, invalid))
    monkeypatch.setattr(
        scenarios,
        "_full_access_flow_isolation_snapshot",
        lambda: next(snapshots),
    )

    capability = scenarios.issue_isolated_full_access_flow_capability()

    with pytest.raises(scenarios.FullAccessFlowIsolationError, match="loopback-only Linux"):
        scenarios._require_isolated_full_access_flow_capability(capability)

    forged = scenarios.FullAccessFlowIsolationCapability(object())
    with pytest.raises(scenarios.FullAccessFlowIsolationError, match="isolated phase1 capability"):
        scenarios._require_isolated_full_access_flow_capability(forged)


@pytest.mark.asyncio
async def test_direct_runner_refuses_before_scenario_selection_or_simulator_globals(monkeypatch) -> None:
    calls: list[str] = []

    def forbidden_scenario_selection(*_args, **_kwargs):
        calls.append("scenario-selection")
        raise AssertionError("The isolation guard must run before scenario selection.")

    class ForbiddenPatchScope:
        def __init__(self, *_args, **_kwargs) -> None:
            calls.append("patch-scope")
            raise AssertionError("The isolation guard must run before global patching.")

    def forbidden_session_factory(*_args, **_kwargs):
        calls.append("database-session")
        raise AssertionError("The isolation guard must run before opening a database session.")

    monkeypatch.setattr(scenarios, "_selected_scenario_ids", forbidden_scenario_selection)
    monkeypatch.setattr(scenarios, "HardwareFreePatchScope", ForbiddenPatchScope)
    monkeypatch.setattr(scenarios, "AsyncSessionLocal", forbidden_session_factory)

    with pytest.raises(scenarios.FullAccessFlowIsolationError, match="isolated phase1 capability"):
        await scenarios.run_full_access_flow(scenarios.FullAccessFlowRequest(cleanup=True))

    assert calls == []
