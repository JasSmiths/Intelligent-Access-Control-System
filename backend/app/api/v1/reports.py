import uuid
from datetime import datetime

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
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    row = await load_report_export(session, report_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report was not found.")
    return report_export_payload(row)


@router.get("/{report_id}/pdf")
async def download_report_export_pdf(
    report_id: str,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> FileResponse:
    row = await load_report_export(session, report_id)
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
