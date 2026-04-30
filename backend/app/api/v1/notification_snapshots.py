from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.services.notification_snapshots import (
    notification_snapshot_content_type,
    notification_snapshot_path,
)

router = APIRouter()


@router.get("/{filename}", include_in_schema=False)
async def notification_snapshot(filename: str) -> FileResponse:
    try:
        path = notification_snapshot_path(filename)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification snapshot was not found.",
        ) from exc
    return FileResponse(
        path,
        media_type=notification_snapshot_content_type(path),
        filename=path.name,
    )
