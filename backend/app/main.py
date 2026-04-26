from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import api_router
from app.api.v1 import auth as auth_routes
from app.api.v1 import schedules as schedule_routes
from app.api.v1 import users as user_routes
from app.api.v1 import webhooks as webhook_routes
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.bootstrap import init_database
from app.db.session import AsyncSessionLocal
from app.services.auth import authenticate_request, count_users
from app.services.event_bus import event_bus
from app.services.access_events import get_access_event_service
from app.services.home_assistant import get_home_assistant_service
from app.services.notifications import get_notification_service
from app.services.settings import get_runtime_config
from app.services.unifi_protect import get_unifi_protect_service

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start and stop process-wide services.

    Long-lived services are registered here so route handlers stay focused on
    request validation and orchestration.
    """

    configure_logging()
    logger.info(
        "starting_backend",
        extra={"app_name": settings.app_name, "environment": settings.environment},
    )
    await init_database()
    await event_bus.start()
    await get_notification_service().start()
    await get_access_event_service().start()
    await get_home_assistant_service().start()
    await get_unifi_protect_service().start()
    try:
        yield
    finally:
        await get_unifi_protect_service().stop()
        await get_home_assistant_service().stop()
        await get_access_event_service().stop()
        await get_notification_service().stop()
        await event_bus.stop()
        logger.info("stopped_backend")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    root_path=settings.root_path,
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
    "/api/auth/status",
    "/api/auth/setup",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/v1/auth/status",
    "/api/v1/auth/setup",
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/webhooks/ubiquiti/lpr",
    "/api/webhooks/ubiquiti/lpr",
}


def _requires_auth(path: str) -> bool:
    if path in {"/", "/health", "/api/v1/health"}:
        return False
    if path in PUBLIC_AUTH_PATHS:
        return False
    return path.startswith("/api/") or path in {"/docs", "/openapi.json", "/redoc"}


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


@app.get("/health", tags=["health"])
async def root_health() -> dict[str, str]:
    return {"status": "ok", "service": "backend"}


@app.get("/", tags=["health"])
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
app.include_router(auth_routes.router, prefix="/api/auth", tags=["auth"])
app.include_router(schedule_routes.router, prefix="/api/schedules", tags=["schedules"])
app.include_router(user_routes.router, prefix="/api/users", tags=["users"])
app.include_router(webhook_routes.router, prefix="/api/webhooks", tags=["webhooks"])
