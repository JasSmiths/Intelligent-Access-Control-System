import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import current_user
from app.api.v1.media import PhotoVariant, data_url_media_response
from app.db.session import get_db_session
from app.models import User
from app.services.auth import (
    authenticate_request,
    clear_session_cookie,
    complete_verified_login,
    count_users,
    create_access_token,
    create_initial_admin,
    extract_http_token,
    normalize_username,
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
    session: Annotated[AsyncSession, Depends(get_db_session)],
    include_photo: Annotated[bool, Query()] = False,
) -> AuthStatusResponse:
    user = await authenticate_request(session, request)
    if user is None and await count_users(session) == 0:
        return AuthStatusResponse(setup_required=True, authenticated=False)
    if user is not None and include_photo:
        await session.refresh(user, ["profile_photo_data_url"])
    return AuthStatusResponse(
        setup_required=False,
        authenticated=bool(user),
        user=UserResponse(
            **serialize_user(
                user,
                include_photo=include_photo,
                photo_url_path="/api/v1/auth/me/photo",
            )
        )
        if user
        else None,
    )


@router.post("/setup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def first_run_setup(
    request: SetupRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserResponse:
    user = await create_initial_admin(session, username=request.username,
        first_name=request.first_name, last_name=request.last_name, password=request.password,
        profile_photo_data_url=await normalize_profile_photo_or_400(request.profile_photo_data_url),
        mobile_phone_number=request.mobile_phone_number, email=request.email)

    token, expires_at = await create_access_token(user, remember_me=True)
    await set_session_cookie(response, token, expires_at)
    return UserResponse(**serialize_user(user, photo_url_path="/api/v1/auth/me/photo"))


@router.post("/login", response_model=UserResponse)
async def login(
    request: LoginRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserResponse:
    user = await session.scalar(
        select(User).where(User.username == normalize_username(request.username))
    )
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    user_id, version, password_hash = user.id, int(user.auth_session_version or 0), user.password_hash
    if not await verify_password_async(request.password, password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    result, token, expires_at = await complete_verified_login(session, user_id=user_id,
        verified_version=version, verified_hash=password_hash, username=normalize_username(request.username),
        remember_me=request.remember_me)
    await set_session_cookie(response, token, expires_at)
    return UserResponse(**result)


@router.post("/logout")
async def logout(
    request: Request, response: Response, session: Annotated[AsyncSession, Depends(get_db_session)]
) -> dict[str, str]:
    await revoke_access_token(session, await extract_http_token(request))
    await clear_session_cookie(response)
    return {"status": "logged_out"}


@router.get("/me", response_model=UserResponse)
async def me(user: Annotated[User, Depends(current_user)]) -> UserResponse:
    return UserResponse(**serialize_user(user, photo_url_path="/api/v1/auth/me/photo"))


@router.get("/me/photo")
async def my_photo(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    variant: Annotated[PhotoVariant, Query()] = "full"
) -> Response:
    photo = await session.scalar(select(User.profile_photo_data_url).where(User.id == user.id))
    return await data_url_media_response(photo, variant=variant)


@router.patch("/me/preferences", response_model=UserResponse)
async def update_my_preferences(
    preferences: dict[str, Any],
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserResponse:
    db_user = await session.get(User, user.id)
    if not db_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    db_user.preferences = {**(db_user.preferences or {}), **preferences}
    await session.commit()
    await session.refresh(db_user)
    return UserResponse(**serialize_user(db_user, photo_url_path="/api/v1/auth/me/photo"))
