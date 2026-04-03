"""Headless CLI entry point — run a single sync cycle and exit.

Designed for cron jobs on Linux. Does not require PySide6 or any GUI.
Usage: fgc-sync-cli [--discord-only]
"""

from __future__ import annotations

import argparse
import logging
import sys

from fgc_sync.services.config import Config
from fgc_sync.services.discord_poster import DiscordPoster
from fgc_sync.services.google_calendar import GoogleCalendarClient
from fgc_sync.services.sync_engine import execute_discord_sync, execute_sync


def main():
    parser = argparse.ArgumentParser(description="FGC Calendar Sync — headless CLI")
    parser.add_argument(
        "--discord-only", action="store_true",
        help="Only sync to Discord, skip Google Calendar",
    )
    args = parser.parse_args()

    config = Config()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.app_data_dir / "sync.log", encoding="utf-8"),
        ],
    )
    log = logging.getLogger(__name__)

    if not config.is_setup_complete and not args.discord_only:
        log.error(
            "Setup incomplete. Run the GUI on Windows first, or manually create "
            "%s with wow_path, account_folder, guild_key, and calendar_id.",
            config.path,
        )
        sys.exit(1)

    # Google Calendar sync
    if not args.discord_only:
        gcal = GoogleCalendarClient(config.token_path, config.client_secrets_path)
        if not gcal.load_credentials():
            log.error("Google credentials not found or expired. Re-authenticate via the GUI.")
            sys.exit(1)
        result = execute_sync(config, gcal)
        log.info("Google Calendar: %s", result)
        if result.errors:
            for err in result.errors:
                log.error("  %s", err)

    # Discord sync
    token = config.get("discord_bot_token", "")
    channel = config.get("discord_channel_id", "")
    guild = config.get("discord_guild_id", "")
    if token and channel and guild:
        discord = DiscordPoster(token, channel, guild)
        result = execute_discord_sync(config, discord)
        log.info("Discord: %s", result)
        if result.errors:
            for err in result.errors:
                log.error("  %s", err)
    elif args.discord_only:
        log.error("Discord not configured. Set discord_bot_token, discord_channel_id, discord_guild_id in config.")
        sys.exit(1)


if __name__ == "__main__":
    main()
