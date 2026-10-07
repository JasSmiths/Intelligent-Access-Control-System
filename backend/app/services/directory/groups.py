import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Group, User
from app.schemas.directory import CreateGroupRequest, GroupResponse, UpdateGroupRequest
from app.services.telemetry import (
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    audit_diff,
    write_audit_log,
)

from .errors import DirectoryOperationError
from .reads import get_group_response
from .representation import group_audit_snapshot


async def add_group(
    request: CreateGroupRequest,
    user: User,
    session: AsyncSession,
) -> GroupResponse:
    group = Group(
        name=request.name.strip(),
        category=request.category,
        subtype=request.subtype.strip() if request.subtype else None,
        description=request.description.strip() if request.description else None,
    )
    session.add(group)
    try:
        await session.flush()
        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_CRUD,
            action="group.create",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="Group",
            target_id=group.id,
            target_label=group.name,
            diff={"old": {}, "new": group_audit_snapshot(group)},
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise DirectoryOperationError(status_code=409, detail="Group already exists") from exc

    return await get_group_response(session, group.id)


async def update_group(
    group_id: uuid.UUID,
    request: UpdateGroupRequest,
    user: User,
    session: AsyncSession,
) -> GroupResponse:
    group = await session.get(Group, group_id)
    if not group:
        raise DirectoryOperationError(status_code=404, detail="Group not found")
    before = group_audit_snapshot(group)

    if request.name is not None:
        group.name = request.name.strip()
    if request.category is not None:
        group.category = request.category
    if "subtype" in request.model_fields_set:
        group.subtype = request.subtype.strip() if request.subtype else None
    if "description" in request.model_fields_set:
        group.description = request.description.strip() if request.description else None

    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_CRUD,
        action="group.update",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Group",
        target_id=group.id,
        target_label=group.name,
        diff=audit_diff(before, group_audit_snapshot(group)),
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise DirectoryOperationError(status_code=409, detail="Group already exists") from exc

    return await get_group_response(session, group.id)
