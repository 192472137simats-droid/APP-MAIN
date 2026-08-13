"""Gemini access over the REST API.

Deliberately no SDK: one httpx call, a stable endpoint, and structured output
enforced server-side via responseSchema so we never parse prose into JSON.
"""

import asyncio
import base64
import json
import re
from typing import Any

import httpx

from ..config import (
    GEMINI_API_KEY,
    GEMINI_ENDPOINT,
    GEMINI_MODEL,
    REQUEST_TIMEOUT,
)


class GeminiError(Exception):
    """Raised when the model call fails or returns something unusable."""


def _headers() -> dict[str, str]:
    return {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}


def _require_key() -> None:
    if not GEMINI_API_KEY:
        raise GeminiError(
            "GEMINI_API_KEY is not set. Add it to your .env file locally, or to "
            "the service variables in Railway."
        )


# --------------------------------------------------------------------------
# Model discovery
#
# Google retires model aliases without warning - a hardcoded name that worked
# last month returns 404 "no longer available to new users" today. So we ask
# the API what this key can actually call, and pick the best option.
# --------------------------------------------------------------------------

# Models that exist but cannot do what we need (or are not text generators).
_EXCLUDED = (
    "embedding", "aqa", "imagen", "veo", "tts", "image", "audio",
    "learnlm", "gemma", "live",
)

_resolved_model: str | None = None

# Appearing in ListModels does NOT mean the key may call it: retired aliases
# stay listed but return 404 on generateContent. Anything that actually fails
# gets recorded here so we stop selecting it.
_failed_models: set[str] = set()


def _rank(name: str) -> tuple:
    """Sort key: usable > stable > newest > flash (cheap) > non-lite."""
    lowered = name.lower()
    if any(term in lowered for term in _EXCLUDED):
        return (-1, 0, 0, 0, 0)

    version = re.search(r"(\d+)(?:[.\-](\d+))?", lowered)
    major = int(version.group(1)) if version else 0
    minor = int(version.group(2)) if version and version.group(2) else 0

    stable = 0 if any(t in lowered for t in ("preview", "exp", "experimental")) else 1
    flash = 1 if "flash" in lowered else 0
    full = 0 if "lite" in lowered else 1

    return (1, stable, major, minor, flash * 2 + full)


async def available_models() -> list[dict[str, Any]]:
    """Every model this API key may call with generateContent."""
    _require_key()
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(
            GEMINI_ENDPOINT, headers=_headers(), params={"pageSize": 200}
        )
    if response.status_code != 200:
        raise GeminiError(f"Could not list models - HTTP {response.status_code}: {response.text[:300]}")

    models = response.json().get("models") or []
    return [
        {
            "name": m.get("name", "").removeprefix("models/"),
            "display_name": m.get("displayName", ""),
            "input_token_limit": m.get("inputTokenLimit"),
            "output_token_limit": m.get("outputTokenLimit"),
        }
        for m in models
        if "generateContent" in (m.get("supportedGenerationMethods") or [])
    ]


async def resolve_model(force: bool = False) -> str:
    """The model we will actually call, verified against the live model list."""
    global _resolved_model
    if _resolved_model and not force:
        return _resolved_model

    models = await available_models()
    usable = [m["name"] for m in models if m["name"] not in _failed_models]
    if not usable:
        raise GeminiError(
            "No callable models left for this API key"
            + (f" (tried and failed: {sorted(_failed_models)})" if _failed_models else "")
        )

    if GEMINI_MODEL in usable:
        _resolved_model = GEMINI_MODEL
    else:
        ranked = sorted(usable, key=_rank, reverse=True)
        best = ranked[0]
        if _rank(best)[0] < 0:
            raise GeminiError(f"No suitable text model available. Saw: {usable[:10]}")
        _resolved_model = best
    return _resolved_model


def resolved_model_name() -> str | None:
    """Whatever we last resolved, without triggering a network call."""
    return _resolved_model


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


async def _call(payload: dict[str, Any], model: str | None = None) -> str:
    _require_key()

    target = model or await resolve_model()
    last_error = "unknown error"

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for attempt in range(3):
            url = f"{GEMINI_ENDPOINT}/{target}:generateContent"
            try:
                response = await client.post(url, headers=_headers(), json=payload)
            except httpx.RequestError as exc:
                last_error = f"network error: {exc}"
                await asyncio.sleep(2 * (attempt + 1))
                continue

            if response.status_code == 200:
                data = response.json()
                candidates = data.get("candidates") or []
                if not candidates:
                    feedback = data.get("promptFeedback", {})
                    last_error = f"no candidates returned ({feedback})"
                    await asyncio.sleep(2)
                    continue

                parts = candidates[0].get("content", {}).get("parts") or []
                text = "".join(part.get("text", "") for part in parts).strip()
                if text:
                    return text

                # Empty text is usually MAX_TOKENS (thinking ate the budget) or
                # a safety stop. Retry rather than losing the whole upload.
                reason = candidates[0].get("finishReason", "unknown")
                last_error = f"empty response (finishReason={reason})"
                await asyncio.sleep(2)
                continue

            # A retired model 404s even though ListModels still advertises it.
            # Blacklist it and pick the next best rather than failing the upload.
            if response.status_code == 404 and model is None:
                last_error = f"model {target} unavailable: {response.text[:200]}"
                _failed_models.add(target)
                target = await resolve_model(force=True)
                continue

            if response.status_code in (429, 500, 502, 503, 504):
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                await asyncio.sleep(3 * (attempt + 1))
                continue

            raise GeminiError(f"HTTP {response.status_code}: {response.text[:500]}")

    raise GeminiError(f"Gemini call failed after retries - {last_error}")


async def generate_json(
    prompt: str,
    schema: dict[str, Any],
    *,
    temperature: float = 0.2,
    thinking_budget: int | None = None,
    model: str | None = None,
) -> Any:
    """Ask for JSON matching `schema` and return it already parsed."""
    generation_config: dict[str, Any] = {
        "temperature": temperature,
        "responseMimeType": "application/json",
        "responseSchema": schema,
    }
    if thinking_budget is not None:
        generation_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }
    raw = await _call(payload, model=model)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GeminiError(f"Model did not return valid JSON: {raw[:300]}") from exc


async def generate_from_images(
    prompt: str,
    images: list[bytes],
    *,
    mime_type: str = "image/jpeg",
    temperature: float = 0.0,
    model: str | None = None,
) -> str:
    """Vision call - used to transcribe scanned pages that hold no text layer."""
    parts: list[dict[str, Any]] = [{"text": prompt}]
    for image in images:
        parts.append(
            {
                "inline_data": {
                    "mime_type": mime_type,
                    "data": base64.b64encode(image).decode("ascii"),
                }
            }
        )
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": temperature},
    }
    return await _call(payload, model=model)


async def generate_text(
    prompt: str,
    *,
    history: list[dict[str, str]] | None = None,
    temperature: float = 0.3,
    model: str | None = None,
) -> str:
    """Plain text generation, with optional prior turns for chat."""
    contents: list[dict[str, Any]] = []
    for message in history or []:
        role = "user" if message["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": message["content"]}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    payload = {
        "contents": contents,
        "generationConfig": {"temperature": temperature},
    }
    return await _call(payload, model=model)
