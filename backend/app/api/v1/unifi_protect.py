from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.api.dependencies import admin_user, current_user
from app.ai.providers import ImageAnalysisUnsupportedError, analyze_image_with_provider
from app.models import User
from app.modules.unifi_protect.client import UnifiProtectError
from app.services.settings import get_runtime_config
from app.services.unifi_protect import get_unifi_protect_service
from app.services.unifi_protect_updates import (
    UnifiProtectUpdateError,
    get_unifi_protect_update_service,
)

router = APIRouter()


class CameraAnalyzeRequest(BaseModel):
    prompt: str = Field(default="Describe what is visible in this camera snapshot.", min_length=1, max_length=1200)
    provider: str | None = Field(default=None, max_length=40)
    width: int | None = Field(default=None, ge=160, le=4096)
    height: int | None = Field(default=None, ge=90, le=2160)
    channel: str | None = Field(default=None, max_length=40)


class ProtectUpdateAnalyzeRequest(BaseModel):
    target_version: str | None = Field(default=None, max_length=40)
    provider: str | None = Field(default=None, max_length=40)


class ProtectUpdateApplyRequest(BaseModel):
    target_version: str | None = Field(default=None, max_length=40)
    confirmed: bool = False


@router.get("/status")
async def unifi_protect_status(
    refresh: bool = False,
    _: User = Depends(current_user),
) -> dict[str, Any]:
    return await get_unifi_protect_service().status(refresh=refresh)


@router.get("/cameras")
async def unifi_protect_cameras(
    refresh: bool = False,
    _: User = Depends(current_user),
) -> dict[str, Any]:
    try:
        cameras = await get_unifi_protect_service().list_cameras(refresh=refresh)
    except UnifiProtectError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"cameras": cameras}


@router.get("/update/status")
async def unifi_protect_update_status(_: User = Depends(current_user)) -> dict[str, Any]:
    try:
        return await get_unifi_protect_update_service().status()
    except UnifiProtectUpdateError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/update/analyze")
async def unifi_protect_update_analyze(
    request: ProtectUpdateAnalyzeRequest,
    _: User = Depends(admin_user),
) -> dict[str, Any]:
    try:
        return await get_unifi_protect_update_service().analyze(
            target_version=request.target_version,
            provider=request.provider,
        )
    except UnifiProtectUpdateError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/update/apply")
async def unifi_protect_update_apply(
    request: ProtectUpdateApplyRequest,
    _: User = Depends(admin_user),
) -> dict[str, Any]:
    try:
        return await get_unifi_protect_update_service().apply(
            target_version=request.target_version,
            confirmed=request.confirmed,
        )
    except UnifiProtectUpdateError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/backups")
async def unifi_protect_create_backup(_: User = Depends(admin_user)) -> dict[str, Any]:
    try:
        return await get_unifi_protect_update_service().create_backup(reason="manual")
    except UnifiProtectUpdateError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/backups")
async def unifi_protect_backups(_: User = Depends(current_user)) -> dict[str, Any]:
    return {"backups": await get_unifi_protect_update_service().list_backups()}


@router.get("/backups/{backup_id}/download")
async def unifi_protect_download_backup(
    backup_id: str,
    _: User = Depends(current_user),
) -> FileResponse:
    try:
        path = get_unifi_protect_update_service().backup_file(backup_id)
    except UnifiProtectUpdateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        filename=f"unifi-protect-backup-{backup_id}.json",
        media_type="application/json",
    )


@router.post("/backups/{backup_id}/restore")
async def unifi_protect_restore_backup(
    backup_id: str,
    _: User = Depends(admin_user),
) -> dict[str, Any]:
    try:
        return await get_unifi_protect_update_service().restore_backup(backup_id)
    except UnifiProtectUpdateError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/backups/{backup_id}", status_code=204)
async def unifi_protect_delete_backup(
    backup_id: str,
    _: User = Depends(admin_user),
) -> None:
    try:
        await get_unifi_protect_update_service().delete_backup(backup_id)
    except UnifiProtectUpdateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/events")
async def unifi_protect_events(
    camera_id: str | None = None,
    type: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    since: datetime | None = None,
    _: User = Depends(current_user),
) -> dict[str, Any]:
    try:
        events = await get_unifi_protect_service().list_events(
            camera_id=camera_id,
            event_type=type,
            limit=limit,
            since=since,
        )
    except UnifiProtectError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"events": events}


@router.get("/cameras/{camera_id}/snapshot")
async def unifi_protect_camera_snapshot(
    camera_id: str,
    width: int | None = Query(default=None, ge=160, le=4096),
    height: int | None = Query(default=None, ge=90, le=2160),
    channel: str | None = Query(default=None, max_length=40),
    _: User = Depends(current_user),
) -> Response:
    runtime = await get_runtime_config()
    try:
        media = await get_unifi_protect_service().snapshot(
            camera_id,
            width=width or runtime.unifi_protect_snapshot_width,
            height=height or runtime.unifi_protect_snapshot_height,
            channel=channel,
        )
    except UnifiProtectError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=media.content,
        media_type=media.content_type,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/events/{event_id}/thumbnail")
async def unifi_protect_event_thumbnail(
    event_id: str,
    width: int | None = Query(default=None, ge=80, le=2048),
    height: int | None = Query(default=None, ge=80, le=2048),
    _: User = Depends(current_user),
) -> Response:
    try:
        media = await get_unifi_protect_service().event_thumbnail(event_id, width=width, height=height)
    except UnifiProtectError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content=media.content, media_type=media.content_type, headers={"Cache-Control": "private, max-age=30"})


@router.get("/events/{event_id}/video")
async def unifi_protect_event_video(event_id: str, _: User = Depends(current_user)) -> Response:
    try:
        media = await get_unifi_protect_service().event_video(event_id)
    except UnifiProtectError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content=media.content, media_type=media.content_type, headers={"Cache-Control": "private, max-age=30"})


@router.post("/cameras/{camera_id}/analyze")
async def unifi_protect_analyze_camera(
    camera_id: str,
    request: CameraAnalyzeRequest,
    _: User = Depends(current_user),
) -> dict[str, Any]:
    runtime = await get_runtime_config()
    provider = request.provider or runtime.llm_provider
    try:
        media = await get_unifi_protect_service().snapshot(
            camera_id,
            width=request.width or runtime.unifi_protect_snapshot_width,
            height=request.height or runtime.unifi_protect_snapshot_height,
            channel=request.channel,
        )
        result = await analyze_image_with_provider(
            provider,
            prompt=request.prompt,
            image_bytes=media.content,
            mime_type=media.content_type,
        )
    except ImageAnalysisUnsupportedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UnifiProtectError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "camera_id": camera_id,
        "provider": provider,
        "text": result.text,
        "snapshot_retained": False,
    }
