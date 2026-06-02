"""Compliance, camera, and file Alfred tool handlers."""
# ruff: noqa: F403, F405

from __future__ import annotations

from typing import Any
from app.ai.tool_groups.access_diagnostics_handlers import calculate_visit_duration, query_access_events
from app.ai.tool_groups.general_handlers import query_presence

from app.ai.tool_groups._shared import *


def _filename_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "file"


def _simple_pdf(lines: list[str]) -> bytes:
    escaped_lines = [_pdf_escape(line) for line in lines[:46]]
    content_lines = [
        "BT",
        "/F1 16 Tf",
        "72 760 Td",
        "20 TL",
    ]
    for index, line in enumerate(escaped_lines):
        if index:
            content_lines.append("T*")
        content_lines.append(f"({line}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = io.BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(output.tell())
        output.write(f"{index} 0 obj\n".encode("ascii"))
        output.write(obj)
        output.write(b"\nendobj\n")
    xref_offset = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return output.getvalue()


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


async def lookup_dvla_vehicle(arguments: dict[str, Any]) -> dict[str, Any]:
    registration_number = normalize_registration_number(str(arguments.get("registration_number") or ""))
    if not registration_number:
        return {"error": "registration_number is required."}
    try:
        vehicle = await lookup_vehicle_registration(registration_number)
    except DvlaVehicleEnquiryError as exc:
        return {
            "registration_number": registration_number,
            "error": str(exc),
        }
    return {
        "registration_number": registration_number,
        "vehicle": vehicle,
        "display_vehicle": display_vehicle_record(vehicle, registration_number),
        "normalized_vehicle": normalize_vehicle_enquiry_response(vehicle, registration_number).as_payload(),
    }


async def analyze_camera_snapshot(arguments: dict[str, Any]) -> dict[str, Any]:
    camera_identifier = str(arguments.get("camera_id") or arguments.get("camera_name") or "").strip()
    if not camera_identifier:
        return {"error": "camera_id or camera_name is required."}
    prompt = str(arguments.get("prompt") or "Describe what is visible in this camera snapshot.").strip()
    runtime = await get_runtime_config()
    provider = str(arguments.get("provider") or runtime.llm_provider)

    try:
        media = await get_unifi_protect_service().snapshot(
            camera_identifier,
            width=runtime.unifi_protect_snapshot_width,
            height=runtime.unifi_protect_snapshot_height,
        )
        result = await analyze_image_with_provider(
            provider,
            prompt=prompt,
            image_bytes=media.content,
            mime_type=media.content_type,
        )
    except (UnifiProtectError, ImageAnalysisUnsupportedError, Exception) as exc:
        return {"camera": camera_identifier, "provider": provider, "error": str(exc)}

    return {
        "camera": camera_identifier,
        "provider": provider,
        "analysis": result.text,
        "snapshot_retained": False,
    }


async def read_chat_attachment(arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_chat_tool_context()
    user_id = str(context.get("user_id") or "")
    if not user_id:
        return {"error": "Attachment access requires an authenticated chat user."}

    file_id = str(arguments.get("file_id") or "").strip()
    if not file_id:
        return {"error": "file_id is required."}

    prompt = str(arguments.get("prompt") or "Summarize this attachment.").strip()
    runtime = await get_runtime_config()
    provider = str(arguments.get("provider") or runtime.llm_provider)

    try:
        attachment = chat_attachment_store.get(file_id)
        chat_attachment_store.require_access(attachment, user_id)
    except ChatAttachmentError as exc:
        return {"file_id": file_id, "error": str(exc)}

    if attachment.kind == "image":
        try:
            _, image_bytes = await asyncio.to_thread(
                chat_attachment_store.read_bytes,
                file_id,
                owner_user_id=user_id,
            )
            result = await analyze_image_with_provider(
                provider,
                prompt=prompt,
                image_bytes=image_bytes,
                mime_type=attachment.content_type,
            )
        except (ChatAttachmentError, ImageAnalysisUnsupportedError, Exception) as exc:
            return {
                "file_id": file_id,
                "filename": attachment.filename,
                "kind": attachment.kind,
                "provider": provider,
                "error": str(exc),
            }
        return {
            "file_id": file_id,
            "filename": attachment.filename,
            "kind": attachment.kind,
            "provider": provider,
            "analysis": result.text,
        }

    try:
        _, text = await asyncio.to_thread(
            chat_attachment_store.read_text,
            file_id,
            owner_user_id=user_id,
        )
    except ChatAttachmentError as exc:
        return {
            "file_id": file_id,
            "filename": attachment.filename,
            "kind": attachment.kind,
            "error": str(exc),
        }

    return {
        "file_id": file_id,
        "filename": attachment.filename,
        "kind": attachment.kind,
        "content_type": attachment.content_type,
        "text": text,
        "characters": len(text),
    }


async def export_presence_report_csv(arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_chat_tool_context()
    user_id = str(context.get("user_id") or "")
    session_id = str(context.get("session_id") or "")
    if not user_id:
        return {"generated": False, "error": "File generation requires an authenticated chat user."}

    runtime = await get_runtime_config()
    day = str(arguments.get("day") or "today")
    presence = await query_presence({"person": arguments.get("person")})
    events = await query_access_events(
        {
            "person": arguments.get("person"),
            "group": arguments.get("group"),
            "day": day,
            "limit": 100,
        }
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["section", "person", "state", "registration_number", "direction", "decision", "occurred_at", "notes"])
    for row in presence.get("presence", []):
        writer.writerow(["presence", row.get("person"), row.get("state"), "", "", "", row.get("last_changed_at"), ""])
    for event in events.get("events", []):
        writer.writerow(
            [
                "access_event",
                event.get("person") or "",
                "",
                event.get("registration_number") or "",
                event.get("direction") or "",
                event.get("decision") or "",
                event.get("occurred_at") or "",
                f"{event.get('anomaly_count', 0)} anomalies",
            ]
        )

    filename = f"presence-report-{day}-{_agent_now(runtime.site_timezone).strftime('%Y%m%d-%H%M%S')}.csv"
    try:
        attachment = chat_attachment_store.save_generated(
            filename=filename,
            content=output.getvalue().encode("utf-8"),
            content_type="text/csv",
            owner_user_id=user_id,
            source="generated",
            session_id=session_id or None,
        )
    except ChatAttachmentError as exc:
        return {"generated": False, "error": str(exc)}

    return {
        "generated": True,
        "period": day,
        "rows": len(presence.get("presence", [])) + len(events.get("events", [])),
        "attachment": attachment.to_public_dict(),
    }


async def generate_contractor_invoice_pdf(arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_chat_tool_context()
    user_id = str(context.get("user_id") or "")
    session_id = str(context.get("session_id") or "")
    if not user_id:
        return {"generated": False, "error": "File generation requires an authenticated chat user."}

    runtime = await get_runtime_config()
    contractor_name = str(arguments.get("contractor_name") or "Contractor").strip()
    day = str(arguments.get("day") or "today")
    hourly_rate = float(arguments.get("hourly_rate") or 0)
    currency = str(arguments.get("currency") or "GBP").strip().upper()
    duration = await calculate_visit_duration({"group": "contractor", "day": day})
    hours = round(int(duration.get("duration_seconds") or 0) / 3600, 2)
    amount = round(hours * hourly_rate, 2)
    issued_at = _agent_now(runtime.site_timezone)

    lines = [
        "Intelligent Access Control System",
        "Contractor Visit Invoice",
        "",
        f"Contractor: {contractor_name}",
        f"Period: {day}",
        f"Issued: {_agent_datetime_display(issued_at, runtime.site_timezone)}",
        f"Matched events: {duration.get('matched_events', 0)}",
        f"Visit duration: {duration.get('duration_human', '0m')} ({hours:.2f} hours)",
        f"Hourly rate: {currency} {hourly_rate:.2f}",
        f"Total: {currency} {amount:.2f}",
        "",
        "Intervals:",
    ]
    intervals = duration.get("intervals") or []
    if intervals:
        lines.extend(f"- {item.get('entry')} to {item.get('exit')}" for item in intervals[:12])
    else:
        lines.append("- No matched contractor intervals found.")

    filename = f"contractor-invoice-{_filename_slug(contractor_name)}-{issued_at.strftime('%Y%m%d-%H%M%S')}.pdf"
    try:
        attachment = chat_attachment_store.save_generated(
            filename=filename,
            content=_simple_pdf(lines),
            content_type="application/pdf",
            owner_user_id=user_id,
            source="generated",
            session_id=session_id or None,
        )
    except ChatAttachmentError as exc:
        return {"generated": False, "error": str(exc)}

    return {
        "generated": True,
        "contractor_name": contractor_name,
        "period": day,
        "duration_human": duration.get("duration_human"),
        "total": amount,
        "currency": currency,
        "attachment": attachment.to_public_dict(),
    }


async def get_camera_snapshot(arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_chat_tool_context()
    user_id = str(context.get("user_id") or "")
    session_id = str(context.get("session_id") or "")
    if not user_id:
        return {"fetched": False, "error": "Camera media requires an authenticated chat user."}

    camera_identifier = str(arguments.get("camera_id") or arguments.get("camera_name") or "").strip()
    if not camera_identifier:
        return {"fetched": False, "error": "camera_id or camera_name is required."}
    runtime = await get_runtime_config()
    try:
        media = await get_unifi_protect_service().snapshot(
            camera_identifier,
            width=runtime.unifi_protect_snapshot_width,
            height=runtime.unifi_protect_snapshot_height,
        )
        attachment = chat_attachment_store.save_generated(
            filename=f"camera-snapshot-{_filename_slug(camera_identifier)}-{_agent_now(runtime.site_timezone).strftime('%Y%m%d-%H%M%S')}.jpg",
            content=media.content,
            content_type=media.content_type,
            owner_user_id=user_id,
            source="system_media",
            session_id=session_id or None,
        )
    except (UnifiProtectError, ChatAttachmentError, Exception) as exc:
        return {"fetched": False, "camera": camera_identifier, "error": str(exc)}

    return {
        "fetched": True,
        "camera": camera_identifier,
        "attachment": attachment.to_public_dict(),
    }
