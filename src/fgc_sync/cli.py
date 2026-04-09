"""Headless CLI entry point — run a single sync cycle and exit.

Designed for cron jobs on Linux. Does not require PySide6 or any GUI.
Usage: fgc-sync-cli [--discord-only]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from fgc_sync._version import __version__, about_text
from fgc_sync.services.config import (
    SAVED_VARIABLES_FILENAME,
    Config,
    decode_setup_code,
    encode_setup_code,
)
from fgc_sync.services.discord_poster import DiscordPoster
from fgc_sync.services.google_calendar import GoogleCalendarClient
from fgc_sync.services.sync_engine import execute_discord_sync, execute_sync


def _normalize_path(p: str) -> str:
    """Convert git-bash /d/... paths to D:/... on Windows."""
    import re
    if os.name == "nt" and re.match(r"^/[a-zA-Z]/", p):
        return p[1].upper() + ":" + p[2:]
    return p


def _run_cli_setup(config: Config) -> bool:
    """Interactive setup using questionary (arrow-key select, tab paths)."""
    import questionary

    from fgc_sync.services.lua_parser import list_guild_keys, parse_saved_variables

    # 1. WoW path
    wow_input = questionary.path(
        "WoW installation directory:",
        default=config.get("wow_path", ""),
        only_directories=True,
    ).ask()
    if wow_input is None:
        return False
    wow_path = Path(_normalize_path(wow_input))
    wtf = wow_path / "WTF" / "Account"
    if not wtf.is_dir():
        print(f"Error: {wtf} not found. Is this the correct WoW directory?")
        return False

    # 2. Account folder (arrow-key select)
    accounts = [
        d.name for d in sorted(wtf.iterdir())
        if d.is_dir() and d.name != "SavedVariables"
    ]
    if not accounts:
        print("Error: No account folders found.")
        return False
    account = questionary.select(
        "Select account:",
        choices=accounts,
    ).ask()
    if account is None:
        return False

    # 3. Guild key (arrow-key select)
    sv_file = wtf / account / "SavedVariables" / SAVED_VARIABLES_FILENAME
    if not sv_file.exists():
        print(f"Error: SavedVariables not found at {sv_file}")
        return False
    try:
        db = parse_saved_variables(sv_file)
        guilds = list_guild_keys(db)
    except Exception as e:
        print(f"Error parsing SavedVariables: {e}")
        return False
    if not guilds:
        print("Error: No guilds found in SavedVariables.")
        return False
    guild = questionary.select(
        "Select guild:",
        choices=guilds,
    ).ask()
    if guild is None:
        return False

    config.set("wow_path", str(wow_path))
    config.set("account_folder", account)
    config.set("guild_key", guild)

    # 4. Discord (optional) — setup code or manual
    discord_choice = questionary.select(
        "Discord bot integration:",
        choices=[
            questionary.Choice("Paste a setup code", value="code"),
            questionary.Choice("Enter credentials manually", value="manual"),
            questionary.Choice("Skip", value="skip"),
        ],
    ).ask()
    if discord_choice == "code":
        code = questionary.text("Setup code:").ask()
        if code:
            values = decode_setup_code(code)
            if values:
                for k, v in values.items():
                    config.set(k, v)
                print("Discord configured from setup code.")
            else:
                print("Invalid setup code. Skipping Discord.")
        else:
            print("Skipped — no code entered.")
    elif discord_choice == "manual":
        token = questionary.password("Bot token:").ask()
        guild_id = questionary.text("Server (Guild) ID:").ask()
        forum_id = questionary.text("Forum Channel ID for raid threads:").ask()
        if token and guild_id and forum_id:
            config.set("discord_bot_token", token)
            config.set("discord_guild_id", guild_id)
            config.set("discord_forum_id", forum_id)
            print("Discord configured.")
        else:
            print("Skipped — not all fields provided.")

    # 5. Google Calendar (optional)
    if questionary.confirm(
        "Set up Google Calendar sync?", default=False,
    ).ask():
        from fgc_sync.services.google_calendar import GoogleCalendarClient
        gcal = GoogleCalendarClient(config.token_path, config.client_secrets_path)
        if not config.client_secrets_path.exists():
            print(f"Place client_secrets.json at: {config.client_secrets_path}")
            print("Skipping Google Calendar — credentials file not found.")
        else:
            print("Opening browser for Google login...")
            try:
                if gcal.authenticate():
                    calendars = gcal.list_calendars()
                    choices = []
                    for cal in calendars:
                        label = cal["summary"]
                        if cal.get("primary"):
                            label += " (primary)"
                        choices.append(questionary.Choice(label, value=cal["id"]))
                    cal_id = questionary.select(
                        "Select calendar:", choices=choices,
                    ).ask()
                    if cal_id:
                        config.set("calendar_id", cal_id)
                        print("Google Calendar configured.")
                else:
                    print("Google login failed.")
            except Exception as e:
                print(f"Google login error: {e}")

    print(f"\nSetup complete! Config saved to {config.path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="FGC Sync — headless CLI")
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
        "--setup", action="store_true",
        help="Re-run the interactive setup (reconfigure WoW, Discord, Google)",
    )
    parser.add_argument(
        "--discord-only", action="store_true",
        help="Only sync to Discord, skip Google Calendar",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Force Discord resync: delete all tracked channels and clear "
             "the mapping before syncing, so every channel is recreated and "
             "every confirmed member is re-pinged",
    )
    parser.add_argument(
        "--export-code", action="store_true",
        help="Print a setup code that encodes the Discord bot config "
             "(token, server ID, forum ID) for sharing with other users",
    )
    parser.add_argument(
        "--config-dir", type=str, default=None,
        help="Use a custom config directory (for testing or multi-user setups)",
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

    if args.config_dir:
        config_dir = Path(args.config_dir)
        config_dir.mkdir(parents=True, exist_ok=True)
        config = Config(config_dir / "config.json")
    else:
        config = Config()

    if args.export_code:
        token = config.get("discord_bot_token", "")
        guild_id = config.get("discord_guild_id", "")
        forum_id = config.get("discord_forum_id", "")
        if not (token and guild_id and forum_id):
            print("Error: Discord is not fully configured. "
                  "Set up discord_bot_token, discord_guild_id, and "
                  "discord_forum_id first.")
            sys.exit(1)
        code = encode_setup_code(config._data)
        print("Share this setup code with other users:\n")
        print(code)
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.app_data_dir / "sync.log", encoding="utf-8"),
        ],
    )
    logging.getLogger("fgc_sync").setLevel(
        getattr(logging, config.log_level, logging.ERROR)
    )
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
    log = logging.getLogger(__name__)

    if args.setup or not config.is_setup_complete:
        print("Starting interactive setup...\n")
        if not _run_cli_setup(config):
            print("Setup cancelled.")
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
    forum = config.get("discord_forum_id", "")
    guild = config.get("discord_guild_id", "")
    if token and forum and guild:
        discord = DiscordPoster(token, forum, guild)
        if args.force:
            mapping = config.get("discord_message_mapping", {})
            log.info("Force resync: deleting %d tracked thread(s)", len(mapping))
            for event_id, info in mapping.items():
                ch_id = info.get("channel_id")
                if not ch_id:
                    continue
                try:
                    discord.delete_thread(ch_id)
                except Exception as e:
                    log.error("Force resync: failed to delete thread %s: %s", ch_id, e)
            config.set("discord_message_mapping", {})
        result = execute_discord_sync(config, discord)
        log.info("Discord: %s", result)
        if result.errors:
            for err in result.errors:
                log.error("  %s", err)
    elif args.discord_only:
        log.error("Discord not configured. Set discord_bot_token, discord_forum_id, discord_guild_id in config.")
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
