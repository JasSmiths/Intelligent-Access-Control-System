"""Provider discovery for configured camera image analysis."""

from typing import Any

from fastapi import APIRouter

from app.services.settings import get_runtime_config

router = APIRouter()


@router.get("/providers")
async def list_providers() -> dict[str, Any]:
    config = await get_runtime_config()
    return {
        "active": config.llm_provider,
        "available": ["local", "openai", "gemini", "claude", "ollama"],
        "models": {
            "openai": config.openai_model,
            "gemini": config.gemini_model,
            "claude": config.anthropic_model,
            "ollama": config.ollama_model,
        },
    }
