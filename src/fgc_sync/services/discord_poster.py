"""Discord REST API client — manages per-event forum threads with roster images."""

from __future__ import annotations

import hashlib
import json as _json
import logging
import re
import time
import unicodedata
from datetime import date as _date

import requests

from fgc_sync import i18n
from fgc_sync._version import __version__
from fgc_sync.i18n import t, tl_for
from fgc_sync.models.enums import Attendance
from fgc_sync.models.events import CalendarEvent
from fgc_sync.services.ics import compute_ics_hash, ics_filename, render_ics
from fgc_sync.services.roster_image import render_roster

log = logging.getLogger(__name__)

BASE_URL = "https://discord.com/api/v10"

# Image filenames optionally embed the writing client's tool version as a
# ``_v<version>`` segment placed *before* ``_h`` (e.g.
# ``roster_<id>_v2.11.1_h<hash>_t<mtime>.png``). It sits before ``_h`` so the
# hash/mtime suffix that older clients rely on still parses unchanged — only
# the leading free segment is affected. This is the sole cross-client version
# signal (no names, no separate message); the defer-to-newer gate reads it.
_FILENAME_PATTERN = re.compile(
    r"roster_(.+?)(?:_v[\d.]+)?_h([a-f0-9]+)(?:_t(\d+))?\.png"
)
_WEEKLY_FILENAME_PATTERN = re.compile(r"weekly_.+\.png")
# Roster ICS attachment: ``<Raid>_<date>_h<hash>.ics``. The ``_h<hash>`` suffix
# lets the thread scan detect a stale calendar file without downloading it.
_ICS_FILENAME_PATTERN = re.compile(r"_h([a-f0-9]+)\.ics$")
# Same shape as above but captures the embedded content hash so we can read the
# image a message *currently* shows (independent of any one client's mapping).
_WEEKLY_HASH_PATTERN = re.compile(r"weekly_.+_h([a-f0-9]+)_t\d+\.png")
# Extracts the embedded tool version from any image filename, if present.
_FILENAME_VERSION_PATTERN = re.compile(r"_v(\d+(?:\.\d+)+)_h[a-f0-9]+")

# Marks the deprecated client-registry control message so leftovers posted by
# pre-2.11.1 clients can be located and deleted.
_REGISTRY_MARKER = "[FGC-SYNC-REGISTRY]"
# Tags a changelog message so the same release isn't announced twice.
_UPDATE_NOTICE_MARKER = "[FGC-SYNC-UPDATE]"


def _version_filename_tag() -> str:
    """The ``_v<version>`` filename segment for this client (empty for dev)."""
    return f"_v{__version__}" if __version__ != "dev" else ""


# Short raid names for thread titles. Keys cover both the addon's canonical
# raid keys (EVENT_OPTIONS in the addon's UI-Editor.lua: ssc, tk, za, ...) and
# the legacy long-form spellings older events used.
RAID_SHORT_NAMES: dict[str, str] = {
    "karazhan": "Kara",
    "gruul": "Gruul",
    "magtheridon": "Maggi",
    "serpentshrine": "SSC",
    "ssc": "SSC",
    "tempest_keep": "TK",
    "tk": "TK",
    "hyjal": "Hyjal",
    "black_temple": "BT",
    "bt": "BT",
    "sunwell": "SWP",
    "swp": "SWP",
    "zulaman": "ZA",
    "za": "ZA",
    "ssc_tk": "SSC+TK",
    "gruul_mag": "Gruul+Maggi",
    "mc": "MC",
    "ony": "Ony",
    "bwl": "BWL",
    "zg": "ZG",
    "aq20": "AQ20",
    "aq40": "AQ40",
    "naxx": "Naxx",
}

# Roster size per raid. Drives the "full" highlight + "open spots" count
# in the weekly overview. Unknown raids fall back to RAID_MAX_SIZE_DEFAULT.
RAID_MAX_SIZE: dict[str, int] = {
    "karazhan": 10,
    "gruul": 25,
    "magtheridon": 25,
    "serpentshrine": 25,
    "ssc": 25,
    "tempest_keep": 25,
    "tk": 25,
    "hyjal": 25,
    "black_temple": 25,
    "bt": 25,
    "sunwell": 25,
    "swp": 25,
    "zulaman": 10,
    "za": 10,
    "ssc_tk": 25,
    "gruul_mag": 25,
    "mc": 40,
    "ony": 40,
    "bwl": 40,
    "zg": 20,
    "aq20": 20,
    "aq40": 40,
    "naxx": 40,
}
RAID_MAX_SIZE_DEFAULT = 25


