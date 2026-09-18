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
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar"]
_HTTP_TIMEOUT = 30  # seconds for all Google API calls
# Errors that mean "the connection died", not "the server said no". The
# httplib2 connection behind the cached service sits idle between poll cycles
# and the peer eventually drops it, so the first call of a cycle can fail on a
# socket that was fine five minutes ago.
_TRANSPORT_ERRORS = (OSError, httplib2.HttpLib2Error)
# Google status codes that mean the event is genuinely gone.
_GONE_STATUS = (404, 410)


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
            response = self._execute(
                lambda token=page_token: service.calendarList().list(pageToken=token)
            )
            for item in response.get("items", []):
                result.append(
                    {
                        "id": item["id"],
                        "summary": item.get("summary", ""),
                        "primary": item.get("primary", False),
                    }
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return result

    def create_event(
        self,
        calendar_id: str,
        summary: str,
        start: datetime,
        duration_hours: float,
        description: str = "",
        location: str = "",
        tentative: bool = False,
    ) -> str:
        """Create a calendar event. Returns the Google event ID."""
        body = self._build_event_body(
            summary, start, duration_hours, description, location, tentative
        )
        event = self._execute(
            lambda: (
                self._get_service().events().insert(calendarId=calendar_id, body=body)
            ),
            retry=False,
        )
        return event["id"]

    def update_event(
        self,
        calendar_id: str,
        event_id: str,
        summary: str,
        start: datetime,
        duration_hours: float,
        description: str = "",
        location: str = "",
        tentative: bool = False,
    ):
        """Update an existing calendar event."""
        body = self._build_event_body(
            summary, start, duration_hours, description, location, tentative
        )
        self._execute(
            lambda: (
                self._get_service()
                .events()
                .update(calendarId=calendar_id, eventId=event_id, body=body)
            )
        )

    def find_event_by_summary(
        self, calendar_id: str, summary: str, date: str
    ) -> str | None:
        """Find an existing event by summary and date. Returns Google event ID or None."""
        time_min = f"{date}T00:00:00+00:00"
        # Search a 48h window to handle timezone offsets
        parts = date.split("-")
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        from datetime import date as dt_date
        from datetime import timedelta

        next_day = dt_date(y, m, d) + timedelta(days=2)
        time_max = f"{next_day.isoformat()}T00:00:00+00:00"

        response = self._execute(
            lambda: (
                self._get_service()
                .events()
                .list(
                    calendarId=calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    maxResults=50,
                )
            )
        )
        for item in response.get("items", []):
            if item.get("summary") == summary and item.get("status") != "cancelled":
                return item["id"]
        return None

    def event_exists(self, calendar_id: str, event_id: str) -> bool:
        """Check if an event still exists in Google Calendar.

        Returns ``False`` only when Google says the event is gone (404/410) or
        cancelled. Every other failure — a dropped connection, a 5xx, an auth
        error — is raised: the caller treats ``False`` as "deleted externally"
        and re-creates the event, so swallowing a transport error here would
        duplicate the entry on every cycle a request happens to fail.
        """
        try:
            evt = self._execute(
                lambda: (
                    self._get_service()
                    .events()
                    .get(calendarId=calendar_id, eventId=event_id)
                )
            )
        except HttpError as e:
            if e.resp.status in _GONE_STATUS:
                return False
            raise
        return evt.get("status") != "cancelled"

    def delete_event(self, calendar_id: str, event_id: str):
        """Delete a calendar event. Silently ignores already-deleted events."""
        try:
            self._execute(
                lambda: (
                    self._get_service()
                    .events()
                    .delete(calendarId=calendar_id, eventId=event_id)
                )
            )
        except HttpError as e:
            if e.resp.status in _GONE_STATUS:
                log.info("Event %s already deleted", event_id)
            else:
                raise

    def _get_service(self):
        if self._service is None:
            if not self._creds:
                raise RuntimeError("Not authenticated")
            http = AuthorizedHttp(
                self._creds, http=httplib2.Http(timeout=_HTTP_TIMEOUT)
            )
            self._service = build("calendar", "v3", http=http)
        return self._service

    def _execute(self, build_request, *, retry: bool = True):
        """Execute an API request, rebuilding a dead connection once.

        *build_request* is a callable so the retry builds its request against
        the fresh service. ``retry=False`` is for non-idempotent calls: the
        connection is still dropped so the next cycle starts clean, but the
        request is not repeated — a connection reset cannot prove the server
        never saw it.
        """
        try:
            return build_request().execute()
        except _TRANSPORT_ERRORS as e:
            self._service = None
            if not retry:
                raise
            log.info("Google: connection lost (%s), reconnecting and retrying", e)
            return build_request().execute()

    def _save_token(self):
        self._token_path.write_text(self._creds.to_json())

    @staticmethod
    def _build_event_body(
        summary: str,
        start: datetime,
        duration_hours: float,
        description: str,
        location: str,
        tentative: bool = False,
    ) -> dict:
        end = start + timedelta(hours=duration_hours)
        tz = str(start.tzinfo)
        body = {
            "summary": summary,
            "start": {"dateTime": start.isoformat(), "timeZone": tz},
            "end": {"dateTime": end.isoformat(), "timeZone": tz},
            # A signed-but-not-confirmed sign-up maps to a tentative event
            # shown as free (does not block availability); a confirmed
            # sign-up is a normal confirmed event shown as busy.
            "status": "tentative" if tentative else "confirmed",
            "transparency": "transparent" if tentative else "opaque",
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        return body
