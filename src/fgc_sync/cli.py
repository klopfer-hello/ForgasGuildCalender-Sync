"""Headless CLI entry point — run a single sync cycle and exit.

Designed for cron jobs on Linux. Does not require PySide6 or any GUI.
Usage: fgc-sync-cli [--discord-only]
"""

from __future__ import annotations

import argparse
import logging
import sys

from fgc_sync._version import __version__, about_text
from fgc_sync.services.config import Config
from fgc_sync.services.discord_poster import DiscordPoster
from fgc_sync.services.google_calendar import GoogleCalendarClient
from fgc_sync.services.sync_engine import execute_discord_sync, execute_sync


def main():
    parser = argparse.ArgumentParser(description="FGC Calendar Sync — headless CLI")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--about", action="store_true",
        help="Show version and license information",
    )
    parser.add_argument(
        "--check-update", action="store_true",
        help="Check if a newer version is available",
    )
    parser.add_argument(
        "--update", action="store_true",
        help="Download and install the latest version",
    )
    parser.add_argument(
        "--discord-only", action="store_true",
        help="Only sync to Discord, skip Google Calendar",
    )
    args = parser.parse_args()

    if args.about:
        print(about_text())
        return

    if args.check_update or args.update:
        from fgc_sync.services.updater import check_for_update, perform_update
        info = check_for_update()
        if info is None:
            print("Could not check for updates.")
            sys.exit(1)
        if not info.is_newer:
            print(f"Already up to date (v{info.current_version}).")
            return
        print(f"Update available: v{info.current_version} -> v{info.latest_version}")
        if args.update:
            result = perform_update(info)
            print(result)
            if result == "exit":
                sys.exit(0)
        return

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

    if not config.is_setup_complete:
        log.error(
            "Setup incomplete. Run the GUI on Windows first, or manually create "
            "%s with wow_path, account_folder, and guild_key.",
            config.path,
        )
        sys.exit(1)

    # Google Calendar sync (optional)
    if not args.discord_only and config.is_google_configured:
        gcal = GoogleCalendarClient(config.token_path, config.client_secrets_path)
        if gcal.load_credentials():
            result = execute_sync(config, gcal)
            log.info("Google Calendar: %s", result)
            if result.errors:
                for err in result.errors:
                    log.error("  %s", err)
        else:
            log.warning("Google credentials not found or expired, skipping Google Calendar sync")

    # Discord sync
    token = config.get("discord_bot_token", "")
    category = config.get("discord_category_id", "")
    guild = config.get("discord_guild_id", "")
    if token and category and guild:
        discord = DiscordPoster(token, category, guild)
        result = execute_discord_sync(config, discord)
        log.info("Discord: %s", result)
        if result.errors:
            for err in result.errors:
                log.error("  %s", err)
    elif args.discord_only:
        log.error("Discord not configured. Set discord_bot_token, discord_category_id, discord_guild_id in config.")
        sys.exit(1)

    # Check for updates (non-blocking, just inform)
    try:
        from fgc_sync.services.updater import check_for_update
        info = check_for_update()
        if info and info.is_newer:
            log.info(
                "Update available: v%s -> v%s. Run with --update to install.",
                info.current_version, info.latest_version,
            )
    except Exception:
        pass  # never fail the sync because of an update check


if __name__ == "__main__":
    main()
