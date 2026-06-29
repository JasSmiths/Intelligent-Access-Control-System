import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import current_user
from app.api.v1.media import PhotoVariant, data_url_media_response
from app.db.session import get_db_session
from app.models import User
from app.models.enums import UserRole
from app.services.auth import (
    authenticate_request,
    clear_session_cookie,
    compose_full_name,
    count_users,
    create_access_token,
    create_user,
    normalize_username,
    extract_http_token,
    revoke_access_token,
    serialize_user,
    set_session_cookie,
    verify_password_async,
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


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)
    remember_me: bool = False


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: EmailStr | None = None
    password: str = Field(min_length=10, max_length=256)
    profile_photo_data_url: str | None = Field(default=None, max_length=11_200_000)
    mobile_phone_number: str | None = Field(default=None, max_length=40)


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


class AuthStatusResponse(BaseModel):
    setup_required: bool
    authenticated: bool
    user: UserResponse | None = None


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(
    request: Request,
    include_photo: bool = Query(default=False),
    session: AsyncSession = Depends(get_db_session),
) -> AuthStatusResponse:
    setup_required = await count_users(session) == 0
    if setup_required:
        return AuthStatusResponse(setup_required=True, authenticated=False)

    user = await authenticate_request(session, request)
    return AuthStatusResponse(
        setup_required=False,
        authenticated=bool(user),
        user=UserResponse(
            **serialize_user(
                user,
                include_photo=include_photo,
                photo_url_path="/api/v1/auth/me/photo",
            )
        ) if user else None,
    )


@router.post("/setup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def first_run_setup(
    request: SetupRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    if await count_users(session) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Initial setup is locked because a user already exists.",
        )

    try:
        user = await create_user(
            session,
            username=request.username,
            first_name=request.first_name,
            last_name=request.last_name,
            full_name=compose_full_name(request.first_name, request.last_name),
            profile_photo_data_url=await normalize_profile_photo_or_400(request.profile_photo_data_url),
            mobile_phone_number=request.mobile_phone_number,
            email=request.email,
            password=request.password,
            role=UserRole.ADMIN,
            is_active=True,
        )
        user.last_login_at = datetime.now(tz=UTC)
        await session.commit()
        await session.refresh(user)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists") from exc

    token, expires_at = await create_access_token(user, remember_me=True)
    await set_session_cookie(response, token, expires_at)
    return UserResponse(**serialize_user(user, photo_url_path="/api/v1/auth/me/photo"))


@router.post("/login", response_model=UserResponse)
async def login(
    request: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    user = await session.scalar(
        select(User).where(User.username == normalize_username(request.username))
    )
    password_ok = bool(user and user.is_active) and await verify_password_async(request.password, user.password_hash)
    if not password_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    user.last_login_at = datetime.now(tz=UTC)
    await session.commit()
    await session.refresh(user)
    token, expires_at = await create_access_token(user, remember_me=request.remember_me)
    await set_session_cookie(response, token, expires_at)
    return UserResponse(**serialize_user(user, photo_url_path="/api/v1/auth/me/photo"))


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    await revoke_access_token(session, await extract_http_token(request))
    await clear_session_cookie(response)
    return {"status": "logged_out"}


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(current_user)) -> UserResponse:
    return UserResponse(**serialize_user(user, photo_url_path="/api/v1/auth/me/photo"))


@router.get("/me/photo")
async def my_photo(
    user: User = Depends(current_user),
    variant: PhotoVariant = Query(default="full"),
) -> Response:
    return await data_url_media_response(user.profile_photo_data_url, variant=variant)


@router.patch("/me/preferences", response_model=UserResponse)
async def update_my_preferences(
    preferences: dict[str, Any],
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    db_user = await session.get(User, user.id)
    if not db_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    db_user.preferences = {**(db_user.preferences or {}), **preferences}
    await session.commit()
    await session.refresh(db_user)
    return UserResponse(**serialize_user(db_user, photo_url_path="/api/v1/auth/me/photo"))
