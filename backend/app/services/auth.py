import base64
import hashlib
import hmac
import json
import secrets
import string
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from fastapi import HTTPException, Request, Response, WebSocket, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import User
from app.models.enums import UserRole
from app.services.settings import get_runtime_config

password_hasher = PasswordHasher()


class AuthError(HTTPException):
    def __init__(self, detail: str = "Authentication required") -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


class AdminRequiredError(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def generate_temporary_password(length: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def normalize_username(username: str) -> str:
    return username.strip().lower()


def compose_full_name(first_name: str, last_name: str) -> str:
    return " ".join(part.strip() for part in [first_name, last_name] if part.strip())


def split_full_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1].strip()


def normalize_mobile_phone_number(mobile_phone_number: str | None) -> str | None:
    if not mobile_phone_number:
        return None
    return mobile_phone_number.strip() or None


def serialize_user(user: User) -> dict[str, Any]:
    first_name = user.first_name or split_full_name(user.full_name)[0]
    last_name = user.last_name or split_full_name(user.full_name)[1]
    return {
        "id": str(user.id),
        "username": user.username,
        "first_name": first_name,
        "last_name": last_name,
        "full_name": compose_full_name(first_name, last_name) or user.full_name,
        "profile_photo_data_url": user.profile_photo_data_url,
        "email": user.email,
        "mobile_phone_number": user.mobile_phone_number,
        "role": user.role.value,
        "is_active": user.is_active,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "person_id": str(user.person_id) if user.person_id else None,
        "preferences": user.preferences or {},
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
    }


async def count_users(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(User)) or 0)


async def count_active_admins(session: AsyncSession, exclude_user_id: uuid.UUID | None = None) -> int:
    query = select(func.count()).select_from(User).where(
        User.role == UserRole.ADMIN,
        User.is_active.is_(True),
    )
    if exclude_user_id:
        query = query.where(User.id != exclude_user_id)
    return int(await session.scalar(query) or 0)


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    full_name: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    profile_photo_data_url: str | None = None,
    mobile_phone_number: str | None = None,
    password: str,
    role: UserRole = UserRole.STANDARD,
    email: str | None = None,
    is_active: bool = True,
    person_id: uuid.UUID | None = None,
    preferences: dict[str, Any] | None = None,
) -> User:
    if full_name and (not first_name and not last_name):
        first_name, last_name = split_full_name(full_name)
    resolved_first_name = (first_name or "").strip()
    resolved_last_name = (last_name or "").strip()
    resolved_full_name = compose_full_name(resolved_first_name, resolved_last_name) or (full_name or "").strip()
    user = User(
        username=normalize_username(username),
        first_name=resolved_first_name,
        last_name=resolved_last_name,
        full_name=resolved_full_name,
        profile_photo_data_url=profile_photo_data_url,
        email=email.strip().lower() if email else None,
        mobile_phone_number=normalize_mobile_phone_number(mobile_phone_number),
        password_hash=hash_password(password),
        role=role,
        is_active=is_active,
        person_id=person_id,
        preferences=preferences or {"sidebarCollapsed": False},
    )
    session.add(user)
    await session.flush()
    return user


async def create_access_token(user: User, *, remember_me: bool = False) -> tuple[str, datetime]:
    runtime = await get_runtime_config()
    now = datetime.now(tz=UTC)
    expires_at = now + (
        timedelta(days=runtime.auth_remember_days)
        if remember_me
        else timedelta(minutes=runtime.auth_access_token_minutes)
    )
    payload = {
        "sub": str(user.id),
        "role": user.role.value,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_urlsafe(18),
    }
    return _encode_jwt(payload), expires_at


async def set_session_cookie(response: Response, token: str, expires_at: datetime) -> None:
    runtime = await get_runtime_config()
    response.set_cookie(
        key=runtime.auth_cookie_name,
        value=token,
        httponly=True,
        secure=runtime.auth_cookie_secure,
        samesite="lax",
        path="/",
        expires=expires_at,
    )


async def clear_session_cookie(response: Response) -> None:
    runtime = await get_runtime_config()
    response.delete_cookie(key=runtime.auth_cookie_name, path="/", samesite="lax")


async def authenticate_token(session: AsyncSession, token: str | None) -> User | None:
    if not token:
        return None
    payload = _decode_jwt(token)
    if not payload:
        return None

    subject = payload.get("sub")
    if not subject:
        return None
    try:
        user_id = uuid.UUID(str(subject))
    except ValueError:
        return None

    user = await session.get(User, user_id)
    if not user or not user.is_active:
        return None
    return user


async def authenticate_request(session: AsyncSession, request: Request) -> User | None:
    return await authenticate_token(session, await _extract_http_token_async(request))


async def authenticate_websocket(session: AsyncSession, websocket: WebSocket) -> User | None:
    runtime = await get_runtime_config()
    token = websocket.cookies.get(runtime.auth_cookie_name) or websocket.query_params.get("token")
    authorization = websocket.headers.get("authorization")
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    return await authenticate_token(session, token)


def require_admin(user: User) -> None:
    if user.role != UserRole.ADMIN:
        raise AdminRequiredError()


async def _extract_http_token_async(request: Request) -> str | None:
    authorization = request.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    runtime = await get_runtime_config()
    return request.cookies.get(runtime.auth_cookie_name)


def _encode_jwt(payload: dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_part = _b64encode_json(header)
    payload_part = _b64encode_json(payload)
    signing_input = f"{header_part}.{payload_part}".encode()
    signature = hmac.new(settings.auth_secret_key.encode(), signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_b64encode(signature)}"


def _decode_jwt(token: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    signing_input = f"{parts[0]}.{parts[1]}".encode()
    expected = hmac.new(settings.auth_secret_key.encode(), signing_input, hashlib.sha256).digest()
    try:
        supplied = _b64decode(parts[2])
    except ValueError:
        return None
    if not hmac.compare_digest(expected, supplied):
        return None
    try:
        payload = json.loads(_b64decode(parts[1]))
    except (ValueError, json.JSONDecodeError):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int) or datetime.now(tz=UTC).timestamp() >= exp:
        return None
    return payload


def _b64encode_json(value: dict[str, Any]) -> str:
    return _b64encode(json.dumps(value, separators=(",", ":"), sort_keys=True).encode())


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
