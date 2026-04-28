from fastapi import APIRouter

from app.api.v1 import (
    ai,
    auth,
    diagnostics,
    directory,
    events,
    gate_malfunctions,
    health,
    integrations,
    leaderboard,
    maintenance,
    notifications,
    realtime,
    schedules,
    settings,
    telemetry,
    unifi_protect,
    users,
    webhooks,
)
from app.simulation.router import router as simulation_router

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(diagnostics.router, prefix="/diagnostics", tags=["diagnostics"])
api_router.include_router(directory.router, tags=["directory"])
api_router.include_router(events.router, tags=["events"])
api_router.include_router(gate_malfunctions.router, prefix="/gate-malfunctions", tags=["gate-malfunctions"])
api_router.include_router(integrations.router, prefix="/integrations", tags=["integrations"])
api_router.include_router(leaderboard.router, tags=["leaderboard"])
api_router.include_router(maintenance.router, prefix="/maintenance", tags=["maintenance"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(schedules.router, prefix="/schedules", tags=["schedules"])
api_router.include_router(unifi_protect.router, prefix="/integrations/unifi-protect", tags=["integrations"])
api_router.include_router(realtime.router, prefix="/realtime", tags=["realtime"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
api_router.include_router(telemetry.router, prefix="/telemetry", tags=["telemetry"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
api_router.include_router(simulation_router, prefix="/simulation", tags=["simulation"])
