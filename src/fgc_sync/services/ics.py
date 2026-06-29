"""iCalendar (.ics) rendering for calendar events.

Produces a minimal RFC 5545 VCALENDAR/VEVENT for a single raid so members can
import the event into their own calendar app via an attachment in the Discord
thread. Times are emitted in UTC (``...Z``) so no VTIMEZONE block is needed —
every calendar client handles UTC.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fgc_sync.models.events import CalendarEvent


def _escape(text: str) -> str:
    """Escape a value per RFC 5545 (TEXT type)."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def _event_start_utc(event: CalendarEvent, tz_name: str) -> datetime:
    y, m, d = (int(p) for p in event.date.split("-"))
    local = datetime(
        y, m, d, event.server_hour, event.server_minute, tzinfo=ZoneInfo(tz_name)
    )
    return local.astimezone(UTC)


def compute_ics_hash(event: CalendarEvent, tz_name: str, duration_hours: float) -> str:
    """Short content hash over every field the .ics encodes.

    Used to detect when a thread's attached calendar file is stale and must be
    re-uploaded (time change, retitle, raid swap, duration change, …).
    """
    start = _event_start_utc(event, tz_name)
    payload = "|".join(
        str(x)
        for x in (
            event.event_id,
            start.isoformat(),
            duration_hours,
            event.title,
            event.type_label,
            event.raid,
            event.comment,
        )
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]


def render_ics(
    event: CalendarEvent, tz_name: str, duration_hours: float, *, dtstamp=None
) -> bytes:
    """Render *event* as a single-VEVENT VCALENDAR in UTF-8 bytes.

    *dtstamp* defaults to the event start (a stable, side-effect-free value) so
    the output is deterministic — callers that want a real generation timestamp
    can pass one explicitly.
    """
    start = _event_start_utc(event, tz_name)
    end = start + timedelta(hours=duration_hours)
    stamp = dtstamp or start

    def fmt(dt: datetime) -> str:
        return dt.strftime("%Y%m%dT%H%M%SZ")

    summary = f"[{event.type_label}] {event.title}"
    location = (event.raid or "").replace("_", " ").title()
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ForgasGuildCalendar-Sync//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{event.event_id}@fgc-sync",
        f"DTSTAMP:{fmt(stamp)}",
        f"DTSTART:{fmt(start)}",
        f"DTEND:{fmt(end)}",
        f"SUMMARY:{_escape(summary)}",
        f"LOCATION:{_escape(location)}",
        f"DESCRIPTION:{_escape(event.comment or '')}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def ics_filename(event: CalendarEvent, content_hash: str) -> str:
    """Download filename for the attachment, embedding the dedup hash.

    Shape: ``<Raid>_<date>_h<hash>.ics`` (e.g. ``Karazhan_2026-06-30_h7d4f5c01.ics``).
    The ``_h<hash>`` suffix lets the thread scan tell a current attachment from
    a stale one without downloading it.
    """
    raid = (event.raid or "event").replace("_", " ").title().replace(" ", "")
    return f"{raid}_{event.date}_h{content_hash}.ics"
