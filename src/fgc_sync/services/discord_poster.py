"""Discord REST API client — posts roster images and pings confirmed members."""

from __future__ import annotations

import logging
import time

import requests

from fgc_sync.models.enums import Attendance
from fgc_sync.models.events import CalendarEvent
from fgc_sync.services.roster_image import render_roster

log = logging.getLogger(__name__)

# Discord API v10
BASE_URL = "https://discord.com/api/v10"


class DiscordPoster:
    """Synchronous Discord REST client for posting roster images."""

    def __init__(self, bot_token: str, channel_id: str, guild_id: str):
        self._bot_token = bot_token
        self._channel_id = channel_id
        self._guild_id = guild_id
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bot {bot_token}"
        self._members_cache: list[dict] | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self._channel_id and self._guild_id)

    # -- Public API --

    def post_event(
        self, event: CalendarEvent, timezone: str,
    ) -> dict:
        """Post roster image + mentions reply. Returns {image_id, mention_id?}."""
        image_bytes = render_roster(event, timezone)

        data = self._upload_image(
            "POST",
            f"/channels/{self._channel_id}/messages",
            image_bytes,
            f"roster_{event.event_id}.png",
            "",
        )
        image_msg_id = data["id"]
        log.info("Discord: posted image %s for %s", image_msg_id, event.title)

        # Post mentions as a reply below the image
        mention_msg_id = self._post_mentions_reply(image_msg_id, event)

        result = {"image_id": image_msg_id}
        if mention_msg_id:
            result["mention_id"] = mention_msg_id
        return result

    def update_event(
        self, message_ids: dict, event: CalendarEvent, timezone: str,
    ) -> dict:
        """Edit an existing event image and update the mention reply."""
        image_bytes = render_roster(event, timezone)
        image_msg_id = message_ids["image_id"]

        self._upload_image(
            "PATCH",
            f"/channels/{self._channel_id}/messages/{image_msg_id}",
            image_bytes,
            f"roster_{event.event_id}.png",
            "",
        )
        log.info("Discord: updated image %s for %s", image_msg_id, event.title)

        # Update or create mention reply
        mentions = self._build_confirmed_mentions(event)
        old_mention_id = message_ids.get("mention_id")

        if mentions and old_mention_id:
            self._request(
                "PATCH",
                f"/channels/{self._channel_id}/messages/{old_mention_id}",
                json={"content": mentions},
            )
        elif mentions and not old_mention_id:
            mention_msg_id = self._post_mentions_reply(image_msg_id, event)
            if mention_msg_id:
                message_ids["mention_id"] = mention_msg_id
        elif not mentions and old_mention_id:
            try:
                self._request("DELETE", f"/channels/{self._channel_id}/messages/{old_mention_id}")
            except requests.HTTPError:
                pass
            message_ids.pop("mention_id", None)

        return message_ids

    def delete_event(self, message_ids: dict | str):
        """Delete event messages (image + optional mention reply)."""
        if isinstance(message_ids, str):
            # Legacy: single message ID
            self._request("DELETE", f"/channels/{self._channel_id}/messages/{message_ids}")
            return
        for key in ("mention_id", "image_id"):
            msg_id = message_ids.get(key)
            if msg_id:
                try:
                    self._request("DELETE", f"/channels/{self._channel_id}/messages/{msg_id}")
                except requests.HTTPError:
                    log.warning("Discord: could not delete message %s", msg_id)
        log.info("Discord: deleted event messages")

    def repost_mentions(self, message_ids: dict, event: CalendarEvent) -> dict:
        """Delete and re-post only the mention reply for fresh pings. Image stays."""
        old_mention_id = message_ids.get("mention_id")
        if old_mention_id:
            try:
                self._request("DELETE", f"/channels/{self._channel_id}/messages/{old_mention_id}")
            except requests.HTTPError:
                pass

        image_msg_id = message_ids["image_id"]
        mention_msg_id = self._post_mentions_reply(image_msg_id, event)
        if mention_msg_id:
            message_ids["mention_id"] = mention_msg_id
        else:
            message_ids.pop("mention_id", None)
        return message_ids

    def message_exists(self, message_ids: dict | str) -> bool:
        """Check if the image message still exists in the channel."""
        msg_id = message_ids["image_id"] if isinstance(message_ids, dict) else message_ids
        try:
            self._request("GET", f"/channels/{self._channel_id}/messages/{msg_id}")
            return True
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return False
            raise

    def _post_mentions_reply(self, image_msg_id: str, event: CalendarEvent) -> str | None:
        """Post a reply with confirmed member mentions below the image."""
        mentions = self._build_confirmed_mentions(event)
        if not mentions:
            return None
        data = self._request(
            "POST",
            f"/channels/{self._channel_id}/messages",
            json={
                "content": mentions,
                "message_reference": {"message_id": image_msg_id},
            },
        )
        mention_id = data["id"]
        log.info("Discord: posted mention reply %s", mention_id)
        return mention_id

    def message_exists(self, message_id: str) -> bool:
        """Check if a message still exists in the channel."""
        try:
            self._request("GET", f"/channels/{self._channel_id}/messages/{message_id}")
            return True
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return False
            raise

    # -- Member lookup & pinging --

    def _fetch_guild_members(self) -> list[dict]:
        """Fetch all guild members (paginated, max 1000 per request)."""
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
        """Get guild members, using a per-sync-cycle cache."""
        if self._members_cache is None:
            self._members_cache = self._fetch_guild_members()
        return self._members_cache

    def clear_members_cache(self):
        """Clear the member cache. Call once per sync cycle."""
        self._members_cache = None

    def _find_member_id(self, character_name: str) -> str | None:
        """Find a Discord user ID whose server name contains the character name."""
        char_lower = character_name.lower()
        for member in self._get_members():
            nick = (member.get("nick") or "").lower()
            user = member.get("user", {})
            global_name = (user.get("global_name") or "").lower()
            username = (user.get("username") or "").lower()

            if char_lower in nick or char_lower in global_name or char_lower in username:
                return user.get("id")
        return None

    def _build_confirmed_mentions(self, event: CalendarEvent) -> str:
        """Build a mention string for all confirmed participants."""
        confirmed = [p for p in event.participants if p.attendance == Attendance.CONFIRMED]
        if not confirmed:
            return ""

        mentions = []
        for p in confirmed:
            user_id = self._find_member_id(p.name)
            if user_id:
                mentions.append(f"<@{user_id}>")
            else:
                log.debug("Discord: no member match for character '%s'", p.name)

        if not mentions:
            return ""
        return "Confirmed: " + " ".join(mentions)

    # -- HTTP helpers --

    def _upload_image(
        self, method: str, path: str, image_bytes: bytes, filename: str, content: str,
    ) -> dict:
        """Upload an image as a multipart message."""
        url = BASE_URL + path
        files = {"files[0]": (filename, image_bytes, "image/png")}
        payload = {"content": content} if content else {}

        for attempt in range(3):
            resp = self._session.request(
                method, url, data=payload, files=files,
            )
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
        # Ensure JSON content-type for non-upload requests
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
