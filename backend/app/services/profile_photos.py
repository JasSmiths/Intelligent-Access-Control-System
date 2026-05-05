from __future__ import annotations

import base64
import re
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

DATA_URL_RE = re.compile(r"^data:(?P<content_type>[^;,]+)(?P<base64>;base64)?,(?P<data>.*)$", re.DOTALL)
HEIF_IMAGE_CONTENT_TYPES = {
    "image/heic",
    "image/heif",
    "image/heic-sequence",
    "image/heif-sequence",
}
PROFILE_PHOTO_MAX_EDGE_PX = 640


class ProfilePhotoError(ValueError):
    """Raised when a profile photo data URL cannot be normalized."""


def normalize_profile_photo_data_url(value: str | None) -> str | None:
    if not value:
        return None

    match = DATA_URL_RE.fullmatch(value)
    if not match or not match.group("base64"):
        return value

    content_type = match.group("content_type").lower()
    if content_type not in HEIF_IMAGE_CONTENT_TYPES:
        return value

    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        raw = base64.b64decode(match.group("data"), validate=False)
        with Image.open(BytesIO(raw)) as image:
            converted = ImageOps.exif_transpose(image)
            converted.thumbnail(
                (PROFILE_PHOTO_MAX_EDGE_PX, PROFILE_PHOTO_MAX_EDGE_PX),
                Image.Resampling.LANCZOS,
            )
            if converted.mode not in {"RGB", "RGBA"}:
                converted = converted.convert("RGBA")
            output = BytesIO()
            converted.save(output, format="PNG", optimize=True)
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ProfilePhotoError("Profile photo could not be processed.") from exc

    return f"data:image/png;base64,{base64.b64encode(output.getvalue()).decode('ascii')}"
