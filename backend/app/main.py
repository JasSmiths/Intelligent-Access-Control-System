from contextlib import AsyncExitStack, asynccontextmanager
from collections.abc import Awaitable, Callable
import asyncio
from datetime import UTC, datetime
from functools import partial

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import api_router
from app.core.config import settings, validate_startup_security_config
from app.core.recovery_hold import is_recovery_hold
from app.recovery_hold import RecoveryHoldMiddleware, verify_readable_schema
from app.core.logging import configure_logging, get_logger
from app.db.bootstrap import init_database
from app.db.session import AsyncSessionLocal, engine
from app.services.auth import authenticate_request, count_users
from app.services.chat import chat_service
from app.services.alfred.feedback import alfred_feedback_service
from app.services.access_devices import get_access_device_service
from app.services.event_bus import event_bus
from app.services.access_events import get_access_event_service
from app.services.automations import get_automation_service
from app.services.dependency_updates import get_dependency_update_service
from app.services.discord_messaging import get_discord_messaging_service
from app.services.home_assistant import get_home_assistant_service
from app.services.gate_malfunctions import get_gate_malfunction_service
from app.services.lpr_webhook_security import verify_lpr_webhook_request
from app.services.maintenance import is_maintenance_mode_active
from app.services.movement_reconciliation import get_movement_reconciliation_service
from app.services.notifications import get_notification_service
from app.services.restart_backfill import (
    backfill_missed_access_events_safely,
    read_backend_runtime_state,
    run_backend_runtime_heartbeat,
    run_missed_access_event_reconciliation,
)
from app.services.settings import get_runtime_config
from app.services.snapshot_recovery import recover_missing_access_event_snapshots_safely
from app.services.telemetry import (
    CURRENT_REQUEST_ID,
    TELEMETRY_CATEGORY_WEBHOOKS_API,
    actor_from_user,
    sanitize_query_string,
    telemetry,
    telemetry_request_id,
)
from app.services.unifi_protect import get_unifi_protect_service
from app.services.visitor_passes import get_visitor_pass_service
from app.services.messaging.whatsapp_delivery import get_whatsapp_delivery_service
from app.services.messaging.whatsapp_incoming import get_whatsapp_incoming_dispatcher
from app.services.messaging.discord_incoming import DiscordIncomingGateway
from app.services.messaging_bridge import messaging_bridge_service

logger = get_logger(__name__)

KIB = 1024
MIB = 1024 * KIB
DEFAULT_API_BODY_LIMIT_BYTES = 2 * MIB
CHAT_UPLOAD_BODY_LIMIT_BYTES = 25 * MIB
WEBHOOK_BODY_LIMIT_BYTES = 1 * MIB
AUTOMATION_WEBHOOK_BODY_LIMIT_BYTES = 256 * KIB


class RequestBodyTooLarge(RuntimeError):
    def __init__(self, limit_bytes: int) -> None:
        self.limit_bytes = limit_bytes
        super().__init__(f"Request body exceeds {limit_bytes} bytes.")


async def _stop_owned_service(name: str, stop: Callable[[], Awaitable[None]]) -> None:
    try:
        await asyncio.wait_for(stop(), timeout=20)
    except (Exception, asyncio.CancelledError) as exc:
        # One failed resource must not keep later resources alive or conceal the
        # original startup exception. Stop methods remain responsible for children.
        logger.error("service_cleanup_failed", extra={"service": name, "error_class": type(exc).__name__})


async def _cancel_owned_task(task: asyncio.Task) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("background_task_failed", extra={"task": task.get_name(), "error_class": type(exc).__name__})


