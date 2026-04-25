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

from fgc_sync import i18n
from fgc_sync._version import __version__, about_text
from fgc_sync.i18n import t
from fgc_sync.models import SyncPlan
from fgc_sync.services.config import (
    SAVED_VARIABLES_FILENAME,
    Config,
    decode_setup_code,
    encode_setup_code,
)
from fgc_sync.services.discord_poster import DiscordPoster
from fgc_sync.services.google_calendar import GoogleCalendarClient
from fgc_sync.services.sync_engine import (
    compute_discord_sync_plan,
    compute_sync_plan,
    compute_weekly_sync_plan,
    execute_discord_sync,
    execute_sync,
    execute_weekly_sync,
)


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

    # 0. Language — must run first so subsequent prompts use the chosen language
    language_choices = [
        questionary.Choice(i18n.display_name(code), value=code)
        for code in i18n.available_languages()
    ]
    current_language = i18n.get_language()
    default_choice = next(
        (c for c in language_choices if c.value == current_language),
        language_choices[0],
    )
    language = questionary.select(
        t("cli.setup.language_prompt"),
        choices=language_choices,
        default=default_choice,
    ).ask()
    if language is None:
        return False
    config.set("language", language)

    # 1. WoW path
    wow_input = questionary.path(
        t("cli.setup.wow_dir_prompt"),
        default=config.get("wow_path", ""),
        only_directories=True,
    ).ask()
    if wow_input is None:
        return False
    wow_path = Path(_normalize_path(wow_input))
    wtf = wow_path / "WTF" / "Account"
    if not wtf.is_dir():
        print(t("cli.setup.wow_not_found", wtf=wtf))
        return False

    # 2. Account folder (arrow-key select)
    accounts = [
        d.name
        for d in sorted(wtf.iterdir())
        if d.is_dir() and d.name != "SavedVariables"
    ]
    if not accounts:
        print(t("cli.setup.no_accounts"))
        return False
    account = questionary.select(
        t("cli.setup.select_account"),
        choices=accounts,
    ).ask()
    if account is None:
        return False

    # 3. Guild key (arrow-key select)
    sv_file = wtf / account / "SavedVariables" / SAVED_VARIABLES_FILENAME
    if not sv_file.exists():
        print(t("cli.setup.sv_not_found", path=sv_file))
        return False
    try:
        db = parse_saved_variables(sv_file)
        guilds = list_guild_keys(db)
    except Exception as e:
        print(t("cli.setup.sv_parse_error", error=e))
        return False
    if not guilds:
        print(t("cli.setup.no_guilds"))
        return False
    guild = questionary.select(
        t("cli.setup.select_guild"),
        choices=guilds,
    ).ask()
    if guild is None:
        return False

    config.set("wow_path", str(wow_path))
    config.set("account_folder", account)
    config.set("guild_key", guild)

    # 4. Discord (optional) — setup code or manual
    discord_choice = questionary.select(
        t("cli.setup.discord_prompt"),
        choices=[
            questionary.Choice(t("cli.setup.discord_choice_code"), value="code"),
            questionary.Choice(t("cli.setup.discord_choice_manual"), value="manual"),
            questionary.Choice(t("cli.setup.discord_choice_skip"), value="skip"),
        ],
    ).ask()
    if discord_choice == "code":
        code = questionary.text(t("cli.setup.setup_code_prompt")).ask()
        if code:
            values = decode_setup_code(code)
            if values:
                for k, v in values.items():
                    config.set(k, v)
                print(t("cli.setup.discord_configured_code"))
            else:
                print(t("cli.setup.discord_invalid_code"))
        else:
            print(t("cli.setup.discord_no_code"))
    elif discord_choice == "manual":
        token = questionary.password(t("cli.setup.bot_token_prompt")).ask()
        guild_id = questionary.text(t("cli.setup.guild_id_prompt")).ask()
        forum_id = questionary.text(t("cli.setup.forum_id_prompt")).ask()
        if token and guild_id and forum_id:
            config.set("discord_bot_token", token)
            config.set("discord_guild_id", guild_id)
            config.set("discord_forum_id", forum_id)
            print(t("cli.setup.discord_configured_manual"))
        else:
            print(t("cli.setup.discord_partial_skip"))

    # 5. Google Calendar (optional)
    if questionary.confirm(
        t("cli.setup.google_prompt"),
        default=False,
    ).ask():
        from fgc_sync.services.google_calendar import GoogleCalendarClient

        gcal = GoogleCalendarClient(config.token_path, config.client_secrets_path)
        if not config.client_secrets_path.exists():
            print(
                t(
                    "cli.setup.google_secrets_missing_path",
                    path=config.client_secrets_path,
                )
            )
            print(t("cli.setup.google_skip_no_secrets"))
        else:
            print(t("cli.setup.google_opening_browser"))
            try:
                if gcal.authenticate():
                    calendars = gcal.list_calendars()
                    choices = []
                    for cal in calendars:
                        label = cal["summary"]
                        if cal.get("primary"):
                            label += t("cli.setup.calendar_primary_suffix")
                        choices.append(questionary.Choice(label, value=cal["id"]))
                    cal_id = questionary.select(
                        t("cli.setup.select_calendar"),
                        choices=choices,
                    ).ask()
                    if cal_id:
                        config.set("calendar_id", cal_id)
                        print(t("cli.setup.google_configured"))
                else:
                    print(t("cli.setup.google_login_failed"))
            except Exception as e:
                print(t("cli.setup.google_login_error", error=e))

    print(t("cli.setup.complete", path=config.path))
    return True


def main():
    # Load default config first so i18n.set_language() runs before we build
    # argparse — that way --help is shown in the user's configured language.
    # If --config-dir is supplied, we'll swap to that config after parsing.
    default_config = Config()

    parser = argparse.ArgumentParser(description=t("cli.description"))
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--about",
        action="store_true",
        help=t("cli.flags.about"),
    )
    parser.add_argument(
        "--check-update",
        action="store_true",
        help=t("cli.flags.check_update"),
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help=t("cli.flags.update"),
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help=t("cli.flags.setup"),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=t("cli.flags.dry_run"),
    )
    parser.add_argument(
        "--discord-only",
        action="store_true",
        help=t("cli.flags.discord_only"),
    )
    parser.add_argument(
        "--weekly-only",
        action="store_true",
        help=t("cli.flags.weekly_only"),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=t("cli.flags.force"),
    )
    parser.add_argument(
        "--export-code",
        action="store_true",
        help=t("cli.flags.export_code"),
    )
    parser.add_argument(
        "--config-dir",
        type=str,
        default=None,
        help=t("cli.flags.config_dir"),
    )
    args = parser.parse_args()

    if args.about:
        print(about_text())
        return

    if args.check_update or args.update:
        from fgc_sync.services.updater import check_for_update, perform_update

        info = check_for_update()
        if info is None:
            print(t("cli.update.could_not_check"))
            sys.exit(1)
        if not info.is_newer:
            print(t("cli.update.up_to_date", version=info.current_version))
            return
        print(
            t(
                "cli.update.available",
                current=info.current_version,
                latest=info.latest_version,
            )
        )
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
        config = default_config

    if args.export_code:
        token = config.get("discord_bot_token", "")
        guild_id = config.get("discord_guild_id", "")
        forum_id = config.get("discord_forum_id", "")
        if not (token and guild_id and forum_id):
            print(t("cli.export_code.incomplete"))
            sys.exit(1)
        code = encode_setup_code(config._data)
        print(t("cli.export_code.share_intro"))
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
        print(t("cli.setup.starting") + "\n")
        config.begin_transaction()
        if _run_cli_setup(config):
            config.commit_transaction()
        else:
            config.rollback_transaction()
            print(t("cli.setup.cancelled"))
            sys.exit(1)

    # Dry-run mode: show what would happen without making any changes
    if args.dry_run:
        plans: list[tuple[str, SyncPlan]] = []

        # Google Calendar plan
        if (
            not args.discord_only
            and not args.weekly_only
            and config.is_google_configured
        ):
            gcal = GoogleCalendarClient(config.token_path, config.client_secrets_path)
            if not gcal.load_credentials():
                gcal = None
                print(t("cli.dry_run.google_creds_unavailable"))
            else:
                gcal = gcal
            plans.append((t("cli.sync.google_label"), compute_sync_plan(config, gcal)))

        # Discord plan
        token = config.get("discord_bot_token", "")
        forum = config.get("discord_forum_id", "")
        guild = config.get("discord_guild_id", "")
        if token and forum and guild:
            discord = DiscordPoster(token, forum, guild)
            if not args.weekly_only:
                plans.append(
                    (
                        t("cli.sync.discord_label"),
                        compute_discord_sync_plan(config, discord),
                    )
                )
            plans.append(
                (t("cli.sync.weekly_label"), compute_weekly_sync_plan(config, discord))
            )

        # Render the weekly overview PNG to disk so you can preview it
        # before any real sync touches Discord.
        if args.weekly_only or not args.discord_only:
            from fgc_sync.services.sync_engine import (
                _collect_week_events_for_overview,
            )
            from fgc_sync.services.weekly_overview import (
                current_week_bounds,
                render_weekly_overview,
            )

            events, preview_errors = _collect_week_events_for_overview(config)
            if preview_errors:
                for err in preview_errors:
                    print(t("cli.dry_run.weekly_preview_error", error=err))
            else:
                monday, _sunday, _wk = current_week_bounds()
                try:
                    png = render_weekly_overview(events, monday)
                    preview_path = config.app_data_dir / "weekly_preview.png"
                    preview_path.write_bytes(png)
                    print(
                        t(
                            "cli.dry_run.weekly_preview_written",
                            path=preview_path,
                            events=len(events),
                            bytes=len(png),
                        )
                    )
                except Exception as e:
                    print(t("cli.dry_run.weekly_preview_render_failed", error=e))

        for label, plan in plans:
            for err in plan.errors:
                print(t("cli.dry_run.label_error", label=label, error=err))
            if plan.errors:
                continue
            if not plan.entries:
                print(t("cli.dry_run.no_changes", label=label))
            else:
                print(
                    t(
                        "cli.dry_run.summary",
                        label=label,
                        creates=len(plan.creates),
                        updates=len(plan.updates),
                        deletes=len(plan.deletes),
                    )
                )
                col_action = t("cli.dry_run.table_action")
                col_title = t("cli.dry_run.table_title")
                col_date = t("cli.dry_run.table_date")
                col_time = t("cli.dry_run.table_time")
                col_info = t("cli.dry_run.table_info")
                action_labels = {
                    e.action.value: t(f"preview.action_{e.action.value}")
                    for e in plan.entries
                }
                act_w = max(
                    max(len(v) for v in action_labels.values()),
                    len(col_action),
                )
                title_w = max(
                    max(len(e.title) for e in plan.entries),
                    len(col_title),
                )
                date_w = max(
                    max((len(e.date) for e in plan.entries), default=0),
                    len(col_date),
                )
                time_w = max(
                    max((len(e.time) for e in plan.entries), default=0),
                    len(col_time),
                )
                header = (
                    f"{col_action:<{act_w}}  {col_title:<{title_w}}  "
                    f"{col_date:<{date_w}}  {col_time:<{time_w}}  {col_info}"
                )
                print(header)
                print("-" * len(header))
                for e in plan.entries:
                    action_text = action_labels[e.action.value]
                    print(
                        f"{action_text:<{act_w}}  {e.title:<{title_w}}  "
                        f"{e.date:<{date_w}}  {e.time:<{time_w}}  {e.participants_info}"
                    )
            print()
        return

    # Google Calendar sync (optional)
    if not args.discord_only and not args.weekly_only and config.is_google_configured:
        gcal = GoogleCalendarClient(config.token_path, config.client_secrets_path)
        if gcal.load_credentials():
            result = execute_sync(config, gcal)
            log.info("%s: %s", t("cli.sync.google_label"), result)
            if result.errors:
                for err in result.errors:
                    log.error("  %s", err)
        else:
            log.warning(t("cli.sync.google_creds_missing_warning"))

    # Discord sync
    token = config.get("discord_bot_token", "")
    forum = config.get("discord_forum_id", "")
    guild = config.get("discord_guild_id", "")
    if token and forum and guild:
        discord = DiscordPoster(token, forum, guild)
        if args.force and not args.weekly_only:
            mapping = config.get("discord_message_mapping", {})
            log.info(t("cli.sync.force_resync_deleting", count=len(mapping)))
            for _event_id, info in mapping.items():
                ch_id = info.get("channel_id")
                if not ch_id:
                    continue
                try:
                    discord.delete_thread(ch_id)
                except Exception as e:
                    log.error(
                        t("cli.sync.force_resync_delete_failed", id=ch_id, error=e)
                    )
            config.set("discord_message_mapping", {})
        if not args.weekly_only:
            result = execute_discord_sync(config, discord)
            log.info("%s: %s", t("cli.sync.discord_label"), result)
            if result.errors:
                for err in result.errors:
                    log.error("  %s", err)
        weekly_result = execute_weekly_sync(config, discord)
        log.info("%s: %s", t("cli.sync.weekly_label"), weekly_result)
        if weekly_result.errors:
            for err in weekly_result.errors:
                log.error("  %s", err)
    elif args.discord_only or args.weekly_only:
        log.error(t("cli.sync.discord_not_configured"))
        sys.exit(1)

    # Check for updates (non-blocking, just inform)
    try:
        from fgc_sync.services.updater import check_for_update

        info = check_for_update()
        if info and info.is_newer:
            log.info(
                t(
                    "cli.update.available_log",
                    current=info.current_version,
                    latest=info.latest_version,
                )
            )
    except Exception:
        pass  # never fail the sync because of an update check


if __name__ == "__main__":
    main()
