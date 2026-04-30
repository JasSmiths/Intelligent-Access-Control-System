from fastapi import APIRouter

from app.api.v1 import (
    ai,
    automations,
    auth,
    dependency_updates,
    diagnostics,
    directory,
    discord,
    events,
    gate_malfunctions,
    health,
    icloud_calendar,
    integrations,
    leaderboard,
    maintenance,
    notification_snapshots,
    notifications,
    realtime,
    schedules,
    settings,
    telemetry,
    unifi_protect,
    users,
    visitor_passes,
    webhooks,
)
from app.simulation.router import router as simulation_router

api_router = APIRouter()
api_router.include_router(health.router, tags=["Health"])
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(dependency_updates.router, prefix="/dependency-updates", tags=["Dependency Updates"])
api_router.include_router(ai.router, prefix="/ai", tags=["AI Agents"])
api_router.include_router(automations.router, prefix="/automations", tags=["Automations"])
api_router.include_router(diagnostics.router, prefix="/diagnostics", tags=["Diagnostics"])
api_router.include_router(directory.router, tags=["Directory"])
api_router.include_router(events.router, tags=["Access Events"])
api_router.include_router(gate_malfunctions.router, prefix="/gate-malfunctions", tags=["Gate Telemetry"])
api_router.include_router(integrations.router, prefix="/integrations", tags=["Integrations"])
api_router.include_router(icloud_calendar.router, prefix="/integrations/icloud-calendar", tags=["Integrations"])
api_router.include_router(discord.router, prefix="/integrations/discord", tags=["Integrations"])
api_router.include_router(leaderboard.router, tags=["Top Charts"])
api_router.include_router(maintenance.router, prefix="/maintenance", tags=["Maintenance"])
api_router.include_router(notification_snapshots.router, prefix="/notification-snapshots", tags=["Notifications"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
api_router.include_router(schedules.router, prefix="/schedules", tags=["Schedules"])
api_router.include_router(unifi_protect.router, prefix="/integrations/unifi-protect", tags=["UniFi Protect"])
api_router.include_router(realtime.router, prefix="/realtime", tags=["Realtime"])
api_router.include_router(settings.router, prefix="/settings", tags=["Settings"])
api_router.include_router(telemetry.router, prefix="/telemetry", tags=["Telemetry"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(visitor_passes.router, prefix="/visitor-passes", tags=["Visitor Passes"])
api_router.include_router(webhooks.router, prefix="/webhooks", tags=["Webhooks"])
api_router.include_router(simulation_router, prefix="/simulation", tags=["Simulation"])
