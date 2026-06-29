import asyncio
from typing import Literal

from fastapi import HTTPException, Response, status

from app.services.profile_photos import ProfilePhotoError, photo_data_url_to_media

PhotoVariant = Literal["thumb", "full"]
PHOTO_VARIANT_MAX_EDGE_PX: dict[PhotoVariant, int] = {
    "thumb": 192,
    "full": 640,
}


async def data_url_media_response(
    value: str | None,
    *,
    detail: str = "Photo not found",
    variant: PhotoVariant = "full",
) -> Response:
    if not value:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    try:
        content_type, content = await asyncio.to_thread(
            photo_data_url_to_media,
            value,
            max_edge_px=PHOTO_VARIANT_MAX_EDGE_PX[variant],
        )
    except ProfilePhotoError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from exc
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )
