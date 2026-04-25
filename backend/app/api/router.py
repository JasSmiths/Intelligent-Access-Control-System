from fastapi import APIRouter

from app.api.v1 import ai, auth, directory, events, health, integrations, realtime, settings, users, webhooks
from app.simulation.router import router as simulation_router

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(directory.router, tags=["directory"])
api_router.include_router(events.router, tags=["events"])
api_router.include_router(integrations.router, prefix="/integrations", tags=["integrations"])
api_router.include_router(realtime.router, prefix="/realtime", tags=["realtime"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
api_router.include_router(simulation_router, prefix="/simulation", tags=["simulation"])
