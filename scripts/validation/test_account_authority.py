"""Account races through real API, confirmation/audit owners and isolated PostgreSQL."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
import sys
from dataclasses import dataclass, field

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, text

from app.api.v1 import action_confirmations, auth as auth_api, users as users_api
from app.db.session import AsyncSessionLocal
from app.models import ActionConfirmation, AuditLog, User
from app.models.enums import UserRole
from app.services import auth, settings, telemetry

pytestmark = pytest.mark.asyncio
@dataclass(repr=False)
class SyntheticAPI:
    client: object = field(repr=False)
    ids: dict = field(repr=False)
    tokens: dict = field(repr=False)
    app: object = field(repr=False)


PASSWORD = "synthetic-original-password"
FIRST = "synthetic-first-reset-password"
SECOND = "synthetic-second-reset-password"


@pytest_asyncio.fixture
async def api(isolated_resources):
    settings.invalidate_runtime_config_cache()
    async with AsyncSessionLocal() as session:
        accounts = {}
        for name, role in [("alpha", UserRole.ADMIN), ("beta", UserRole.ADMIN), ("standard", UserRole.STANDARD)]:
            user = await auth.create_user(session, username=name, first_name=name, last_name="Synthetic",
                password=PASSWORD, role=role)
            accounts[name] = user
        await session.commit()
        tokens = {name: (await auth.create_access_token(user))[0] for name, user in accounts.items()}
        ids = {name: user.id for name, user in accounts.items()}
    app = FastAPI()
    app.include_router(auth_api.router, prefix="/api/v1/auth")
    app.include_router(users_api.router, prefix="/api/v1/users")
    app.include_router(action_confirmations.router, prefix="/api/v1/action-confirmations")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app, raise_app_exceptions=False),
        base_url="http://synthetic.invalid", trust_env=False) as client:
        yield SyntheticAPI(client=client, ids=ids, tokens=tokens, app=app)
    settings.invalidate_runtime_config_cache()


def headers(api, actor="alpha"):
    return {"Authorization": "Bearer " + api.tokens[actor]}


async def approved(api, action, target, *, actor="alpha", changes=None):
    payload = {"user_id": str(api.ids[target]), **(changes or {})}
    result = await api.client.post("/api/v1/action-confirmations", headers=headers(api, actor),
        json={"action": action, "payload": payload, "target_entity": "User", "target_id": str(api.ids[target])})
    assert result.status_code == 200
    return result.json()["confirmation_token"]


async def reset(api, password, *, actor="alpha", target="standard", token=None):
    token = token or await approved(api, "user.reset_password", target, actor=actor)
    return await api.client.post(f"/api/v1/users/{api.ids[target]}/reset-password", headers=headers(api, actor),
        json={"temporary_password": password, "confirmation_token": token})


async def login(api, password=PASSWORD, name="standard"):
    api.client.cookies.clear()
    response = await api.client.post("/api/v1/auth/login", json={"username": name, "password": password})
    return response, api.client.cookies.get("iacs_session")


async def valid(token):
    async with AsyncSessionLocal() as session:
        return await auth.authenticate_token(session, token) is not None


@pytest.mark.parametrize("password,expected", [(PASSWORD, 200), ("synthetic-wrong-password", 401)])
async def test_login_controls(api, password, expected):
    response, token = await login(api, password)
    assert response.status_code == expected
    assert await valid(token) is (expected == 200)


async def test_login_cannot_adopt_reset_version_after_verifying_old_password(api, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = auth_api.verify_password_async
    async def paused(*args):
        result = await original(*args)
        entered.set()
        await asyncio.wait_for(release.wait(), 8)
        return result
    monkeypatch.setattr(auth_api, "verify_password_async", paused)
    pending = asyncio.create_task(login(api))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        assert (await reset(api, FIRST)).status_code == 200
    finally:
        release.set()
    response, token = await pending
    assert response.status_code in (200, 401)
    assert not await valid(token), "Old verified credentials acquired post-reset authority"


async def test_overlapping_resets_increment_twice_and_revoke_intermediate_token(api, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = users_api.hash_password_async
    async def paused(password):
        result = await original(password)
        if password == FIRST:
            entered.set()
            await asyncio.wait_for(release.wait(), 8)
        return result
    monkeypatch.setattr(users_api, "hash_password_async", paused)
    pending = asyncio.create_task(reset(api, FIRST))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        assert (await reset(api, SECOND)).status_code == 200
        response, intermediate = await login(api, SECOND)
        assert response.status_code == 200 and await valid(intermediate)
    finally:
        release.set()
    assert (await pending).status_code == 200
    async with AsyncSessionLocal() as session:
        version = await session.scalar(select(User.auth_session_version).where(User.id == api.ids["standard"]))
    assert version == 2, "Two successful resets collapsed into one session-version increment"
    assert not await valid(intermediate)
    assert not await valid(api.tokens["standard"])


async def wait_second_reaches_boundary_or_blocks(second_entered):
    # An actual database lock is an acceptable serialization boundary. A second
    # audit entry before first commit exposes the old unlocked mutation window.
    async with asyncio.timeout(8):
        while not second_entered.is_set():
            async with AsyncSessionLocal() as observer:
                blocked = await observer.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock')"))
            if blocked:
                return
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("actors,targets", [(("alpha", "alpha"), ("beta", "alpha")),
    (("alpha", "beta"), ("alpha", "beta")), (("alpha", "beta"), ("beta", "alpha"))])
async def test_concurrent_deletions_preserve_a_current_active_admin(api, monkeypatch, actors, targets):
    tokens = [await approved(api, "user.delete", target, actor=actor) for actor, target in zip(actors, targets)]
    entered = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    original = telemetry.write_audit_log
    calls = 0
    async def paused(session, **kwargs):
        nonlocal calls
        if kwargs.get("action") == "user.delete":
            index = calls
            calls += 1
            entered[index].set()
            await asyncio.wait_for(release[index].wait(), 8)
        return await original(session, **kwargs)
    patch_account_audit(monkeypatch, paused)
    async def delete(index):
        return await api.client.request("DELETE", f"/api/v1/users/{api.ids[targets[index]]}",
            headers=headers(api, actors[index]), json={"confirmation_token": tokens[index]})
    first = asyncio.create_task(delete(0))
    second = None
    try:
        await asyncio.wait_for(entered[0].wait(), 8)
        second = asyncio.create_task(delete(1))
        await wait_second_reaches_boundary_or_blocks(entered[1])
        release[0].set()
        first_response = await asyncio.wait_for(first, 8)
        release[1].set()
        second_response = await asyncio.wait_for(second, 8)
    finally:
        for event in release:
            event.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
    assert first_response.status_code == 204
    assert second_response.status_code in (401, 403, 409), "Second deletion did not safely reject stale last-admin/actor authority"
    async with AsyncSessionLocal() as session:
        admins = (await session.scalars(select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True)))).all()
    assert len(admins) == 1


async def test_sequential_reset_confirmation_and_old_password_controls(api):
    token = await approved(api, "user.reset_password", "standard")
    assert (await reset(api, FIRST, token=token)).status_code == 200
    assert (await reset(api, SECOND, token=token)).status_code == 409
    assert (await login(api))[0].status_code == 401
    assert (await login(api, FIRST))[0].status_code == 200
    assert (await reset(api, SECOND)).status_code == 200
    assert (await login(api, FIRST))[0].status_code == 401
    async with AsyncSessionLocal() as session:
        user = await session.get(User, api.ids["standard"])
        assert user.auth_session_version == 2
        assert len((await session.scalars(select(AuditLog).where(AuditLog.action == "user.reset_password"))).all()) == 2
        assert len((await session.scalars(select(ActionConfirmation).where(ActionConfirmation.outcome == "consumed"))).all()) == 2


def patch_account_audit(monkeypatch, replacement):
    if hasattr(users_api, "write_audit_log"):
        monkeypatch.setattr(users_api, "write_audit_log", replacement)
    owner = sys.modules.get("app.services.user_accounts")
    if owner is not None:
        monkeypatch.setattr(owner, "write_audit_log", replacement)


@pytest.mark.parametrize("cancel", [False, True])
async def test_reset_failure_or_cancellation_rolls_back_confirmation_and_account(api, monkeypatch, cancel):
    token = await approved(api, "user.reset_password", "standard")
    entered, release = asyncio.Event(), asyncio.Event()
    original = telemetry.write_audit_log
    async def fault(session, **kwargs):
        if kwargs.get("action") == "user.reset_password":
            entered.set()
            if cancel:
                await release.wait()
            else:
                raise RuntimeError("synthetic required audit failure")
        return await original(session, **kwargs)
    patch_account_audit(monkeypatch, fault)
    request = asyncio.create_task(reset(api, FIRST, token=token))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        if cancel:
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        else:
            assert (await request).status_code == 500
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)
    async with AsyncSessionLocal() as session:
        user = await session.get(User, api.ids["standard"])
        assert user.auth_session_version == 0 and auth.verify_password(PASSWORD, user.password_hash)
        assert not (await session.scalars(select(ActionConfirmation).where(ActionConfirmation.outcome == "consumed"))).all()
        assert not (await session.scalars(select(AuditLog).where(AuditLog.action == "user.reset_password"))).all()


async def test_reset_rechecks_actor_after_external_hash_work(api, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = users_api.hash_password_async
    async def paused(password):
        hashed = await original(password)
        entered.set()
        await asyncio.wait_for(release.wait(), 8)
        return hashed
    monkeypatch.setattr(users_api, "hash_password_async", paused)
    request = asyncio.create_task(reset(api, FIRST))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        confirmation = await approved(api, "user.update", "alpha", actor="beta", changes={"role": "standard"})
        response = await api.client.patch(f"/api/v1/users/{api.ids['alpha']}", headers=headers(api, "beta"),
            json={"role": "standard", "confirmation_token": confirmation})
        assert response.status_code == 200
    finally:
        release.set()
    assert (await request).status_code in (401, 403)
    async with AsyncSessionLocal() as session:
        assert (await session.get(User, api.ids["standard"])).auth_session_version == 0


async def test_login_rechecks_disabling_after_password_verification(api, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = auth_api.verify_password_async
    async def paused(*args):
        result = await original(*args)
        entered.set()
        await asyncio.wait_for(release.wait(), 8)
        return result
    monkeypatch.setattr(auth_api, "verify_password_async", paused)
    request = asyncio.create_task(login(api))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        confirmation = await approved(api, "user.update", "standard", changes={"is_active": False})
        assert (await api.client.patch(f"/api/v1/users/{api.ids['standard']}", headers=headers(api),
            json={"is_active": False, "confirmation_token": confirmation})).status_code == 200
    finally:
        release.set()
    response, token = await request
    assert response.status_code == 401
    assert not await valid(token)


@pytest.mark.parametrize("operation", ["delete", "demote", "deactivate"])
async def test_last_admin_rejected_and_ordinary_removal_allowed(api, operation):
    token = await approved(api, "user.delete", "beta")
    assert (await api.client.request("DELETE", f"/api/v1/users/{api.ids['beta']}", headers=headers(api),
        json={"confirmation_token": token})).status_code == 204
    if operation == "delete":
        token = await approved(api, "user.delete", "alpha")
        response = await api.client.request("DELETE", f"/api/v1/users/{api.ids['alpha']}", headers=headers(api),
            json={"confirmation_token": token})
    else:
        changes = {"role": "standard"} if operation == "demote" else {"is_active": False}
        token = await approved(api, "user.update", "alpha", changes=changes)
        response = await api.client.patch(f"/api/v1/users/{api.ids['alpha']}", headers=headers(api),
            json={**changes, "confirmation_token": token})
    assert response.status_code == 409
    async with AsyncSessionLocal() as session:
        user = await session.get(User, api.ids["alpha"])
        assert user.is_active and user.role == UserRole.ADMIN


@pytest.mark.parametrize("changes", [{"role": "standard"}, {"is_active": False}])
async def test_delete_racing_demotion_or_deactivation_preserves_admin(api, monkeypatch, changes):
    delete_token = await approved(api, "user.delete", "beta")
    update_token = await approved(api, "user.update", "alpha", changes=changes)
    entered = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    original = telemetry.write_audit_log
    async def pause(session, **kwargs):
        action = kwargs.get("action")
        if action in ("user.delete", "user.update"):
            index = 0 if action == "user.delete" else 1
            entered[index].set()
            await asyncio.wait_for(release[index].wait(), 8)
        return await original(session, **kwargs)
    patch_account_audit(monkeypatch, pause)
    first = asyncio.create_task(api.client.request("DELETE", f"/api/v1/users/{api.ids['beta']}",
        headers=headers(api), json={"confirmation_token": delete_token}))
    second = None
    try:
        await asyncio.wait_for(entered[0].wait(), 8)
        second = asyncio.create_task(api.client.patch(f"/api/v1/users/{api.ids['alpha']}",
            headers=headers(api), json={**changes, "confirmation_token": update_token}))
        await wait_second_reaches_boundary_or_blocks(entered[1])
        if entered[1].is_set():
            # Baseline update holds User locks after a stale invariant check.
            # Let it commit first so the test does not manufacture a deadlock.
            release[1].set()
            second_response = await asyncio.wait_for(second, 8)
            release[0].set()
            first_response = await asyncio.wait_for(first, 8)
        else:
            release[0].set()
            first_response = await asyncio.wait_for(first, 8)
            release[1].set()
            second_response = await asyncio.wait_for(second, 8)
    finally:
        for event in release:
            event.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
    assert first_response.status_code == 204 and second_response.status_code == 409
    async with AsyncSessionLocal() as session:
        admins = (await session.scalars(select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True)))).all()
        assert len(admins) == 1


@pytest.mark.parametrize("change", ["delete", "reset"])
async def test_reset_rechecks_deleted_or_session_invalidated_actor_and_audits_safely(api, monkeypatch, change):
    entered, release = asyncio.Event(), asyncio.Event()
    original = users_api.hash_password_async
    async def pause(password):
        result = await original(password)
        if password == FIRST:
            entered.set()
            await asyncio.wait_for(release.wait(), 8)
        return result
    monkeypatch.setattr(users_api, "hash_password_async", pause)
    first = asyncio.create_task(reset(api, FIRST))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        if change == "delete":
            token = await approved(api, "user.delete", "alpha", actor="beta")
            result = await api.client.request("DELETE", f"/api/v1/users/{api.ids['alpha']}",
                headers=headers(api, "beta"), json={"confirmation_token": token})
        else:
            result = await reset(api, SECOND, actor="beta", target="alpha")
        assert result.status_code in (200, 204)
    finally:
        release.set()
    assert (await first).status_code == 403
    async with AsyncSessionLocal() as session:
        assert (await session.get(User, api.ids["standard"])).auth_session_version == 0
        rejected = (await session.scalars(select(AuditLog).where(AuditLog.action == "user.mutation.rejected"))).all()
        assert len(rejected) == 1
        if change == "delete":
            assert rejected[0].actor_user_id is None


async def test_reset_committing_after_login_issuance_checkpoint_revokes_that_authority(api, monkeypatch):
    entered, release, reset_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = auth.create_access_token
    async def pause(*args, **kwargs):
        result = await original(*args, **kwargs)
        entered.set()
        await asyncio.wait_for(release.wait(), 8)
        return result
    monkeypatch.setattr(auth, "create_access_token", pause)
    if hasattr(auth_api, "create_access_token"):
        monkeypatch.setattr(auth_api, "create_access_token", pause)
    first = asyncio.create_task(login(api))
    second = None
    async def reset_later():
        result = await reset(api, FIRST)
        reset_done.set()
        return result
    try:
        await asyncio.wait_for(entered.wait(), 8)
        second = asyncio.create_task(reset_later())
        await wait_second_reaches_boundary_or_blocks(reset_done)
    finally:
        release.set()
    response, token = await first
    assert response.status_code == 200 and (await second).status_code == 200
    assert not await valid(token)


async def test_account_creation_revalidates_actor_after_hashing(api, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = auth.hash_password_async
    async def pause(password):
        result = await original(password)
        entered.set()
        await asyncio.wait_for(release.wait(), 8)
        return result
    monkeypatch.setattr(auth, "hash_password_async", pause)
    monkeypatch.setattr(users_api, "hash_password_async", pause)
    payload = {"username": "candidate", "first_name": "Candidate", "last_name": "Synthetic"}
    confirmation = await api.client.post("/api/v1/action-confirmations", headers=headers(api),
        json={"action": "user.create", "payload": payload, "target_entity": "User"})
    assert confirmation.status_code == 200
    request = asyncio.create_task(api.client.post("/api/v1/users", headers=headers(api),
        json={**payload, "temporary_password": FIRST, "confirmation_token": confirmation.json()["confirmation_token"]}))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        token = await approved(api, "user.update", "alpha", actor="beta", changes={"role": "standard"})
        result = await api.client.patch(f"/api/v1/users/{api.ids['alpha']}", headers=headers(api, "beta"),
            json={"role": "standard", "confirmation_token": token})
        assert result.status_code == 200
    finally:
        release.set()
    assert (await request).status_code == 403
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(User.id).where(User.username == "candidate")) is None


async def test_participant_confirmation_rejection_survives_actor_deletion_after_rollback(api):
    from app.services.action_confirmations import ActionConfirmationError, consume_action_confirmation, persist_confirmation_rejection
    async with AsyncSessionLocal() as session:
        actor = await session.get(User, api.ids["alpha"])
        try:
            await consume_action_confirmation(session, user=actor, action="user.delete", payload={"user_id": str(api.ids["standard"])},
                confirmation_token="synthetic-nonexistent-confirmation", commit=False)
        except ActionConfirmationError as error:
            rejection = error.rejection
        else:
            pytest.fail("Invalid confirmation was accepted")
        await session.rollback()
    token = await approved(api, "user.delete", "alpha", actor="beta")
    assert (await api.client.request("DELETE", f"/api/v1/users/{api.ids['alpha']}", headers=headers(api, "beta"),
        json={"confirmation_token": token})).status_code == 204
    await persist_confirmation_rejection(rejection)
    async with AsyncSessionLocal() as session:
        row = await session.scalar(select(AuditLog).where(AuditLog.action == "real_world_action.confirmation.rejected"))
        assert row is not None and row.actor_user_id is None


async def test_missing_confirmation_rejects_before_hash_without_password_or_authority_change(api, monkeypatch):
    async def no_hash(_password):
        raise AssertionError("A missing approval must not hash a password")
    monkeypatch.setattr(users_api, "hash_password_async", no_hash)
    result = await api.client.post(f"/api/v1/users/{api.ids['standard']}/reset-password",
        headers=headers(api), json={"generate_password": True})
    assert result.status_code == 428
    async with AsyncSessionLocal() as session:
        assert (await session.get(User, api.ids["standard"])).auth_session_version == 0
        assert await session.scalar(select(AuditLog.id).where(AuditLog.action == "user.mutation.rejected")) is not None


@pytest.mark.parametrize("fault", ["audit", "cancel"])
async def test_delete_required_audit_failure_and_cancellation_roll_back_confirmation_and_user(api, monkeypatch, fault):
    token = await approved(api, "user.delete", "beta")
    entered = asyncio.Event()
    original = telemetry.write_audit_log
    async def fail_or_wait(session, **kwargs):
        if kwargs.get("action") == "user.delete":
            entered.set()
            if fault == "audit":
                raise RuntimeError("synthetic required audit failure")
            await asyncio.Event().wait()
        return await original(session, **kwargs)
    patch_account_audit(monkeypatch, fail_or_wait)
    task = asyncio.create_task(api.client.request("DELETE", f"/api/v1/users/{api.ids['beta']}",
        headers=headers(api), json={"confirmation_token": token}))
    await asyncio.wait_for(entered.wait(), 8)
    if fault == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert (await task).status_code == 500
    async with AsyncSessionLocal() as session:
        assert (await session.get(User, api.ids["beta"])).is_active
        assert not (await session.scalars(select(ActionConfirmation).where(ActionConfirmation.outcome == "consumed"))).all()
        assert await session.scalar(select(AuditLog.id).where(AuditLog.action == "user.delete")) is None


@pytest.mark.parametrize("operation", ["reset", "create"])
@pytest.mark.parametrize("invalidation", ["logout", "expiry"])
async def test_prepared_account_mutation_rechecks_exact_actor_session(api, monkeypatch, operation, invalidation):
    import time
    import uuid
    entered, release = asyncio.Event(), asyncio.Event()
    original = users_api.hash_password_async
    async def pause(password):
        result = await original(password)
        entered.set()
        await asyncio.wait_for(release.wait(), 8)
        return result
    monkeypatch.setattr(users_api, "hash_password_async", pause)
    if invalidation == "expiry":
        now = int(time.time())
        expiry = now + 3
        api.tokens["alpha"] = auth._encode_jwt({"sub": str(api.ids["alpha"]), "ver": 0,
            "iat": now, "exp": expiry, "jti": "synthetic-" + uuid.uuid4().hex})
    if operation == "reset":
        token = await approved(api, "user.reset_password", "standard")
        pending = reset(api, FIRST, token=token)
    else:
        payload = {"username": "session-candidate", "first_name": "Synthetic", "last_name": "Candidate"}
        response = await api.client.post("/api/v1/action-confirmations", headers=headers(api),
            json={"action": "user.create", "payload": payload, "target_entity": "User"})
        assert response.status_code == 200
        token = response.json()["confirmation_token"]
        pending = api.client.post("/api/v1/users", headers=headers(api),
            json={**payload, "temporary_password": FIRST, "confirmation_token": token})
    task = asyncio.create_task(pending)
    try:
        await asyncio.wait_for(entered.wait(), 8)
        if invalidation == "logout":
            assert (await api.client.post("/api/v1/auth/logout", headers=headers(api))).status_code == 200
        else:
            await asyncio.sleep(max(0, expiry - time.time()) + .05)
    finally:
        release.set()
    assert (await task).status_code in (401, 403)
    async with AsyncSessionLocal() as session:
        assert (await session.get(User, api.ids["standard"])).auth_session_version == 0
        assert await session.scalar(select(User.id).where(User.username == "session-candidate")) is None
        assert not (await session.scalars(select(ActionConfirmation).where(ActionConfirmation.outcome == "consumed"))).all()
        assert await session.scalar(select(AuditLog.id).where(AuditLog.action == "user.mutation.rejected")) is not None


async def test_photo_heavy_admin_fence_defers_blobs_and_preserves_identity_photo_url(api, tmp_path):
    from sqlalchemy import event, inspect
    from app.db.session import engine
    sample = "data:image/png;base64," + "A" * 500_000
    async with AsyncSessionLocal() as session:
        for name in ("alpha", "beta"):
            (await session.get(User, api.ids[name])).profile_photo_data_url = sample
        for number in range(8):
            session.add(User(username=f"photo-admin-{number}", full_name="Synthetic Photo Admin",
                password_hash="synthetic-inert", role=UserRole.ADMIN, is_active=True, profile_photo_data_url=sample))
        await session.commit()
    loaded, materialized = [], []
    def observed(user, context):
        loaded.append(1)
        value = user.__dict__.get("profile_photo_data_url")
        if isinstance(value, str):
            materialized.append(len(value.encode()))
    event.listen(User, "load", observed)
    try:
        token = await approved(api, "user.update", "beta", changes={"first_name": "Updated"})
        response = await api.client.patch(f"/api/v1/users/{api.ids['beta']}", headers=headers(api),
            json={"first_name": "Updated", "confirmation_token": token})
        assert response.status_code == 200
        assert response.json()["profile_photo_url"].startswith(f"/api/v1/users/{api.ids['beta']}/photo")
        assert response.json()["profile_photo_data_url"] is None
    finally:
        event.remove(User, "load", observed)
    (tmp_path / "account-photo-materialization.json").write_text(__import__('json').dumps({
        "admins": 10, "photo_bytes_per_admin": len(sample.encode()), "orm_user_loads": len(loaded),
        "materialized_photo_text_bytes": sum(materialized), "measurement": "Python ORM attribute materialization, not wire bytes"}, indent=2))
    assert not materialized, "The account authority fence materialized administrative profile blobs"
