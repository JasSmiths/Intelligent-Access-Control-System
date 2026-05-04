import os
import secrets
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from app.core.config import settings


DEFAULT_AUTH_SECRET_KEY = "change-me-before-production"
INSECURE_AUTH_SECRET_VALUES = {DEFAULT_AUTH_SECRET_KEY, "replace-with-a-long-random-secret"}
AUTH_SECRET_PREVIOUS_SUFFIX = ".previous"
DEVELOPMENT_ENVIRONMENTS = {"dev", "development", "local", "test", "testing"}


class AuthSecretError(RuntimeError):
    """Raised when the auth root secret cannot be loaded safely."""


@dataclass(frozen=True)
class AuthSecretStatus:
    source: Literal["env", "file", "generated"]
    environment: str
    file_path: str
    env_configured: bool
    env_default_configured: bool
    rotation_required: bool
    ui_rotation_available: bool
    detail: str


_AUTH_SECRET_CACHE: tuple[str, AuthSecretStatus] | None = None


def get_auth_secret() -> str:
    secret, _ = _load_auth_secret()
    return secret


def get_auth_secret_status() -> dict[str, object]:
    _, status = _load_auth_secret()
    return asdict(status)


def reset_auth_secret_cache() -> None:
    global _AUTH_SECRET_CACHE
    _AUTH_SECRET_CACHE = None


def generate_auth_secret_value() -> str:
    return secrets.token_urlsafe(48)


def validate_new_auth_secret(secret: str) -> str:
    value = secret.strip()
    if not value:
        raise AuthSecretError("Auth secret cannot be blank.")
    if value in INSECURE_AUTH_SECRET_VALUES:
        raise AuthSecretError("Auth secret cannot use a documented placeholder value.")
    if len(value) < 32:
        raise AuthSecretError("Auth secret must be at least 32 characters.")
    return value


def write_auth_secret_file(path: Path, secret: str, *, allow_default: bool = False) -> None:
    value = secret.strip() if allow_default else validate_new_auth_secret(secret)
    if not value:
        raise AuthSecretError("Auth secret cannot be blank.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(value + "\n")
        os.replace(temp_path, path)
        path.chmod(0o600)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def previous_auth_secret_path(path: Path | None = None) -> Path:
    target = path or settings.auth_secret_file
    return target.with_name(target.name + AUTH_SECRET_PREVIOUS_SUFFIX)


def read_previous_auth_secret(path: Path | None = None) -> str | None:
    previous = previous_auth_secret_path(path)
    if not previous.exists():
        return None
    value = previous.read_text(encoding="utf-8").strip()
    return value or None


def clear_previous_auth_secret(path: Path | None = None) -> None:
    previous_auth_secret_path(path).unlink(missing_ok=True)


def _load_auth_secret() -> tuple[str, AuthSecretStatus]:
    global _AUTH_SECRET_CACHE
    if _AUTH_SECRET_CACHE is not None:
        return _AUTH_SECRET_CACHE

    environment = settings.environment.strip().lower()
    file_path = settings.auth_secret_file
    env_value = (os.environ.get("IACS_AUTH_SECRET_KEY") or settings.auth_secret_key or "").strip()
    env_configured = bool(env_value)
    env_default_configured = env_value in INSECURE_AUTH_SECRET_VALUES
    is_development = environment in DEVELOPMENT_ENVIRONMENTS

    if env_configured and env_default_configured and not is_development:
        raise AuthSecretError(
            "IACS_AUTH_SECRET_KEY is set to a documented placeholder value. "
            "Set a non-default secret or use IACS_AUTH_SECRET_FILE."
        )

    if env_configured and not env_default_configured:
        _AUTH_SECRET_CACHE = (
            env_value,
            AuthSecretStatus(
                source="env",
                environment=environment,
                file_path=str(file_path),
                env_configured=True,
                env_default_configured=False,
                rotation_required=False,
                ui_rotation_available=False,
                detail="Auth secret is provided by IACS_AUTH_SECRET_KEY.",
            ),
        )
        return _AUTH_SECRET_CACHE

    if file_path.exists():
        value = file_path.read_text(encoding="utf-8").strip()
        if not value:
            raise AuthSecretError(f"Auth secret file is empty: {file_path}")
        if value in INSECURE_AUTH_SECRET_VALUES and not is_development:
            raise AuthSecretError(f"Auth secret file contains a documented placeholder value: {file_path}")
        _AUTH_SECRET_CACHE = (
            value,
            AuthSecretStatus(
                source="file",
                environment=environment,
                file_path=str(file_path),
                env_configured=env_configured,
                env_default_configured=env_default_configured,
                rotation_required=value in INSECURE_AUTH_SECRET_VALUES,
                ui_rotation_available=True,
                detail="Auth secret is loaded from the configured bind-mounted file.",
            ),
        )
        return _AUTH_SECRET_CACHE

    if not is_development:
        raise AuthSecretError(
            "Auth secret file does not exist and no non-default IACS_AUTH_SECRET_KEY is configured. "
            f"Create {file_path} with a long random secret before starting this environment."
        )

    generated = generate_auth_secret_value()
    write_auth_secret_file(file_path, generated)
    _AUTH_SECRET_CACHE = (
        generated,
        AuthSecretStatus(
            source="generated",
            environment=environment,
            file_path=str(file_path),
            env_configured=env_configured,
            env_default_configured=env_default_configured,
            rotation_required=False,
            ui_rotation_available=True,
            detail="Generated a new file-backed auth secret for this development/test environment.",
        ),
    )
    return _AUTH_SECRET_CACHE
