from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.ai.providers import ImageAnalysisUnsupportedError, analyze_image_with_provider
from app.api.dependencies import current_user
from app.models import User
from app.modules.unifi_protect.client import UnifiProtectError
from app.services.settings import get_runtime_config
from app.services.unifi_protect import get_unifi_protect_service

router = APIRouter()


class CameraAnalyzeRequest(BaseModel):
    prompt: str = Field(
        default="Describe what is visible in this camera snapshot.", min_length=1, max_length=1200
    )
    provider: str | None = Field(default=None, max_length=40)
    width: int | None = Field(default=None, ge=160, le=4096)
    height: int | None = Field(default=None, ge=90, le=2160)
    channel: str | None = Field(default=None, max_length=40)


@router.get("/status")
async def unifi_protect_status(
    _: Annotated[User, Depends(current_user)], refresh: bool = False
) -> dict[str, Any]:
    return await get_unifi_protect_service().status(refresh=refresh)


@router.get("/cameras")
async def unifi_protect_cameras(
    _: Annotated[User, Depends(current_user)], refresh: bool = False
) -> dict[str, Any]:
    try:
        cameras = await get_unifi_protect_service().list_cameras(refresh=refresh)
    except UnifiProtectError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"cameras": cameras}


@router.get("/events")
async def unifi_protect_events(
    _: Annotated[User, Depends(current_user)],
    camera_id: str | None = None,
    type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    since: datetime | None = None,
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
    _: Annotated[User, Depends(current_user)],
    width: Annotated[int | None, Query(ge=160, le=4096)] = None,
    height: Annotated[int | None, Query(ge=90, le=2160)] = None,
    channel: Annotated[str | None, Query(max_length=40)] = None,
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
    _: Annotated[User, Depends(current_user)],
    width: Annotated[int | None, Query(ge=80, le=2048)] = None,
    height: Annotated[int | None, Query(ge=80, le=2048)] = None,
) -> Response:
    try:
        media = await get_unifi_protect_service().event_thumbnail(
            event_id, width=width, height=height
        )
    except UnifiProtectError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=media.content,
        media_type=media.content_type,
        headers={"Cache-Control": "private, max-age=30"},
    )


@router.get("/events/{event_id}/video")
async def unifi_protect_event_video(
    event_id: str, _: Annotated[User, Depends(current_user)]
) -> Response:
    try:
        media = await get_unifi_protect_service().event_video(event_id)
    except UnifiProtectError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=media.content,
        media_type=media.content_type,
        headers={"Cache-Control": "private, max-age=30"},
    )


@router.post("/cameras/{camera_id}/analyze")
async def unifi_protect_analyze_camera(
    camera_id: str, request: CameraAnalyzeRequest, _: Annotated[User, Depends(current_user)]
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
