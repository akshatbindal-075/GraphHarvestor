"""
llm/groq_client.py
------------------
Wrapper around the official Groq Python SDK.

Groq provides ultra-fast inference for open-weight models (Llama, Mixtral,
Gemma). The interface mirrors ``OpenRouterClient`` for drop-in swappability.

Usage
-----
    from llm.groq_client import GroqClient

    client = GroqClient()
    reply = client.chat("Summarise this text in 3 bullet points.")
"""

from __future__ import annotations

from typing import Any

from groq import Groq
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from utils.config import settings
from utils.logger import get_logger

log = get_logger(__name__)

DEFAULT_MODEL = "llama-3.3-70b-versatile"


class GroqClient:
    """Groq inference client with automatic retries."""

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = DEFAULT_MODEL,
    ) -> None:
        self._client = Groq(api_key=api_key or settings.groq_api_key)
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
            Either a plain string (user message) or a full messages list.
        model:
            Groq model ID. Defaults to ``llama-3.3-70b-versatile``.
        temperature:
            Sampling temperature.
        max_tokens:
            Maximum completion tokens.

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
        log.debug("Groq request | model={model} | tokens≤{max_tokens}", model=model, max_tokens=max_tokens)

        response = self._client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        content = response.choices[0].message.content or ""
        log.debug("Groq response | chars={n}", n=len(content))
        return content
