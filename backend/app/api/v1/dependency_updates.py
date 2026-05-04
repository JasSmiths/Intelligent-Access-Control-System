import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.api.dependencies import admin_user, current_user
from app.db.session import AsyncSessionLocal
from app.models import User
from app.services.auth import authenticate_websocket
from app.services.dependency_updates import DependencyUpdateError, get_dependency_update_service

router = APIRouter()


class DependencyAnalyzeRequest(BaseModel):
    target_version: str | None = Field(default=None, max_length=120)
    provider: str | None = Field(default=None, max_length=40)


class DependencyApplyRequest(BaseModel):
    target_version: str | None = Field(default=None, max_length=120)
    confirmed: bool = False


class DependencyCheckAllRequest(BaseModel):
    direct_only: bool = False


class DependencyRestoreRequest(BaseModel):
    confirmed: bool = False


class DependencyStorageConfigRequest(BaseModel):
    mode: str = Field(pattern="^(local|nfs|samba)$")
    mount_source: str = Field(default="", max_length=600)
    mount_options: str | None = Field(default=None, max_length=1200)
    retention_days: str | None = Field(default="", max_length=20)
    min_free_bytes: int = Field(default=1073741824, ge=0)


@router.get("/packages")
async def dependency_packages(
    update_only: bool = False,
    _: User = Depends(current_user),
) -> dict[str, Any]:
    return {"packages": await get_dependency_update_service().list_packages(update_only=update_only)}


@router.post("/sync")
async def sync_dependency_enrollment(user: User = Depends(admin_user)) -> dict[str, Any]:
    try:
        return await get_dependency_update_service().sync_enrollment(reason="manual", user=user)
    except DependencyUpdateError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/check")
async def check_all_dependency_updates(
    request: DependencyCheckAllRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    try:
        return await get_dependency_update_service().check_all_packages(
            direct_only=request.direct_only,
            user=user,
        )
    except DependencyUpdateError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/packages/{dependency_id}/check")
async def check_dependency_update(
    dependency_id: uuid.UUID,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    try:
        return await get_dependency_update_service().check_package(dependency_id, user=user)
    except DependencyUpdateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/packages/{dependency_id}/analyze")
async def analyze_dependency_update(
    dependency_id: uuid.UUID,
    request: DependencyAnalyzeRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    try:
        return await get_dependency_update_service().analyze_package(
            dependency_id,
            target_version=request.target_version,
            provider=request.provider,
            user=user,
        )
    except DependencyUpdateError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/packages/{dependency_id}/apply")
async def apply_dependency_update(
    dependency_id: uuid.UUID,
    request: DependencyApplyRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    try:
        return await get_dependency_update_service().start_apply_job(
            dependency_id,
            target_version=request.target_version,
            confirmed=request.confirmed,
            user=user,
        )
    except DependencyUpdateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/packages/{dependency_id}/backups")
async def dependency_backups(
    dependency_id: uuid.UUID,
    _: User = Depends(current_user),
) -> dict[str, Any]:
    return {"backups": await get_dependency_update_service().list_backups(dependency_id)}


@router.get("/backups")
async def all_dependency_backups(_: User = Depends(current_user)) -> dict[str, Any]:
    return {"backups": await get_dependency_update_service().list_backups()}


@router.post("/backups/{backup_id}/restore")
async def restore_dependency_backup(
    backup_id: uuid.UUID,
    request: DependencyRestoreRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    try:
        return await get_dependency_update_service().start_restore_job(
            backup_id,
            confirmed=request.confirmed,
            user=user,
        )
    except DependencyUpdateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs/{job_id}")
async def dependency_update_job(
    job_id: uuid.UUID,
    _: User = Depends(current_user),
) -> dict[str, Any]:
    try:
        return await get_dependency_update_service().job_status(job_id)
    except DependencyUpdateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/storage/status")
async def dependency_update_storage_status(_: User = Depends(current_user)) -> dict[str, Any]:
    return await get_dependency_update_service().storage_status()


@router.post("/storage/validate")
async def validate_dependency_update_storage(_: User = Depends(admin_user)) -> dict[str, Any]:
    return await get_dependency_update_service().validate_storage()


@router.post("/storage/config")
async def configure_dependency_update_storage(
    request: DependencyStorageConfigRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    try:
        return await get_dependency_update_service().save_storage_config(request.model_dump(exclude_unset=True), user=user)
    except DependencyUpdateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.websocket("/jobs/{job_id}/ws")
async def dependency_update_job_websocket(websocket: WebSocket, job_id: str) -> None:
    async with AsyncSessionLocal() as session:
        user = await authenticate_websocket(session, websocket)
    if not user:
        await websocket.close(code=1008, reason="Authentication required")
        return

    await websocket.accept()
    queue = await get_dependency_update_service().subscribe_job(job_id)
    try:
        await websocket.send_json({"type": "connection.ready", "job_id": job_id})
        while True:
            event = await queue.get()
            await websocket.send_json(event)
            if event.get("type") in {"completed", "failed"}:
                return
    except WebSocketDisconnect:
        return
    finally:
        get_dependency_update_service().unsubscribe_job(job_id, queue)
