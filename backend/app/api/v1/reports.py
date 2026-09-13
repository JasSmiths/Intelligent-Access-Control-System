import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import current_user
from app.db.session import get_db_session
from app.models import User
from app.services.reports import (
    ReportExportError,
    create_person_movement_report_export,
    load_report_export,
    report_export_payload,
    report_pdf_path,
    preview_person_movement_report,
    report_preview_context,
)

router = APIRouter()


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
async def get_report_preview_context(actor: User = Depends(current_user)) -> dict:
    return await report_preview_context()


@router.post("/person-movements/preview", response_model=ReportPreviewReady | ReportPreviewTimeChoiceRequired)
async def preview_movement_report(
    request: PersonMovementReportPreviewRequest,
    actor: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    try:
        return await preview_person_movement_report(session, **request.model_dump())
    except ReportExportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/person-movements/export")
async def export_person_movement_report(
    request: PersonMovementReportExportRequest,
    actor: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    try:
        row = await create_person_movement_report_export(
            session,
            person_id=request.person_id,
            visitor_pass_id=request.visitor_pass_id,
            period_start=request.period_start,
            period_end=request.period_end,
            include_denied=request.include_denied,
            include_snapshots=request.include_snapshots,
            include_confidence=request.include_confidence,
            actor=actor,
        )
    except ReportExportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return report_export_payload(row)


@router.get("/{report_id}")
async def get_report_export(
    report_id: str,
    actor: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    row = await load_report_export(session, report_id, actor=actor)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report was not found.")
    return report_export_payload(row)


@router.get("/{report_id}/pdf")
async def download_report_export_pdf(
    report_id: str,
    actor: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> FileResponse:
    row = await load_report_export(session, report_id, actor=actor)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report was not found.")
    try:
        path = report_pdf_path(row)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report PDF was not found.") from exc
    return FileResponse(
        path,
        filename=f"Crest-House-Access-Report-{row.report_number}.pdf",
        media_type="application/pdf",
        content_disposition_type="attachment",
        headers={"Cache-Control": "private, max-age=0"},
    )
