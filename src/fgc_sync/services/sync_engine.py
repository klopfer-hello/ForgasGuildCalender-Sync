"""Sync engine — diffs WoW events against Google Calendar and reconciles."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fgc_sync._version import __version__
from fgc_sync.models import (
    Attendance,
    CalendarEvent,
    SyncAction,
    SyncPlan,
    SyncPlanEntry,
    SyncResult,
)
from fgc_sync.services.config import Config
from fgc_sync.services.discord_poster import DiscordPoster, compute_event_hash
from fgc_sync.services.google_calendar import GoogleCalendarClient
from fgc_sync.services.lua_parser import (
    extract_events,
    get_deleted_event_ids,
    list_character_names,
    parse_saved_variables,
)
from fgc_sync.services.updater import check_for_update
from fgc_sync.services.weekly_overview import (
    EMPTY_WEEK_HASH,
    candidate_weekly_thread_names,
    collect_week_events,
    compute_weekly_hash,
    current_week_bounds,
    format_weekly_summary,
    get_weekly_thread_name,
    next_week_bounds,
    render_weekly_overview,
)

log = logging.getLogger(__name__)

EXPIRED_EVENT_HOURS = 24  # delete Discord threads this long after event start
DISCORD_LOOKAHEAD_DAYS = 7  # only post events within this window
_SLOW_OPERATION_SECONDS = 5  # warn when a single event takes longer

# Bulk-deletion guard for per-event Discord cleanup. The all-empty guard
# (no_events_guard) only catches a *fully* empty parse; a divergent or partial
# read — e.g. another client resolving a different namespace, or one whose
# SavedVariables lacks this guild's events — yields a non-empty-but-wrong event
# set that can mark many still-valid events as "removed" at once. Thread
# deletion is permanent, so refuse to bulk-delete an implausibly large share of
# the mapping in a single sync. Small removals (normal churn) always proceed.
_BULK_DELETION_GUARD_MIN = 3  # don't guard removals smaller than this
_BULK_DELETION_GUARD_FRACTION = 0.5  # suspicious above this share of the mapping


def _coerce_pinged(existing: dict | None) -> dict[str, str]:
    """Return ``{name: message_id}``, accepting legacy list / ``confirmed`` shapes.

    Pre-v2 configs stored ``pinged`` as ``list[str]`` (and even older ones used
    ``confirmed``). The v2 migration rewrites these on disk, but adoption-time
    structures built in this module also pass through here so we tolerate both
    shapes defensively. Empty-string message ids mean "we know this name was
    pinged but don't know which message contained it" — they still suppress
    re-pings but cannot be edited.
    """
    if not existing:
        return {}
    raw = existing.get("pinged")
    if raw is None:
        raw = existing.get("confirmed", [])
    if isinstance(raw, dict):
        return {str(k): str(v or "") for k, v in raw.items()}
    if isinstance(raw, list):
        return {name: "" for name in raw}
    return {}


def _is_local_data_stale(config: Config, discord: DiscordPoster) -> bool:
    """Return True if another client has newer SavedVariables data on Discord."""
    sv_path = config.saved_variables_path
    local_sv_mtime = 0
    if sv_path and sv_path.exists():
        local_sv_mtime = int(sv_path.stat().st_mtime)
    try:
        remote_sv_mtime = discord.get_max_remote_sv_mtime()
    except Exception as e:
        log.warning("Discord: failed to read remote sv_mtime: %s", e)
        remote_sv_mtime = 0
    if local_sv_mtime and remote_sv_mtime and local_sv_mtime < remote_sv_mtime:
        log.warning(
            "Discord: local SavedVariables (%d) is older than "
            "the most recent remote update (%d). Another client has newer data.",
            local_sv_mtime,
            remote_sv_mtime,
        )
        return True
    return False


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse '2.11.1' into a comparable tuple; unparseable → ``(0,)``."""
    try:
        return tuple(int(x) for x in str(v).split("."))
    except (ValueError, TypeError):
        return (0,)


def coordinate_client_versions(
    config: Config, discord: DiscordPoster
) -> tuple[bool, list[str]]:
    """Decide whether to defer to a newer client and post the release changelog.

    Returns ``(should_defer, errors)``. ``should_defer`` True means a newer
    client version is active (read from the ``_v<version>`` tag embedded in
    forum image filenames — the only cross-client version signal; no message,
    no names) and this client should skip its Discord writes this cycle, like
    the stale-data guard. Also deletes any leftover deprecated registry message
    and posts the changelog. Best-effort — never raises into the sync.
    """
    errors: list[str] = []
    if not discord.is_configured or __version__ == "dev":
        return False, errors

    # Clean up the deprecated client-registry message (posted by pre-2.11.1
    # clients) so it stops being visible once those clients update.
    weekly_channel = (config.get("discord_weekly_mapping", {}) or {}).get("channel_id")
    if weekly_channel:
        try:
            discord.delete_registry_messages(weekly_channel)
        except Exception as e:
            log.debug("Registry cleanup failed: %s", e)

    # Defer-to-newer gate: compare our version against the highest version any
    # client has embedded in a forum image filename.
    should_defer = False
    try:
        remote = discord.get_max_remote_version()
        if remote and _parse_version(remote) > _parse_version(__version__):
            should_defer = True
            log.warning(
                "Version gate: a newer client (v%s) is active — deferring this "
                "client (v%s) to avoid clobbering newer data.",
                remote,
                __version__,
            )
    except Exception as e:
        errors.append(f"Version coordination failed: {e}")
        log.warning("Version coordination failed: %s", e)

    # A deferring (older) client leaves the changelog to the newer active one.
    if not should_defer:
        _maybe_post_changelog(config, discord, errors)
    return should_defer, errors