@asynccontextmanager
async def lifespan(app: FastAPI):
    """One process owner; unwind partial startup and drain producers before sinks."""
    app.state.startup_complete = False
    configure_logging()
    validate_startup_security_config()
    logger.info("starting_backend", extra={"app_name": settings.app_name, "environment": settings.environment})
    try:
        async with AsyncExitStack() as resources, AsyncExitStack() as approvals, AsyncExitStack() as producers:
            resources.push_async_callback(_stop_owned_service, "database", engine.dispose)
            if is_recovery_hold():
                await verify_readable_schema()
                app.state.startup_complete = True
                try:
                    yield
                finally:
                    app.state.startup_complete = False
                return
            await init_database()
            # Drain intake first, then its shielded approval tasks, while hardware,
            # delivery sinks and database resources are still available.
            approvals.push_async_callback(_stop_owned_service, "alfred_approvals", chat_service.stop)
            resources.push_async_callback(_stop_owned_service, "whatsapp_delivery", get_whatsapp_delivery_service().stop)
            discord_service = get_discord_messaging_service()
            discord_gateway = DiscordIncomingGateway(
                discord_service, message_handler=messaging_bridge_service.handle_message,
                confirmation_handler=partial(messaging_bridge_service.handle_confirmation, provider="discord"),
            )
            discord_service.configure_gateway(discord_gateway)
            # Register before start so a partially started service is also closed.
            for name, service in (
                ("realtime", event_bus),
                ("dependency_updates", get_dependency_update_service()),
                ("notifications", get_notification_service()),
                ("automations", get_automation_service()),
                ("alfred_feedback", alfred_feedback_service),
                ("discord", discord_service),
                ("visitor_passes", get_visitor_pass_service()),
                ("access_devices", get_access_device_service()),
                ("access_events", get_access_event_service()),
                ("movement_reconciliation", get_movement_reconciliation_service()),
                ("home_assistant", get_home_assistant_service()),
                ("gate_malfunctions", get_gate_malfunction_service()),
                ("unifi_protect", get_unifi_protect_service()),
            ):
                if name == "discord":
                    # Reverse cleanup stops the bot producer before draining its
                    # incoming worker, while notification sinks and DB stay alive.
                    producers.push_async_callback(_stop_owned_service, "discord_incoming", discord_gateway.stop)
                    discord_gateway.start()
                owner = producers if name in {
                    "discord", "automations", "unifi_protect", "access_events", "movement_reconciliation"
                } else resources
                owner.push_async_callback(_stop_owned_service, name, service.stop)
                await service.start()
            whatsapp_incoming = get_whatsapp_incoming_dispatcher()
            producers.push_async_callback(_stop_owned_service, "whatsapp_incoming", whatsapp_incoming.stop)
            whatsapp_incoming.start()
            startup_at = datetime.now(tz=UTC)
            previous_runtime_state = read_backend_runtime_state()
            for name, create_coroutine in (
                ("backend-runtime-heartbeat", lambda: run_backend_runtime_heartbeat(started_at=startup_at, previous_state=previous_runtime_state)),
                ("missed-access-event-backfill", lambda: backfill_missed_access_events_safely(previous_runtime_state=previous_runtime_state, startup_at=startup_at)),
                ("missed-access-event-reconciliation", lambda: run_missed_access_event_reconciliation()),
                ("access-event-snapshot-recovery", lambda: recover_missing_access_event_snapshots_safely()),
            ):
                producers.push_async_callback(_cancel_owned_task, asyncio.create_task(create_coroutine(), name=name))
            app.state.startup_complete = True
            try:
                yield
            finally:
                app.state.startup_complete = False
    finally:
        logger.info("stopped_backend")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    root_path=settings.root_path,
    openapi_tags=[
        {"name": "Health", "description": "Backend health and service readiness checks."},
        {"name": "Authentication", "description": "First-run setup, login, logout, and current-user preferences."},
        {"name": "AI Agents", "description": "Provider discovery, agent tooling, chat, uploads, and chat realtime."},
        {"name": "Automations", "description": "System-wide trigger, condition, and action automation rules."},
        {"name": "Diagnostics", "description": "Operational diagnostics and LPR timing instrumentation."},
        {"name": "Dependency Updates", "description": "System-wide dependency enrollment, analysis, backups, update jobs, and rollback."},
        {"name": "Directory", "description": "People, vehicles, groups, and directory-owned DVLA refresh actions."},
        {"name": "Access Events", "description": "Access history, presence, anomalies, alerts, and alert snapshots."},
        {"name": "Gate Telemetry", "description": "Gate malfunction state, history, trace lookup, and operator override."},
        {"name": "Integrations", "description": "Home Assistant, Apprise, Discord, DVLA, iCloud Calendar, gate, cover, and announcement operations."},
        {"name": "UniFi Protect", "description": "UniFi Protect cameras, media, managed package updates, and backups."},
        {"name": "Top Charts", "description": "Leaderboard and access rhythm rankings."},
        {"name": "Maintenance", "description": "Maintenance mode status and controls."},
        {"name": "Notifications", "description": "Notification workflow catalog, rules, previews, and tests."},
        {"name": "Schedules", "description": "Reusable weekly access windows and dependency checks."},
        {"name": "Visitor Passes", "description": "Anticipated one-shot visitor access windows and telemetry."},
        {"name": "Realtime", "description": "Dashboard realtime WebSocket channel."},
        {"name": "Reports", "description": "Generated access and presence reports with PDF export."},
        {"name": "Settings", "description": "Dynamic runtime settings and integration test actions."},
        {"name": "Telemetry", "description": "Trace, audit, category, artifact, and purge endpoints."},
        {"name": "Users", "description": "Admin-managed local dashboard users."},
        {"name": "Webhooks", "description": "External event ingestion endpoints."},
        {"name": "Simulation", "description": "Hardware-free LPR simulation endpoints."},
    ],
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


