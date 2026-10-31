"""HTTP bridge to the optional local C++ structured-analysis service."""

from typing import Any

import httpx

from ..config import CPP_CORE_URL, REQUEST_TIMEOUT
from .llm_service import LLMError


async def analyse_paper(
    body: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Return structured paper analysis from the configured C++ core."""
    if not CPP_CORE_URL:
        raise LLMError("The C++ analysis service is not configured.")

    async def request(using: httpx.AsyncClient) -> httpx.Response:
        return await using.post(
            f"{CPP_CORE_URL}/api/core/analyze",
            content=body.encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )

    try:
        if client is None:
            async with httpx.AsyncClient(timeout=max(REQUEST_TIMEOUT, 190.0)) as using:
                response = await request(using)
        else:
            response = await request(client)
    except httpx.HTTPError as exc:
        raise LLMError("The C++ analysis service is unavailable.") from exc

    if response.status_code != 200:
        raise LLMError(
            f"The C++ analysis service returned HTTP {response.status_code}."
        )

    try:
        result = response.json()
    except ValueError as exc:
        raise LLMError("The C++ analysis service returned invalid JSON.") from exc

    if (
        not isinstance(result, dict)
        or not isinstance(result.get("metadata"), dict)
        or not isinstance(result.get("summary"), dict)
        or not isinstance(result["summary"].get("short"), str)
        or not result["summary"]["short"].strip()
        or not isinstance(result.get("gaps"), list)
    ):
        raise LLMError("The C++ analysis service returned an invalid analysis.")
    return result
