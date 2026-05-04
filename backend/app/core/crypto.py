import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.auth_secret import get_auth_secret


def _fernet(secret: str | None = None) -> Fernet:
    digest = hashlib.sha256((secret or get_auth_secret()).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str, *, secret: str | None = None) -> str:
    return _fernet(secret).encrypt(value.encode()).decode()


def decrypt_secret(value: str, *, secret: str | None = None) -> str:
    try:
        return _fernet(secret).decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt setting with the configured auth secret.") from exc
