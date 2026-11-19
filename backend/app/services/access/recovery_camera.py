"""Compare two recorded positions of the exact plate during a resident return conflict."""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.providers import OpenAIResponsesProvider
from app.core.task_lifecycle import drain_owned
from app.modules.lpr.base import PlateRead
from app.modules.unifi_protect.client import DirectionFrameError
from app.services.access.reads import GATE_CAMERA_IDENTIFIER
from app.services.movement.sessions import normalize_registration_number
from app.services.telemetry import TELEMETRY_CATEGORY_LPR, telemetry
from app.services.unifi_protect import get_unifi_protect_service

PROMPT = (
    "Compare these two gate camera frames in chronological order. Read the exact plate "
    "independently in each frame and locate the same vehicle. Describe actual movement "
    "toward the gate camera (entry) or away toward the road (exit). Facing direction alone "
    "does not show travel. If parked, reversing ambiguously, obscured, clipped, or the "
    "identity or trajectory is uncertain, return unknown. Return JSON only with motion "
    "(entry, exit, unknown), confidence (0 to 1), and first and last objects each with "
    "plate (empty if unreadable) and box [left,top,right,bottom] normalized to 0..1."
)


class FrameObservation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)
    plate: str = Field(max_length=32)
    box: tuple[float, float, float, float]

    def area(self) -> float:
        left, top, right, bottom = self.box
        if 0 < left < right < 1 and 0 < top < bottom < 1:
            return (right - left) * (bottom - top)
        return 0


class MotionObservation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)
    motion: Literal["entry", "exit", "unknown"]
    confidence: float = Field(ge=0, le=1)
    first: FrameObservation
    last: FrameObservation


def motion_result(response: str, plate: str) -> dict[str, Any]:
    try:
        observed = MotionObservation.model_validate_json(response)
    except ValidationError:
        return {"direction": "unknown", "reason": "invalid_motion_observation"}
    expected = normalize_registration_number(plate)
    if not expected or any(normalize_registration_number(frame.plate) != expected
                           for frame in (observed.first, observed.last)):
        return {"direction": "unknown", "reason": "motion_plate_unconfirmed"}
    first, last = observed.first.area(), observed.last.area()
    # Require at least a 25% change in apparent vehicle area, consistent with
    # the observed travel on this fixed camera. Small shifts can be box jitter.
    if (min(first, last) < 0.001 or observed.motion == "unknown"
            or max(first, last) < 1.25 * min(first, last)
            or (observed.motion == "exit") != (last < first)):
        return {"direction": "unknown", "reason": "motion_unconfirmed"}
    return {"direction": observed.motion, "confidence": observed.confidence, "motion_confirmed": True,
            "reason": "same_plate_recorded_motion", "area_ratio": round(last / first, 3)}


async def recovery_camera(read: PlateRead, trace: Any) -> dict[str, Any]:
    span = trace.start_span("Recorded gate camera motion", category=TELEMETRY_CATEGORY_LPR,
                            attributes={"camera": GATE_CAMERA_IDENTIFIER}) if trace else None
    artifacts: list[dict[str, Any]] = []
    stage = "recording_provider_unavailable"
    retried = False
    try:
        protect = get_unifi_protect_service()
        try:
            first, last = await protect.direction_frames(GATE_CAMERA_IDENTIFIER, read.captured_at)
        except DirectionFrameError as exc:
            if exc.reason != "recording_frame_empty":
                raise
            # A recording at the capture boundary can become available shortly after ingestion.
            # The caller owns the original five-second deadline for this entire task.
            retried = True
            await asyncio.sleep(0.4)
            first, last = await protect.direction_frames(GATE_CAMERA_IDENTIFIER, read.captured_at)
        if first.content == last.content:
            result: dict[str, Any] = {"direction": "unknown", "reason": "identical_recorded_frames"}
        else:
            stage = "recording_artifact_failed"
            if span:
                for frame in (first, last):
                    artifacts.append(await drain_owned(telemetry.store_artifact(
                        frame.content, content_type=frame.content_type, kind="camera_snapshot",
                        trace_id=trace.trace_id, span_id=span.span_id,
                        metadata={"camera": GATE_CAMERA_IDENTIFIER, "frame_index": len(artifacts)},
                    )))
            stage = "recording_analysis_failed"
            analysis = await OpenAIResponsesProvider().analyze_images(
                PROMPT, ((first.content, first.content_type), (last.content, last.content_type)))
            result = motion_result(analysis.text, read.registration_number)
    except asyncio.CancelledError:
        if span:
            span.finish(status="cancelled")
        raise
    except DirectionFrameError as exc:
        result = {"direction": "unknown", "reason": exc.reason, "frame": exc.frame}
    except Exception:  # noqa: BLE001 - retain the failed stage without exposing provider data.
        result = {"direction": "unknown", "reason": stage}
    result["retried"] = retried
    if span:
        span.finish(output_payload={**result, "artifacts": artifacts})
    return result
