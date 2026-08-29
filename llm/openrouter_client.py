"""
llm/openrouter_client.py
------------------------
Thin wrapper around the OpenAI SDK pointing at the OpenRouter API.

OpenRouter exposes an OpenAI-compatible endpoint, so we reuse the
``openai`` package and just override the base URL and API key.

Usage
-----
    from llm.openrouter_client import OpenRouterClient

    client = OpenRouterClient()
    reply = client.chat("What is a knowledge graph?")
"""

from __future__ import annotations

from typing import Any

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from utils.config import settings
from utils.logger import get_logger

log = get_logger(__name__)

# Default model available on OpenRouter (change as needed)
DEFAULT_MODEL = "mistralai/mistral-7b-instruct"


class OpenRouterClient:
    """OpenAI-compatible client routed through OpenRouter."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str = DEFAULT_MODEL,
    ) -> None:
        self._client = OpenAI(
            api_key=api_key or settings.openrouter_api_key,
            base_url=base_url or settings.openrouter_base_url,
        )
        self.default_model = default_model

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        reraise=True,
    )
    def chat(
        self,
        prompt: str | list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> str:
        """Send a chat completion request and return the response text.

        Parameters
        ----------
        prompt:
            Either a plain string (converted to a user message) or a full
            ``messages`` list in OpenAI format.
        model:
            Model identifier (defaults to ``self.default_model``).
        temperature:
            Sampling temperature.
        max_tokens:
            Maximum tokens in the completion.

        Returns
        -------
        str
            The assistant's reply text.
        """
        messages: list[dict[str, str]] = (
            [{"role": "user", "content": prompt}]
            if isinstance(prompt, str)
            else prompt
        )
        model = model or self.default_model
        log.debug("OpenRouter request | model={model} | tokens≤{max_tokens}", model=model, max_tokens=max_tokens)

        response = self._client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        content = response.choices[0].message.content or ""
        log.debug("OpenRouter response | chars={n}", n=len(content))
        return content

    def models(self) -> list[str]:
        """Return a list of model IDs available on OpenRouter."""
        return [m.id for m in self._client.models.list().data]
