"""LLM provider client for the Task Automation Engine.

Calls an OpenAI-compatible chat-completions endpoint when ``LLM_PROVIDER_URL``
is configured. Falls back to ``None`` (so callers can use their own heuristic)
when unconfigured or the call fails.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _headers(settings: Settings) -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    return headers


def _chat_payload(model: str, messages: list[dict[str, str]], temperature: float = 0.3) -> dict[str, Any]:
    return {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 256,
    }


def _extract_text(data: dict[str, Any]) -> str | None:
    try:
        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            return (message.get("content") or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def _clean_fenced_json(content: str) -> str:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    return cleaned


def complete(
    prompt: str,
    *,
    system: str = "You are a helpful, concise assistant for a mortgage CRM.",
    settings: Settings | None = None,
) -> str | None:
    """Send a single-turn prompt to the configured LLM and return the trimmed response.

    Returns ``None`` when no provider is configured or the call fails, so the
    caller can fall back to a deterministic stub.
    """
    settings = settings or get_settings()
    if not settings.llm_provider_url:
        return None

    payload = _chat_payload(
        settings.llm_model,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )

    try:
        with httpx.Client(timeout=settings.llm_timeout_s) as client:
            resp = client.post(
                settings.llm_provider_url,
                headers=_headers(settings),
                json=payload,
            )
        if resp.is_success:
            return _extract_text(resp.json())
        logger.warning("llm_completion_failed", extra={"status": resp.status_code, "body": resp.text})
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_completion_error", extra={"error": str(exc)})
    return None


def complete_json(
    prompt: str,
    *,
    system: str = "You are a helpful, concise assistant for a mortgage CRM. Return only valid JSON.",
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """Send a single-turn prompt and parse the response as JSON.

    Returns ``None`` when no provider is configured, the call fails, or the
    response cannot be parsed as JSON.
    """
    content = complete(prompt, system=system, settings=settings)
    if not content:
        return None
    try:
        return json.loads(_clean_fenced_json(content))
    except Exception:  # noqa: BLE001
        logger.warning("llm_json_parse_failed", extra={"content": content})
    return None


def classify(
    text: str,
    labels: list[str],
    *,
    system: str | None = None,
    settings: Settings | None = None,
) -> tuple[str, float] | None:
    """Ask the LLM to classify ``text`` into one of ``labels``.

    Returns ``(label, confidence)`` on a successful, valid response, otherwise
    ``None`` so the caller can fall back to local heuristics.
    """
    settings = settings or get_settings()
    if not settings.llm_provider_url:
        return None

    joined = ", ".join(labels)
    prompt = (
        f"Classify the following message into exactly one of these labels: {joined}.\n\n"
        f"Message: {text}\n\n"
        "Return only a JSON object with two keys: \"label\" (one of the allowed labels) and \"confidence\" (0.0–1.0)."
    )
    default_system = (
        "You are a classification assistant for a mortgage CRM. "
        "Return only the requested JSON with no extra prose."
    )
    content = complete(prompt, system=system or default_system, settings=settings)
    if not content:
        return None

    # Try to parse JSON; if the model returns fenced JSON, strip the fences.
    cleaned = content
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        parsed = json.loads(cleaned)
        label = str(parsed.get("label", "")).strip()
        if label not in labels:
            return None
        confidence = float(parsed.get("confidence", 0.0))
        return label, max(0.0, min(1.0, confidence))
    except Exception:  # noqa: BLE001
        # Some providers return just the label word; accept it with a neutral confidence.
        stripped = content.strip().lower()
        for label in labels:
            if label in stripped:
                return label, 0.85
    return None
