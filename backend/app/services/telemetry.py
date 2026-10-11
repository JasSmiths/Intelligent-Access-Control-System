import asyncio
import contextvars
import json
import re
import secrets
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import parse_qsl, urlencode

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.db.session import AsyncSessionLocal
from app.models import AuditLog, TelemetrySpan, TelemetryTrace, User
from app.services.event_bus import event_bus

logger = get_logger(__name__)

TELEMETRY_CATEGORY_WEBHOOKS_API = "webhooks_api"
TELEMETRY_CATEGORY_ACCESS = "access_presence"
TELEMETRY_CATEGORY_LPR = "lpr_telemetry"
TELEMETRY_CATEGORY_CRUD = "entity_management"
TELEMETRY_CATEGORY_INTEGRATIONS = "integrations"
TELEMETRY_CATEGORY_ALFRED = "alfred_ai"
TELEMETRY_CATEGORY_MAINTENANCE = "maintenance_mode"
TELEMETRY_CATEGORY_GATE_MALFUNCTION = "gate_malfunction"
TELEMETRY_CATEGORY_AUTOMATION = "automation_engine"
TELEMETRY_CATEGORY_DEPENDENCY_UPDATES = "dependency_updates"

TELEMETRY_CATEGORIES = [
    {
        "id": TELEMETRY_CATEGORY_LPR,
        "label": "LPR Telemetry",
        "description": "Plate detections, debounce windows, access decisions, and gate automation.",
    },
    {
        "id": TELEMETRY_CATEGORY_ALFRED,
        "label": "Historical Alfred Audit",
        "description": "Retained assistant provider usage, tools, actions, and outcomes from before retirement.",
    },
    {
        "id": TELEMETRY_CATEGORY_CRUD,
        "label": "System CRUD",
        "description": "People, vehicles, schedules, notification workflows, users, and settings.",
    },
    {
        "id": TELEMETRY_CATEGORY_WEBHOOKS_API,
        "label": "Webhooks & API",
        "description": "Inbound HTTP/API requests, webhook payload shapes, and response timings.",
    },
    {
        "id": TELEMETRY_CATEGORY_INTEGRATIONS,
        "label": "Integrations",
        "description": "Home Assistant, Apprise, DVLA, UniFi Protect, and provider connectivity.",
    },
    {
        "id": TELEMETRY_CATEGORY_DEPENDENCY_UPDATES,
        "label": "Retired updater history",
        "description": "Retained audit records from the removed dependency and UniFi package updaters.",
    },
    {
        "id": TELEMETRY_CATEGORY_GATE_MALFUNCTION,
        "label": "Gate Events",
        "description": "Gate malfunction declarations, recovery attempts, notifications, and resolution.",
    },
    {
        "id": TELEMETRY_CATEGORY_AUTOMATION,
        "label": "Automations",
        "description": "Trigger, condition, and action execution for automation workflows.",
    },
    {
        "id": TELEMETRY_CATEGORY_MAINTENANCE,
        "label": "Maintenance Mode",
        "description": "Global automation kill-switch changes, duration, actor, and HA sync outcome.",
    },
    {
        "id": TELEMETRY_CATEGORY_ACCESS,
        "label": "Access & Presence",
        "description": "Access decisions, presence transitions, anomalies, and schedule rules.",
    },
]

CURRENT_TRACE_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "iacs_current_trace_id",
    default=None,
)
CURRENT_PARENT_SPAN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "iacs_current_parent_span_id",
    default=None,
)
CURRENT_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "iacs_current_request_id",
    default=None,
)

SECRET_KEY_PATTERNS = (
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "cookie",
    "csrf",
    "jwt",
    "mount_options",
    "password",
    "secret",
    "session",
    "set-cookie",
    "token",
    "webhook_key",
    "x-api-key",
)
LARGE_MEDIA_KEY_PATTERNS = (
    "image",
    "photo",
    "profile_photo_data_url",
    "snapshot",
    "thumbnail",
    "video",
    "vehicle_photo_data_url",
)
MAX_STRING_LENGTH = 2000
MAX_LIST_ITEMS = 40
MAX_DICT_KEYS = 80
MAX_DEPTH = 6
CHAT_FILE_URL_RE = re.compile(r"/api/v1/ai/chat/files/[A-Za-z0-9_-]+")


