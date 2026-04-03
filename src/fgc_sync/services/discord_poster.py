"""Discord REST API client — manages per-event channels with roster images."""

from __future__ import annotations

import hashlib
import logging
import re
import time
import unicodedata

import requests

from fgc_sync.models.enums import Attendance
from fgc_sync.models.events import CalendarEvent
from fgc_sync.services.roster_image import render_roster

log = logging.getLogger(__name__)

BASE_URL = "https://discord.com/api/v10"

_FILENAME_PATTERN = re.compile(r"roster_(.+)_h([a-f0-9]+)\.png")


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
        (p.name, p.group, p.slot)
        for p in event.participants if p.group > 0
    )
    payload = f"{event.event_id}|{event.revision}|{confirmed}|{signed}|{benched}|{groups}"
    return hashlib.sha256(payload.encode()).hexdigest()[:8]


def _slugify(text: str, max_len: int = 90) -> str:
    """Convert text to a Discord channel name (lowercase, hyphens, ascii)."""
    # Normalize unicode, strip accents
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    # Replace non-alphanumeric with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:max_len]


class DiscordPoster:
    """Synchronous Discord REST client with per-event channel management."""

    def __init__(self, bot_token: str, category_id: str, guild_id: str):
        self._category_id = category_id
        self._guild_id = guild_id
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bot {bot_token}"
        self._members_cache: list[dict] | None = None
        self._category_channels_cache: list[dict] | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self._category_id and self._guild_id)

    # -- Channel management --

    def create_event_channel(self, event: CalendarEvent) -> str:
        """Create a private text channel for an event. Only confirmed members can see it."""
        name = f"{event.date}-{_slugify(event.title)}"
        location = event.raid.replace("_", " ").title() if event.raid else ""
        topic = f"{event.summary_line()} | {event.date} {event.time_str}"
        if location:
            topic += f" | {location}"

        # Build permission overwrites:
        # - Deny @everyone from viewing
        # - Allow the bot to manage the channel
        # - Allow each confirmed member to view
        overwrites = [
            {
                "id": self._guild_id,  # @everyone role ID = guild ID
                "type": 0,  # role
                "deny": "1024",  # VIEW_CHANNEL
                "allow": "0",
            },
        ]

        # Allow the bot itself
        bot_id = self._get_bot_user_id()
        if bot_id:
            overwrites.append({
                "id": bot_id,
                "type": 1,  # member
                "allow": str(1024 | 2048 | 16384 | 32768),  # VIEW + SEND + READ_HISTORY + MANAGE_MESSAGES
                "deny": "0",
            })

        # Allow each confirmed participant that has a Discord account
        confirmed = [p for p in event.participants if p.attendance == Attendance.CONFIRMED]
        for p in confirmed:
            user_id = self._find_member_id(p.name)
            if user_id:
                overwrites.append({
                    "id": user_id,
                    "type": 1,  # member
                    "allow": str(1024 | 2048 | 16384),  # VIEW + SEND + READ_HISTORY
                    "deny": "0",
                })

        data = self._request(
            "POST",
            f"/guilds/{self._guild_id}/channels",
            json={
                "name": name,
                "type": 0,  # GUILD_TEXT
                "parent_id": self._category_id,
                "topic": topic,
                "permission_overwrites": overwrites,
            },
        )
        channel_id = data["id"]
        log.info("Discord: created private channel #%s (%s) for %s", name, channel_id, event.title)
        return channel_id

    def update_channel_permissions(self, channel_id: str, event: CalendarEvent):
        """Update channel permissions when confirmed members change."""
        confirmed = [p for p in event.participants if p.attendance == Attendance.CONFIRMED]
        for p in confirmed:
            user_id = self._find_member_id(p.name)
            if user_id:
                try:
                    self._request(
                        "PUT",
                        f"/channels/{channel_id}/permissions/{user_id}",
                        json={
                            "type": 1,  # member
                            "allow": str(1024 | 2048 | 16384),  # VIEW + SEND + READ_HISTORY
                        },
                    )
                except requests.HTTPError as e:
                    log.warning("Discord: could not set permissions for %s: %s", p.name, e)

    def delete_channel(self, channel_id: str):
        """Delete a channel."""
        self._request("DELETE", f"/channels/{channel_id}")
        log.info("Discord: deleted channel %s", channel_id)

    def channel_exists(self, channel_id: str) -> bool:
        """Check if a channel still exists."""
        try:
            self._request("GET", f"/channels/{channel_id}")
            return True
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (404, 403):
                return False
            raise

    def find_existing_channel(self, event_id: str) -> dict | None:
        """Search category channels for one that has a roster image for this event."""
        for channel in self._get_category_channels():
            ch_id = channel["id"]
            # Check pinned or recent messages for a matching roster filename
            try:
                messages = self._request(
                    "GET", f"/channels/{ch_id}/messages", params={"limit": 5},
                )
                if not messages:
                    continue
                for msg in messages:
                    for att in msg.get("attachments", []):
                        m = _FILENAME_PATTERN.match(att.get("filename", ""))
                        if m and m.group(1) == event_id:
                            return {
                                "channel_id": ch_id,
                                "image_id": msg["id"],
                                "hash": m.group(2),
                            }
            except requests.HTTPError:
                continue
        return None

    def _get_category_channels(self) -> list[dict]:
        """Get text channels under the configured category (cached per cycle)."""
        if self._category_channels_cache is not None:
            return self._category_channels_cache

        all_channels = self._request("GET", f"/guilds/{self._guild_id}/channels")
        self._category_channels_cache = [
            ch for ch in (all_channels or [])
            if ch.get("parent_id") == self._category_id and ch.get("type") == 0
        ]
        return self._category_channels_cache

    def clear_channel_cache(self):
        """Clear category channels cache. Call once per sync cycle."""
        self._category_channels_cache = None

    # -- Message posting --

    def post_event(
        self, channel_id: str, event: CalendarEvent, timezone: str,
    ) -> dict:
        """Post roster image in a channel. Returns {image_id, hash}."""
        content_hash = compute_event_hash(event)
        image_bytes = render_roster(event, timezone)
        filename = f"roster_{event.event_id}_h{content_hash}.png"

        data = self._upload_image(
            "POST",
            f"/channels/{channel_id}/messages",
            image_bytes,
            filename,
            "",
        )
        image_msg_id = data["id"]
        log.info("Discord: posted image %s for %s", image_msg_id, event.title)

        return {"image_id": image_msg_id, "hash": content_hash}

    def update_event(
        self, channel_id: str, message_ids: dict, event: CalendarEvent, timezone: str,
    ) -> dict:
        """Edit an existing roster image."""
        content_hash = compute_event_hash(event)
        image_bytes = render_roster(event, timezone)
        image_msg_id = message_ids["image_id"]
        filename = f"roster_{event.event_id}_h{content_hash}.png"

        self._upload_image(
            "PATCH",
            f"/channels/{channel_id}/messages/{image_msg_id}",
            image_bytes,
            filename,
            "",
        )
        message_ids["hash"] = content_hash
        log.info("Discord: updated image %s for %s", image_msg_id, event.title)
        return message_ids

    def ping_members(
        self, channel_id: str, names: set[str], label: str = "Confirmed",
    ):
        """Post a one-off ping message for the given character names."""
        mentions = []
        for name in sorted(names):
            user_id = self._find_member_id(name)
            if user_id:
                mentions.append(f"<@{user_id}>")

        if mentions:
            self._request(
                "POST",
                f"/channels/{channel_id}/messages",
                json={"content": f"{label}: " + " ".join(mentions)},
            )
            log.info("Discord: pinged %d members (%s)", len(mentions), label)

    def message_exists(self, channel_id: str, message_ids: dict | str) -> bool:
        """Check if the image message still exists."""
        msg_id = message_ids["image_id"] if isinstance(message_ids, dict) else message_ids
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
        while True:
            batch = self._request(
                "GET",
                f"/guilds/{self._guild_id}/members",
                params={"limit": 1000, "after": after},
            )
            if not batch:
                break
            members.extend(batch)
            if len(batch) < 1000:
                break
            after = batch[-1]["user"]["id"]
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
            if char_lower in nick or char_lower in global_name or char_lower in username:
                return user.get("id")
        return None

    # -- HTTP helpers --

    def _upload_image(
        self, method: str, path: str, image_bytes: bytes, filename: str, content: str,
    ) -> dict:
        url = BASE_URL + path
        files = {"files[0]": (filename, image_bytes, "image/png")}
        payload = {"content": content} if content else {}
        for attempt in range(3):
            resp = self._session.request(method, url, data=payload, files=files)
            if resp.status_code == 429:
                retry_after = resp.json().get("retry_after", 1.0)
                log.warning("Discord rate limited, retrying after %.1fs", retry_after)
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return {}

    def _request(self, method: str, path: str, **kwargs) -> dict | list | None:
        url = BASE_URL + path
        headers = kwargs.pop("headers", {})
        headers["Content-Type"] = "application/json"
        for attempt in range(3):
            resp = self._session.request(method, url, headers=headers, **kwargs)
            if resp.status_code == 429:
                retry_after = resp.json().get("retry_after", 1.0)
                log.warning("Discord rate limited, retrying after %.1fs", retry_after)
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            if resp.status_code == 204:
                return None
            return resp.json()
        resp.raise_for_status()
        return None
