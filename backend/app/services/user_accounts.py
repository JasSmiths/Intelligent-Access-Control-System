"""Account mutations: root-first authority, admin invariant, confirmation and audit."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, with_expression

from app.core.task_lifecycle import drain_owned
from app.db.session import AsyncSessionLocal
from app.models import Person, User
from app.models.enums import UserRole
from app.services.action_confirmations import (
    ActionConfirmationError,
    consume_action_confirmation,
    persist_confirmation_rejection,
)
from app.services.auth import (
    authenticated_token,
    compose_full_name,
    new_user,
    normalize_mobile_phone_number,
    normalize_username,
    serialize_user,
)
from app.services.auth_root import reload_auth_root_for_transaction
from app.services.profile_photos import photo_reference_expression
from app.services.telemetry import (
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    audit_diff,
    write_audit_log,
)


@dataclass(frozen=True)
class AccountActor:
    id: uuid.UUID
    version: int
    label: str
    session_token: str | None = field(default=None, repr=False, compare=False)

    @classmethod
    def capture(cls, user: User) -> AccountActor:
        return cls(user.id, int(user.auth_session_version or 0), actor_from_user(user))


def user_audit_snapshot(user: User) -> dict[str, Any]:
    return {"id": str(user.id), "username": user.username, "first_name": user.first_name,
        "last_name": user.last_name, "full_name": user.full_name, "email": user.email,
        "mobile_phone_number": user.mobile_phone_number, "person_id": str(user.person_id) if user.person_id else None,
        "role": user.role.value, "is_active": user.is_active, "preferences": user.preferences or {}}


async def _record_account_rejection(proof: AccountActor, action: str, reason: str) -> None:
    # The caller has fully rolled back. Preserve textual attribution if another
    # transaction deleted the actor, and hold a key-share until audit commit.
    async with AsyncSessionLocal() as session:
        live_id = await session.scalar(select(User.id).where(User.id == proof.id).with_for_update(read=True, key_share=True))
        await write_audit_log(session, category=TELEMETRY_CATEGORY_CRUD, action="user.mutation.rejected",
            actor=proof.label, actor_user_id=live_id, target_entity="User", outcome="failed", level="warning",
            metadata={"operation": action, "reason": reason})
        await session.commit()


@asynccontextmanager
async def _mutation(session: AsyncSession, proof: AccountActor, action: str, payload: dict[str, Any],
                    token: str | None, target_id: uuid.UUID | None = None) -> AsyncIterator[tuple[User, User | None, list[User]]]:
    # Authentication may have opened a read transaction. Scalar proof was captured
    # before expensive preparation; refreshing this session cannot rewrite it.
    await session.rollback()
    try:
        await reload_auth_root_for_transaction(session)
        ids = {proof.id} | ({target_id} if target_id is not None else set())
        rows = list((await session.scalars(select(User).options(defer(User.password_hash, raiseload=True),
            defer(User.profile_photo_data_url, raiseload=True),
            with_expression(User.profile_photo_reference, photo_reference_expression(User.profile_photo_data_url)))
            .where(or_(User.id.in_(ids), (User.role == UserRole.ADMIN) & User.is_active.is_(True)))
            .order_by(User.id).with_for_update().execution_options(populate_existing=True))).all())
        actor = next((user for user in rows if user.id == proof.id), None)
        if actor is None or not actor.is_active or actor.role != UserRole.ADMIN or actor.auth_session_version != proof.version:
            raise HTTPException(status_code=403, detail="Current active Admin authentication is required.")
        verified = await authenticated_token(session, proof.session_token)
        if verified is None or verified.user.id != proof.id:
            raise HTTPException(status_code=403, detail="Current active Admin authentication is required.")
        target = next((user for user in rows if user.id == target_id), None)
        if target_id is not None and target is None:
            raise HTTPException(status_code=404, detail="User not found")
        await consume_action_confirmation(session, user=actor, action=action, payload=payload,
            confirmation_token=token, commit=False)
        yield actor, target, rows
        await session.commit()
    except BaseException as exc:
        await drain_owned(session.rollback())
        if isinstance(exc, ActionConfirmationError):
            if exc.rejection is None:
                await drain_owned(_record_account_rejection(proof, action, exc.detail))
            else:
                await drain_owned(persist_confirmation_rejection(exc.rejection))
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if isinstance(exc, HTTPException):
            await drain_owned(_record_account_rejection(proof, action, exc.detail))
        if isinstance(exc, IntegrityError):
            raise HTTPException(status_code=409, detail="User already exists or linked data changed.") from exc
        raise


def _preserve_admin(target: User, rows: list[User], *, role: UserRole, active: bool) -> None:
    removing = target.is_active and target.role == UserRole.ADMIN and (not active or role != UserRole.ADMIN)
    if removing and not any(user.id != target.id and user.is_active and user.role == UserRole.ADMIN for user in rows):
        raise HTTPException(status_code=409, detail="Cannot remove the last active admin account.")


async def create_account(session: AsyncSession, proof: AccountActor, *, payload: dict[str, Any],
                         token: str | None, values: dict[str, Any], password_hash: str, generated: bool) -> dict[str, Any]:
    async with _mutation(session, proof, "user.create", payload, token) as (actor, _, _rows):
        if values.get("person_id") and not await session.scalar(select(Person.id).where(Person.id == values["person_id"])):
            raise HTTPException(status_code=404, detail="Linked person not found")
        user = new_user(password_hash=password_hash, **values)
        session.add(user)
        await session.flush()
        await write_audit_log(session, category=TELEMETRY_CATEGORY_CRUD, action="user.create",
            actor=actor_from_user(actor), actor_user_id=actor.id, target_entity="User", target_id=user.id,
            target_label=user.username, diff={"old": {}, "new": user_audit_snapshot(user)},
            metadata={"temporary_password_generated": generated})
        result = serialize_user(user)
    return result


async def update_account(session: AsyncSession, proof: AccountActor, target_id: uuid.UUID, *,
                         payload: dict[str, Any], token: str | None, changes: dict[str, Any]) -> dict[str, Any]:
    async with _mutation(session, proof, "user.update", payload, token, target_id) as (actor, target, rows):
        assert target is not None
        before = user_audit_snapshot(target)
        role = changes.get("role") or target.role
        active = target.is_active if changes.get("is_active") is None else changes["is_active"]
        _preserve_admin(target, rows, role=role, active=active)
        target.role, target.is_active = role, active
        if changes.get("username") is not None:
            target.username = normalize_username(changes["username"])
        for key in ("first_name", "last_name"):
            if changes.get(key) is not None:
                setattr(target, key, changes[key].strip())
        if changes.get("first_name") is not None or changes.get("last_name") is not None:
            target.full_name = compose_full_name(target.first_name, target.last_name)
        if "profile_photo_data_url" in changes:
            target.profile_photo_data_url = changes["profile_photo_data_url"]
        if "email" in changes:
            target.email = changes["email"].strip().lower() if changes["email"] else None
        if "mobile_phone_number" in changes:
            target.mobile_phone_number = normalize_mobile_phone_number(changes["mobile_phone_number"])
        if "person_id" in changes:
            if changes["person_id"] and not await session.scalar(select(Person.id).where(Person.id == changes["person_id"])):
                raise HTTPException(status_code=404, detail="Linked person not found")
            target.person_id = changes["person_id"]
        if changes.get("preferences") is not None:
            target.preferences = {**(target.preferences or {}), **changes["preferences"]}
        await write_audit_log(session, category=TELEMETRY_CATEGORY_CRUD, action="user.update",
            actor=actor_from_user(actor), actor_user_id=actor.id, target_entity="User", target_id=target.id,
            target_label=target.username, diff=audit_diff(before, user_audit_snapshot(target)))
        await session.flush()
        # Flush expires query_expression values. Refresh the narrow identity
        # projection too, so profile URLs survive edits without loading blobs.
        await session.scalar(select(User).where(User.id == target.id).options(
            defer(User.password_hash, raiseload=True), defer(User.profile_photo_data_url, raiseload=True),
            with_expression(User.profile_photo_reference, photo_reference_expression(User.profile_photo_data_url)))
            .execution_options(populate_existing=True))
        result = serialize_user(target)
    return result


async def reset_account_password(session: AsyncSession, proof: AccountActor, target_id: uuid.UUID, *,
                               payload: dict[str, Any], token: str | None, password_hash: str, generated: bool) -> None:
    async with _mutation(session, proof, "user.reset_password", payload, token, target_id) as (actor, target, _rows):
        assert target is not None
        target.password_hash = password_hash
        # Fresh FOR UPDATE state under the shared root fence; concurrent resets
        # and auth-root rotation cannot collapse increments or refresh old proof.
        target.auth_session_version = int(target.auth_session_version or 0) + 1
        target.password_changed_at = datetime.now(UTC)
        await write_audit_log(session, category=TELEMETRY_CATEGORY_CRUD, action="user.reset_password",
            actor=actor_from_user(actor), actor_user_id=actor.id, target_entity="User", target_id=target.id,
            target_label=target.username, diff={"old": {"password": "[redacted]"}, "new": {"password": "[redacted]"}},
            metadata={"temporary_password_generated": generated})


async def delete_account(session: AsyncSession, proof: AccountActor, target_id: uuid.UUID, *, token: str | None) -> None:
    async with _mutation(session, proof, "user.delete", {"user_id": str(target_id)}, token, target_id) as (actor, target, rows):
        assert target is not None
        _preserve_admin(target, rows, role=target.role, active=False)
        await write_audit_log(session, category=TELEMETRY_CATEGORY_CRUD, action="user.delete",
            actor=actor_from_user(actor), actor_user_id=actor.id, target_entity="User", target_id=target.id,
            target_label=target.username, diff={"old": user_audit_snapshot(target), "new": {}})
        await session.delete(target)
