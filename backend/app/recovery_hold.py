"""Same application, explicit recovery posture: inspect records without executors.

Enable IACS_RECOVERY_HOLD before starting a compatible image after rollback or
database restoration. This does not prove the restored journal includes effects
accepted after the backup; an operator must reconcile that gap before activation.
"""
import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.recovery_hold import is_recovery_hold
from app.db.session import AsyncSessionLocal

_UUID = r"[0-9a-fA-F-]{36}"
_READ_PATHS = (
    r"/api/v1/auth/(status|me|me/photo)",
    rf"/api/v1/integrations/(gate|cover)/commands(?:/{_UUID})?",
    rf"/api/v1/(automations|notifications)/runs(?:/{_UUID})?",
    rf"/api/v1/integrations/(whatsapp|discord)/incoming(?:/{_UUID})?",
    r"/api/v1/notifications/recovery/gate-outbox",
    r"/api/v1/ai/chat/approvals(?:/confirm-[0-9a-f]{32})?",
    r"/api/v1/ai/training/(feedback|lessons|eval-examples|eval-export)",
    r"/(docs|openapi.json|redoc)",
)


def allows_request(method: str, path: str) -> bool:
    if method == "POST":
        # Authentication may change session credentials; domain records stay held.
        return path in {"/api/v1/auth/login", "/api/v1/auth/logout"}
    return method in {"GET", "HEAD"} and any(re.fullmatch(pattern, path) for pattern in _READ_PATHS)


async def verify_readable_schema() -> None:
    """Refuse incompatible source/schema instead of migrating a restored database."""
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    expected = set(ScriptDirectory.from_config(config).get_heads())
    async with AsyncSessionLocal() as session:
        actual = set((await session.execute(text("SELECT version_num FROM alembic_version"))).scalars())
    if not expected or actual != expected:
        raise RuntimeError("Recovery hold requires a source image compatible with the retained schema; no migration was attempted.")


class RecoveryHoldMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not is_recovery_hold() or scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "Recovery hold is active"})
            return
        path, method = scope.get("path", ""), scope.get("method", "")
        if method in {"GET", "HEAD"} and path in {"/", "/health", "/api/v1/health", "/api/v1/health/ready"}:
            ready_path = path == "/api/v1/health/ready"
            response = JSONResponse({"status": "recovery_hold", "recovery_hold": True,
                "ready": False, "recovery_readable": bool(getattr(scope["app"].state, "startup_complete", False)),
                "message": "Executors are stopped. Reconcile retained and potentially lost work before activation."},
                status_code=503 if ready_path else 200)
            await response(scope, receive, send)
            return
        if allows_request(method, path):
            await self.app(scope, receive, send)
            return
        await JSONResponse({"detail": "recovery_hold", "recovery_hold": True,
            "message": "Recovery hold permits authentication and audited recovery reads only."},
            status_code=503)(scope, receive, send)
