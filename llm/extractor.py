"""
llm/extractor.py
----------------
Entity and relation extraction from text using any LLM client.

The extractor sends a structured prompt asking the model to return JSON in
the following schema::

    {
      "entities": [
        {"id": "e1", "name": "Albert Einstein", "type": "Person"},
        ...
      ],
      "relations": [
        {"source": "e1", "target": "e2", "relation": "developed"},
        ...
      ]
    }

Usage
-----
    from llm.extractor import extract_graph
    from llm.groq_client import GroqClient

    client = GroqClient()
    result = extract_graph("Einstein developed the theory of relativity.", client)
    # result == {"entities": [...], "relations": [...]}
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from tenacity import retry, stop_after_attempt, wait_exponential

from utils.logger import get_logger

log = get_logger(__name__)

# ── Protocol so any client (OpenRouter, Groq, …) is accepted ─────────────────

class LLMClient(Protocol):
    def chat(self, prompt: str | list[dict[str, str]], **kwargs: Any) -> str: ...


# ── Prompt ───────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a knowledge-graph extraction engine.
Given a passage of text, extract all named entities and the relationships between them.

Return ONLY valid JSON matching this exact schema — no markdown, no explanation:
{
  "entities": [
    {"id": "<short_unique_id>", "name": "<entity name>", "type": "<Person|Organization|Location|Concept|Event|Product|Other>"}
  ],
  "relations": [
    {"source": "<entity_id>", "target": "<entity_id>", "relation": "<verb or short phrase>"}
  ]
}

Rules:
- Use short snake_case IDs (e.g. "e1", "org_2").
- Relation must be a concise verb phrase (e.g. "founded", "located_in", "acquired").
- Include only entities and relations explicitly stated or strongly implied in the text.
- If nothing can be extracted, return {"entities": [], "relations": []}.
"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json(raw: str) -> dict[str, Any]:
    """Extract and parse the first JSON object from *raw*."""
    match = _JSON_RE.search(raw)
    if not match:
        raise ValueError(f"No JSON object found in LLM response:\n{raw[:500]}")
    return json.loads(match.group())


# ── Public API ────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15), reraise=True)
def extract_graph(
    text: str,
    client: LLMClient,
    *,
    model: str | None = None,
    max_tokens: int = 1024,
) -> dict[str, list[dict[str, str]]]:
    """Extract entities and relations from *text* using *client*.

    Parameters
    ----------
    text:
        The passage to analyse (should be a single chunk, ≤ ~1500 tokens).
    client:
        Any LLM client implementing ``chat(prompt, **kwargs) -> str``.
    model:
        Optional model override forwarded to the client.
    max_tokens:
        Maximum completion tokens.

    Returns
    -------
    dict
        ``{"entities": [...], "relations": [...]}``

    Raises
    ------
    ValueError
        If the LLM response cannot be parsed as valid JSON after retries.
    """
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"Extract from this text:\n\n{text}"},
    ]
    kwargs: dict[str, Any] = {"max_tokens": max_tokens}
    if model:
        kwargs["model"] = model

    log.debug("Extracting graph from {n} chars …", n=len(text))
    raw = client.chat(messages, **kwargs)

    try:
        result = _parse_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        log.warning("JSON parse failed ({err}), retrying …", err=exc)
        raise

    entities = result.get("entities", [])
    relations = result.get("relations", [])
    log.info(
        "Extracted {e} entities, {r} relations",
        e=len(entities),
        r=len(relations),
    )
    return {"entities": entities, "relations": relations}
