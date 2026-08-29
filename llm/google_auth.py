"""
llm/google_auth.py
------------------
Google OAuth 2.0 helper using the client-secret JSON file.

The first call opens a local browser window for consent. The resulting
token is cached at ``credentials/token.json`` and refreshed automatically
on subsequent calls.

Usage
-----
    from llm.google_auth import GoogleAuthClient

    auth = GoogleAuthClient(scopes=["https://www.googleapis.com/auth/drive.readonly"])
    creds = auth.get_credentials()
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from utils.config import settings
from utils.logger import get_logger

log = get_logger(__name__)

_TOKEN_PATH = Path("credentials/token.json")


class GoogleAuthClient:
    """Manages Google OAuth 2.0 credentials with token caching."""

    def __init__(
        self,
        scopes: Sequence[str],
        credentials_json: Path | None = None,
        token_path: Path = _TOKEN_PATH,
    ) -> None:
        self.scopes = list(scopes)
        self.credentials_json = credentials_json or settings.google_credentials_json
        self.token_path = token_path

    def get_credentials(self) -> Credentials:
        """Return valid credentials, refreshing or re-authorising as needed.

        Returns
        -------
        google.oauth2.credentials.Credentials
            Ready-to-use OAuth credentials.
        """
        creds: Credentials | None = None

        # Load cached token if it exists
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(
                str(self.token_path), self.scopes
            )
            log.debug("Loaded cached Google token from {path}", path=self.token_path)

        # Refresh expired token
        if creds and creds.expired and creds.refresh_token:
            log.info("Refreshing expired Google token …")
            creds.refresh(Request())

        # Full OAuth flow if no valid token
        if not creds or not creds.valid:
            if not self.credentials_json.exists():
                raise FileNotFoundError(
                    f"Google credentials JSON not found: {self.credentials_json}\n"
                    "Download it from: https://console.cloud.google.com/apis/credentials"
                )
            log.info("Starting Google OAuth flow …")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.credentials_json), self.scopes
            )
            creds = flow.run_local_server(port=8080, open_browser=True)

        # Cache the token for next time
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(creds.to_json(), encoding="utf-8")
        log.info("Google credentials valid. Token cached at {path}", path=self.token_path)
        return creds
