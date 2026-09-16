"""LLM provider interface for Numby (Phase 7).

``AIProvider`` is the documented interface: ``complete(system, messages)``
returns the assistant's reply text. Two implementations ship:

- ``NotConfiguredProvider`` — honest degradation when ``AI_API_KEY`` is
  unset. The responder falls back to deterministic grounded templates.
- ``OpenAICompatibleProvider`` — POSTs to an OpenAI-style
  ``/chat/completions`` endpoint using the curl binary (the same
  proxy-safe transport pattern as the nflverse/odds providers).

``AI_PROVIDER`` selects the endpoint:

- ``openai`` (default) -> https://api.openai.com/v1
- any ``https://...`` value is used verbatim as the base URL

``AI_MODEL`` selects the model (default ``gpt-4o-mini``). The key comes
only from ``AI_API_KEY`` — it is never logged.

Even when an LLM is configured, it only ever sees the grounded context
bundle plus a strict system prompt; it is instructed to say "I don't have
that data" rather than invent numbers.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class AIProvider(ABC):
    """Interface every Numby LLM provider implements."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name for status reporting."""

    @abstractmethod
    def complete(self, system: str, messages: List[Dict[str, str]]) -> str:
        """Return the assistant's reply text for the conversation."""


class NotConfiguredProvider(AIProvider):
    """Honest fallback: no AI provider is configured."""

    @property
    def name(self) -> str:
        return "not_configured"

    def complete(self, system: str, messages: List[Dict[str, str]]) -> str:
        raise RuntimeError("AI provider is not configured (AI_API_KEY is unset).")


class OpenAICompatibleProvider(AIProvider):
    """OpenAI-style chat completions over HTTPS (curl transport)."""

    def __init__(self, api_key: str, base_url: str, model: str):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model

    @property
    def name(self) -> str:
        return f"openai_compatible:{self._base_url}"

    def build_payload(
        self, system: str, messages: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """Build the chat-completions request body (pure function, testable)."""
        chat_messages = [{"role": "system", "content": system}]
        chat_messages.extend(
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages
        )
        return {
            "model": self._model,
            "messages": chat_messages,
            "temperature": 0.2,
            "max_tokens": 600,
        }

    @staticmethod
    def parse_response(body: Dict[str, Any]) -> str:
        """Extract the assistant text from a chat-completions response body."""
        try:
            return str(body["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError, AttributeError):
            raise RuntimeError("Unexpected chat-completions response shape.")

    def complete(self, system: str, messages: List[Dict[str, str]]) -> str:
        payload = self.build_payload(system, messages)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            json.dump(payload, tmp)
            payload_path = tmp.name
        try:
            proc = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "--max-time",
                    "60",
                    "-X",
                    "POST",
                    f"{self._base_url}/chat/completions",
                    "-H",
                    "Content-Type: application/json",
                    "-H",
                    f"Authorization: Bearer {self._api_key}",
                    "--data-binary",
                    f"@{payload_path}",
                ],
                capture_output=True,
                text=True,
                timeout=70,
            )
        finally:
            try:
                os.unlink(payload_path)
            except OSError:
                pass
        if proc.returncode != 0:
            raise RuntimeError(f"AI provider request failed: {proc.stderr[:200]}")
        try:
            body = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise RuntimeError("AI provider returned non-JSON output.")
        if isinstance(body, dict) and body.get("error"):
            raise RuntimeError(f"AI provider error: {str(body['error'])[:200]}")
        return self.parse_response(body)


def resolve_base_url(ai_provider: Optional[str]) -> str:
    """Map AI_PROVIDER to a chat-completions base URL."""
    if not ai_provider or ai_provider.strip().lower() == "openai":
        return "https://api.openai.com/v1"
    candidate = ai_provider.strip()
    if candidate.startswith("https://"):
        return candidate
    raise ValueError(
        "AI_PROVIDER must be 'openai' or an https:// base URL for an "
        "OpenAI-compatible endpoint."
    )


def get_provider() -> AIProvider:
    """Return the configured provider, or the honest not-configured fallback."""
    from ..config import get_settings

    settings = get_settings()
    api_key = (settings.AI_API_KEY or "").strip()
    if not api_key:
        return NotConfiguredProvider()
    try:
        base_url = resolve_base_url(settings.AI_PROVIDER)
    except ValueError:
        return NotConfiguredProvider()
    model = (settings.AI_MODEL or "gpt-4o-mini").strip() or "gpt-4o-mini"
    return OpenAICompatibleProvider(api_key=api_key, base_url=base_url, model=model)


def provider_status() -> Dict[str, Any]:
    """Honest status object for the Numby status endpoint."""
    provider = get_provider()
    configured = not isinstance(provider, NotConfiguredProvider)
    return {
        "ai_configured": configured,
        "provider": provider.name,
        "mode": "llm" if configured else "basic",
        "note": (
            "LLM-enhanced answers."
            if configured
            else "AI provider not configured (AI_API_KEY unset). Numby answers "
            "from stored platform data with deterministic templates."
        ),
    }
