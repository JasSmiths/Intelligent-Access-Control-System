"""Pure URL normalization and sanitized labels; no transports or settings reads."""
from urllib.parse import urlparse


def split_apprise_urls(configured: str) -> list[str]:
    return [url.strip() for url in configured.replace("\n", ",").split(",") if url.strip()]


def normalize_apprise_url(url: str) -> str:
    """Accept common Pushover spellings and convert them to Apprise's schema."""
    parsed = urlparse(url)
    if parsed.scheme not in {"pushover", "pover"}:
        return url

    scheme = "pover"
    if "@" in parsed.netloc or not parsed.path.strip("/"):
        return f"{scheme}://{url.split('://', 1)[1]}"

    user_key = parsed.netloc
    app_token = parsed.path.strip("/").split("/", 1)[0]
    suffix = ""
    if "?" in url:
        suffix = f"?{url.split('?', 1)[1]}"
    return f"{scheme}://{user_key}@{app_token}{suffix}"


def summarize_apprise_url(index: int, url: str) -> dict[str, str | int]:
    parsed = urlparse(url)
    label = _apprise_service_label(parsed.scheme)
    credentials = f"{label} destination {index + 1}"
    return {
        "id": f"apprise:{index}",
        "index": index,
        "type": label,
        "scheme": parsed.scheme or "unknown",
        "preview": credentials,
    }


def mask_apprise_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme:
        return "***"
    return f"{parsed.scheme}://***"


def _apprise_service_label(scheme: str) -> str:
    labels = {
        "pover": "Pushover",
        "mailto": "Email",
        "slack": "Slack",
        "tgram": "Telegram",
        "telegram": "Telegram",
    }
    return labels.get(scheme, scheme.replace("_", " ").title() if scheme else "Unknown")
