import hmac
from ipaddress import ip_address, ip_network
from typing import Any

from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.core.logging import get_logger


LPR_WEBHOOK_TOKEN_HEADER = "X-IACS-LPR-Token"

logger = get_logger(__name__)


def verify_lpr_webhook_request(request: Request, *, runtime: Any) -> None:
    """Fail closed unless the request has the configured shared secret and source IP."""

    configured_token = str(getattr(runtime, "lpr_webhook_token", "") or "")
    provided_token = request.headers.get(LPR_WEBHOOK_TOKEN_HEADER)
    source_ip = client_ip_from_request(
        request,
        trusted_proxy_ips=(
            getattr(runtime, "lpr_webhook_trusted_proxy_ips", None) or settings.trusted_proxy_ips
        ),
    )
    allowed_networks, invalid_allowlist_entries = _allowed_source_networks(
        getattr(runtime, "lpr_webhook_allowed_source_ips", [])
    )

    if not configured_token:
        _log_rejection(
            request,
            reason="token_unconfigured",
            source_ip=source_ip,
            token_present=bool(provided_token),
            token_configured=False,
            valid_allowlist_entries=len(allowed_networks),
            invalid_allowlist_entries=invalid_allowlist_entries,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ubiquiti LPR webhook security is not configured.",
        )

    token_matches = bool(provided_token and _token_matches(provided_token, configured_token))
    if not token_matches:
        _log_rejection(
            request,
            reason="token_mismatch" if provided_token else "token_missing",
            source_ip=source_ip,
            token_present=bool(provided_token),
            token_configured=True,
            valid_allowlist_entries=len(allowed_networks),
            invalid_allowlist_entries=invalid_allowlist_entries,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Ubiquiti LPR webhook credentials.",
        )

    if not allowed_networks:
        _log_rejection(
            request,
            reason="source_allowlist_unconfigured",
            source_ip=source_ip,
            token_present=True,
            token_configured=True,
            valid_allowlist_entries=0,
            invalid_allowlist_entries=invalid_allowlist_entries,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ubiquiti LPR webhook source allowlist is not configured.",
        )

    if not _source_ip_allowed(source_ip, allowed_networks):
        _log_rejection(
            request,
            reason="source_ip_not_allowed",
            source_ip=source_ip,
            token_present=True,
            token_configured=True,
            valid_allowlist_entries=len(allowed_networks),
            invalid_allowlist_entries=invalid_allowlist_entries,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ubiquiti LPR webhook source is not allowed.",
        )


def client_ip_from_request(request: Request, *, trusted_proxy_ips: Any = None) -> str:
    """Return the verified client IP, trusting forwarded headers only from configured proxies."""

    direct_ip = _direct_client_ip(request)
    proxy_networks, _invalid_entries = _allowed_source_networks(trusted_proxy_ips or [])
    if not direct_ip or not proxy_networks or not _source_ip_allowed(direct_ip, proxy_networks):
        return direct_ip

    forwarded_ip = _first_forwarded_ip(request.headers.get("x-forwarded-for"))
    if forwarded_ip:
        return forwarded_ip
    real_ip = _valid_ip_text(request.headers.get("x-real-ip"))
    return real_ip or direct_ip


def _token_matches(provided_token: str, configured_token: str) -> bool:
    try:
        return hmac.compare_digest(provided_token, configured_token)
    except TypeError:
        return False


def _allowed_source_networks(values: Any) -> tuple[list[Any], int]:
    if isinstance(values, str):
        raw_values = [part.strip() for part in values.replace(",", "\n").splitlines()]
    elif isinstance(values, list | tuple | set):
        raw_values = [str(value).strip() for value in values]
    else:
        raw_values = []

    networks = []
    invalid_entries = 0
    for raw_value in raw_values:
        if not raw_value:
            continue
        try:
            networks.append(ip_network(raw_value, strict=False))
        except ValueError:
            invalid_entries += 1
    return networks, invalid_entries


def _source_ip_allowed(source_ip: str, networks: list[Any]) -> bool:
    try:
        address = ip_address(source_ip)
    except ValueError:
        return False
    return any(address in network for network in networks)


def _direct_client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _first_forwarded_ip(value: str | None) -> str:
    if not value:
        return ""
    for part in value.split(","):
        candidate = _valid_ip_text(part)
        if candidate:
            return candidate
    return ""


def _valid_ip_text(value: str | None) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    try:
        return str(ip_address(candidate))
    except ValueError:
        return ""


def _log_rejection(
    request: Request,
    *,
    reason: str,
    source_ip: str,
    token_present: bool,
    token_configured: bool,
    valid_allowlist_entries: int,
    invalid_allowlist_entries: int,
) -> None:
    logger.warning(
        "ubiquiti_lpr_webhook_rejected",
        extra={
            "method": request.method,
            "path": str(request.scope.get("path") or request.url.path),
            "authorization_status": "unauthorized",
            "authorization_reason": reason,
            "source_ip": source_ip or "unknown",
            "token_present": token_present,
            "token_configured": token_configured,
            "valid_allowlist_entries": valid_allowlist_entries,
            "invalid_allowlist_entries": invalid_allowlist_entries,
        },
    )
