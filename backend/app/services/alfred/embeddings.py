"""Embedding helpers for Alfred semantic memory."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

import httpx

from app.core.logging import get_logger
from app.services.settings import get_runtime_config

logger = get_logger(__name__)

ALFRED_EMBEDDING_DIMENSION = 1536


async def generate_embedding(text: str, *, purpose: str = "alfred_semantic_memory") -> list[float] | None:
    """Generate an embedding for Alfred memory/learning text.

    The function is deliberately fail-soft: provider errors disable only the
    semantic enhancement, never the core Alfred turn or exact memory storage.
    """

    runtime = await get_runtime_config()
    if not bool(getattr(runtime, "alfred_semantic_memory_enabled", True)):
        return None
    normalized = normalize_embedding_text(text)
    if not normalized:
        return None

    provider = str(getattr(runtime, "alfred_embedding_provider", "openai") or "openai").strip().lower()
    if provider == "disabled":
        return None
    dimension = int(getattr(runtime, "alfred_embedding_dimension", ALFRED_EMBEDDING_DIMENSION) or ALFRED_EMBEDDING_DIMENSION)
    if dimension != ALFRED_EMBEDDING_DIMENSION:
        logger.info(
            "alfred_embedding_dimension_unsupported",
            extra={"configured_dimension": dimension, "expected_dimension": ALFRED_EMBEDDING_DIMENSION},
        )
        return None

    try:
        if provider == "openai":
            return await _openai_embedding(runtime, normalized, purpose=purpose)
        if provider == "ollama":
            return await _ollama_embedding(runtime, normalized, purpose=purpose)
        if provider == "local":
            return _local_embedding(normalized, dimension=ALFRED_EMBEDDING_DIMENSION)
    except Exception as exc:
        logger.info(
            "alfred_embedding_generation_failed",
            extra={"provider": provider, "purpose": purpose, "error": str(exc)[:180]},
        )
        return None

    logger.info("alfred_embedding_provider_unsupported", extra={"provider": provider})
    return None


def normalize_embedding_text(text: str) -> str:
    """Compact text before sending it to an embedding provider."""

    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    return normalized[:8000]


def embedding_text(*parts: Any) -> str:
    """Join public, non-empty text fragments for embedding."""

    return normalize_embedding_text(" ".join(str(part) for part in parts if part not in (None, "", [], {})))


def vector_literal(vector: list[float]) -> str:
    """Return a pgvector literal for safe bind-parameter casting."""

    return "[" + ",".join(f"{float(value):.8f}" for value in vector[:ALFRED_EMBEDDING_DIMENSION]) + "]"


async def _openai_embedding(runtime: Any, text: str, *, purpose: str) -> list[float] | None:
    api_key = str(getattr(runtime, "openai_api_key", "") or "")
    if not api_key:
        return None
    base_url = str(getattr(runtime, "openai_base_url", "https://api.openai.com/v1") or "").rstrip("/")
    model = str(getattr(runtime, "alfred_embedding_model", "text-embedding-3-small") or "text-embedding-3-small")
    timeout = min(float(getattr(runtime, "llm_timeout_seconds", 45.0) or 45.0), 15.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.post(
            f"{base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"model": model, "input": text, "encoding_format": "float", "user": purpose[:64]},
        )
    if response.status_code >= 400:
        raise RuntimeError(f"openai embeddings returned {response.status_code}: {response.text[:240]}")
    data = response.json()
    embedding = (data.get("data") or [{}])[0].get("embedding")
    return _validated_embedding(embedding)


async def _ollama_embedding(runtime: Any, text: str, *, purpose: str) -> list[float] | None:
    base_url = str(getattr(runtime, "ollama_base_url", "") or "").rstrip("/")
    if not base_url:
        return None
    model = str(getattr(runtime, "alfred_embedding_model", "") or getattr(runtime, "ollama_model", "") or "nomic-embed-text")
    timeout = min(float(getattr(runtime, "llm_timeout_seconds", 45.0) or 45.0), 15.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.post(f"{base_url}/api/embeddings", json={"model": model, "prompt": text})
    if response.status_code >= 400:
        raise RuntimeError(f"ollama embeddings returned {response.status_code}: {response.text[:240]}")
    data = response.json()
    logger.debug("alfred_ollama_embedding_generated", extra={"purpose": purpose})
    return _validated_embedding(data.get("embedding"))


def _validated_embedding(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != ALFRED_EMBEDDING_DIMENSION:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _local_embedding(text: str, *, dimension: int) -> list[float]:
    vector = [0.0] * dimension
    tokens = re.findall(r"[a-z0-9][a-z0-9_-]{1,}", text.lower())
    for token in tokens[:768]:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimension
        sign = -1.0 if digest[4] & 1 else 1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]