def _maybe_post_changelog(
    config: Config, discord: DiscordPoster, errors: list[str]
) -> None:
    """Post the latest release's changelog to the dedicated updates thread, once
    per version, pinging the Discord users listed in config.

    The thread is created on first use and recovered by name (across languages)
    or via self-heal if its stored id 404s. The changelog body is the GitHub
    release notes; only the header is translated.
    """
    try:
        info = check_for_update()
    except Exception as e:
        log.debug("Changelog: update check failed: %s", e)
        return
    if not info or not info.latest_version:
        return
    version = info.latest_version

    from fgc_sync.i18n import t as _t
    from fgc_sync.i18n import t_all

    name = _t("updates.thread_name")
    header = _t("updates.changelog_header", version=version)
    changelog = (info.release_notes or "").strip()
    body = f"{header}\n\n{changelog}" if changelog else header
    ping_ids = config.get("discord_update_ping_user_ids", []) or []

    try:
        thread_id = config.get("discord_updates_thread_id")
        # Self-heal: drop a deleted thread so it's recreated below.
        if thread_id and not discord.ensure_unarchived(thread_id):
            thread_id = None
        # Adopt an existing thread by name (any supported language).
        if not thread_id:
            discord.clear_thread_cache()
            for candidate in t_all("updates.thread_name"):
                found = discord.find_thread_by_name(candidate)
                if found:
                    thread_id = found
                    break

        if thread_id and discord.changelog_exists(thread_id, version):
            config.set("discord_updates_thread_id", thread_id)
            return  # already announced

        if thread_id:
            discord.post_changelog(thread_id, body, ping_ids, version)
        else:
            thread_id = discord.create_changelog_thread(name, body, ping_ids, version)
        if thread_id:
            config.set("discord_updates_thread_id", thread_id)
    except Exception as e:
        errors.append(f"Changelog post failed: {e}")
        log.warning("Changelog post failed: %s", e)


def _would_blank_remote_week(
    discord: DiscordPoster,
    channel_id: str | None,
    message_id: str | None,
    local_hash: str,
) -> bool:
    """Return True if pushing *local_hash* would blank a populated message.

    Guards against a stale/incomplete local read (a week with zero events)
    overwriting an overview image another client already populated. Only the
    empty-render case ever triggers a remote read, so the common path is free.
    Fails open (returns False) if the remote hash can't be determined — better
    to attempt the write than to wedge a legitimately-changing overview.
    """
    if local_hash != EMPTY_WEEK_HASH or not channel_id or not message_id:
        return False
    remote_hash = discord.get_weekly_image_hash(channel_id, message_id)
    return bool(remote_hash) and remote_hash != EMPTY_WEEK_HASH


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
            plan.entries.append(
                SyncPlanEntry(
                    SyncAction.CREATE,
                    event_id,
                    title,
                    evt.date,
                    evt.time_str,
                    evt.type_label,
                    info,
                )
            )
        elif existing.get("revision") != evt.revision:
            plan.entries.append(
                SyncPlanEntry(
                    SyncAction.UPDATE,
                    event_id,
                    title,
                    evt.date,
                    evt.time_str,
                    evt.type_label,
                    info,
                )
            )
        elif (
            gcal
            and calendar_id
            and not gcal.event_exists(calendar_id, existing["google_id"])
        ):
            plan.entries.append(
                SyncPlanEntry(
                    SyncAction.CREATE,
                    event_id,
                    title,
                    evt.date,
                    evt.time_str,
                    evt.type_label,
                    info + " (deleted externally)",
                )
            )

    for event_id in mapping:
        if event_id not in syncable or event_id in deleted_ids:
            plan.entries.append(
                SyncPlanEntry(
                    SyncAction.DELETE,
                    event_id,
                    mapping[event_id].get("title", event_id),
                    "",
                    "",
                    "",
                )
            )

    return plan