PUBLIC_AUTH_PATHS = {
    "/api/v1/auth/status",
    "/api/v1/auth/setup",
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/webhooks/ubiquiti/lpr",
    "/api/v1/webhooks/whatsapp",
}
PUBLIC_AUTH_PREFIXES = (
    "/api/v1/automations/webhooks/",
    "/api/v1/notification-snapshots/",
)

READ_ONLY_METHODS = {"GET", "HEAD"}
ALWAYS_TRACE_API_PREFIXES = (
    "/api/v1/webhooks/",
)
MAINTENANCE_IGNORED_WEBHOOK_PATHS = {
    "/api/v1/webhooks/ubiquiti/lpr",
}


def _requires_auth(path: str) -> bool:
    if path in {"/", "/health", "/api/v1/health", "/api/v1/health/ready"}:
        return False
    if path in PUBLIC_AUTH_PATHS:
        return False
    if any(path.startswith(prefix) for prefix in PUBLIC_AUTH_PREFIXES):
        return False
    return path.startswith("/api/v1/") or path in {"/docs", "/openapi.json", "/redoc"}


def _should_trace_api_request(method: str, path: str) -> bool:
    if not path.startswith("/api/v1/") or path.startswith("/api/v1/telemetry"):
        return False
    if path.startswith(ALWAYS_TRACE_API_PREFIXES):
        return True
    return method.upper() not in READ_ONLY_METHODS


def _body_limit_for_request(method: str, path: str) -> int | None:
    if method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if path == "/api/v1/ai/chat/upload":
        return CHAT_UPLOAD_BODY_LIMIT_BYTES
    if path == "/api/v1/webhooks/whatsapp" or path == "/api/v1/webhooks/ubiquiti/lpr":
        return WEBHOOK_BODY_LIMIT_BYTES
    if path.startswith("/api/v1/automations/webhooks/"):
        return AUTOMATION_WEBHOOK_BODY_LIMIT_BYTES
    if path.startswith("/api/v1/"):
        return DEFAULT_API_BODY_LIMIT_BYTES
    return None


def _payload_too_large_response(limit_bytes: int) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={
            "detail": "Request body is too large.",
            "max_body_bytes": limit_bytes,
        },
    )


@app.middleware("http")
async def request_size_limit_middleware(request: Request, call_next):
    limit_bytes = _body_limit_for_request(request.method, request.url.path)
    if limit_bytes is None:
        return await call_next(request)

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > limit_bytes:
                return _payload_too_large_response(limit_bytes)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header."})

    original_receive = request._receive
    bytes_seen = 0

    async def receive_with_limit():
        nonlocal bytes_seen
        message = await original_receive()
        if message.get("type") == "http.request":
            bytes_seen += len(message.get("body") or b"")
            if bytes_seen > limit_bytes:
                raise RequestBodyTooLarge(limit_bytes)
        return message

    request._receive = receive_with_limit
    try:
        return await call_next(request)
    except RequestBodyTooLarge as exc:
        return _payload_too_large_response(exc.limit_bytes)


