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

from fgc_sync.models.enums import Attendance
from fgc_sync.models.events import CalendarEvent
from fgc_sync.services.roster_image import render_roster

log = logging.getLogger(__name__)

BASE_URL = "https://discord.com/api/v10"

_FILENAME_PATTERN = re.compile(r"roster_(.+)_h([a-f0-9]+)(?:_t(\d+))?\.png")

# German weekday abbreviations (Monday=0 … Sunday=6)
_WEEKDAYS_DE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

# Short raid names for thread titles
RAID_SHORT_NAMES: dict[str, str] = {
    "karazhan": "Kara",
    "gruul": "Gruul",
    "magtheridon": "Maggi",
    "serpentshrine": "SSC",
    "tempest_keep": "TK",
    "hyjal": "Hyjal",
    "black_temple": "BT",
    "sunwell": "SWP",
    "zulaman": "ZA",
}


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
    def _thread_name(event: CalendarEvent) -> str:
        """Generate a human-readable forum thread name.

        Example: ``Do 10.04. 20:00 — Kara mit Forga``
        """
        dt = _date.fromisoformat(event.date)
        weekday = _WEEKDAYS_DE[dt.weekday()]
        date_part = f"{dt.day:02d}.{dt.month:02d}."
        time_part = f"{event.server_hour:02d}:{event.server_minute:02d}"
        raid_part = _short_raid_name(event.raid) if event.raid else event.title
        creator = event.creator or "Unknown"
        return f"{weekday} {date_part} {time_part} \u2014 {raid_part} mit {creator}"

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
        filename = f"roster_{event.event_id}_h{content_hash}_t{sv_mtime}.png"

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

    def ensure_unarchived(self, thread_id: str):
        """Unarchive a forum thread if it has been auto-archived."""
        try:
            data = self._request("GET", f"/channels/{thread_id}")
            if data and data.get("thread_metadata", {}).get("archived"):
                self._request(
                    "PATCH", f"/channels/{thread_id}", json={"archived": False}
                )
                log.debug("Discord: unarchived thread %s", thread_id)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (404, 403):
                return
            raise

    def find_existing_thread(self, event: CalendarEvent) -> dict | None:
        """Search forum threads for one that belongs to this event.

        Matches first by deterministic thread name (avoids races where another
        client just created the thread but has not uploaded the image yet),
        then falls back to scanning recent messages for a matching roster image.
        """
        event_id = event.event_id
        expected_name = self._thread_name(event)
        threads = self._get_forum_threads()
        log.debug("Scanning %d forum threads for event %s", len(threads), event_id)

        # 1. Match by deterministic thread name
        for thread in threads:
            if thread.get("name") == expected_name:
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

        Returns ``{"image_id": ..., "hash": ...}`` or ``None``.
        """
        try:
            messages = self._request(
                "GET",
                f"/channels/{thread_id}/messages",
                params={"limit": _PING_HISTORY_SCAN_LIMIT},
            )
            for msg in messages or []:
                for att in msg.get("attachments", []):
                    m = _FILENAME_PATTERN.match(att.get("filename", ""))
                    if m and m.group(1) == event_id:
                        return {"image_id": msg["id"], "hash": m.group(2)}
        except requests.HTTPError:
            pass
        return None

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
    ) -> str:
        """Post the weekly overview image in an existing thread. Returns message id."""
        data = self._upload_image(
            "POST",
            f"/channels/{channel_id}/messages",
            image_bytes,
            filename,
            "",
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
        filename = f"roster_{event.event_id}_h{content_hash}_t{sv_mtime}.png"

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
        filename = f"roster_{event.event_id}_h{content_hash}_t{sv_mtime}.png"

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

    def ping_members(
        self,
        channel_id: str,
        names: set[str],
        label: str = "Confirmed",
    ) -> set[str]:
        """Post a one-off ping message for the given character names.

        Returns the subset of names that resolved to a Discord member and
        were actually mentioned. Names that did not resolve are not returned,
        so the caller can retry them on a later sync (e.g. when the user
        finally joins the Discord server).
        """
        mentions = []
        resolved: set[str] = set()
        for name in sorted(names):
            user_id = self._find_member_id(name)
            if user_id:
                mentions.append(f"<@{user_id}>")
                resolved.add(name)

        if mentions:
            self._request(
                "POST",
                f"/channels/{channel_id}/messages",
                json={"content": f"{label}: " + " ".join(mentions)},
            )
            log.info("Discord: pinged %d members (%s)", len(mentions), label)
        return resolved

    def get_already_pinged_names(
        self,
        channel_id: str,
        candidate_names: set[str],
    ) -> set[str]:
        """Scan thread history for bot ping messages and return character
        names (from *candidate_names*) that have already been mentioned.

        This makes ping deduplication resilient to multi-client scenarios
        where the local ``pinged`` list is empty but the thread already
        contains ping messages from another client.
        """
        bot_id = self._get_bot_user_id()
        if not bot_id:
            return set()

        try:
            messages = self._request(
                "GET",
                f"/channels/{channel_id}/messages",
                params={"limit": _PING_HISTORY_SCAN_LIMIT},
            )
        except requests.HTTPError:
            return set()

        # Collect all user IDs the bot has already pinged
        pinged_user_ids: set[str] = set()
        for msg in messages or []:
            if msg.get("author", {}).get("id") != bot_id:
                continue
            content = msg.get("content", "")
            if content.startswith("Confirmed:") or content.startswith(
                "Newly confirmed:"
            ):
                pinged_user_ids.update(re.findall(r"<@(\d+)>", content))

        if not pinged_user_ids:
            return set()

        # Reverse-resolve: check which candidate names map to already-pinged IDs
        result: set[str] = set()
        for name in candidate_names:
            user_id = self._find_member_id(name)
            if user_id and user_id in pinged_user_ids:
                result.add(name)

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
    ) -> dict:
        files = {"files[0]": (filename, image_bytes, "image/png")}
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
