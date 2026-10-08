import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import current_user
from app.db.session import AsyncSessionLocal, get_db_session
from app.models import User
from app.services.auth import authenticate_token, extract_http_token
from app.services.report_contracts import ReportAuthority, ReportExportError, ReportRequest
from app.services.report_exports import report_exports
from app.services.reports import (
    load_report_download,
    load_report_export,
    preview_person_movement_report,
    report_export_payload,
    report_pdf_path,
    report_preview_context,
)

router = APIRouter()


async def report_authority(request: Request) -> ReportAuthority:
    # Do not use the request-yield session/current_user fallback here: rendering
    # must hold neither its source nor its authentication transaction.
    token = await extract_http_token(request)
    async with AsyncSessionLocal() as session:
        actor = await authenticate_token(session, token)
        if actor is None or token is None:
            raise HTTPException(status_code=401, detail="Sign in before exporting a report.")
        return ReportAuthority(actor.id, token)


class PersonMovementReportExportRequest(BaseModel):
    person_id: uuid.UUID | None = None
    visitor_pass_id: uuid.UUID | None = None
    period_start: datetime
    period_end: datetime
    include_denied: bool = False
    include_snapshots: bool = True
    include_confidence: bool = True


class PersonMovementReportPreviewRequest(PersonMovementReportExportRequest):
    period_start_fold: Literal[0, 1] | None = None
    period_end_fold: Literal[0, 1] | None = None


class ReportTimeOccurrence(BaseModel):
    fold: Literal[0, 1]
    utc_offset_minutes: int
    label: str


class ReportTimeChoice(BaseModel):
    field: Literal["period_start", "period_end"]
    local_time: str
    choices: list[ReportTimeOccurrence]


class ReportPreviewReady(BaseModel):
    status: Literal["ready"]
    complete: Literal[True]
    report: dict[str, Any]


class ReportPreviewTimeChoiceRequired(BaseModel):
    status: Literal["time_choice_required"]
    site_timezone: str
    time_choices: list[ReportTimeChoice]


class ReportPreviewContext(BaseModel):
    site_timezone: str
    now: datetime


@router.get("/context", response_model=ReportPreviewContext)
async def get_report_preview_context(actor: Annotated[User, Depends(current_user)]) -> dict:
    return await report_preview_context()


@router.post(
    "/person-movements/preview", response_model=ReportPreviewReady | ReportPreviewTimeChoiceRequired
)
async def preview_movement_report(
    request: PersonMovementReportPreviewRequest,
    actor: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    try:
        return await preview_person_movement_report(session, **request.model_dump())
    except ReportExportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/person-movements/export")
async def export_person_movement_report(
    request: PersonMovementReportExportRequest,
    authority: Annotated[ReportAuthority, Depends(report_authority)],
    http_request: Request,
) -> dict:
    async def disconnected() -> None:
        # FastAPI has consumed the JSON body before invoking this adapter. The
        # server exposes disconnect through receive; it need not cancel our task.
        while (await http_request.receive())["type"] != "http.disconnect":
            pass
    try:
        return await report_exports.export(ReportRequest(**request.model_dump()), authority, disconnected=disconnected)
    except ReportExportError as exc:
        headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
        raise HTTPException(status_code=exc.status_code, detail=str(exc), headers=headers) from exc


@router.get("/{report_id}")
async def get_report_export(
    report_id: str,
    actor: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    row = await load_report_export(session, report_id, actor=actor)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report was not found.")
    return report_export_payload(row)


@router.get("/{report_id}/pdf")
async def download_report_export_pdf(
    report_id: str,
    actor: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FileResponse:
    row = await load_report_download(session, report_id, actor=actor)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report was not found.")
    try:
        path = report_pdf_path(row)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report PDF was not found."
        ) from exc
    return FileResponse(
        path,
        filename=f"Crest-House-Access-Report-{row.report_number}.pdf",
        media_type="application/pdf",
        content_disposition_type="attachment",
        headers={"Cache-Control": "private, max-age=0"},
    )