@app.middleware("http")
async def maintenance_webhook_guard(request: Request, call_next):
    if (
        request.method.upper() == "POST"
        and request.url.path in MAINTENANCE_IGNORED_WEBHOOK_PATHS
        and await is_maintenance_mode_active()
    ):
        try:
            verify_lpr_webhook_request(request, runtime=await get_runtime_config())
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers,
            )
        return JSONResponse(
            status_code=202,
            content={"status": "ignored", "reason": "maintenance_mode"},
        )
    return await call_next(request)


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    if request.method == "OPTIONS" or not _requires_auth(request.url.path):
        return await call_next(request)

    async with AsyncSessionLocal() as session:
        user_count = await count_users(session)

    if user_count == 0:
        return JSONResponse(
            status_code=428,
            content={
                "detail": "setup_required",
                "setup_required": True,
                "setup_path": "/setup",
            },
        )

    async with AsyncSessionLocal() as session:
        user = await authenticate_request(session, request)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
        )
    request.state.user = user

    return await call_next(request)


@app.middleware("http")
async def telemetry_http_middleware(request: Request, call_next):
    path = request.url.path
    if is_recovery_hold():
        return await call_next(request)
    if not _should_trace_api_request(request.method, path):
        return await call_next(request)

    request_id = request.headers.get("x-request-id") or telemetry_request_id()
    request_token = CURRENT_REQUEST_ID.set(request_id)
    client_host = request.client.host if request.client else None
    trace = telemetry.start_trace(
        f"HTTP {request.method} {path}",
        category=TELEMETRY_CATEGORY_WEBHOOKS_API,
        actor="System",
        source=client_host,
        context={
            "method": request.method,
            "path": path,
            "query": sanitize_query_string(request.url.query),
            "client": client_host,
            "user_agent": request.headers.get("user-agent"),
            "request_id": request_id,
        },
    )
    span = trace.start_span(
        "HTTP request execution",
        attributes={"method": request.method, "path": path},
    )
    try:
        response = await call_next(request)
    except Exception as exc:
        span.finish(status="error", error=exc)
        trace.actor = actor_from_user(getattr(request.state, "user", None))
        trace.finish(
            status="error",
            level="error",
            summary=f"{request.method} {path} failed",
            error=exc,
        )
        CURRENT_REQUEST_ID.reset(request_token)
        raise

    span.finish(output_payload={"status_code": response.status_code})
    level = "error" if response.status_code >= 500 else "warning" if response.status_code >= 400 else "info"
    trace.actor = actor_from_user(getattr(request.state, "user", None))
    trace.finish(
        status="error" if response.status_code >= 500 else "ok",
        level=level,
        summary=f"{request.method} {path} returned HTTP {response.status_code}",
        context={"status_code": response.status_code},
    )
    response.headers["X-IACS-Request-ID"] = request_id
    CURRENT_REQUEST_ID.reset(request_token)
    return response


@app.middleware("http")
async def api_error_response_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except RequestBodyTooLarge as exc:
        return _payload_too_large_response(exc.limit_bytes)
    except Exception:
        if not request.url.path.startswith("/api/"):
            raise
        request_id = request.headers.get("x-request-id") or telemetry_request_id()
        logger.exception(
            "api_request_failed",
            extra={"method": request.method, "path": request.url.path, "request_id": request_id},
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": (
                    f"Unexpected backend error while handling {request.method} {request.url.path}. "
                    f"Check backend logs with request ID {request_id}."
                ),
                "request_id": request_id,
            },
            headers={"X-IACS-Request-ID": request_id},
        )


@app.get("/health", include_in_schema=False)
async def root_health() -> dict[str, str]:
    return {"status": "ok", "service": "backend"}


@app.get("/", include_in_schema=False)
async def service_root() -> dict[str, object]:
    """Identify the backend when a LAN user browses to the base URL."""

    runtime = await get_runtime_config()
    return {
        "service": runtime.app_name,
        "status": "ok",
        "message": "IACS backend is running. Use the frontend service on port 8089 for the web UI.",
        "endpoints": {
            "health": "/health",
            "api_health": "/api/v1/health",
            "docs": "/docs",
            "realtime": "/api/v1/realtime/ws",
            "ai_chat": "/api/v1/ai/chat/ws",
        },
    }


app.include_router(api_router, prefix="/api/v1")
app.add_middleware(RecoveryHoldMiddleware)
