"""Real authenticated/confirmed API mutations on guarded disposable PostgreSQL."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, text

from app.api.v1 import action_confirmations, integrations, settings as settings_api
from app.db.session import AsyncSessionLocal
from app.models import AuditLog, SystemSetting, User
from app.models.enums import UserRole
from app.services import settings
from app.services.auth import create_access_token
from app.services.settings_activation import SettingsActivationCoordinator

pytestmark = pytest.mark.asyncio
URLS = [f"json://synthetic.invalid/{letter}" for letter in "abc"]


@pytest_asyncio.fixture
async def api(isolated_resources, monkeypatch):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE system_settings, action_confirmations, audit_logs CASCADE"))
        await session.commit()
        await settings.seed_dynamic_settings_for_session(session)
        user = User(username=f"integration-admin-{uuid.uuid4()}", full_name="Synthetic Admin",
                    password_hash="synthetic-not-password", role=UserRole.ADMIN, is_active=True)
        session.add(user)
        await session.commit()
        token, _ = await create_access_token(user)
        user_id = user.id
    settings.invalidate_runtime_config_cache()
    restarts = {name: AsyncMock() for name in ("home_assistant", "access_devices", "unifi_protect")}
    coordinator = SettingsActivationCoordinator(restarts)
    monkeypatch.setattr(integrations, "settings_activation", coordinator)
    monkeypatch.setattr(settings_api, "settings_activation", coordinator)
    app = FastAPI()
    app.include_router(integrations.router, prefix="/api/v1/integrations")
    app.include_router(settings_api.router, prefix="/api/v1/settings")
    app.include_router(action_confirmations.router, prefix="/api/v1/action-confirmations")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app, raise_app_exceptions=False),
        base_url="http://synthetic.invalid", headers={"Authorization": f"Bearer {token}"}, trust_env=False) as client:
        yield SimpleNamespace(client=client, coordinator=coordinator, restarts=restarts, user_id=user_id, app=app)
    await coordinator.stop()
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE system_settings, action_confirmations, audit_logs CASCADE"))
        await session.commit()
    settings.invalidate_runtime_config_cache()


async def approval(api, action, payload):
    response = await api.client.post("/api/v1/action-confirmations", json={"action": action,
        "payload": payload, "target_entity": "Integration", "target_label": "Synthetic destination"})
    assert response.status_code == 200, response.text
    return {**payload, "confirmation_token": response.json()["confirmation_token"]}


async def device_request(api, method, path, action, payload):
    body = await approval(api, action, payload)
    # The route binds the path ID too; it is not an editable device field.
    body.pop("device_id", None)
    return api.client.request(method, "/api/v1/integrations" + path, json=body)


def device(name="First", host="first.synthetic.invalid"):
    return {"name": name, "host": host}


def pause_first_save(api, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = api.coordinator.save
    calls = 0
    async def paused(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.wait_for(release.wait(), 8)
        return await original(*args, **kwargs)
    monkeypatch.setattr(api.coordinator, "save", paused)
    return entered, release


@pytest.mark.parametrize("separate_coordinators", [False, True])
async def test_two_real_add_routes_preserve_both_additions(api, monkeypatch, separate_coordinators):
    original = api.coordinator.save
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def paused(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
        await asyncio.wait_for(release.wait(), 8)
        owner = SettingsActivationCoordinator(api.restarts).save if separate_coordinators else original
        return await owner(*args, **kwargs)
    monkeypatch.setattr(api.coordinator, "save", paused)
    requests = [await device_request(api, "POST", "/esphome/devices", "esphome.device.create", device(name))
                for name in ("Same name", "Same name")]
    tasks = [asyncio.create_task(request) for request in requests]
    try:
        await asyncio.wait_for(entered.wait(), 8)
    finally:
        release.set()
    responses = await asyncio.gather(*tasks)
    assert [r.status_code for r in responses] == [200, 200]
    config = await settings.get_runtime_config()
    assert len(config.esphome_devices) == 2
    assert len({d["id"] for d in config.esphome_devices}) == 2
    assert sorted(len(r.json()["devices"]) for r in responses) == [1, 2]


async def test_add_after_concurrent_remove_cannot_resurrect_endpoint(api, monkeypatch):
    response = await (await device_request(api, "POST", "/esphome/devices", "esphome.device.create", device()))
    identity = response.json()["devices"][0]["id"]
    entered, release = pause_first_save(api, monkeypatch)
    add = asyncio.create_task(await device_request(api, "POST", "/esphome/devices", "esphome.device.create", device("Second")))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        remove = await (await device_request(api, "DELETE", f"/esphome/devices/{identity}", "esphome.device.delete", {"device_id": identity}))
        assert remove.status_code == 200
    finally:
        release.set()
    assert (await add).status_code == 200
    assert [d["name"] for d in (await settings.get_runtime_config()).esphome_devices] == ["Second"]


async def test_independent_device_patches_preserve_both_accepted_fields(api, monkeypatch):
    response = await (await device_request(api, "POST", "/esphome/devices", "esphome.device.create", device()))
    identity = response.json()["devices"][0]["id"]
    entered, release = pause_first_save(api, monkeypatch)
    first = asyncio.create_task(await device_request(api, "PATCH", f"/esphome/devices/{identity}",
        "esphome.device.update", {"device_id": identity, "name": "Renamed"}))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        second = await (await device_request(api, "PATCH", f"/esphome/devices/{identity}",
            "esphome.device.update", {"device_id": identity, "host": "changed.synthetic.invalid"}))
        assert second.status_code == 200
    finally:
        release.set()
    accepted = await first
    assert accepted.status_code == 200
    assert accepted.json()["devices"][0]["host"] == "changed.synthetic.invalid"
    assert (await settings.get_runtime_config()).esphome_devices[0]["name"] == "Renamed"


async def test_concurrent_apprise_additions_preserve_both(api, monkeypatch):
    entered, release = pause_first_save(api, monkeypatch)
    bodies = [await approval(api, "apprise.url.create", {"url": url}) for url in URLS[:2]]
    first = asyncio.create_task(api.client.post("/api/v1/integrations/apprise/urls", json=bodies[0]))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        second = await api.client.post("/api/v1/integrations/apprise/urls", json=bodies[1])
        assert second.status_code == 200
    finally:
        release.set()
    accepted = await first
    assert accepted.status_code == 200 and len(accepted.json()["urls"]) == 2
    assert set((await settings.get_runtime_config()).apprise_urls.splitlines()) == set(URLS[:2])


def delete_binding(row):
    result = {"index": row["index"]}
    # Also reproduces the actual pre-fix API contract against starting source.
    if "destination_id" in row:
        result.update(destination_id=row["destination_id"], revision=row["revision"])
    return result


async def delete(api, row, body):
    payload = {k: v for k, v in body.items() if k != "index"}
    return await api.client.request("DELETE", f"/api/v1/integrations/apprise/urls/{row['index']}", json=payload)


async def test_preceding_item_deletion_never_substitutes_another_target(api):
    await settings.update_settings({"apprise_urls": "\n".join(URLS)})
    rows = (await api.client.get("/api/v1/integrations/apprise/urls")).json()["urls"]
    bodies = [await approval(api, "apprise.url.delete", delete_binding(row)) for row in rows[:2]]
    assert (await delete(api, rows[0], bodies[0])).status_code == 200
    stale = await delete(api, rows[1], bodies[1])
    assert stale.status_code == 409
    assert (await settings.get_runtime_config()).apprise_urls.splitlines() == URLS[1:]


@pytest.mark.parametrize("replacement", [list(reversed(URLS)), [URLS[0], URLS[1], URLS[1]]])
async def test_reordering_or_duplicate_change_conflicts_with_stale_approval(api, replacement):
    await settings.update_settings({"apprise_urls": "\n".join(URLS)})
    row = (await api.client.get("/api/v1/integrations/apprise/urls")).json()["urls"][1]
    body = await approval(api, "apprise.url.delete", delete_binding(row))
    await settings.update_settings({"apprise_urls": "\n".join(replacement)})
    assert (await delete(api, row, body)).status_code == 409
    assert (await settings.get_runtime_config()).apprise_urls.splitlines() == replacement


async def test_fresh_duplicate_selection_removes_one_and_old_approval_conflicts(api):
    await settings.update_settings({"apprise_urls": "\n".join([URLS[0], URLS[0], URLS[2]])})
    rows = (await api.client.get("/api/v1/integrations/apprise/urls")).json()["urls"]
    first = await approval(api, "apprise.url.delete", delete_binding(rows[1]))
    stale = await approval(api, "apprise.url.delete", delete_binding(rows[0]))
    assert (await delete(api, rows[1], first)).status_code == 200
    assert (await settings.get_runtime_config()).apprise_urls.splitlines() == [URLS[0], URLS[2]]
    assert (await delete(api, rows[0], stale)).status_code == 409


async def test_index_only_delete_is_not_an_approval_bypass(api):
    await settings.update_settings({"apprise_urls": URLS[0]})
    body = await approval(api, "apprise.url.delete", {"index": 0})
    assert (await delete(api, {"index": 0}, body)).status_code == 422
    assert (await settings.get_runtime_config()).apprise_urls == URLS[0]


@pytest.mark.parametrize("kind", ["gates", "garage-doors"])
async def test_discovery_merges_fresh_configuration_after_external_work(api, monkeypatch, kind):
    key = "home_assistant_gate_entities" if kind == "gates" else "home_assistant_garage_door_entities"
    entered, release = asyncio.Event(), asyncio.Event()
    async def discover():
        entered.set()
        await asyncio.wait_for(release.wait(), 8)
        return []
    monkeypatch.setattr(integrations, "_home_assistant_client", lambda: SimpleNamespace(list_states=discover))
    detected = [{"entity_id": "cover.detected", "name": "Detected"}]
    monkeypatch.setattr(integrations, "detected_gate_entities" if kind == "gates" else "detected_garage_door_entities", lambda _: detected)
    body = await approval(api, f"home_assistant.{kind.replace('-', '_')}.auto_detect", {})
    request = asyncio.create_task(api.client.post(f"/api/v1/integrations/home-assistant/{kind}/auto-detect", json=body))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        # Provider discovery must not hold settings SQL locks.
        await asyncio.wait_for(settings.update_settings({key: [{"entity_id": "cover.manual", "name": "Manual"}]}), 3)
    finally:
        release.set()
    response = await request
    assert response.status_code == 200, response.text
    assert {row["entity_id"] for row in getattr(await settings.get_runtime_config(), key)} == {"cover.manual", "cover.detected"}


async def test_required_settings_audit_failure_rolls_back_compound_edit(api, monkeypatch):
    monkeypatch.setattr(settings, "write_audit_log", AsyncMock(side_effect=RuntimeError("Synthetic audit failure")))
    response = await (await device_request(api, "POST", "/esphome/devices", "esphome.device.create", device()))
    assert response.status_code == 500
    assert (await settings.get_runtime_config()).esphome_devices == []
    assert all(restart.await_count == 0 for restart in api.restarts.values())
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(AuditLog).where(AuditLog.action == "real_world_action.confirmation.consumed")) is not None


async def test_public_apprise_metadata_and_audit_do_not_include_credentials(api):
    url = "json://sensitive-user:sensitive-password@synthetic.invalid/a?token=sensitive-token"
    body = await approval(api, "apprise.url.create", {"url": url})
    response = await api.client.post("/api/v1/integrations/apprise/urls", json=body)
    assert response.status_code == 200
    assert "sensit" not in response.text
    serialized = response.text
    async with AsyncSessionLocal() as session:
        serialized += str([(row.diff, row.metadata_) for row in (await session.scalars(select(AuditLog))).all()])
        serialized += str((await session.get(SystemSetting, "apprise_urls")).value)
    assert all(secret not in serialized for secret in ("sensitive-user", "sensitive-password", "sensitive-token"))


@pytest.mark.parametrize("kind", ["gates", "garage-doors"])
async def test_discovery_merge_uses_current_settings_at_save_boundary(api, monkeypatch, kind):
    key = "home_assistant_gate_entities" if kind == "gates" else "home_assistant_garage_door_entities"
    monkeypatch.setattr(integrations, "_home_assistant_client", lambda: SimpleNamespace(list_states=AsyncMock(return_value=[])))
    monkeypatch.setattr(integrations, "detected_gate_entities" if kind == "gates" else "detected_garage_door_entities",
        lambda _: [{"entity_id": "cover.detected", "name": "Discovered"}])
    entered, release = pause_first_save(api, monkeypatch)
    body = await approval(api, f"home_assistant.{kind.replace('-', '_')}.auto_detect", {})
    request = asyncio.create_task(api.client.post(f"/api/v1/integrations/home-assistant/{kind}/auto-detect", json=body))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        await settings.update_settings({key: [{"entity_id": "cover.manual", "name": "Manual"}]})
    finally:
        release.set()
    response = await request
    assert response.status_code == 200
    output_key = "gate_entities" if kind == "gates" else "garage_door_entities"
    assert {row["entity_id"] for row in response.json()[output_key]} == {"cover.manual", "cover.detected"}


async def test_concurrent_apprise_addition_conflicts_with_approved_delete(api, monkeypatch):
    await settings.update_settings({"apprise_urls": "\n".join(URLS)})
    row = (await api.client.get("/api/v1/integrations/apprise/urls")).json()["urls"][1]
    body = await approval(api, "apprise.url.delete", delete_binding(row))
    entered, release = pause_first_save(api, monkeypatch)
    pending = asyncio.create_task(delete(api, row, body))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        add_body = await approval(api, "apprise.url.create", {"url": "json://synthetic.invalid/d"})
        assert (await api.client.post("/api/v1/integrations/apprise/urls", json=add_body)).status_code == 200
    finally:
        release.set()
    assert (await pending).status_code == 409
    assert (await settings.get_runtime_config()).apprise_urls.splitlines() == [*URLS, "json://synthetic.invalid/d"]


async def test_apprise_add_after_delete_does_not_restore_removed_destination(api, monkeypatch):
    await settings.update_settings({"apprise_urls": "\n".join(URLS)})
    row = (await api.client.get("/api/v1/integrations/apprise/urls")).json()["urls"][0]
    delete_body = await approval(api, "apprise.url.delete", delete_binding(row))
    add_body = await approval(api, "apprise.url.create", {"url": "json://synthetic.invalid/d"})
    entered, release = pause_first_save(api, monkeypatch)
    pending = asyncio.create_task(api.client.post("/api/v1/integrations/apprise/urls", json=add_body))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        assert (await delete(api, row, delete_body)).status_code == 200
    finally:
        release.set()
    response = await pending
    assert response.status_code == 200 and len(response.json()["urls"]) == 3
    assert (await settings.get_runtime_config()).apprise_urls.splitlines() == [*URLS[1:], "json://synthetic.invalid/d"]


async def test_confirmation_is_bound_to_exact_opaque_target_and_cannot_be_replayed(api):
    await settings.update_settings({"apprise_urls": "\n".join(URLS)})
    rows = (await api.client.get("/api/v1/integrations/apprise/urls")).json()["urls"]
    body = await approval(api, "apprise.url.delete", delete_binding(rows[0]))
    forged = {**body, **delete_binding(rows[1])}
    assert (await delete(api, rows[1], forged)).status_code == 403
    assert (await settings.get_runtime_config()).apprise_urls.splitlines() == URLS
    fresh = await approval(api, "apprise.url.delete", delete_binding(rows[0]))
    assert (await delete(api, rows[0], fresh)).status_code == 200
    assert (await delete(api, rows[0], fresh)).status_code == 409
    assert (await settings.get_runtime_config()).apprise_urls.splitlines() == URLS[1:]
    async with AsyncSessionLocal() as session:
        rejected = (await session.scalars(select(AuditLog).where(AuditLog.action == "real_world_action.confirmation.rejected"))).all()
        assert len(rejected) == 2


async def test_current_actor_is_revalidated_after_route_confirmation(api, monkeypatch):
    entered, release = pause_first_save(api, monkeypatch)
    request = asyncio.create_task(await device_request(api, "POST", "/esphome/devices", "esphome.device.create", device()))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        async with AsyncSessionLocal() as session:
            user = await session.get(User, api.user_id)
            user.role = UserRole.STANDARD
            await session.commit()
    finally:
        release.set()
    assert (await request).status_code == 403
    assert (await settings.get_runtime_config()).esphome_devices == []


async def test_compound_edit_cancellation_drains_activation_before_shutdown(api):
    entered, release = asyncio.Event(), asyncio.Event()
    async def restart():
        entered.set()
        await asyncio.wait_for(release.wait(), 8)
    api.restarts["access_devices"].side_effect = restart
    request = asyncio.create_task(await device_request(api, "POST", "/esphome/devices", "esphome.device.create", device()))
    await asyncio.wait_for(entered.wait(), 8)
    request.cancel()
    stopping = asyncio.create_task(api.coordinator.stop())
    try:
        await asyncio.sleep(0)
        assert not request.done() and not stopping.done()
        assert len((await settings.get_runtime_config()).esphome_devices) == 1
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    await asyncio.wait_for(stopping, 8)
    refused = await (await device_request(api, "POST", "/esphome/devices", "esphome.device.create", device("Second")))
    assert refused.status_code == 503
    assert len((await settings.get_runtime_config()).esphome_devices) == 1


async def test_duplicate_add_is_authoritative_noop_with_confirmation_and_no_restart(api):
    body = await approval(api, "apprise.url.create", {"url": URLS[0]})
    first = await api.client.post("/api/v1/integrations/apprise/urls", json=body)
    body = await approval(api, "apprise.url.create", {"url": URLS[0]})
    second = await api.client.post("/api/v1/integrations/apprise/urls", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["urls"] == second.json()["urls"]
    assert second.json()["activation"] == {"state": "not_required", "services": {}}
    async with AsyncSessionLocal() as session:
        audits = (await session.scalars(select(AuditLog))).all()
        assert sum(row.action == "settings.update" for row in audits) == 1
        assert sum(row.action == "real_world_action.confirmation.consumed" for row in audits) == 2


async def test_explicit_null_host_remains_invalid_and_secret_omission_and_clear_are_distinct(api):
    initial = {**device(), "encryption_key": "synthetic-device-key"}
    response = await (await device_request(api, "POST", "/esphome/devices", "esphome.device.create", initial))
    identity = response.json()["devices"][0]["id"]
    # Existing confirmation canonicalization omits null fields; the actual
    # request still carries explicit null and must retain host validation.
    body = await approval(api, "esphome.device.update", {"device_id": identity})
    body.pop("device_id")
    body["host"] = None
    rejected = await api.client.patch(f"/api/v1/integrations/esphome/devices/{identity}", json=body)
    assert rejected.status_code == 400
    assert (await settings.get_runtime_config()).esphome_devices[0]["host"] == initial["host"]
    renamed = await (await device_request(api, "PATCH", f"/esphome/devices/{identity}",
        "esphome.device.update", {"device_id": identity, "name": "Renamed"}))
    assert renamed.status_code == 200 and renamed.json()["devices"][0]["encryption_key_configured"]
    cleared = await (await device_request(api, "PATCH", f"/esphome/devices/{identity}",
        "esphome.device.update", {"device_id": identity, "encryption_key": ""}))
    assert cleared.status_code == 200 and not cleared.json()["devices"][0]["encryption_key_configured"]