def trace_id() -> str:
    return secrets.token_hex(16)


def span_id() -> str:
    return secrets.token_hex(8)


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def telemetry_request_id() -> str:
    return f"req_{secrets.token_hex(10)}"


def current_request_id() -> str | None:
    return CURRENT_REQUEST_ID.get()


def current_trace_id() -> str | None:
    return CURRENT_TRACE_ID.get()


def actor_from_user(user: User | None) -> str:
    if not user:
        return "System"
    username = getattr(user, "username", None)
    label = getattr(user, "full_name", None) or username or getattr(user, "id", None) or "User"
    return f"{label} ({username})" if username else str(label)


def audit_diff(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    old_clean = sanitize_payload(old or {})
    new_clean = sanitize_payload(new or {})
    if not isinstance(old_clean, dict) or not isinstance(new_clean, dict):
        return {"old": old_clean, "new": new_clean}

    changed_old: dict[str, Any] = {}
    changed_new: dict[str, Any] = {}
    for key in sorted(set(old_clean) | set(new_clean)):
        if old_clean.get(key) != new_clean.get(key):
            changed_old[key] = old_clean.get(key)
            changed_new[key] = new_clean.get(key)
    return {"old": changed_old, "new": changed_new}


def payload_shape(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(key): payload_shape(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [payload_shape(value[0], depth=depth + 1)] if value else []
    return type(value).__name__


def sanitize_payload(value: Any, *, depth: int = 0, key: str | None = None) -> Any:
    key_lower = (key or "").lower()
    if _is_secret_key(key_lower):
        return "[redacted]"
    if _is_large_media_key(key_lower):
        return _media_placeholder(value)
    if depth >= MAX_DEPTH:
        return f"[max depth {MAX_DEPTH}]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        if _looks_like_data_url(value):
            return _media_placeholder(value)
        return _truncate_string(CHAT_FILE_URL_RE.sub("/api/v1/ai/chat/files/[redacted]", value))
    if isinstance(value, bytes | bytearray):
        return f"[bytes {len(value)}]"
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for index, (item_key, item_value) in enumerate(value.items()):
            if index >= MAX_DICT_KEYS:
                sanitized["..."] = f"[{len(value) - MAX_DICT_KEYS} keys truncated]"
                break
            sanitized[str(item_key)] = sanitize_payload(
                item_value,
                depth=depth + 1,
                key=str(item_key),
            )
        return sanitized
    if isinstance(value, list | tuple | set):
        items = list(value)
        sanitized_list = [
            sanitize_payload(item, depth=depth + 1, key=key) for item in items[:MAX_LIST_ITEMS]
        ]
        if len(items) > MAX_LIST_ITEMS:
            sanitized_list.append(f"[{len(items) - MAX_LIST_ITEMS} items truncated]")
        return sanitized_list
    return _truncate_string(str(value))


def sanitize_query_string(query: str | bytes | None) -> str:
    if not query:
        return ""
    query_text = query.decode("utf-8", errors="replace") if isinstance(query, bytes) else str(query)
    pairs = parse_qsl(query_text, keep_blank_values=True)
    sanitized = [
        (key, "[redacted]" if _is_secret_key(key.lower()) else value) for key, value in pairs
    ]
    return urlencode(sanitized, doseq=True)


async def write_audit_log(
    session: AsyncSession,
    *,
    category: str,
    action: str,
    actor: str,
    actor_user_id: uuid.UUID | str | None = None,
    target_entity: str | None = None,
    target_id: str | uuid.UUID | None = None,
    target_label: str | None = None,
    diff: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    outcome: str = "success",
    level: str = "info",
    trace_id: str | None = None,
    request_id: str | None = None,
) -> AuditLog:
    row = AuditLog(
        category=category,
        action=action,
        actor=actor,
        actor_user_id=_coerce_uuid(actor_user_id),
        target_entity=target_entity,
        target_id=str(target_id) if target_id is not None else None,
        target_label=target_label,
        diff=sanitize_payload(diff) if diff is not None else None,
        metadata_=sanitize_payload(metadata) if metadata is not None else None,
        outcome=outcome,
        level=level,
        trace_id=trace_id or current_trace_id(),
        request_id=request_id or current_request_id(),
    )
    if isinstance(session, AsyncSession):
        session.add(row)
    return row


def audit_log_event_payload(row: AuditLog) -> dict[str, Any]:
    log = {
        "id": str(row.id),
        "timestamp": row.timestamp.isoformat()
        if row.timestamp
        else datetime.now(tz=UTC).isoformat(),
        "category": row.category,
        "action": row.action,
        "actor": row.actor,
        "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
        "target_entity": row.target_entity,
        "target_id": row.target_id,
        "target_label": row.target_label,
        "diff": row.diff or {},
        "metadata": row.metadata_ or {},
        "outcome": row.outcome,
        "level": row.level,
        "trace_id": row.trace_id,
        "request_id": row.request_id,
    }
    return {
        "id": log["id"],
        "category": row.category,
        "action": row.action,
        "actor": row.actor,
        "target_entity": row.target_entity,
        "target_id": row.target_id,
        "level": row.level,
        "outcome": row.outcome,
        "log": log,
    }


async def record_audit_log(**kwargs: Any) -> None:
    """Await mandatory audit durability independently of lossy diagnostics."""
    session = AsyncSessionLocal()
    try:
        row = await write_audit_log(session, **kwargs)
        await session.commit()
        payload = audit_log_event_payload(row)
    finally:
        await drain_owned(session.close())
    await publish_audit_log_hint(payload)


async def publish_audit_log_hint(payload: dict[str, Any]) -> None:
    """Best-effort invalidation after the owning audit transaction has closed."""
    try:
        await event_bus.publish("audit.log.created", payload)
    except Exception:  # noqa: BLE001 - A realtime observer cannot undo committed mandatory audit.
        logger.warning("audit_realtime_hint_unavailable")


@dataclass
class ActiveTrace:
    service: "TelemetryService"
    name: str
    category: str
    trace_id: str = field(default_factory=trace_id)
    status: str = "ok"
    level: str = "info"
    actor: str | None = None
    source: str | None = None
    registration_number: str | None = None
    started_at: datetime = field(default_factory=utc_now)
    context: dict[str, Any] | None = None
    summary: str | None = None
    error: str | None = None
    access_event_id: uuid.UUID | str | None = None

    def __post_init__(self) -> None:
        self._started_perf_ns = time.perf_counter_ns()
        self._step_order = 0
        self._trace_token = CURRENT_TRACE_ID.set(self.trace_id)
        self._parent_token = CURRENT_PARENT_SPAN_ID.set(None)

    def start_span(
        self,
        name: str,
        *,
        category: str | None = None,
        attributes: dict[str, Any] | None = None,
        input_payload: dict[str, Any] | None = None,
    ) -> "ActiveSpan":
        self._step_order += 1
        return ActiveSpan(
            service=self.service,
            trace_id=self.trace_id,
            parent_span_id=CURRENT_PARENT_SPAN_ID.get(),
            name=name,
            category=category or self.category,
            step_order=self._step_order,
            attributes=attributes,
            input_payload=input_payload,
        )

    def record_span(
        self,
        name: str,
        *,
        started_at: datetime,
        ended_at: datetime | None = None,
        category: str | None = None,
        attributes: dict[str, Any] | None = None,
        input_payload: dict[str, Any] | None = None,
        output_payload: dict[str, Any] | None = None,
        status: str = "ok",
        error: str | None = None,
    ) -> None:
        self._step_order += 1
        ended = ended_at or utc_now()
        self.service.enqueue_span(
            TelemetrySpan(
                span_id=span_id(),
                trace_id=self.trace_id,
                parent_span_id=CURRENT_PARENT_SPAN_ID.get(),
                name=name,
                category=category or self.category,
                step_order=self._step_order,
                started_at=started_at,
                ended_at=ended,
                duration_ms=max(0.0, (ended - started_at).total_seconds() * 1000),
                status=status,
                attributes=sanitize_payload(attributes) if attributes is not None else None,
                input_payload=sanitize_payload(input_payload)
                if input_payload is not None
                else None,
                output_payload=sanitize_payload(output_payload)
                if output_payload is not None
                else None,
                error=_truncate_string(error) if error else None,
            )
        )

    def finish(
        self,
        *,
        status: str | None = None,
        level: str | None = None,
        summary: str | None = None,
        context: dict[str, Any] | None = None,
        error: str | Exception | None = None,
        access_event_id: uuid.UUID | str | None = None,
        ended_at: datetime | None = None,
    ) -> None:
        ended = ended_at or utc_now()
        error_text = str(error) if error else self.error
        row = TelemetryTrace(
            trace_id=self.trace_id,
            name=self.name,
            category=self.category,
            status=status or self.status,
            level=level or self.level,
            started_at=self.started_at,
            ended_at=ended,
            duration_ms=max(0.0, (ended - self.started_at).total_seconds() * 1000),
            actor=self.actor,
            source=self.source,
            registration_number=self.registration_number,
            access_event_id=_coerce_uuid(access_event_id or self.access_event_id),
            summary=summary or self.summary,
            context=sanitize_payload({**(self.context or {}), **(context or {})})
            if (self.context or context)
            else None,
            error=_truncate_string(error_text) if error_text else None,
        )
        self.service.enqueue_trace(row)
        CURRENT_TRACE_ID.reset(self._trace_token)
        CURRENT_PARENT_SPAN_ID.reset(self._parent_token)


@dataclass
class ActiveSpan:
    service: "TelemetryService"
    trace_id: str
    parent_span_id: str | None
    name: str
    category: str
    step_order: int
    attributes: dict[str, Any] | None = None
    input_payload: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.span_id = span_id()
        self.started_at = utc_now()
        self._started_perf_ns = time.perf_counter_ns()
        self._parent_token = CURRENT_PARENT_SPAN_ID.set(self.span_id)
        self._finished = False

    def finish(
        self,
        *,
        status: str = "ok",
        output_payload: dict[str, Any] | None = None,
        error: str | Exception | None = None,
    ) -> None:
        if self._finished:
            return
        self._finished = True
        ended_at = utc_now()
        error_text = str(error) if error else None
        self.service.enqueue_span(
            TelemetrySpan(
                span_id=self.span_id,
                trace_id=self.trace_id,
                parent_span_id=self.parent_span_id,
                name=self.name,
                category=self.category,
                step_order=self.step_order,
                started_at=self.started_at,
                ended_at=ended_at,
                duration_ms=(time.perf_counter_ns() - self._started_perf_ns) / 1_000_000,
                status=status,
                attributes=sanitize_payload(self.attributes)
                if self.attributes is not None
                else None,
                input_payload=sanitize_payload(self.input_payload)
                if self.input_payload is not None
                else None,
                output_payload=sanitize_payload(output_payload)
                if output_payload is not None
                else None,
                error=_truncate_string(error_text) if error_text else None,
            )
        )
        CURRENT_PARENT_SPAN_ID.reset(self._parent_token)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _exc_type, exc, _traceback) -> None:
        self.finish(status="error" if exc else "ok", error=exc)


DIAGNOSTIC_RECORD_LIMIT = 1024
DIAGNOSTIC_BYTE_LIMIT = 16 * 1024 * 1024
DIAGNOSTIC_BATCH_LIMIT = 100
DIAGNOSTIC_DRAIN_SECONDS = 10.0


@dataclass(frozen=True)
class _DiagnosticRecord:
    sequence: int
    kind: Literal["trace", "span"]
    payload: bytes


class TelemetryService:
    """One bounded diagnostic writer; mandatory audit never enters this FIFO."""

    def __init__(self, *, sessions: Callable[[], AsyncSession] | None = None) -> None:
        self._sessions = sessions or (lambda: AsyncSessionLocal())
        self._queue: deque[_DiagnosticRecord] = deque()
        self._writer: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._progress = asyncio.Event()
        self._accepting = False
        self._stopping = False
        self._retained_records = 0
        self._retained_bytes = 0
        self._accepted = 0
        self._settled = 0
        self._dropped: dict[str, int] = {}
        self._hint_failures = 0
        self._inflight_committed = False
        self._inflight_hints = 0

    async def start(self) -> None:
        if self._writer is not None and not self._writer.done():
            return
        self._accepting = True
        self._stopping = False
        self._writer = asyncio.create_task(self._run(), name="telemetry-diagnostic-writer")

    async def stop(self) -> None:
        self._accepting = False
        self._stopping = True
        self._wake.set()
        task = self._writer
        if task is not None:
            try:
                # Repeated shutdown cancellation cannot abandon DB close or a
                # committed batch's hint while its bytes still occupy capacity.
                await drain_owned(self._stop_writer(task))
            finally:
                if task.done():
                    self._writer = None

    async def _stop_writer(self, task: asyncio.Task[None]) -> None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=DIAGNOSTIC_DRAIN_SECONDS)
        except TimeoutError:
            # The deadline ends diagnostic work, not ownership of its cleanup.
            # Cancel the sole writer once, then drain actual DB close.
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def status(self) -> dict[str, Any]:
        return {
            "accepting": self._accepting,
            "writer_running": self._writer is not None and not self._writer.done(),
            "queued_records": len(self._queue),
            "retained_records": self._retained_records,
            "retained_serialized_bytes": self._retained_bytes,
            "record_limit": DIAGNOSTIC_RECORD_LIMIT,
            "serialized_byte_limit": DIAGNOSTIC_BYTE_LIMIT,
            "batch_limit": DIAGNOSTIC_BATCH_LIMIT,
            "accepted_records": self._accepted,
            "settled_records": self._settled,
            "dropped_records": dict(self._dropped),
            "realtime_hint_failures": self._hint_failures,
        }

    def start_trace(self, name: str, *, category: str, **kwargs: Any) -> ActiveTrace:
        return ActiveTrace(service=self, name=name, category=category, **kwargs)

    def enqueue_trace(self, trace: TelemetryTrace) -> None:
        self._enqueue("trace", trace)

    def enqueue_span(self, span: TelemetrySpan) -> None:
        self._enqueue("span", span)

    def _enqueue(self, kind: Literal["trace", "span"], row: TelemetryTrace | TelemetrySpan) -> None:
        if not self._accepting:
            self._drop("not_started_or_stopping", 1)
            return
        if self._retained_records >= DIAGNOSTIC_RECORD_LIMIT:
            self._drop("record_capacity", 1)
            return
        # Retain immutable encoded rows, not caller-owned mutable ORM objects.
        # The byte budget is serialized diagnostic payload, not a process RSS claim.
        values = {column.key: getattr(row, column.key) for column in row.__table__.columns
                  if getattr(row, column.key) is not None}
        payload = json.dumps(values, default=str, ensure_ascii=False, separators=(",", ":")).encode()
        if len(payload) + self._retained_bytes > DIAGNOSTIC_BYTE_LIMIT:
            self._drop("byte_capacity", 1)
            return
        self._accepted += 1
        self._queue.append(_DiagnosticRecord(self._accepted, kind, payload))
        self._retained_records += 1
        self._retained_bytes += len(payload)
        self._wake.set()

    def _drop(self, reason: str, count: int) -> None:
        self._dropped[reason] = self._dropped.get(reason, 0) + count

    async def _run(self) -> None:
        try:
            while True:
                self._wake.clear()
                if not self._queue:
                    if self._stopping:
                        return
                    await self._wake.wait()
                    continue
                batch = [self._queue.popleft() for _ in range(min(len(self._queue), DIAGNOSTIC_BATCH_LIMIT))]
                count, size, settled = len(batch), sum(len(row.payload) for row in batch), batch[-1].sequence
                self._inflight_committed = False
                self._inflight_hints = 0
                try:
                    await self._write_batch(batch)
                except asyncio.CancelledError:
                    if self._inflight_committed:
                        # The rows are durable; only unfinished realtime hints
                        # were abandoned. Never count these rows as dropped.
                        self._hint_failures += self._inflight_hints
                    else:
                        # A cancellation during commit cannot prove absence.
                        self._drop("shutdown_unconfirmed", count)
                    raise
                except Exception:  # noqa: BLE001 - Diagnostics are lossy; mandatory audit uses another path.
                    if self._inflight_committed:
                        self._hint_failures += self._inflight_hints
                        logger.warning("telemetry_committed_batch_cleanup_unavailable")
                    else:
                        self._drop("persistence_failed", count)
                        logger.warning("telemetry_diagnostic_batch_failed")
                finally:
                    # _write_batch has drained its actual session close.
                    # Release encoded bodies before returning their budget.
                    batch.clear()
                    self._retained_records -= count
                    self._retained_bytes -= size
                    self._settled = settled
                    self._progress.set()
        finally:
            self._accepting = False
            count, size = len(self._queue), sum(len(row.payload) for row in self._queue)
            self._queue.clear()
            if count:
                self._drop("shutdown_queued", count)
            self._retained_records -= count
            self._retained_bytes -= size
            self._settled = self._accepted
            self._progress.set()

    async def _write_batch(self, batch: list[_DiagnosticRecord]) -> None:
        traces: list[dict[str, Any]] = []
        session = self._sessions()
        try:
            await session.execute(text("SET LOCAL statement_timeout = '30s'"))
            await session.execute(text("SET LOCAL lock_timeout = '5s'"))
            for record in batch:
                values = json.loads(record.payload)
                for name in ("started_at", "ended_at"):
                    if values.get(name) is not None:
                        values[name] = datetime.fromisoformat(values[name])
                if record.kind == "trace":
                    if values.get("access_event_id") is not None:
                        values["access_event_id"] = uuid.UUID(values["access_event_id"])
                    session.add(TelemetryTrace(**values))
                    traces.append(values)
                else:
                    session.add(TelemetrySpan(**values))
            await session.commit()
            self._inflight_committed = True
            self._inflight_hints = len(traces)
        finally:
            await drain_owned(session.close())
        for trace in traces:
            try:
                await event_bus.publish("telemetry.trace.created", {
                    name: trace.get(name) for name in (
                        "trace_id", "name", "category", "status", "level", "duration_ms", "registration_number"
                    )
                })
            except Exception:  # noqa: BLE001 - Durable diagnostics survive unavailable realtime hints.
                self._hint_failures += 1
                logger.warning("telemetry_realtime_hint_unavailable")
            self._inflight_hints -= 1

    def record_span(
        self,
        name: str,
        *,
        trace_id: str | None = None,
        category: str,
        attributes: dict[str, Any] | None = None,
        input_payload: dict[str, Any] | None = None,
        output_payload: dict[str, Any] | None = None,
        status: str = "ok",
        error: str | Exception | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
    ) -> None:
        active_trace_id = trace_id or CURRENT_TRACE_ID.get()
        if not active_trace_id:
            return
        started = started_at or utc_now()
        ended = ended_at or started
        self.enqueue_span(
            TelemetrySpan(
                span_id=span_id(),
                trace_id=active_trace_id,
                parent_span_id=CURRENT_PARENT_SPAN_ID.get(),
                name=name,
                category=category,
                step_order=0,
                started_at=started,
                ended_at=ended,
                duration_ms=max(0.0, (ended - started).total_seconds() * 1000),
                status=status,
                attributes=sanitize_payload(attributes) if attributes is not None else None,
                input_payload=sanitize_payload(input_payload)
                if input_payload is not None
                else None,
                output_payload=sanitize_payload(output_payload)
                if output_payload is not None
                else None,
                error=_truncate_string(str(error)) if error else None,
            )
        )

    async def flush(self) -> None:
        # Only the prefix accepted before this call belongs to its barrier.
        # Continuous producers must not postpone every diagnostic read forever.
        target = self._accepted
        while self._settled < target:
            self._progress.clear()
            if self._settled < target:
                await self._progress.wait()

    async def store_artifact(
        self,
        content: bytes,
        *,
        content_type: str,
        kind: str,
        trace_id: str,
        span_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await drain_owned(asyncio.to_thread(
            self._write_artifact,
            content,
            content_type=content_type,
            kind=kind,
            trace_id=trace_id,
            span_id=span_id,
            metadata=metadata,
        ))

    def artifact_path(self, artifact_id: str) -> tuple[Path, dict[str, Any]]:
        if not re.fullmatch(r"[a-f0-9]{32}", artifact_id):
            raise FileNotFoundError(artifact_id)
        directory = settings.data_dir / "telemetry-artifacts"
        metadata_path = directory / f"{artifact_id}.json"
        if not metadata_path.exists():
            raise FileNotFoundError(artifact_id)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        file_path = directory / str(metadata["filename"])
        if not file_path.exists():
            raise FileNotFoundError(artifact_id)
        return file_path, metadata

    def _write_artifact(
        self,
        content: bytes,
        *,
        content_type: str,
        kind: str,
        trace_id: str,
        span_id: str | None,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        directory = settings.data_dir / "telemetry-artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        artifact_id = secrets.token_hex(16)
        extension = _extension_for_content_type(content_type)
        filename = f"{artifact_id}.{extension}"
        file_path = directory / filename
        file_path.write_bytes(content)
        sidecar = {
            "id": artifact_id,
            "filename": filename,
            "content_type": content_type,
            "kind": kind,
            "trace_id": trace_id,
            "span_id": span_id,
            "size_bytes": len(content),
            "created_at": utc_now().isoformat(),
            "metadata": sanitize_payload(metadata or {}),
        }
        (directory / f"{artifact_id}.json").write_text(
            json.dumps(sidecar, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return {
            "id": artifact_id,
            "kind": kind,
            "content_type": content_type,
            "size_bytes": len(content),
            "url": f"/api/v1/telemetry/artifacts/{artifact_id}",
        }


def _is_secret_key(key: str) -> bool:
    return any(pattern in key for pattern in SECRET_KEY_PATTERNS)


def _is_large_media_key(key: str) -> bool:
    if key in {"configuration_snapshot", "config_snapshot"}:
        return False
    return any(pattern in key for pattern in LARGE_MEDIA_KEY_PATTERNS)


def _looks_like_data_url(value: str) -> bool:
    return value.startswith(("data:image/", "data:video/"))


def _truncate_string(value: str) -> str:
    if len(value) <= MAX_STRING_LENGTH:
        return value
    return f"{value[:MAX_STRING_LENGTH]}...[truncated {len(value) - MAX_STRING_LENGTH} chars]"


def _media_placeholder(value: Any) -> str | None:
    if not value:
        return None
    try:
        length = len(value)
    except TypeError:
        length = 0
    return f"[media redacted {length} bytes]"


def _coerce_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _extension_for_content_type(content_type: str) -> str:
    normalized = content_type.split(";", 1)[0].strip().lower()
    return {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "application/json": "json",
    }.get(normalized, "bin")


telemetry = TelemetryService()