def execute_sync(config: Config, gcal: GoogleCalendarClient) -> SyncResult:
    """Parse SavedVariables and sync events to Google Calendar."""
    import time as _time

    _gs_start = _time.monotonic()
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

    # Safety guard: if SavedVariables yielded no events but we have a non-empty
    # mapping, treat this as a parser/structure mismatch and skip the sync
    # entirely — otherwise cleanup would delete every mapped event.
    if not syncable and mapping:
        msg = (
            f"Google sync: SavedVariables yielded no events but mapping has "
            f"{len(mapping)} entries — skipping to avoid mass deletion "
            "(possible SavedVariables structure change)."
        )
        log.warning(msg)
        result.errors.append(msg)
        return result

    for event_id, (evt, char_name) in syncable.items():
        start_dt = _event_to_datetime(evt, timezone)
        summary = evt.summary_line(char_name)
        description = evt.description_text()
        location = evt.raid.replace("_", " ").title() if evt.raid else ""
        existing = mapping.get(event_id)

        try:
            if existing is None:
                log.debug(
                    "Event %s (%s) not in mapping, will create or adopt",
                    event_id,
                    evt.title,
                )
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
                log.debug(
                    "Revision mismatch for %s (%s): stored=%r (%s) parsed=%r (%s)",
                    event_id,
                    evt.title,
                    existing.get("revision"),
                    type(existing.get("revision")).__name__,
                    evt.revision,
                    type(evt.revision).__name__,
                )
                if gcal.event_exists(calendar_id, existing["google_id"]):
                    gcal.update_event(
                        calendar_id,
                        existing["google_id"],
                        summary,
                        start_dt,
                        duration,
                        description,
                        location,
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
                    log.info(
                        "Re-created (deleted externally): %s (%s)", summary, evt.date
                    )

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
    _gs_elapsed = _time.monotonic() - _gs_start
    log.debug(
        "Google sync finished in %.1fs: %d created, %d updated, %d deleted, %d skipped",
        _gs_elapsed,
        result.created,
        result.updated,
        result.deleted,
        result.skipped,
    )
    return result


def compute_discord_sync_plan(
    config: Config,
    discord: DiscordPoster,
) -> SyncPlan:
    """Compute what Discord sync would do without making any changes.

    Read-only: queries Discord threads and message history but does not
    create, update, delete, or ping anything.  Does not modify config.
    """
    plan = SyncPlan()
    timezone = config.get("timezone", "Europe/Berlin")

    if not discord.is_configured:
        return plan

    all_events, deleted_ids, errors = _collect_all_future_events(config)
    plan.errors.extend(errors)
    if errors:
        return plan

    mapping: dict = config.get("discord_message_mapping", {})
    discord.clear_members_cache()
    discord.clear_thread_cache()

    if _is_local_data_stale(config, discord):
        plan.errors.append(
            "Local SavedVariables is older than remote — "
            "another client has newer data. Sync would be skipped."
        )
        return plan

    now = datetime.now(ZoneInfo(timezone))

    for event_id, evt in sorted(
        all_events.items(),
        key=lambda x: (x[1].date, x[1].server_hour, x[1].server_minute),
    ):
        event_dt = _event_to_datetime(evt, timezone)
        if (now - event_dt).total_seconds() / 3600 >= EXPIRED_EVENT_HOURS:
            continue

        existing = mapping.get(event_id)
        content_hash = compute_event_hash(evt)
        confirmed_names = sorted(
            p.name for p in evt.participants if p.attendance == Attendance.CONFIRMED
        )
        title = evt.title
        info = f"{evt.confirmed_count} confirmed, {evt.signed_count} signed"

        # Check if another client already created a thread
        if existing is None:
            remote = discord.find_existing_thread(evt)
            if remote:
                existing = {
                    "channel_id": remote["channel_id"],
                    "message_ids": {
                        "image_id": remote.get("image_id"),
                        "hash": remote.get("hash"),
                    },
                    "pinged": {},
                }

        channel_id = (existing or {}).get("channel_id")
        msg_ids = (existing or {}).get("message_ids")
        prev_pinged = _coerce_pinged(existing)

        if channel_id is None:
            plan.entries.append(
                SyncPlanEntry(
                    SyncAction.CREATE,
                    event_id,
                    title,
                    evt.date,
                    evt.time_str,
                    evt.type_label,
                    info + f", ping {len(confirmed_names)}",
                )
            )
        else:
            # Check ping history from the thread
            history_pinged = discord.get_already_pinged_names(
                channel_id,
                set(confirmed_names),
            )
            prev_pinged = {**prev_pinged, **history_pinged}
            confirmed_set = set(confirmed_names)
            to_ping = confirmed_set - set(prev_pinged.keys())
            to_remove = {
                name
                for name, msg_id in prev_pinged.items()
                if name not in confirmed_set and msg_id
            }

            old_hash = (msg_ids or {}).get("hash")
            change_bits: list[str] = []
            if to_ping:
                change_bits.append(f"ping {len(to_ping)} new")
            if to_remove:
                change_bits.append(f"unping {len(to_remove)}")

            if old_hash != content_hash:
                detail = info
                if change_bits:
                    detail += ", " + ", ".join(change_bits)
                plan.entries.append(
                    SyncPlanEntry(
                        SyncAction.UPDATE,
                        event_id,
                        title,
                        evt.date,
                        evt.time_str,
                        evt.type_label,
                        detail,
                    )
                )
            elif change_bits:
                plan.entries.append(
                    SyncPlanEntry(
                        SyncAction.UPDATE,
                        event_id,
                        title,
                        evt.date,
                        evt.time_str,
                        evt.type_label,
                        ", ".join(change_bits),
                    )
                )

    # Events in mapping but no longer in WoW or deleted. Mirror the execute
    # path's bulk-deletion guard so the dry-run doesn't show removals that the
    # real sync would refuse (divergent/partial parse).
    removal_candidates = [
        event_id
        for event_id in mapping
        if event_id not in all_events or event_id in deleted_ids
    ]
    bulk_guarded = len(removal_candidates) >= _BULK_DELETION_GUARD_MIN and len(
        removal_candidates
    ) > _BULK_DELETION_GUARD_FRACTION * len(mapping)
    if bulk_guarded:
        plan.errors.append(
            f"Bulk-deletion guard would skip removing {len(removal_candidates)} "
            f"of {len(mapping)} threads (likely divergent/partial data)."
        )

    for event_id, _info_map in mapping.items():
        if event_id not in all_events or event_id in deleted_ids:
            if not bulk_guarded:
                plan.entries.append(
                    SyncPlanEntry(
                        SyncAction.DELETE,
                        event_id,
                        event_id,
                        "",
                        "",
                        "",
                    )
                )
            continue
        # Expired (24+ hours ago)
        evt = all_events.get(event_id)
        if evt:
            event_dt = _event_to_datetime(evt, timezone)
            if (now - event_dt).total_seconds() / 3600 >= EXPIRED_EVENT_HOURS:
                plan.entries.append(
                    SyncPlanEntry(
                        SyncAction.DELETE,
                        event_id,
                        evt.title,
                        evt.date,
                        evt.time_str,
                        evt.type_label,
                        "expired",
                    )
                )

    return plan


def execute_discord_sync(config: Config, discord: DiscordPoster) -> SyncResult:
    """Sync guild events to Discord — one forum thread per event."""
    import time as _time

    _ds_start = _time.monotonic()
    result = SyncResult()
    timezone = config.get("timezone", "Europe/Berlin")
    duration_hours = float(config.get("default_duration_hours", 3))

    if not discord.is_configured:
        return result

    all_events, deleted_ids, errors = _collect_all_future_events(config)
    result.errors.extend(errors)
    if errors:
        return result

    log.debug("Discord sync: %d events to process", len(all_events))
    mapping: dict = config.get("discord_message_mapping", {})
    discord.clear_members_cache()
    discord.clear_thread_cache()

    # Safety guard: if SavedVariables yielded no events but we have a non-empty
    # mapping, only run the 24h-expired cleanup and skip the "not in all_events"
    # deletion — otherwise a parser/structure mismatch would wipe every thread.
    no_events_guard = not all_events and bool(mapping)
    if no_events_guard:
        msg = (
            f"Discord sync: SavedVariables yielded no events but mapping has "
            f"{len(mapping)} entries — skipping create/update and non-expiry "
            "deletions (possible SavedVariables structure change)."
        )
        log.warning(msg)
        result.errors.append(msg)

    # Stale-data guard: if another client has already written newer
    # SavedVariables data to Discord, skip our entire Discord sync to avoid
    # flapping images and duplicate pings between clients.
    if _is_local_data_stale(config, discord):
        return result

    sv_path = config.saved_variables_path
    local_sv_mtime = 0
    if sv_path and sv_path.exists():
        local_sv_mtime = int(sv_path.stat().st_mtime)

    now = datetime.now(ZoneInfo(timezone))

    for event_id, evt in (
        []
        if no_events_guard
        else sorted(
            all_events.items(),
            key=lambda x: (x[1].date, x[1].server_hour, x[1].server_minute),
        )
    ):
        # Skip expired events — they are only in all_events so the
        # cleanup phase below can evaluate and delete their threads.
        event_dt = _event_to_datetime(evt, timezone)
        if (now - event_dt).total_seconds() / 3600 >= EXPIRED_EVENT_HOURS:
            continue

        existing = mapping.get(event_id)
        content_hash = compute_event_hash(evt)
        confirmed_names = sorted(
            p.name for p in evt.participants if p.attendance == Attendance.CONFIRMED
        )

        try:
            _evt_start = _time.monotonic()

            channel_id = (existing or {}).get("channel_id")
            msg_ids = (existing or {}).get("message_ids")
            prev_pinged = _coerce_pinged(existing)
            is_new_thread = False

            # Self-heal: verify a mapped thread still exists. A 404 means it was
            # deleted externally (another client's cleanup, manual delete) — forget
            # it so the adopt-before-create path below recovers, instead of
            # erroring forever. Clients never have to hand-clear the mapping.
            if channel_id is not None and not discord.ensure_unarchived(channel_id):
                log.warning(
                    "Discord: mapped thread %s for %s no longer exists — "
                    "re-adopting or recreating",
                    channel_id,
                    evt.title,
                )
                channel_id = msg_ids = None
                prev_pinged = {}

            # Adopt-before-create — covers both no-mapping and the self-heal case
            # above. Collect every thread matching this event in *any* language
            # and keep a single deterministic survivor, deleting the rest. This
            # both prevents and cleans up cross-language / multi-client duplicate
            # threads. The survivor is the thread whose roster image carries the
            # highest tool version (so the up-to-date copy wins, not an old
            # client's), tie-broken by lowest id — a client-independent choice so
            # every client converges on the same thread.
            if channel_id is None:
                matches = discord.find_event_threads(evt)
                if matches:
                    survivor = max(
                        matches,
                        key=lambda t: (
                            _parse_version(t.get("version") or "0"),
                            -int(t["channel_id"]),
                        ),
                    )
                    for dup in matches:
                        if dup["channel_id"] == survivor["channel_id"]:
                            continue
                        try:
                            discord.delete_thread(dup["channel_id"])
                            result.deleted += 1
                            log.info(
                                "Discord: removed duplicate thread %s for %s",
                                dup["channel_id"],
                                evt.title,
                            )
                        except Exception as e:
                            log.warning(
                                "Discord: failed to delete duplicate %s: %s",
                                dup["channel_id"],
                                e,
                            )
                    discord.ensure_unarchived(survivor["channel_id"])
                    channel_id = survivor["channel_id"]
                    msg_ids = {
                        "image_id": survivor.get("image_id"),
                        "hash": survivor.get("hash"),
                    }
                    prev_pinged = {}
                    log.info("Discord: adopted existing thread for %s", evt.title)

            if channel_id is None:
                # New event — create forum thread with roster image
                channel_id, msg_ids = discord.create_event_thread(
                    evt,
                    timezone,
                    local_sv_mtime,
                )
                prev_pinged = {}
                is_new_thread = True
                result.created += 1
            else:
                # Existing thread (already unarchived above)
                if msg_ids and msg_ids.get("hash") == content_hash:
                    # Image up to date — fall through to ping retry below
                    pass
                else:
                    # Content changed — update image in place, or find it
                    # if the local mapping lost track of the message ID
                    can_patch = (
                        msg_ids
                        and msg_ids.get("image_id")
                        and discord.message_exists(channel_id, msg_ids)
                    )
                    if not can_patch:
                        # Try to locate the original image before posting a duplicate
                        found_id = discord.find_image_message(channel_id, evt.event_id)
                        if found_id:
                            msg_ids = msg_ids or {}
                            msg_ids["image_id"] = found_id
                            can_patch = True
                    if can_patch:
                        msg_ids = discord.update_event(
                            channel_id, msg_ids, evt, timezone, local_sv_mtime
                        )
                    else:
                        msg_ids = discord.post_event(
                            channel_id, evt, timezone, local_sv_mtime
                        )
                    result.updated += 1

            # Scan thread history to catch pings from other clients that
            # are not reflected in our local mapping. History wins on
            # conflict so we always have the freshest message id we can
            # later edit.
            if channel_id and not is_new_thread:
                history_pinged = discord.get_already_pinged_names(
                    channel_id,
                    set(confirmed_names) | set(prev_pinged.keys()),
                )
                prev_pinged = {**prev_pinged, **history_pinged}

            confirmed_set = set(confirmed_names)

            # Strike out @mentions for members who left the roster, before
            # adding new pings so the same sync cycle can compress one ping
            # message in place. Discord does not re-notify on edits, so the
            # other members in the same message are not re-pinged.
            removals = {
                name: msg_id
                for name, msg_id in prev_pinged.items()
                if name not in confirmed_set and msg_id
            }
            if removals and channel_id and not is_new_thread:
                discord.remove_mentions(channel_id, removals)

            # Ping any confirmed members not yet successfully pinged. This
            # also retries members whose Discord account did not exist at
            # the time of the original ping (late server joiners).
            to_ping = confirmed_set - set(prev_pinged.keys())
            newly_pinged: dict[str, str] = {}
            if to_ping:
                from fgc_sync.i18n import t as _t

                label = (
                    _t("discord.ping_confirmed")
                    if is_new_thread
                    else _t("discord.ping_newly_confirmed")
                )
                newly_pinged = discord.ping_members(channel_id, to_ping, label)

            # New pinged dict: keep entries still on the roster, drop those
            # that left (their @mention has been edited away above), then
            # layer in the freshly-pinged names with their message ids.
            pinged: dict[str, str] = {
                name: msg_id
                for name, msg_id in prev_pinged.items()
                if name in confirmed_set
            }
            pinged.update(newly_pinged)

            # Ensure the "Add to my calendar" .ics attachment exists and is
            # current. Posted as its own message beneath the roster image so
            # members can one-tap import the raid into their calendar app.
            ics_info = _sync_event_ics(
                discord,
                channel_id,
                evt,
                timezone,
                duration_hours,
                (existing or {}).get("ics"),
                is_new_thread,
            )

            unchanged = (
                not is_new_thread
                and msg_ids
                and msg_ids.get("hash") == content_hash
                and not newly_pinged
                and not removals
            )
            if unchanged:
                result.skipped += 1

            mapping[event_id] = {
                "channel_id": channel_id,
                "message_ids": msg_ids,
                "pinged": pinged,
                "ics": ics_info,
            }
            _evt_elapsed = _time.monotonic() - _evt_start
            if _evt_elapsed > _SLOW_OPERATION_SECONDS:
                log.warning(
                    "Discord: slow operation for %s: %.1fs", evt.title, _evt_elapsed
                )

        except Exception as e:
            result.errors.append(f"Discord error for {evt.title}: {e}")
            log.error("Discord error for %s: %s", evt.title, e)

    # Clean up: delete threads for events no longer active.
    # When the no-events guard is active, skip this phase — we can't tell
    # "genuinely removed" from "parser couldn't see them" — and only let the
    # 24h-expired cleanup below run (which is a no-op when all_events is empty).
    removal_candidates = (
        []
        if no_events_guard
        else [
            event_id
            for event_id in mapping
            if event_id not in all_events or event_id in deleted_ids
        ]
    )

    # Bulk-deletion guard: a divergent/partial parse can make many still-valid
    # events look "removed" at once (the all-empty guard above only catches a
    # fully-empty parse). Deleting a thread is permanent, so when a single sync
    # would remove an implausibly large share of the mapping, skip these
    # removals entirely and let only the 24h-expired cleanup run.
    if len(removal_candidates) >= _BULK_DELETION_GUARD_MIN and len(
        removal_candidates
    ) > _BULK_DELETION_GUARD_FRACTION * len(mapping):
        msg = (
            f"Discord cleanup: refusing to delete {len(removal_candidates)} of "
            f"{len(mapping)} threads in one sync — exceeds the bulk-deletion "
            "guard (likely divergent/partial SavedVariables data). Skipping "
            "removals; only 24h-expired cleanup will run."
        )
        log.warning(msg)
        result.errors.append(msg)
        removal_candidates = []

    ids_to_remove = []
    for event_id in removal_candidates:
        info = mapping[event_id]
        log.info(
            "Discord cleanup: removing %s (not_in_all_events=%s, in_deleted_ids=%s, thread=%s)",
            event_id,
            event_id not in all_events,
            event_id in deleted_ids,
            info.get("channel_id"),
        )
        ch_id = info.get("channel_id")
        if ch_id:
            try:
                discord.delete_thread(ch_id)
                result.deleted += 1
            except Exception as e:
                result.errors.append(f"Discord thread delete error {event_id}: {e}")
                log.error("Discord thread delete error %s: %s", event_id, e)
        ids_to_remove.append(event_id)

    # Clean up: delete threads for events that happened 24+ hours ago
    for event_id, info in mapping.items():
        if event_id in ids_to_remove:
            continue
        evt = all_events.get(event_id)
        if not evt:
            continue
        event_dt = _event_to_datetime(evt, timezone)
        hours_since = (now - event_dt).total_seconds() / 3600
        if hours_since >= 24:
            log.info(
                "Discord cleanup: removing expired %s (%s, %.1f hours ago, thread=%s)",
                event_id,
                evt.title,
                hours_since,
                info.get("channel_id"),
            )
            ch_id = info.get("channel_id")
            if ch_id:
                try:
                    discord.delete_thread(ch_id)
                    result.deleted += 1
                except Exception as e:
                    log.error("Discord expired thread delete error: %s", e)
            ids_to_remove.append(event_id)

    for eid in ids_to_remove:
        mapping.pop(eid, None)

    config.set("discord_message_mapping", mapping)
    _ds_elapsed = _time.monotonic() - _ds_start
    log.debug(
        "Discord sync finished in %.1fs: %d created, %d updated, %d deleted, %d skipped",
        _ds_elapsed,
        result.created,
        result.updated,
        result.deleted,
        result.skipped,
    )
    return result


def _load_events_for_overview(
    config: Config,
) -> tuple[dict[str, CalendarEvent], list[str]]:
    """Return ({event_id: event}, errors). No roster filter, no date-window filter."""
    errors: list[str] = []
    sv_path = config.saved_variables_path
    if not sv_path or not sv_path.exists():
        errors.append(f"SavedVariables not found: {sv_path}")
        return {}, errors

    guild_key = config.get("guild_key")
    if not guild_key:
        errors.append("Guild key not configured")
        return {}, errors

    try:
        db = parse_saved_variables(sv_path)
    except Exception as e:
        errors.append(f"Failed to parse SavedVariables: {e}")
        return {}, errors

    wow_events = extract_events(db, guild_key)
    deleted_ids = get_deleted_event_ids(db, guild_key)
    by_id = {e.event_id: e for e in wow_events if e.event_id not in deleted_ids}
    return by_id, errors


def _collect_week_events_for_overview(
    config: Config,
    *,
    week_offset: int = 0,
) -> tuple[list[CalendarEvent], list[str]]:
    """Return (events_in_week, errors) for the requested ISO week relative to today."""
    by_id, errors = _load_events_for_overview(config)
    if errors:
        return [], errors
    return collect_week_events(by_id, week_offset=week_offset), errors


def compute_weekly_sync_plan(
    config: Config,
    discord: DiscordPoster,
) -> SyncPlan:
    """Compute what the weekly-overview sync would do without touching Discord."""
    plan = SyncPlan()
    if not discord.is_configured:
        return plan

    by_id, errors = _load_events_for_overview(config)
    plan.errors.extend(errors)
    if errors:
        return plan

    mapping: dict = config.get("discord_weekly_mapping", {}) or {}
    weekly_name = get_weekly_thread_name()

    cur_monday, cur_sunday, cur_week_key = current_week_bounds()
    cur_events = collect_week_events(by_id, week_offset=0)
    cur_hash = compute_weekly_hash(cur_events)
    cur_info = (
        f"{len(cur_events)} raid(s), week {cur_week_key} "
        f"({cur_monday.isoformat()}..{cur_sunday.isoformat()})"
    )
    if not mapping.get("channel_id"):
        plan.entries.append(
            SyncPlanEntry(
                SyncAction.CREATE,
                "weekly_overview",
                weekly_name,
                cur_monday.isoformat(),
                "",
                "Overview (current week)",
                cur_info,
            )
        )
    elif (
        mapping.get("hash") != cur_hash or mapping.get("week_key") != cur_week_key
    ) and not _would_blank_remote_week(
        discord, mapping.get("channel_id"), mapping.get("channel_id"), cur_hash
    ):
        plan.entries.append(
            SyncPlanEntry(
                SyncAction.UPDATE,
                "weekly_overview",
                weekly_name,
                cur_monday.isoformat(),
                "",
                "Overview (current week)",
                cur_info,
            )
        )

    nxt_monday, nxt_sunday, nxt_week_key = next_week_bounds()
    nxt_events = collect_week_events(by_id, week_offset=1)
    nxt_hash = compute_weekly_hash(nxt_events)
    nxt_info = (
        f"{len(nxt_events)} raid(s), week {nxt_week_key} "
        f"({nxt_monday.isoformat()}..{nxt_sunday.isoformat()})"
    )
    if not mapping.get("next_message_id"):
        plan.entries.append(
            SyncPlanEntry(
                SyncAction.CREATE,
                "weekly_overview",
                weekly_name,
                nxt_monday.isoformat(),
                "",
                "Overview (next week)",
                nxt_info,
            )
        )
    elif (
        mapping.get("next_hash") != nxt_hash
        or mapping.get("next_week_key") != nxt_week_key
    ) and not _would_blank_remote_week(
        discord, mapping.get("channel_id"), mapping.get("next_message_id"), nxt_hash
    ):
        plan.entries.append(
            SyncPlanEntry(
                SyncAction.UPDATE,
                "weekly_overview",
                weekly_name,
                nxt_monday.isoformat(),
                "",
                "Overview (next week)",
                nxt_info,
            )
        )

    return plan


def execute_weekly_sync(config: Config, discord: DiscordPoster) -> SyncResult:
    """Create or update the permanent weekly-overview thread (current + next week)."""
    result = SyncResult()
    if not discord.is_configured:
        return result

    by_id, errors = _load_events_for_overview(config)
    result.errors.extend(errors)
    if errors:
        return result

    # Skip writing if another client already pushed newer SavedVariables data.
    # Same guard used by per-event Discord sync.
    if _is_local_data_stale(config, discord):
        return result

    cur_monday, _cur_sunday, cur_week_key = current_week_bounds()
    nxt_monday, _nxt_sunday, nxt_week_key = next_week_bounds()
    cur_events = collect_week_events(by_id, week_offset=0)
    nxt_events = collect_week_events(by_id, week_offset=1)
    cur_hash = compute_weekly_hash(cur_events)
    nxt_hash = compute_weekly_hash(nxt_events)
    mapping: dict = config.get("discord_weekly_mapping", {}) or {}

    sv_path = config.saved_variables_path
    sv_mtime = int(sv_path.stat().st_mtime) if sv_path and sv_path.exists() else 0

    # Embed the tool version (before _h, so the hash/mtime suffix older clients
    # parse stays intact) — the sole cross-client version signal for the gate.
    vtag = f"_v{__version__}" if __version__ != "dev" else ""
    cur_filename = f"weekly_{cur_week_key}{vtag}_h{cur_hash}_t{sv_mtime}.png"
    nxt_filename = f"weekly_{nxt_week_key}{vtag}_h{nxt_hash}_t{sv_mtime}.png"

    try:
        cur_image = render_weekly_overview(cur_events, cur_monday)
        nxt_image = render_weekly_overview(nxt_events, nxt_monday)
    except Exception as e:
        result.errors.append(f"Weekly overview render failed: {e}")
        log.error("Weekly overview render failed: %s", e)
        return result

    channel_id = mapping.get("channel_id")

    # Self-heal: if the mapped weekly thread was deleted externally, drop it so
    # the adoption/creation path below recreates it instead of erroring on
    # every PATCH. (ensure_unarchived also unarchives a live thread here.)
    if channel_id and not discord.ensure_unarchived(channel_id):
        log.warning(
            "Weekly overview: mapped thread %s no longer exists — "
            "re-adopting or recreating",
            channel_id,
        )
        channel_id = None

    # Adopt an existing thread if another client already created it.
    # Try every supported language so a language switch doesn't recreate
    # the thread (e.g. an old "Wöchentliche Raid Übersicht" thread is
    # adopted after switching to English).
    if not channel_id:
        discord.clear_thread_cache()
        try:
            for candidate in candidate_weekly_thread_names():
                channel_id = discord.find_thread_by_name(candidate)
                if channel_id:
                    break
        except Exception as e:
            log.warning("Weekly overview: thread lookup failed: %s", e)
            channel_id = None

    cur_summary = format_weekly_summary(cur_monday, len(cur_events))
    nxt_summary = format_weekly_summary(nxt_monday, len(nxt_events))
    weekly_name = get_weekly_thread_name()
    next_message_id: str | None = mapping.get("next_message_id")

    try:
        # ---- Starter message (current week) ----
        if not channel_id:
            channel_id, _starter_id = discord.create_weekly_thread(
                weekly_name, cur_image, cur_filename, cur_summary
            )
            result.created += 1
        else:
            discord.ensure_unarchived(channel_id)
            # Discord forum-channel invariant: the starter-message id of a
            # forum thread equals the thread id itself. We always target the
            # starter, even if a previous (buggy) run stored a different
            # message_id in mapping.
            starter_id = channel_id
            mapping_was_correct = mapping.get("message_id") == channel_id
            same_week = mapping.get("week_key") == cur_week_key
            same_hash = mapping.get("hash") == cur_hash

            if mapping_was_correct and same_week and same_hash:
                result.skipped += 1
            elif _would_blank_remote_week(discord, channel_id, starter_id, cur_hash):
                # Local render is empty but the message currently shows a
                # populated week — almost always a stale/incomplete local read
                # (e.g. a second client whose SavedVariables lacks this week's
                # events). Refuse to clobber good remote data. See the Google
                # mass-deletion guard for the same philosophy.
                log.warning(
                    "Weekly overview: refusing to overwrite populated current "
                    "week with an empty render (likely stale local data)"
                )
                result.skipped += 1
            elif discord.message_exists(channel_id, starter_id):
                discord.update_weekly_image(
                    channel_id, starter_id, cur_image, cur_filename, cur_summary
                )
                result.updated += 1
            else:
                # Forum starter shouldn't disappear without the thread also
                # being gone — but if it does, post a fresh image.
                discord.post_weekly_image(channel_id, cur_image, cur_filename)
                result.created += 1

        # ---- Second message (next week) ----
        # Existing message: PATCH if anything changed, else skip.
        # Missing or gone: POST a new reply.
        if next_message_id and discord.message_exists(channel_id, next_message_id):
            same_week = mapping.get("next_week_key") == nxt_week_key
            same_hash = mapping.get("next_hash") == nxt_hash
            if same_week and same_hash:
                result.skipped += 1
            elif _would_blank_remote_week(
                discord, channel_id, next_message_id, nxt_hash
            ):
                log.warning(
                    "Weekly overview: refusing to overwrite populated next "
                    "week with an empty render (likely stale local data)"
                )
                result.skipped += 1
            else:
                discord.update_weekly_image(
                    channel_id,
                    next_message_id,
                    nxt_image,
                    nxt_filename,
                    nxt_summary,
                )
                result.updated += 1
        else:
            next_message_id = discord.post_weekly_image(
                channel_id, nxt_image, nxt_filename, content=nxt_summary
            )
            result.created += 1

        # Delete any leftover weekly images in the thread that aren't the
        # starter or the next-week message (residue from older clients).
        try:
            removed = discord.cleanup_weekly_thread_orphans(
                channel_id, keep_ids={next_message_id} if next_message_id else None
            )
            if removed:
                log.info(
                    "Weekly overview: cleaned up %d orphan reply image(s)",
                    removed,
                )
        except Exception as e:
            log.warning("Weekly overview: orphan cleanup failed: %s", e)
    except Exception as e:
        result.errors.append(f"Weekly overview sync failed: {e}")
        log.error("Weekly overview sync failed: %s", e)
        return result

    config.set(
        "discord_weekly_mapping",
        {
            "channel_id": channel_id,
            "message_id": channel_id,
            "hash": cur_hash,
            "week_key": cur_week_key,
            "next_message_id": next_message_id,
            "next_hash": nxt_hash,
            "next_week_key": nxt_week_key,
            "sv_mtime": sv_mtime,
        },
    )
    log.debug(
        "Weekly overview: %d created, %d updated, %d skipped",
        result.created,
        result.updated,
        result.skipped,
    )
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


def _collect_all_future_events(
    config: Config,
) -> tuple[dict[str, CalendarEvent], set[str], list[str]]:
    """Return all future events for the guild, regardless of participation."""
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

    wow_events = extract_events(db, guild_key)
    deleted_ids = get_deleted_event_ids(db, guild_key)
    today = date.today()
    # Include events from yesterday so the 24-hour cleanup logic can
    # evaluate them instead of deleting their channels immediately.
    earliest = today - timedelta(days=1)
    cutoff = today + timedelta(days=DISCORD_LOOKAHEAD_DAYS)

    result: dict[str, CalendarEvent] = {}
    for evt in wow_events:
        try:
            evt_date = date.fromisoformat(evt.date)
        except ValueError:
            log.debug(
                "Discord collect: skipping %s (%s) — invalid date",
                evt.event_id,
                evt.title,
            )
            continue
        if evt_date < earliest or evt_date > cutoff:
            log.debug(
                "Discord collect: skipping %s (%s) — date %s outside window [%s, %s]",
                evt.event_id,
                evt.title,
                evt.date,
                earliest,
                cutoff,
            )
            continue
        # Only include events where a roster has been created (confirmed members with groups)
        has_roster = any(
            p.group > 0 and p.attendance == Attendance.CONFIRMED
            for p in evt.participants
        )
        if not has_roster:
            log.debug(
                "Discord collect: skipping %s (%s) — no confirmed roster",
                evt.event_id,
                evt.title,
            )
            continue
        result[evt.event_id] = evt

    return result, deleted_ids, errors


def _find_participating_character(
    evt: CalendarEvent, character_names: list[str]
) -> str | None:
    for p in evt.participants:
        if p.name in character_names and Attendance.is_active(p.attendance):
            return p.name
    return None


def _sync_event_ics(
    discord: DiscordPoster,
    channel_id: str,
    event: CalendarEvent,
    timezone: str,
    duration_hours: float,
    existing: dict | None,
    is_new_thread: bool,
) -> dict | None:
    """Create / update / adopt the calendar (.ics) attachment for an event.

    Mirrors the roster-image lifecycle: adopt an existing file before posting a
    duplicate, PATCH in place when the content hash changes, skip when current.
    Best-effort — a failure here never aborts the event sync. Returns the
    ``{"ics_id", "hash"}`` mapping entry, or ``None`` on failure.
    """
    from fgc_sync.i18n import t as _t
    from fgc_sync.services.ics import compute_ics_hash

    label = _t("discord.calendar_attachment")
    target_hash = compute_ics_hash(event, timezone, duration_hours)
    try:
        ics_id = (existing or {}).get("ics_id")
        ics_hash = (existing or {}).get("hash")

        # Recover the message id / current hash from the thread when the local
        # mapping has lost track (new adoption, multi-client, legacy mapping).
        if not is_new_thread and not ics_id:
            found = discord.find_ics_message(channel_id)
            if found:
                ics_id = found["ics_id"]
                ics_hash = found["hash"]

        if not ics_id:
            return discord.post_ics(channel_id, event, timezone, duration_hours, label)
        if ics_hash != target_hash:
            return discord.update_ics(
                channel_id, ics_id, event, timezone, duration_hours
            )
        return {"ics_id": ics_id, "hash": ics_hash}
    except Exception as e:  # best-effort; never break the event sync
        log.warning("Discord: calendar file sync failed for %s: %s", event.title, e)
        return existing


def _event_to_datetime(event: CalendarEvent, timezone: str) -> datetime:
    parts = event.date.split("-")
    return datetime(
        int(parts[0]),
        int(parts[1]),
        int(parts[2]),
        event.server_hour,
        event.server_minute,
        tzinfo=ZoneInfo(timezone),
    )
