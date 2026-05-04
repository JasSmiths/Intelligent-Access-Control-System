import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user
from app.db.session import get_db_session
from app.models import Person, User
from app.models.enums import UserRole
from app.services.auth import (
    compose_full_name,
    count_active_admins,
    create_user,
    generate_temporary_password,
    hash_password,
    normalize_username,
    normalize_mobile_phone_number,
    serialize_user,
)
from app.services.telemetry import (
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    audit_diff,
    write_audit_log,
)

router = APIRouter()


def user_audit_snapshot(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": user.full_name,
        "email": user.email,
        "mobile_phone_number": user.mobile_phone_number,
        "person_id": str(user.person_id) if user.person_id else None,
        "role": user.role.value,
        "is_active": user.is_active,
        "preferences": user.preferences or {},
    }


class UserResponse(BaseModel):
    id: str
    username: str
    first_name: str
    last_name: str
    full_name: str
    profile_photo_data_url: str | None
    email: str | None
    mobile_phone_number: str | None
    role: str
    is_active: bool
    last_login_at: str | None
    person_id: str | None = None
    preferences: dict[str, Any]
    created_at: str
    updated_at: str


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: EmailStr | None = None
    profile_photo_data_url: str | None = Field(default=None, max_length=11_200_000)
    mobile_phone_number: str | None = Field(default=None, max_length=40)
    person_id: uuid.UUID | None = None
    role: UserRole = UserRole.STANDARD
    is_active: bool = True
    temporary_password: str | None = Field(default=None, min_length=10, max_length=256)
    generate_password: bool = False


class CreateUserResponse(BaseModel):
    user: UserResponse
    temporary_password: str | None = None


class UpdateUserRequest(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=80)
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    email: EmailStr | None = None
    profile_photo_data_url: str | None = Field(default=None, max_length=11_200_000)
    mobile_phone_number: str | None = Field(default=None, max_length=40)
    person_id: uuid.UUID | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    preferences: dict[str, Any] | None = None


class ResetPasswordRequest(BaseModel):
    temporary_password: str | None = Field(default=None, min_length=10, max_length=256)
    generate_password: bool = False


class ResetPasswordResponse(BaseModel):
    temporary_password: str


@router.get("", response_model=list[UserResponse])
async def list_users(
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[UserResponse]:
    users = (await session.scalars(select(User).order_by(User.first_name, User.last_name))).all()
    return [UserResponse(**serialize_user(user)) for user in users]


@router.post("", response_model=CreateUserResponse, status_code=status.HTTP_201_CREATED)
async def add_user(
    request: CreateUserRequest,
    actor: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> CreateUserResponse:
    temporary_password = (
        generate_temporary_password()
        if request.generate_password or not request.temporary_password
        else request.temporary_password
    )
    try:
        if request.person_id and not await session.get(Person, request.person_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Linked person not found")
        user = await create_user(
            session,
            username=request.username,
            first_name=request.first_name,
            last_name=request.last_name,
            full_name=compose_full_name(request.first_name, request.last_name),
            profile_photo_data_url=request.profile_photo_data_url,
            mobile_phone_number=request.mobile_phone_number,
            email=request.email,
            password=temporary_password,
            role=request.role,
            is_active=request.is_active,
            person_id=request.person_id,
        )
        await session.flush()
        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_CRUD,
            action="user.create",
            actor=actor_from_user(actor),
            actor_user_id=actor.id,
            target_entity="User",
            target_id=user.id,
            target_label=user.username,
            diff={"old": {}, "new": user_audit_snapshot(user)},
            metadata={"temporary_password_generated": bool(request.generate_password or not request.temporary_password)},
        )
        await session.commit()
        await session.refresh(user)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists") from exc

    return CreateUserResponse(user=UserResponse(**serialize_user(user)), temporary_password=temporary_password)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    request: UpdateUserRequest,
    actor: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    before = user_audit_snapshot(user)

    if request.role is not None and request.role != user.role:
        if user.role == UserRole.ADMIN and await count_active_admins(session, exclude_user_id=user.id) == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot remove the last active admin account.",
            )
        user.role = request.role

    if request.is_active is not None and request.is_active != user.is_active:
        if user.is_active and user.role == UserRole.ADMIN and await count_active_admins(session, exclude_user_id=user.id) == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot deactivate the last active admin account.",
            )
        user.is_active = request.is_active

    if request.username is not None:
        user.username = normalize_username(request.username)
    if request.first_name is not None:
        user.first_name = request.first_name.strip()
    if request.last_name is not None:
        user.last_name = request.last_name.strip()
    if request.first_name is not None or request.last_name is not None:
        user.full_name = compose_full_name(user.first_name, user.last_name)
    if "profile_photo_data_url" in request.model_fields_set:
        user.profile_photo_data_url = request.profile_photo_data_url
    if "email" in request.model_fields_set:
        user.email = request.email.strip().lower() if request.email else None
    if "mobile_phone_number" in request.model_fields_set:
        user.mobile_phone_number = normalize_mobile_phone_number(request.mobile_phone_number)
    if "person_id" in request.model_fields_set:
        if request.person_id and not await session.get(Person, request.person_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Linked person not found")
        user.person_id = request.person_id
    if request.preferences is not None:
        user.preferences = {**(user.preferences or {}), **request.preferences}

    try:
        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_CRUD,
            action="user.update",
            actor=actor_from_user(actor),
            actor_user_id=actor.id,
            target_entity="User",
            target_id=user.id,
            target_label=user.username,
            diff=audit_diff(before, user_audit_snapshot(user)),
        )
        await session.commit()
        await session.refresh(user)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists") from exc

    return UserResponse(**serialize_user(user))


@router.post("/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    user_id: uuid.UUID,
    request: ResetPasswordRequest,
    actor: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> ResetPasswordResponse:
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    temporary_password = (
        generate_temporary_password()
        if request.generate_password or not request.temporary_password
        else request.temporary_password
    )
    user.password_hash = hash_password(temporary_password)
    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_CRUD,
        action="user.reset_password",
        actor=actor_from_user(actor),
        actor_user_id=actor.id,
        target_entity="User",
        target_id=user.id,
        target_label=user.username,
        diff={"old": {"password": "[redacted]"}, "new": {"password": "[redacted]"}},
        metadata={"temporary_password_generated": bool(request.generate_password or not request.temporary_password)},
    )
    await session.commit()
    return ResetPasswordResponse(temporary_password=temporary_password)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    actor: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.role == UserRole.ADMIN and user.is_active and await count_active_admins(session, exclude_user_id=user.id) == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete the last active admin account.",
        )
    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_CRUD,
        action="user.delete",
        actor=actor_from_user(actor),
        actor_user_id=actor.id,
        target_entity="User",
        target_id=user.id,
        target_label=user.username,
        diff={"old": user_audit_snapshot(user), "new": {}},
    )
    await session.delete(user)
    await session.commit()
