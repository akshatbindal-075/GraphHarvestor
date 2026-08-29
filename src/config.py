"""
src/config.py
-------------
Central configuration loader for GraphHarvester.

Reads all secrets from .env (never hardcoded).
Provides get_sheets_client() supporting both service_account and oauth auth,
controlled by GOOGLE_AUTH_METHOD env var.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.oauth2.service_account import Credentials as SACredentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

load_dotenv()

# ── Google Sheets ─────────────────────────────────────────────────────────────
GOOGLE_AUTH_METHOD: str = os.getenv("GOOGLE_AUTH_METHOD", "oauth").split("#")[0].strip()  # "oauth" | "service_account"
GOOGLE_SERVICE_ACCOUNT_FILE: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials/service_account.json")
GOOGLE_CREDENTIALS_JSON: str = os.getenv("GOOGLE_CREDENTIALS_JSON", "credentials/google_client_secret.json")
GOOGLE_TOKEN_FILE: str = os.getenv("GOOGLE_TOKEN_FILE", "credentials/token.json")
GOOGLE_SHEET_ID: str = os.getenv("GOOGLE_SHEET_ID", "")

_SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ── LLM providers ─────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")

# LLM fallback order: Gemini 3.6 Flash -> Groq Llama3 8B -> OpenRouter -> DeepSeek
LLM_MODELS = [
    "gemini/gemini-3.6-flash",               # Google's current recommended model
    "groq/llama3-8b-8192",                   # always available on Groq free tier
    "openrouter/meta-llama/llama-3.1-70b-instruct",  # OpenRouter fallback
    "deepseek/deepseek-chat",
]

# ── External APIs ─────────────────────────────────────────────────────────────
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
PRODUCT_HUNT_TOKEN: str = os.getenv("PRODUCT_HUNT_TOKEN", "")

# ── Scraping / concurrency ────────────────────────────────────────────────────
DEFAULT_SEMAPHORE: int = int(os.getenv("DEFAULT_SEMAPHORE", "10"))
REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "30"))

# ── Arxiv ─────────────────────────────────────────────────────────────────────
ARXIV_QUERY: str = os.getenv("ARXIV_QUERY", "artificial intelligence")
ARXIV_MAX_RESULTS: int = int(os.getenv("ARXIV_MAX_RESULTS", "1000"))

# ── Output paths ─────────────────────────────────────────────────────────────
RAW_DIR = Path("raw")
LOGS_DIR = Path("logs")


def get_sheets_client() -> gspread.Client:
    """Return an authenticated gspread client.

    Auth method is controlled by GOOGLE_AUTH_METHOD:
    - "service_account": reads GOOGLE_SERVICE_ACCOUNT_FILE (JSON key file)
    - "oauth" (default): runs InstalledAppFlow on first run, caches token
    """
    if GOOGLE_AUTH_METHOD == "service_account":
        creds = SACredentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_FILE, scopes=_SHEETS_SCOPES
        )
    else:
        creds: OAuthCredentials | None = None
        token_path = Path(GOOGLE_TOKEN_FILE)

        if token_path.exists():
            creds = OAuthCredentials.from_authorized_user_file(str(token_path), _SHEETS_SCOPES)

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                GOOGLE_CREDENTIALS_JSON, _SHEETS_SCOPES
            )
            creds = flow.run_local_server(port=8080)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return gspread.authorize(creds)
