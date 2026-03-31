"""Sync engine — diffs WoW events against Google Calendar and reconciles."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fgc_sync.models import (
    Attendance,
    CalendarEvent,
    SyncAction,
    SyncPlan,
    SyncPlanEntry,
    SyncResult,
)
from fgc_sync.services.config import Config
from fgc_sync.services.google_calendar import GoogleCalendarClient
from fgc_sync.services.lua_parser import (
    extract_events,
    get_deleted_event_ids,
    list_character_names,
    parse_saved_variables,
)

log = logging.getLogger(__name__)


def compute_sync_plan(
    config: Config, gcal: GoogleCalendarClient | None = None
) -> SyncPlan:
    """Compute what sync would do without making any changes."""
    plan = SyncPlan()
    syncable, deleted_ids, errors = _collect_syncable_events(config)
    plan.errors.extend(errors)
    if errors:
        return plan

    mapping: dict = config.get("event_mapping", {})
    calendar_id = config.get("calendar_id", "")

    for event_id, (evt, char_name) in syncable.items():
        existing = mapping.get(event_id)
        title = evt.summary_line(char_name)
        info = f"{evt.confirmed_count} confirmed, {evt.signed_count} signed"

        if existing is None:
            plan.entries.append(SyncPlanEntry(
                SyncAction.CREATE, event_id, title, evt.date, evt.time_str, evt.type_label, info
            ))
        elif existing.get("revision") != evt.revision:
            plan.entries.append(SyncPlanEntry(
                SyncAction.UPDATE, event_id, title, evt.date, evt.time_str, evt.type_label, info
            ))
        elif gcal and calendar_id and not gcal.event_exists(calendar_id, existing["google_id"]):
            plan.entries.append(SyncPlanEntry(
                SyncAction.CREATE, event_id, title, evt.date, evt.time_str, evt.type_label,
                info + " (deleted externally)"
            ))

    for event_id in mapping:
        if event_id not in syncable or event_id in deleted_ids:
            plan.entries.append(SyncPlanEntry(
                SyncAction.DELETE, event_id,
                mapping[event_id].get("title", event_id), "", "", ""
            ))

    return plan


def execute_sync(config: Config, gcal: GoogleCalendarClient) -> SyncResult:
    """Parse SavedVariables and sync events to Google Calendar."""
    result = SyncResult()

    calendar_id = config.get("calendar_id")
    timezone = config.get("timezone", "Europe/Berlin")
    duration = config.get("default_duration_hours", 3)

    if not calendar_id:
        result.errors.append("Calendar ID not configured")
        return result

    syncable, deleted_ids, errors = _collect_syncable_events(config)
    result.errors.extend(errors)
    if errors:
        return result

    mapping: dict = config.get("event_mapping", {})

    for event_id, (evt, char_name) in syncable.items():
        start_dt = _event_to_datetime(evt, timezone)
        summary = evt.summary_line(char_name)
        description = evt.description_text()
        location = evt.raid.replace("_", " ").title() if evt.raid else ""
        existing = mapping.get(event_id)

        try:
            if existing is None:
                # Check for duplicate before creating
                found_id = gcal.find_event_by_summary(calendar_id, summary, evt.date)
                if found_id:
                    mapping[event_id] = {
                        "google_id": found_id,
                        "revision": evt.revision,
                        "title": evt.title,
                    }
                    result.skipped += 1
                    log.info("Adopted existing: %s (%s)", summary, evt.date)
                else:
                    google_id = gcal.create_event(
                        calendar_id, summary, start_dt, duration, description, location
                    )
                    mapping[event_id] = {
                        "google_id": google_id,
                        "revision": evt.revision,
                        "title": evt.title,
                    }
                    result.created += 1
                    log.info("Created: %s (%s)", summary, evt.date)

            elif existing.get("revision") != evt.revision:
                if gcal.event_exists(calendar_id, existing["google_id"]):
                    gcal.update_event(
                        calendar_id, existing["google_id"],
                        summary, start_dt, duration, description, location,
                    )
                    mapping[event_id]["revision"] = evt.revision
                    mapping[event_id]["title"] = evt.title
                    result.updated += 1
                    log.info("Updated: %s (%s)", summary, evt.date)
                else:
                    # Event was deleted externally, re-create it
                    google_id = gcal.create_event(
                        calendar_id, summary, start_dt, duration, description, location
                    )
                    mapping[event_id] = {
                        "google_id": google_id,
                        "revision": evt.revision,
                        "title": evt.title,
                    }
                    result.created += 1
                    log.info("Re-created (deleted externally): %s (%s)", summary, evt.date)

            elif not gcal.event_exists(calendar_id, existing["google_id"]):
                # Revision unchanged but event deleted externally, re-create
                google_id = gcal.create_event(
                    calendar_id, summary, start_dt, duration, description, location
                )
                mapping[event_id] = {
                    "google_id": google_id,
                    "revision": evt.revision,
                    "title": evt.title,
                }
                result.created += 1
                log.info("Re-created (deleted externally): %s (%s)", summary, evt.date)

            else:
                result.skipped += 1

        except Exception as e:
            result.errors.append(f"Error syncing {evt.title}: {e}")
            log.error("Error syncing %s: %s", evt.title, e)

    ids_to_remove = []
    for event_id, info in mapping.items():
        if event_id not in syncable or event_id in deleted_ids:
            try:
                gcal.delete_event(calendar_id, info["google_id"])
                result.deleted += 1
                log.info("Deleted: %s", event_id)
            except Exception as e:
                result.errors.append(f"Error deleting {event_id}: {e}")
                log.error("Error deleting %s: %s", event_id, e)
            ids_to_remove.append(event_id)

    for eid in ids_to_remove:
        mapping.pop(eid, None)

    config.set("event_mapping", mapping)
    return result


# --- Private helpers ---

def _collect_syncable_events(
    config: Config,
) -> tuple[dict[str, tuple[CalendarEvent, str]], set[str], list[str]]:
    """Return ({event_id: (event, char_name)}, deleted_ids, errors)."""
    errors: list[str] = []
    sv_path = config.saved_variables_path
    if not sv_path or not sv_path.exists():
        errors.append(f"SavedVariables not found: {sv_path}")
        return {}, set(), errors

    guild_key = config.get("guild_key")
    if not guild_key:
        errors.append("Guild key not configured")
        return {}, set(), errors

    try:
        db = parse_saved_variables(sv_path)
    except Exception as e:
        errors.append(f"Failed to parse SavedVariables: {e}")
        return {}, set(), errors

    char_names = list_character_names(db)
    wow_events = extract_events(db, guild_key)
    deleted_ids = get_deleted_event_ids(db, guild_key)
    today = date.today()

    result: dict[str, tuple[CalendarEvent, str]] = {}
    for evt in wow_events:
        try:
            evt_date = date.fromisoformat(evt.date)
        except ValueError:
            continue
        if evt_date < today:
            continue
        char_name = _find_participating_character(evt, char_names)
        if char_name:
            result[evt.event_id] = (evt, char_name)

    return result, deleted_ids, errors


def _find_participating_character(
    evt: CalendarEvent, character_names: list[str]
) -> str | None:
    for p in evt.participants:
        if p.name in character_names and Attendance.is_active(p.attendance):
            return p.name
    return None


def _event_to_datetime(event: CalendarEvent, timezone: str) -> datetime:
    parts = event.date.split("-")
    return datetime(
        int(parts[0]), int(parts[1]), int(parts[2]),
        event.server_hour, event.server_minute,
        tzinfo=ZoneInfo(timezone),
    )