def _short_raid_name(raid: str) -> str:
    """Convert a raid field value to a short name for thread titles."""
    raid_lower = raid.lower().replace(" ", "_")
    if raid_lower in RAID_SHORT_NAMES:
        return RAID_SHORT_NAMES[raid_lower]
    for key, short in RAID_SHORT_NAMES.items():
        if key in raid_lower:
            return short
    # Fallback: titlecase the raw raid name
    return raid.replace("_", " ").title()[:15] or "Event"


def max_roster_size(raid: str) -> int:
    """Return the max roster size for *raid* (e.g. 10 for Kara, 25 for Gruul)."""
    if not raid:
        return RAID_MAX_SIZE_DEFAULT
    raid_lower = raid.lower().replace(" ", "_")
    if raid_lower in RAID_MAX_SIZE:
        return RAID_MAX_SIZE[raid_lower]
    for key, size in RAID_MAX_SIZE.items():
        if key in raid_lower:
            return size
    return RAID_MAX_SIZE_DEFAULT


def compute_event_hash(event: CalendarEvent) -> str:
    """Compute a short content hash from event data."""
    confirmed = sorted(
        p.name for p in event.participants if p.attendance == Attendance.CONFIRMED
    )
    signed = sorted(
        p.name for p in event.participants if p.attendance == Attendance.SIGNED
    )
    benched = sorted(
        p.name for p in event.participants if p.attendance == Attendance.BENCHED
    )
    groups = sorted(
        (p.name, p.group, p.slot) for p in event.participants if p.group > 0
    )
    payload = (
        f"{event.event_id}|{event.revision}|{confirmed}|{signed}|{benched}|{groups}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:8]


def _slugify(text: str, max_len: int = 90) -> str:
    """Convert text to a Discord channel name (lowercase, hyphens, ascii)."""
    # Normalize unicode, strip accents
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    # Replace non-alphanumeric with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:max_len]


_HTTP_TIMEOUT = 30  # seconds for all Discord API calls
_MAX_RETRIES = 3
_MEMBERS_PER_PAGE = 1000
_MESSAGE_SCAN_LIMIT = 5
_PING_HISTORY_SCAN_LIMIT = 100


class DiscordPoster:
    """Synchronous Discord REST client with per-event forum thread management."""

    def __init__(self, bot_token: str, forum_id: str, guild_id: str):
        self._forum_id = forum_id
        self._guild_id = guild_id
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bot {bot_token}"
        self._members_cache: list[dict] | None = None
        self._forum_threads_cache: list[dict] | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self._forum_id and self._guild_id)

    # -- Thread management --

    @staticmethod
    def _format_thread_name(event: CalendarEvent, language: str) -> str:
        """Generate a forum thread name in *language*.

        Example: ``Do 10.04. 20:00 — Kara mit Forga``
        """
        dt = _date.fromisoformat(event.date)
        weekdays = tl_for(language, "discord.weekday_abbrev")
        weekday = weekdays[dt.weekday()] if len(weekdays) == 7 else dt.strftime("%a")
        date_part = f"{dt.day:02d}.{dt.month:02d}."
        time_part = f"{event.server_hour:02d}:{event.server_minute:02d}"
        raid_part = _short_raid_name(event.raid) if event.raid else event.title
        creator = event.creator or "Unknown"
        with_word = i18n.t_for(language, "discord.thread_with_word")
        return f"{weekday} {date_part} {time_part} \u2014 {raid_part} {with_word} {creator}"

    @staticmethod
    def _thread_name(event: CalendarEvent) -> str:
        """Generate the thread name in the currently active language."""
        return DiscordPoster._format_thread_name(event, i18n.get_language())

    @staticmethod
    def _candidate_thread_names(event: CalendarEvent) -> list[str]:
        """All thread-name variants the event might have under any supported
        language. Used so existing threads remain discoverable after a
        language switch.
        """
        seen: set[str] = set()
        out: list[str] = []
        for code in (i18n.get_language(), *i18n.available_languages()):
            name = DiscordPoster._format_thread_name(event, code)
            if name not in seen:
                seen.add(name)
                out.append(name)
        return out

    def create_event_thread(
        self,
        event: CalendarEvent,
        timezone: str,
        sv_mtime: int = 0,
    ) -> tuple[str, dict]:
        """Create a forum thread with a roster image as the starter message.

        Returns (thread_id, message_ids) where message_ids contains
        image_id, hash, and sv_mtime.
        """
        name = self._thread_name(event)
        content_hash = compute_event_hash(event)
        image_bytes = render_roster(event, timezone)
        filename = (
            f"roster_{event.event_id}{_version_filename_tag()}"
            f"_h{content_hash}_t{sv_mtime}.png"
        )

        payload = {
            "name": name,
            "message": {
                "content": "",
                "attachments": [{"id": 0, "filename": filename}],
            },
        }
        data = self._upload_multipart(
            "POST",
            f"/channels/{self._forum_id}/threads",
            payload,
            image_bytes,
            filename,
        )
        thread_id = data["id"]
        message_id = data.get("message", {}).get("id")
        log.info("Discord: created thread %s (%s) for %s", name, thread_id, event.title)

        return thread_id, {
            "image_id": message_id,
            "hash": content_hash,
            "sv_mtime": sv_mtime,
        }

    def delete_thread(self, thread_id: str):
        """Delete a forum thread. Silently succeeds if already deleted."""
        try:
            self._request("DELETE", f"/channels/{thread_id}")
            log.info("Discord: deleted thread %s", thread_id)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                log.info("Discord: thread %s already deleted", thread_id)
            else:
                raise

    def thread_exists(self, thread_id: str) -> bool:
        """Check if a thread still exists."""
        try:
            self._request("GET", f"/channels/{thread_id}")
            return True
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (404, 403):
                return False
            raise

    def ensure_unarchived(self, thread_id: str) -> bool:
        """Unarchive a forum thread if it has been auto-archived.

        Returns ``False`` only when the thread no longer exists (404) so the
        caller can self-heal by recreating it. A 403 (exists but inaccessible)
        returns ``True`` — recreating in that case would just spawn a duplicate.
        """
        try:
            data = self._request("GET", f"/channels/{thread_id}")
            if data and data.get("thread_metadata", {}).get("archived"):
                self._request(
                    "PATCH", f"/channels/{thread_id}", json={"archived": False}
                )
                log.debug("Discord: unarchived thread %s", thread_id)
            return True
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return False
            if e.response is not None and e.response.status_code == 403:
                return True
            raise

    def find_existing_thread(self, event: CalendarEvent) -> dict | None:
        """Search forum threads for one that belongs to this event.

        Matches first by deterministic thread name in the active language,
        then by names in any other supported language (so threads created
        under a different language are still discoverable), and finally
        falls back to scanning recent messages for a matching roster image.
        """
        event_id = event.event_id
        candidate_names = set(self._candidate_thread_names(event))
        threads = self._get_forum_threads()
        log.debug("Scanning %d forum threads for event %s", len(threads), event_id)

        # 1. Match by deterministic thread name (any language)
        for thread in threads:
            if thread.get("name") in candidate_names:
                th_id = thread["id"]
                log.info("Discord: matched existing thread by name for %s", event.title)
                found = self._find_image_in_thread(th_id, event_id)
                if found:
                    return {"channel_id": th_id, **found}
                return {"channel_id": th_id, "image_id": None, "hash": None}

        # 2. Fall back to attachment scan (legacy threads with non-matching names)
        for thread in threads:
            th_id = thread["id"]
            found = self._find_image_in_thread(th_id, event_id)
            if found:
                return {"channel_id": th_id, **found}
        return None

    def _find_image_in_thread(self, thread_id: str, event_id: str) -> dict | None:
        """Scan up to 100 messages in a thread for a roster image attachment.

        Returns ``{"image_id": ..., "hash": ..., "version": ...}`` (version is
        ``None`` if the filename carries no ``_v`` tag) or ``None``.
        """
        try:
            messages = self._request(
                "GET",
                f"/channels/{thread_id}/messages",
                params={"limit": _PING_HISTORY_SCAN_LIMIT},
            )
            for msg in messages or []:
                for att in msg.get("attachments", []):
                    fn = att.get("filename", "")
                    m = _FILENAME_PATTERN.match(fn)
                    if m and m.group(1) == event_id:
                        vm = _FILENAME_VERSION_PATTERN.search(fn)
                        return {
                            "image_id": msg["id"],
                            "hash": m.group(2),
                            "version": vm.group(1) if vm else None,
                        }
        except requests.HTTPError:
            pass
        return None

    def find_event_threads(self, event: CalendarEvent) -> list[dict]:
        """Return every forum thread that belongs to *event*, in any language.

        Each entry is ``{channel_id, image_id, hash, version}``. Matches by
        deterministic thread name (any supported language) and by roster-image
        attachment. Used to detect and collapse cross-language / multi-client
        duplicate threads down to a single survivor.
        """
        candidate_names = set(self._candidate_thread_names(event))
        out: list[dict] = []
        seen: set[str] = set()
        for thread in self._get_forum_threads():
            th_id = thread["id"]
            if th_id in seen:
                continue
            found = self._find_image_in_thread(th_id, event.event_id)
            if thread.get("name") in candidate_names or found:
                seen.add(th_id)
                out.append(
                    {
                        "channel_id": th_id,
                        "image_id": (found or {}).get("image_id"),
                        "hash": (found or {}).get("hash"),
                        "version": (found or {}).get("version"),
                    }
                )
        return out

    def find_image_message(self, channel_id: str, event_id: str) -> str | None:
        """Find the message ID of the roster image in a thread.

        Called by the sync engine before posting a duplicate image.
        """
        found = self._find_image_in_thread(channel_id, event_id)
        return found["image_id"] if found else None

    def _get_forum_threads(self) -> list[dict]:
        """Get threads under the configured forum (cached per cycle)."""
        if self._forum_threads_cache is not None:
            return self._forum_threads_cache

        threads: list[dict] = []

        # Active threads (guild-wide endpoint, filter to our forum)
        data = self._request("GET", f"/guilds/{self._guild_id}/threads/active")
        if data and "threads" in data:
            threads.extend(
                t for t in data["threads"] if t.get("parent_id") == self._forum_id
            )

        active_ids = {t["id"] for t in threads}

        # Archived threads (forum-specific)
        data = self._request(
            "GET",
            f"/channels/{self._forum_id}/threads/archived/public",
            params={"limit": _PING_HISTORY_SCAN_LIMIT},
        )
        if data and "threads" in data:
            threads.extend(t for t in data["threads"] if t["id"] not in active_ids)

        self._forum_threads_cache = threads
        return threads

    def clear_thread_cache(self):
        """Clear forum threads cache. Call once per sync cycle."""
        self._forum_threads_cache = None

    def find_thread_by_name(self, name: str) -> str | None:
        """Return the thread id of the first forum thread matching *name*."""
        for thread in self._get_forum_threads():
            if thread.get("name") == name:
                return thread["id"]
        return None

    def create_weekly_thread(
        self,
        name: str,
        image_bytes: bytes,
        filename: str,
        content: str = "",
    ) -> tuple[str, str]:
        """Create a forum thread with a starter image. Returns (thread_id, image_id)."""
        payload = {
            "name": name,
            "message": {
                "content": content,
                "attachments": [{"id": 0, "filename": filename}],
            },
        }
        data = self._upload_multipart(
            "POST",
            f"/channels/{self._forum_id}/threads",
            payload,
            image_bytes,
            filename,
        )
        thread_id = data["id"]
        message_id = data.get("message", {}).get("id")
        log.info("Discord: created weekly thread %s (%s)", name, thread_id)
        return thread_id, message_id

    def post_weekly_image(
        self,
        channel_id: str,
        image_bytes: bytes,
        filename: str,
        content: str = "",
    ) -> str:
        """Post the weekly overview image in an existing thread. Returns message id."""
        data = self._upload_image(
            "POST",
            f"/channels/{channel_id}/messages",
            image_bytes,
            filename,
            content,
        )
        return data["id"]

    def update_weekly_image(
        self,
        channel_id: str,
        message_id: str,
        image_bytes: bytes,
        filename: str,
        content: str = "",
    ):
        """Patch the image and (optionally) content of an existing message."""
        self._upload_image(
            "PATCH",
            f"/channels/{channel_id}/messages/{message_id}",
            image_bytes,
            filename,
            content,
        )

    def cleanup_weekly_thread_orphans(
        self,
        channel_id: str,
        keep_ids: set[str] | None = None,
    ) -> int:
        """Delete leftover weekly-overview reply messages in *channel_id*.

        The weekly thread holds the starter (current-week image, id == thread id)
        plus one reply (next-week image, id in *keep_ids*). Anything else with a
        ``weekly_*.png`` attachment is residue from past buggy clients — scan
        the last 100 messages and delete it. Returns the number removed.
        """
        protected = {channel_id}
        if keep_ids:
            protected.update(kid for kid in keep_ids if kid)

        try:
            messages = self._request(
                "GET",
                f"/channels/{channel_id}/messages",
                params={"limit": _PING_HISTORY_SCAN_LIMIT},
            )
        except requests.HTTPError:
            return 0

        removed = 0
        for msg in messages or []:
            msg_id = msg.get("id")
            if not msg_id or msg_id in protected:
                continue
            has_weekly_image = any(
                _WEEKLY_FILENAME_PATTERN.match(att.get("filename", ""))
                for att in msg.get("attachments", [])
            )
            if not has_weekly_image:
                continue
            try:
                self._request("DELETE", f"/channels/{channel_id}/messages/{msg_id}")
                removed += 1
                log.info(
                    "Discord: deleted orphan weekly reply %s in thread %s",
                    msg_id,
                    channel_id,
                )
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    continue
                log.warning("Discord: failed to delete orphan %s: %s", msg_id, e)
        return removed

    def get_max_remote_sv_mtime(self) -> int:
        """Scan the last few messages of every forum thread and return the
        highest SavedVariables mtime embedded in any roster image filename.

        Used to decide whether the local client has stale data and should
        skip writing to Discord (avoiding flapping between two clients).
        """
        threads = self._get_forum_threads()
        max_ts = 0
        for th in threads:
            th_id = th["id"]
            try:
                messages = self._request(
                    "GET",
                    f"/channels/{th_id}/messages",
                    params={"limit": _MESSAGE_SCAN_LIMIT},
                )
            except requests.HTTPError:
                continue
            for msg in messages or []:
                for att in msg.get("attachments", []):
                    m = _FILENAME_PATTERN.match(att.get("filename", ""))
                    if m and m.group(3):
                        try:
                            ts = int(m.group(3))
                            if ts > max_ts:
                                max_ts = ts
                        except ValueError:
                            pass
        return max_ts

    # -- Message posting --

    def post_event(
        self,
        channel_id: str,
        event: CalendarEvent,
        timezone: str,
        sv_mtime: int = 0,
    ) -> dict:
        """Post roster image in an existing thread. Returns {image_id, hash, sv_mtime}.

        Used as fallback when the original image was deleted from a thread.
        """
        content_hash = compute_event_hash(event)
        image_bytes = render_roster(event, timezone)
        filename = (
            f"roster_{event.event_id}{_version_filename_tag()}"
            f"_h{content_hash}_t{sv_mtime}.png"
        )

        data = self._upload_image(
            "POST",
            f"/channels/{channel_id}/messages",
            image_bytes,
            filename,
            "",
        )
        image_msg_id = data["id"]
        log.info("Discord: posted image %s for %s", image_msg_id, event.title)

        return {"image_id": image_msg_id, "hash": content_hash, "sv_mtime": sv_mtime}

    def update_event(
        self,
        channel_id: str,
        message_ids: dict,
        event: CalendarEvent,
        timezone: str,
        sv_mtime: int = 0,
    ) -> dict:
        """Edit an existing roster image."""
        content_hash = compute_event_hash(event)
        image_bytes = render_roster(event, timezone)
        image_msg_id = message_ids["image_id"]
        filename = (
            f"roster_{event.event_id}{_version_filename_tag()}"
            f"_h{content_hash}_t{sv_mtime}.png"
        )

        self._upload_image(
            "PATCH",
            f"/channels/{channel_id}/messages/{image_msg_id}",
            image_bytes,
            filename,
            "",
        )
        message_ids["hash"] = content_hash
        message_ids["sv_mtime"] = sv_mtime
        log.info("Discord: updated image %s for %s", image_msg_id, event.title)
        return message_ids

    # -- Calendar (.ics) attachment --

    def find_ics_message(self, channel_id: str) -> dict | None:
        """Scan a thread for the 'Add to my calendar' .ics attachment.

        Returns ``{"ics_id": <message_id>, "hash": <embedded_hash>}`` for the
        first ``*.ics`` attachment found, or ``None``. One thread holds at most
        one calendar file, so the extension alone identifies it; the embedded
        ``_h<hash>`` tells the caller whether it is current.
        """
        try:
            messages = self._request(
                "GET",
                f"/channels/{channel_id}/messages",
                params={"limit": _PING_HISTORY_SCAN_LIMIT},
            )
            for msg in messages or []:
                for att in msg.get("attachments", []):
                    m = _ICS_FILENAME_PATTERN.search(att.get("filename", ""))
                    if m:
                        return {"ics_id": msg["id"], "hash": m.group(1)}
        except requests.HTTPError:
            pass
        return None

    def post_ics(
        self,
        channel_id: str,
        event: CalendarEvent,
        timezone: str,
        duration_hours: float,
        content: str,
    ) -> dict:
        """Post the calendar (.ics) attachment as a new message in a thread.

        Returns ``{"ics_id": <message_id>, "hash": <content_hash>}``.
        """
        content_hash = compute_ics_hash(event, timezone, duration_hours)
        ics_bytes = render_ics(event, timezone, duration_hours)
        filename = ics_filename(event, content_hash)
        data = self._upload_image(
            "POST",
            f"/channels/{channel_id}/messages",
            ics_bytes,
            filename,
            content,
            content_type="text/calendar",
        )
        ics_msg_id = data["id"]
        log.info("Discord: posted calendar file %s for %s", ics_msg_id, event.title)
        return {"ics_id": ics_msg_id, "hash": content_hash}

    def update_ics(
        self,
        channel_id: str,
        ics_msg_id: str,
        event: CalendarEvent,
        timezone: str,
        duration_hours: float,
    ) -> dict:
        """Replace the .ics attachment on an existing message in place."""
        content_hash = compute_ics_hash(event, timezone, duration_hours)
        ics_bytes = render_ics(event, timezone, duration_hours)
        filename = ics_filename(event, content_hash)
        self._upload_image(
            "PATCH",
            f"/channels/{channel_id}/messages/{ics_msg_id}",
            ics_bytes,
            filename,
            "",
            content_type="text/calendar",
        )
        log.info("Discord: updated calendar file %s for %s", ics_msg_id, event.title)
        return {"ics_id": ics_msg_id, "hash": content_hash}

    def ping_members(
        self,
        channel_id: str,
        names: set[str],
        label: str | None = None,
    ) -> dict[str, str]:
        """Post a one-off ping message for the given character names.

        If *label* is None, the active-language ``ping_confirmed`` label is
        used. Returns ``{name: message_id}`` for names that resolved to a
        Discord member and were actually mentioned (all share the same
        message id). Names that did not resolve are absent, so the caller
        can retry them on a later sync (e.g. when the user finally joins
        the Discord server). The message id lets the caller later edit the
        @mention away if the member is removed from the roster.
        """
        if label is None:
            label = t("discord.ping_confirmed")
        mentions = []
        resolved: list[str] = []
        for name in sorted(names):
            user_id = self._find_member_id(name)
            if user_id:
                mentions.append(f"<@{user_id}>")
                resolved.append(name)

        if not mentions:
            return {}

        data = self._request(
            "POST",
            f"/channels/{channel_id}/messages",
            json={"content": f"{label}: " + " ".join(mentions)},
        )
        msg_id = data.get("id", "") if isinstance(data, dict) else ""
        log.info("Discord: pinged %d members (%s)", len(mentions), label)
        return {name: msg_id for name in resolved}

    def remove_mentions(
        self,
        channel_id: str,
        removals: dict[str, str],
    ) -> None:
        """Edit prior ping messages to strike out the @mentions of *removals*.

        *removals* is ``{character_name: message_id}``. Mentions are replaced
        with ``~~@<character_name>~~`` so they no longer match the
        ``<@user_id>`` syntax — Discord renders them struck through, the
        user is no longer counted as pinged by ``get_already_pinged_names``,
        and (because Discord does not re-notify on edits) the other members
        in the same message are not re-pinged. ``allowed_mentions`` is set
        to ``parse: []`` as a belt-and-braces guard.
        """
        by_message: dict[str, list[str]] = {}
        for name, msg_id in removals.items():
            if msg_id:
                by_message.setdefault(msg_id, []).append(name)

        for msg_id, names in by_message.items():
            try:
                msg = self._request("GET", f"/channels/{channel_id}/messages/{msg_id}")
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    log.debug("Discord: ping message %s already deleted", msg_id)
                    continue
                log.warning("Discord: failed to fetch message %s: %s", msg_id, e)
                continue
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", "")
            new_content = content
            for name in names:
                user_id = self._find_member_id(name)
                if not user_id:
                    continue
                pattern = re.compile(rf"<@!?{user_id}>")
                new_content = pattern.sub(f"~~@{name}~~", new_content)
            if new_content == content:
                continue
            try:
                self._request(
                    "PATCH",
                    f"/channels/{channel_id}/messages/{msg_id}",
                    json={
                        "content": new_content,
                        "allowed_mentions": {"parse": []},
                    },
                )
                log.info(
                    "Discord: removed %d mention(s) from message %s",
                    len(names),
                    msg_id,
                )
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    log.debug("Discord: ping message %s deleted before edit", msg_id)
                else:
                    log.warning(
                        "Discord: failed to edit ping message %s: %s", msg_id, e
                    )

    def get_already_pinged_names(
        self,
        channel_id: str,
        candidate_names: set[str],
    ) -> dict[str, str]:
        """Scan thread history for bot ping messages and return
        ``{name: message_id}`` for names from *candidate_names* that have
        already been mentioned.

        Returns the *most recent* message id per name when a name appears
        in multiple ping messages. Makes ping deduplication resilient to
        multi-client scenarios where the local ``pinged`` mapping is empty
        but the thread already contains ping messages from another client,
        and gives the caller a message id it can later edit to remove the
        @mention if the member leaves the roster.
        """
        bot_id = self._get_bot_user_id()
        if not bot_id:
            return {}

        try:
            messages = self._request(
                "GET",
                f"/channels/{channel_id}/messages",
                params={"limit": _PING_HISTORY_SCAN_LIMIT},
            )
        except requests.HTTPError:
            return {}

        # Collect every label prefix this bot might use, across all
        # supported languages — so language switches don't cause re-pings.
        ping_prefixes: tuple[str, ...] = tuple(
            f"{label}:"
            for label in (
                *i18n.t_all("discord.ping_confirmed"),
                *i18n.t_all("discord.ping_newly_confirmed"),
            )
        )

        # Pre-resolve each candidate to its Discord user id once.
        user_id_to_name: dict[str, str] = {}
        for name in candidate_names:
            uid = self._find_member_id(name)
            if uid:
                user_id_to_name[uid] = name

        if not user_id_to_name:
            return {}

        # Discord returns messages newest-first, so the first time we see
        # a user id is the most recent ping for that name.
        result: dict[str, str] = {}
        for msg in messages or []:
            if msg.get("author", {}).get("id") != bot_id:
                continue
            content = msg.get("content", "")
            if not any(content.startswith(p) for p in ping_prefixes):
                continue
            msg_id = msg.get("id", "")
            for uid in re.findall(r"<@!?(\d+)>", content):
                name = user_id_to_name.get(uid)
                if name and name not in result:
                    result[name] = msg_id

        if result:
            log.debug("Discord: %d names already pinged in thread history", len(result))
        return result

    def message_exists(self, channel_id: str, message_ids: dict | str) -> bool:
        """Check if the image message still exists."""
        msg_id = (
            message_ids["image_id"] if isinstance(message_ids, dict) else message_ids
        )
        try:
            self._request("GET", f"/channels/{channel_id}/messages/{msg_id}")
            return True
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return False
            raise

    def get_weekly_image_hash(self, channel_id: str, message_id: str) -> str | None:
        """Return the content hash embedded in *message_id*'s weekly image.

        Reads the attachment filename (``weekly_<week>_h<hash>_t<mtime>.png``)
        directly from Discord, so callers can tell what a message *actually*
        shows regardless of their own local mapping — needed to avoid an empty
        local render clobbering a populated image another client pushed.
        Returns ``None`` if the message is gone or carries no weekly image.
        """
        try:
            msg = self._request("GET", f"/channels/{channel_id}/messages/{message_id}")
        except requests.HTTPError:
            return None
        if not isinstance(msg, dict):
            return None
        for att in msg.get("attachments", []):
            m = _WEEKLY_HASH_PATTERN.match(att.get("filename", ""))
            if m:
                return m.group(1)
        return None

    def get_max_remote_version(self) -> str | None:
        """Highest tool version embedded in any forum image filename.

        The defer-to-newer gate compares this against the local version. Scans
        the last few messages of every forum thread for the ``_v<version>``
        filename segment. Returns ``None`` if no versioned image is found.
        """
        threads = self._get_forum_threads()
        best: tuple[int, ...] | None = None
        best_str: str | None = None
        for th in threads:
            try:
                messages = self._request(
                    "GET",
                    f"/channels/{th['id']}/messages",
                    params={"limit": _MESSAGE_SCAN_LIMIT},
                )
            except requests.HTTPError:
                continue
            for msg in messages or []:
                for att in msg.get("attachments", []):
                    m = _FILENAME_VERSION_PATTERN.search(att.get("filename", ""))
                    if not m:
                        continue
                    parts = tuple(int(x) for x in m.group(1).split("."))
                    if best is None or parts > best:
                        best, best_str = parts, m.group(1)
        return best_str

    def delete_registry_messages(self, channel_id: str) -> int:
        """Delete any leftover deprecated client-registry control messages.

        Pre-2.11.1 clients posted a ``[FGC-SYNC-REGISTRY]`` JSON message; this
        removes them so the data stops being visible once such clients update.
        Returns the number deleted.
        """
        try:
            messages = self._request(
                "GET",
                f"/channels/{channel_id}/messages",
                params={"limit": _PING_HISTORY_SCAN_LIMIT},
            )
        except requests.HTTPError:
            return 0
        removed = 0
        for msg in messages or []:
            if _REGISTRY_MARKER not in (msg.get("content") or ""):
                continue
            try:
                self._request("DELETE", f"/channels/{channel_id}/messages/{msg['id']}")
                removed += 1
            except requests.HTTPError as e:
                if e.response is None or e.response.status_code != 404:
                    log.warning("Discord: failed to delete registry message: %s", e)
        return removed

    def changelog_exists(self, channel_id: str, version: str) -> bool:
        """True if a changelog for *version* was already posted (dedup)."""
        try:
            messages = self._request(
                "GET",
                f"/channels/{channel_id}/messages",
                params={"limit": _PING_HISTORY_SCAN_LIMIT},
            )
        except requests.HTTPError:
            return False
        token = f"{_UPDATE_NOTICE_MARKER}{version}"
        return any(token in (m.get("content") or "") for m in messages or [])

    def create_changelog_thread(
        self,
        name: str,
        body: str,
        ping_user_ids: list[str],
        version: str,
    ) -> str | None:
        """Create the dedicated updates forum thread with *body* as its starter.

        Returns the new thread id. Pings the configured user ids and stamps the
        dedup marker, like :meth:`post_changelog`.
        """
        payload = {
            "name": name,
            "message": self._changelog_message(body, ping_user_ids, version),
        }
        data = self._request(
            "POST", f"/channels/{self._forum_id}/threads", json=payload
        )
        thread_id = data.get("id") if isinstance(data, dict) else None
        log.info("Discord: created updates thread %s for changelog %s", name, version)
        return thread_id

    def post_changelog(
        self,
        channel_id: str,
        body: str,
        ping_user_ids: list[str],
        version: str,
    ) -> str | None:
        """Post a changelog entry as a reply in the existing updates thread."""
        data = self._request(
            "POST",
            f"/channels/{channel_id}/messages",
            json=self._changelog_message(body, ping_user_ids, version),
        )
        log.info(
            "Discord: posted changelog for %s, pinged %d user(s)",
            version,
            len(ping_user_ids),
        )
        return data.get("id") if isinstance(data, dict) else None

    @staticmethod
    def _changelog_message(body: str, ping_user_ids: list[str], version: str) -> dict:
        """Build the message payload for a changelog post.

        Appends the configured pings and the per-version dedup marker, caps the
        content to Discord's 2000-char limit, and allows user mentions so the
        pings actually notify.
        """
        mentions = []
        for raw in ping_user_ids:
            uid = str(raw).strip()
            if not uid:
                continue
            mentions.append(uid if uid.startswith("<@") else f"<@{uid}>")
        marker = f"\n{_UPDATE_NOTICE_MARKER}{version}"
        ping_line = ("\n" + " ".join(mentions)) if mentions else ""
        # Reserve room for the ping line + marker within the 2000-char ceiling.
        budget = 2000 - len(marker) - len(ping_line)
        if len(body) > budget:
            body = body[: max(0, budget - 1)].rstrip() + "…"
        return {
            "content": f"{body}{ping_line}{marker}",
            "allowed_mentions": {"parse": ["users"]},
        }

    # -- Member lookup & pinging --

    def _fetch_guild_members(self) -> list[dict]:
        members: list[dict] = []
        after = "0"
        _start = time.monotonic()
        while True:
            batch = self._request(
                "GET",
                f"/guilds/{self._guild_id}/members",
                params={"limit": _MEMBERS_PER_PAGE, "after": after},
            )
            if not batch:
                break
            members.extend(batch)
            if len(batch) < _MEMBERS_PER_PAGE:
                break
            after = batch[-1]["user"]["id"]
        log.debug(
            "Fetched %d guild members in %.1fs", len(members), time.monotonic() - _start
        )
        return members

    def _get_members(self) -> list[dict]:
        if self._members_cache is None:
            self._members_cache = self._fetch_guild_members()
        return self._members_cache

    def clear_members_cache(self):
        self._members_cache = None

    def _get_bot_user_id(self) -> str | None:
        """Get the bot's own user ID (cached)."""
        if not hasattr(self, "_bot_user_id"):
            data = self._request("GET", "/users/@me")
            self._bot_user_id = data["id"] if data else None
        return self._bot_user_id

    def _find_member_id(self, character_name: str) -> str | None:
        char_lower = character_name.lower()
        for member in self._get_members():
            nick = (member.get("nick") or "").lower()
            user = member.get("user", {})
            global_name = (user.get("global_name") or "").lower()
            username = (user.get("username") or "").lower()
            if (
                char_lower in nick
                or char_lower in global_name
                or char_lower in username
            ):
                return user.get("id")
        return None

    # -- HTTP helpers --

    def _retry_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Execute an HTTP request with rate-limit retry."""
        resp = None
        for _attempt in range(_MAX_RETRIES):
            resp = self._session.request(
                method,
                url,
                timeout=_HTTP_TIMEOUT,
                **kwargs,
            )
            if resp.status_code == 429:
                retry_after = resp.json().get("retry_after", 1.0)
                log.warning("Discord rate limited, retrying after %.1fs", retry_after)
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp  # type: ignore[return-value]

    def _upload_image(
        self,
        method: str,
        path: str,
        image_bytes: bytes,
        filename: str,
        content: str,
        content_type: str = "image/png",
    ) -> dict:
        files = {"files[0]": (filename, image_bytes, content_type)}
        data = {"content": content} if content else {}
        resp = self._retry_request(
            method,
            BASE_URL + path,
            data=data,
            files=files,
        )
        return resp.json()

    def _upload_multipart(
        self,
        method: str,
        path: str,
        payload: dict,
        image_bytes: bytes,
        filename: str,
    ) -> dict:
        """Send a multipart request with payload_json + file attachment."""
        files = {
            "payload_json": (None, _json.dumps(payload), "application/json"),
            "files[0]": (filename, image_bytes, "image/png"),
        }
        resp = self._retry_request(method, BASE_URL + path, files=files)
        return resp.json()

    def _request(self, method: str, path: str, **kwargs) -> dict | list | None:
        headers = kwargs.pop("headers", {})
        headers["Content-Type"] = "application/json"
        resp = self._retry_request(
            method,
            BASE_URL + path,
            headers=headers,
            **kwargs,
        )
        if resp.status_code == 204:
            return None
        return resp.json()
