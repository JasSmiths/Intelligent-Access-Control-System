import asyncio
import uuid
from dataclasses import replace
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user
from app.api.v1.media import PhotoVariant, data_url_media_response
from app.db.session import get_db_session
from app.models import User
from app.models.enums import UserRole
from app.services import user_accounts
from app.services.auth import (
    extract_http_token,
    generate_temporary_password,
    hash_password_async,
    serialize_user,
)
from app.services.profile_photos import ProfilePhotoError, normalize_profile_photo_data_url

router = APIRouter()


async def normalize_profile_photo_or_400(profile_photo_data_url: str | None) -> str | None:
    try:
        return await asyncio.to_thread(normalize_profile_photo_data_url, profile_photo_data_url)
    except ProfilePhotoError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profile photo could not be processed.",
        ) from exc




class UserResponse(BaseModel):
    id: str
    username: str
    first_name: str
    last_name: str
    full_name: str
    profile_photo_data_url: str | None
    profile_photo_url: str | None = None
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
    confirmation_token: str | None = Field(default=None, max_length=160)


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
    confirmation_token: str | None = Field(default=None, max_length=160)


class ResetPasswordRequest(BaseModel):
    temporary_password: str | None = Field(default=None, min_length=10, max_length=256)
    generate_password: bool = False
    confirmation_token: str | None = Field(default=None, max_length=160)


class ResetPasswordResponse(BaseModel):
    temporary_password: str


class DeleteUserRequest(BaseModel):
    confirmation_token: str | None = Field(default=None, max_length=160)


@router.get("", response_model=list[UserResponse])
async def list_users(
    _: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    include_photo: Annotated[bool, Query()] = False,
) -> list[UserResponse]:
    users = (await session.scalars(select(User).order_by(User.first_name, User.last_name))).all()
    return [UserResponse(**serialize_user(user, include_photo=include_photo)) for user in users]


@router.get("/{user_id}/photo")
async def user_photo(
    user_id: uuid.UUID,
    _: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    variant: Annotated[PhotoVariant, Query()] = "full",
) -> Response:
    row = (await session.execute(select(User.profile_photo_data_url).where(User.id == user_id))).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return await data_url_media_response(row.profile_photo_data_url, variant=variant)


@router.post("", response_model=CreateUserResponse, status_code=status.HTTP_201_CREATED)
async def add_user(request: CreateUserRequest, actor: Annotated[User, Depends(admin_user)],
                   session: Annotated[AsyncSession, Depends(get_db_session)], http_request: Request) -> CreateUserResponse:
    proof = user_accounts.AccountActor.capture(actor)
    proof = replace(proof, session_token=await extract_http_token(http_request))
    payload = request.model_dump(mode="json", exclude={"confirmation_token", "temporary_password", "profile_photo_data_url"},
        exclude_none=True, exclude_unset=True)
    generated = bool(request.generate_password or not request.temporary_password)
    password = generate_temporary_password() if generated else request.temporary_password
    assert password is not None
    password_hash = await hash_password_async(password) if request.confirmation_token else ""
    values = request.model_dump(exclude={"confirmation_token", "temporary_password", "generate_password"})
    values["profile_photo_data_url"] = await normalize_profile_photo_or_400(request.profile_photo_data_url)
    result = await user_accounts.create_account(session, proof, payload=payload, token=request.confirmation_token,
        values=values, password_hash=password_hash, generated=generated)
    return CreateUserResponse(user=UserResponse(**result), temporary_password=password)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(user_id: uuid.UUID, request: UpdateUserRequest, actor: Annotated[User, Depends(admin_user)],
                      session: Annotated[AsyncSession, Depends(get_db_session)], http_request: Request) -> UserResponse:
    proof = user_accounts.AccountActor.capture(actor)
    proof = replace(proof, session_token=await extract_http_token(http_request))
    payload = request.model_dump(mode="json", exclude={"confirmation_token", "profile_photo_data_url"},
        exclude_none=True, exclude_unset=True)
    changes = request.model_dump(exclude={"confirmation_token"}, exclude_unset=True)
    if "profile_photo_data_url" in changes:
        changes["profile_photo_data_url"] = await normalize_profile_photo_or_400(changes["profile_photo_data_url"])
    result = await user_accounts.update_account(session, proof, user_id,
        payload={"user_id": str(user_id), **payload}, token=request.confirmation_token, changes=changes)
    return UserResponse(**result)


@router.post("/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def reset_password(user_id: uuid.UUID, request: ResetPasswordRequest, actor: Annotated[User, Depends(admin_user)],
                         session: Annotated[AsyncSession, Depends(get_db_session)], http_request: Request) -> ResetPasswordResponse:
    proof = user_accounts.AccountActor.capture(actor)
    proof = replace(proof, session_token=await extract_http_token(http_request))
    payload = request.model_dump(mode="json", exclude={"confirmation_token", "temporary_password"},
        exclude_none=True, exclude_unset=True)
    generated = bool(request.generate_password or not request.temporary_password)
    password = generate_temporary_password() if generated else request.temporary_password
    assert password is not None
    password_hash = await hash_password_async(password) if request.confirmation_token else ""
    await user_accounts.reset_account_password(session, proof, user_id,
        payload={"user_id": str(user_id), **payload}, token=request.confirmation_token,
        password_hash=password_hash, generated=generated)
    return ResetPasswordResponse(temporary_password=password)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: uuid.UUID, actor: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)], http_request: Request,
    request: Annotated[DeleteUserRequest | None, Body()] = None) -> None:
    proof = user_accounts.AccountActor.capture(actor)
    proof = replace(proof, session_token=await extract_http_token(http_request))
    await user_accounts.delete_account(session, proof, user_id, token=request.confirmation_token if request else None)
