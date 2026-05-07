"""Runtime mode and provider readiness helpers for Alfred."""

from __future__ import annotations

from typing import Any


DEFAULT_MODE = "v3"
NON_AGENT_PROVIDERS = {"local"}


def agent_mode(runtime: Any) -> str:
    return DEFAULT_MODE


def provider_agent_capability(runtime: Any, provider_name: str) -> dict[str, Any]:
    provider = str(provider_name or "").strip().lower()
    configured = _provider_configured(runtime, provider)
    local_limited = provider in NON_AGENT_PROVIDERS
    return {
        "provider": provider,
        "configured": configured,
        "agent_capable": bool(configured and not local_limited),
        "local_provider_limited": local_limited,
        "reason": _capability_reason(provider, configured, local_limited),
    }


def agent_status_payload(runtime: Any, *, memory_status: dict[str, Any] | None = None) -> dict[str, Any]:
    mode = agent_mode(runtime)
    active_provider = str(getattr(runtime, "llm_provider", "local") or "local").strip().lower()
    capability = provider_agent_capability(runtime, active_provider)
    return {
        "active_mode": mode,
        "rollback_available": False,
        "provider": active_provider,
        "provider_capability": capability,
        "v3_ready": mode == DEFAULT_MODE and capability["agent_capable"],
        "local_provider_limitation": (
            "The local provider is retained for diagnostics and summaries, but it is not capable of Alfred 3.0 free-form agent chat."
            if capability["local_provider_limited"]
            else ""
        ),
        "memory": memory_status or {"enabled": True, "backend": "postgres_json"},
    }


def _provider_configured(runtime: Any, provider: str) -> bool:
    if provider == "local":
        return True
    if provider == "openai":
        return bool(getattr(runtime, "openai_api_key", ""))
    if provider == "gemini":
        return bool(getattr(runtime, "gemini_api_key", ""))
    if provider in {"claude", "anthropic"}:
        return bool(getattr(runtime, "anthropic_api_key", ""))
    if provider == "ollama":
        return bool(getattr(runtime, "ollama_base_url", ""))
    return False


def _capability_reason(provider: str, configured: bool, local_limited: bool) -> str:
    if local_limited:
        return "local_provider_non_agent"
    if not configured:
        return "provider_not_configured"
    if not provider:
        return "provider_missing"
    return "ready"
