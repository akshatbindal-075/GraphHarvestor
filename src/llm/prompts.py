"""
src/llm/prompts.py
-------------------
One extraction prompt per schema type.

Rules enforced in every prompt:
  - Return ONLY valid JSON — no markdown fences, no commentary.
  - If a field cannot be found in the text, set it to null.
  - Never invent data not present in the source text.
"""

from __future__ import annotations

PROMPTS: dict[str, str] = {
    "STARTUP": """\
You are a data extraction engine. Extract startup information from the text below.
Return ONLY valid JSON — no markdown fences, no explanation, no extra keys.
If a field cannot be found, set it to null. Never invent data not in the text.

Required JSON schema:
{
  "schemaVersion": "1.0",
  "recordType": "STARTUP",
  "source": {"name": "<source site name>", "url": "<source URL or null>"},
  "content": {
    "entityName": "<company name>",
    "description": "<one-line description or null>",
    "website": "<website URL or null>",
    "data": {
      "employeeCount": <integer or null>,
      "founded": <year integer or null>,
      "location": "<city, country or null>"
    }
  },
  "collectedAt": "<ISO-8601 timestamp>"
}

Text to extract from:
{text}
""",

    "PRODUCT": """\
You are a data extraction engine. Extract product information from the text below.
Return ONLY valid JSON — no markdown fences, no explanation, no extra keys.
If a field cannot be found, set it to null. Never invent data not in the text.
pricingModel must be exactly one of: FREE, FREEMIUM, PAID, ENTERPRISE.

Required JSON schema:
{
  "schemaVersion": "1.0",
  "recordType": "PRODUCT",
  "source": {"name": "<source site name>", "url": "<source URL or null>"},
  "content": {
    "startupName": "<product or company name>",
    "tagline": "<short description or null>",
    "website": "<website URL or null>",
    "pricingModel": "FREE|FREEMIUM|PAID|ENTERPRISE"
  },
  "collectedAt": "<ISO-8601 timestamp>"
}

Text to extract from:
{text}
""",

    "RESEARCH_PAPER": """\
You are a data extraction engine. Extract research paper metadata from the text below.
Return ONLY valid JSON — no markdown fences, no explanation, no extra keys.
If a field cannot be found, set it to null. Never invent data not in the text.
published_date must be ISO-8601 format (e.g. "2024-01-15T00:00:00+00:00").

Required JSON schema:
{
  "schemaVersion": "1.0",
  "recordType": "RESEARCH_PAPER",
  "content": {
    "title": "<paper title>",
    "authors": ["<author 1>", "<author 2>"],
    "paper_url": "<arXiv or paper URL or null>",
    "github_url": "<GitHub repo URL or null>",
    "github_stars": <integer or null>,
    "published_date": "<ISO-8601 timestamp or null>"
  },
  "collectedAt": "<ISO-8601 timestamp>"
}

Text to extract from:
{text}
""",

    "JOB": """\
You are a data extraction engine. Extract job listing information from the text below.
Return ONLY valid JSON — no markdown fences, no explanation, no extra keys.
If a field cannot be found, set it to null. Never invent data not in the text.
is_remote must be a boolean (true/false).
date must be ISO-8601 format or null.
role_family must be one of: ML Engineering, Data Science, AI Research, Data Engineering,
  LLM/GenAI, Software Engineering, Product, DevOps/Infra, Design, Sales/BD, Other.

Required JSON schema:
{
  "schemaVersion": "1.0",
  "recordType": "JOB",
  "content": {
    "company": "<company name>",
    "title": "<job title>",
    "date": "<ISO-8601 timestamp or null>",
    "is_remote": true,
    "role_family": "<role family string>"
  },
  "collectedAt": "<ISO-8601 timestamp>"
}

Text to extract from:
{text}
""",

    "NEWS": """\
You are a data extraction engine. Extract news article metadata from the text below.
Return ONLY valid JSON — no markdown fences, no explanation, no extra keys.
If a field cannot be found, set it to null.

Required JSON schema:
{
  "schemaVersion": "1.0",
  "recordType": "NEWS",
  "source": {"name": "<publication name>", "url": "<article URL or null>"},
  "content": {
    "title": "<article title>",
    "summary": "<2-3 sentence summary of key facts>",
    "publishedAt": "<ISO-8601 timestamp or null>",
    "entities_mentioned": ["<company or person name>"]
  },
  "collectedAt": "<ISO-8601 timestamp>"
}

Text to extract from:
{text}
""",
}


def get_prompt(schema_type: str, text: str) -> str:
    """Return the filled extraction prompt for *schema_type*.

    Parameters
    ----------
    schema_type:
        One of STARTUP, PRODUCT, RESEARCH_PAPER, JOB, NEWS.
    text:
        The raw text to extract from (already chunked/truncated).
    """
    template = PROMPTS.get(schema_type.upper())
    if template is None:
        raise ValueError(f"Unknown schema_type: {schema_type!r}. Choose from {list(PROMPTS)}")
    return template.replace("{text}", text)
