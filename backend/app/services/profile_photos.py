from __future__ import annotations

import base64
import re
from io import BytesIO
from urllib.parse import unquote_to_bytes

from PIL import Image, ImageOps, UnidentifiedImageError

DATA_URL_RE = re.compile(r"^data:(?P<content_type>[^;,]+)(?P<base64>;base64)?,(?P<data>.*)$", re.DOTALL)
HEIF_IMAGE_CONTENT_TYPES = {
    "image/heic",
    "image/heif",
    "image/heic-sequence",
    "image/heif-sequence",
}
PROFILE_PHOTO_MAX_EDGE_PX = 640
PHOTO_RESPONSE_MAX_EDGE_PX = 640
JPEG_QUALITY = 82


class ProfilePhotoError(ValueError):
    """Raised when a profile photo data URL cannot be normalized."""


def normalize_profile_photo_data_url(value: str | None) -> str | None:
    if not value:
        return None

    match = DATA_URL_RE.fullmatch(value)
    if not match:
        return value
    if not match.group("base64"):
        raise ProfilePhotoError("Profile photo must be a base64-encoded raster image.")

    content_type = match.group("content_type").lower()

    try:
        raw = decode_data_url(match)
        output_content_type, output_bytes = compact_image_bytes(
            raw,
            content_type,
            max_edge_px=PROFILE_PHOTO_MAX_EDGE_PX,
        )
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ProfilePhotoError("Profile photo could not be processed.") from exc

    return f"data:{output_content_type};base64,{base64.b64encode(output_bytes).decode('ascii')}"


def stored_image_url(value: str | None, path: str, version: object | None = None) -> str | None:
    if not value:
        return None
    if not value.startswith("data:"):
        return value
    timestamp = int(version.timestamp()) if hasattr(version, "timestamp") else None
    return f"{path}?v={timestamp}" if timestamp is not None else path


def photo_data_url_to_media(value: str, *, max_edge_px: int = PHOTO_RESPONSE_MAX_EDGE_PX) -> tuple[str, bytes]:
    match = DATA_URL_RE.fullmatch(value)
    if not match:
        raise ProfilePhotoError("Profile photo could not be processed.")
    content_type = match.group("content_type").lower()
    raw = decode_data_url(match)
    try:
        return compact_image_bytes(raw, content_type, max_edge_px=max_edge_px)
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ProfilePhotoError("Profile photo could not be processed.") from exc


def decode_data_url(match: re.Match[str]) -> bytes:
    data = match.group("data")
    if match.group("base64"):
        return base64.b64decode(data, validate=False)
    return unquote_to_bytes(data)


def compact_image_bytes(raw: bytes, content_type: str, *, max_edge_px: int) -> tuple[str, bytes]:
    if content_type in HEIF_IMAGE_CONTENT_TYPES:
        import pillow_heif

        pillow_heif.register_heif_opener()

    with Image.open(BytesIO(raw)) as image:
        converted = ImageOps.exif_transpose(image)
        converted.thumbnail((max_edge_px, max_edge_px), Image.Resampling.LANCZOS)
        has_alpha = converted.mode in {"LA", "RGBA", "PA"} or (
            converted.mode == "P" and "transparency" in converted.info
        )
        output = BytesIO()
        if has_alpha:
            converted = converted.convert("RGBA")
            converted.save(output, format="PNG", optimize=True)
            return "image/png", output.getvalue()
        converted = converted.convert("RGB")
        converted.save(output, format="JPEG", optimize=True, quality=JPEG_QUALITY)
        return "image/jpeg", output.getvalue()
