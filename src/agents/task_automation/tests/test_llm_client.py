"""LLM client fallback, JSON parsing, and httpx-backed classify behavior."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from app.core.config import Settings
from app.integrations import llm_client


def _make_fake_client(*, status: int = 200, content: str | None = None, exc: Exception | None = None):
    """Return a class that replaces ``httpx.Client`` for a single request."""
    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ARG002
            pass

        def __enter__(self):
            mock = MagicMock()
            if exc is not None:
                mock.post.side_effect = exc
            else:
                response = MagicMock()
                response.status_code = status
                response.is_success = 200 <= status < 300
                response.text = content or ""
                response.json.return_value = {"choices": [{"message": {"content": content or ""}}]}
                mock.post.return_value = response
            return mock

        def __exit__(self, *args, **kwargs):  # noqa: ARG002
            return False

    return FakeClient


def _settings_with_provider() -> Settings:
    return Settings(
        env="test",
        api_key="test-key",
        llm_provider_url="http://mock-llm/v1/chat/completions",
        llm_api_key="sk-test",
        llm_model="gpt-4o-mini",
        llm_timeout_s=1.0,
    )


def test_complete_returns_none_when_provider_unset() -> None:
    """FR-013: unset provider falls back to None so callers can use a deterministic stub."""
    settings = Settings(env="test", api_key="test-key", llm_provider_url="")
    assert llm_client.complete("say hi", settings=settings) is None


def test_complete_json_returns_none_when_provider_unset() -> None:
    assert llm_client.complete_json("return json", settings=Settings(llm_provider_url="")) is None


def test_classify_returns_none_when_provider_unset() -> None:
    assert llm_client.classify("hello", ["call", "email"], settings=Settings(llm_provider_url="")) is None


def test_complete_calls_provider_and_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """complete() trims the OpenAI-compatible response and passes the API key."""
    captured: dict[str, Any] = {}

    class FakeClient(_make_fake_client(content="Call borrower by 5pm")):
        def __enter__(self):
            mock = super().__enter__()
            original_post = mock.post

            def _capture_post(url, *, json, headers, **kwargs):  # noqa: ARG001
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers
                return original_post(url, json=json, headers=headers)

            mock.post = _capture_post
            return mock

    monkeypatch.setattr(httpx, "Client", FakeClient)
    settings = _settings_with_provider()
    result = llm_client.complete("Draft a call task note for lead L-1", settings=settings)

    assert result == "Call borrower by 5pm"
    assert captured["url"] == settings.llm_provider_url
    assert captured["headers"].get("Authorization") == f"Bearer {settings.llm_api_key}"
    assert captured["json"]["model"] == settings.llm_model


def test_complete_returns_none_on_provider_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "Client", _make_fake_client(status=500, content="model overloaded"))
    result = llm_client.complete("hello", settings=_settings_with_provider())
    assert result is None


def test_complete_returns_none_on_provider_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "Client", _make_fake_client(exc=httpx.ConnectError("unreachable")))
    result = llm_client.complete("hello", settings=_settings_with_provider())
    assert result is None


@pytest.mark.parametrize("content", [
    '{"summary": "follow up today"}',
    '```json\n{"summary": "follow up today"}\n```',
    '```\n{"summary": "follow up today"}\n```',
])
def test_complete_json_parses_fenced_and_unfenced_json(content: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "Client", _make_fake_client(content=content))
    result = llm_client.complete_json("summarise", settings=_settings_with_provider())
    assert result == {"summary": "follow up today"}


def test_complete_json_returns_none_for_non_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "Client", _make_fake_client(content="not valid json"))
    result = llm_client.complete_json("return json", settings=_settings_with_provider())
    assert result is None


def test_classify_parses_label_and_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    content = '{"label": "call", "confidence": 0.92}'
    monkeypatch.setattr(httpx, "Client", _make_fake_client(content=content))
    result = llm_client.classify("call the borrower", ["call", "email"], settings=_settings_with_provider())
    assert result == ("call", 0.92)


def test_classify_clips_confidence_to_unit_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    content = '{"label": "call", "confidence": 1.25}'
    monkeypatch.setattr(httpx, "Client", _make_fake_client(content=content))
    label, confidence = llm_client.classify("x", ["call"], settings=_settings_with_provider())
    assert label == "call"
    assert confidence == 1.0


def test_classify_rejects_unknown_label(monkeypatch: pytest.MonkeyPatch) -> None:
    content = '{"label": "fax", "confidence": 0.9}'
    monkeypatch.setattr(httpx, "Client", _make_fake_client(content=content))
    result = llm_client.classify("x", ["call", "email"], settings=_settings_with_provider())
    assert result is None


def test_classify_fallback_to_label_match_when_not_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Classify falls back to a neutral 0.85 confidence if the model returns prose."""
    monkeypatch.setattr(httpx, "Client", _make_fake_client(content="The right label is email"))
    result = llm_client.classify("x", ["call", "email"], settings=_settings_with_provider())
    assert result == ("email", 0.85)


def test_classify_returns_none_when_no_label_found_in_prose(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "Client", _make_fake_client(content="I am not sure"))
    result = llm_client.classify("x", ["call", "email"], settings=_settings_with_provider())
    assert result is None


def test_classify_returns_none_on_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "Client", _make_fake_client(status=503))
    result = llm_client.classify("x", ["call"], settings=_settings_with_provider())
    assert result is None
