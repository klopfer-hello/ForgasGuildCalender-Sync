"""Google Calendar API wrapper with OAuth2 authentication."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar"]
_HTTP_TIMEOUT = 30  # seconds for all Google API calls


class GoogleCalendarClient:
    def __init__(self, token_path: Path, client_secrets_path: Path):
        self._token_path = token_path
        self._client_secrets_path = client_secrets_path
        self._creds: Credentials | None = None
        self._service = None

    @property
    def is_authenticated(self) -> bool:
        return self._creds is not None and self._creds.valid

    def load_credentials(self) -> bool:
        """Load saved credentials. Returns True if valid."""
        if not self._token_path.exists():
            return False
        self._creds = Credentials.from_authorized_user_file(
            str(self._token_path), _SCOPES
        )
        if self._creds and self._creds.expired and self._creds.refresh_token:
            try:
                self._creds.refresh(Request())
                self._save_token()
                log.info("OAuth token refreshed")
            except Exception:
                log.warning("Token refresh failed, re-auth needed")
                self._creds = None
                return False
        return self.is_authenticated

    def authenticate(self) -> bool:
        """Run OAuth2 flow (opens browser). Returns True on success."""
        if not self._client_secrets_path.exists():
            raise FileNotFoundError(
                f"client_secrets.json not found at {self._client_secrets_path}"
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(self._client_secrets_path), _SCOPES
        )
        self._creds = flow.run_local_server(port=0)
        self._save_token()
        return self.is_authenticated

    def logout(self):
        self._creds = None
        self._service = None
        if self._token_path.exists():
            self._token_path.unlink()

    def list_calendars(self) -> list[dict]:
        """Return list of {id, summary, primary}."""
        service = self._get_service()
        result = []
        page_token = None
        while True:
            response = (
                service.calendarList().list(pageToken=page_token).execute()
            )
            for item in response.get("items", []):
                result.append({
                    "id": item["id"],
                    "summary": item.get("summary", ""),
                    "primary": item.get("primary", False),
                })
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return result

    def create_event(
        self,
        calendar_id: str,
        summary: str,
        start: datetime,
        duration_hours: int,
        description: str = "",
        location: str = "",
    ) -> str:
        """Create a calendar event. Returns the Google event ID."""
        body = self._build_event_body(summary, start, duration_hours, description, location)
        event = (
            self._get_service()
            .events()
            .insert(calendarId=calendar_id, body=body)
            .execute()
        )
        return event["id"]

    def update_event(
        self,
        calendar_id: str,
        event_id: str,
        summary: str,
        start: datetime,
        duration_hours: int,
        description: str = "",
        location: str = "",
    ):
        """Update an existing calendar event."""
        body = self._build_event_body(summary, start, duration_hours, description, location)
        (
            self._get_service()
            .events()
            .update(calendarId=calendar_id, eventId=event_id, body=body)
            .execute()
        )

    def find_event_by_summary(
        self, calendar_id: str, summary: str, date: str
    ) -> str | None:
        """Find an existing event by summary and date. Returns Google event ID or None."""
        try:
            time_min = f"{date}T00:00:00+00:00"
            # Search a 48h window to handle timezone offsets
            parts = date.split("-")
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            from datetime import date as dt_date, timedelta
            next_day = dt_date(y, m, d) + timedelta(days=2)
            time_max = f"{next_day.isoformat()}T00:00:00+00:00"

            response = (
                self._get_service()
                .events()
                .list(
                    calendarId=calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    maxResults=50,
                )
                .execute()
            )
            for item in response.get("items", []):
                if item.get("summary") == summary and item.get("status") != "cancelled":
                    return item["id"]
        except Exception as e:
            log.debug("Error searching for event: %s", e)
        return None

    def event_exists(self, calendar_id: str, event_id: str) -> bool:
        """Check if an event still exists in Google Calendar."""
        try:
            evt = (
                self._get_service()
                .events()
                .get(calendarId=calendar_id, eventId=event_id)
                .execute()
            )
            return evt.get("status") != "cancelled"
        except Exception:
            return False

    def delete_event(self, calendar_id: str, event_id: str):
        """Delete a calendar event. Silently ignores already-deleted events."""
        try:
            self._get_service().events().delete(
                calendarId=calendar_id, eventId=event_id
            ).execute()
        except Exception as e:
            if "404" in str(e) or "410" in str(e):
                log.info("Event %s already deleted", event_id)
            else:
                raise

    def _get_service(self):
        if self._service is None:
            if not self._creds:
                raise RuntimeError("Not authenticated")
            http = AuthorizedHttp(self._creds, http=httplib2.Http(timeout=_HTTP_TIMEOUT))
            self._service = build("calendar", "v3", http=http)
        return self._service

    def _save_token(self):
        self._token_path.write_text(self._creds.to_json())

    @staticmethod
    def _build_event_body(
        summary: str,
        start: datetime,
        duration_hours: int,
        description: str,
        location: str,
    ) -> dict:
        end = start + timedelta(hours=duration_hours)
        tz = str(start.tzinfo)
        body = {
            "summary": summary,
            "start": {"dateTime": start.isoformat(), "timeZone": tz},
            "end": {"dateTime": end.isoformat(), "timeZone": tz},
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        return body
