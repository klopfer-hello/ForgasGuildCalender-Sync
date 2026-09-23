"""Threads for every planned raid, and the cancellation notice window.

Every raid within the lookahead gets a forum thread as soon as it is planned —
no roster needed. A raid the addon marks cancelled (comment prefixed ``!}``)
never gets a new thread; an existing one gets a single notice pinging everyone
who signed up and is deleted CANCELLED_THREAD_GRACE_HOURS after that notice,
timed by the notice's Discord id so every client computes the same deadline.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
import requests

from fgc_sync.i18n import t
from fgc_sync.models.enums import Attendance, EventType
from fgc_sync.models.events import CalendarEvent, Participant
from fgc_sync.services import sync_engine
from fgc_sync.services.config import Config
from fgc_sync.services.discord_poster import (
    CANCELLATION_MARKER_CANCELLED,
    CANCELLATION_MARKER_REINSTATED,
    DiscordPoster,
    compute_event_hash,
    snowflake_datetime,
)

_THREAD = "1552267816579760218"
_DISCORD_EPOCH_MS = 1420070400000


def _snowflake(at: datetime) -> str:
    """A Discord id whose embedded creation time is *at*."""
    return str((int(at.timestamp() * 1000) - _DISCORD_EPOCH_MS) << 22)


def _raid(
    *,
    event_id: str = "e1",
    days_ahead: int = 3,
    cancelled: bool = False,
    event_type: EventType = EventType.RAID,
    participants: list[Participant] | None = None,
    when: datetime | None = None,
) -> CalendarEvent:
    if when is None:
        when = datetime.combine(
            date.today() + timedelta(days=days_ahead), datetime.min.time()
        ).replace(hour=20)
    return CalendarEvent(
        event_id=event_id,
        title="BT mit Forga",
        event_type=event_type,
        raid="bt",
        date=when.date().isoformat(),
        server_hour=when.hour,
        server_minute=when.minute,
        comment="!}Bis nächste Woche" if cancelled else "Bis nächste Woche",
        creator="Forga",
        revision=1,
        participants=participants
        if participants is not None
        else [
            Participant("Signy", Attendance.SIGNED, "MAGE", "dps"),
            Participant("Confy", Attendance.CONFIRMED, "PRIEST", "healer"),
            Participant("Benchy", Attendance.BENCHED, "ROGUE", "dps"),
            Participant("Nope", Attendance.DECLINED, "HUNTER", "dps"),
        ],
    )


@pytest.fixture
def config(tmp_path):
    cfg = Config(path=tmp_path / "config.json")
    cfg.set("wow_path", str(tmp_path / "wow"))
    cfg.set("account_folder", "acc")
    cfg.set("guild_key", "Guild")
    sv = cfg.saved_variables_path
    sv.parent.mkdir(parents=True, exist_ok=True)
    sv.write_text("-- fake")
    return cfg


def _patch_collect(monkeypatch, *events: CalendarEvent):
    monkeypatch.setattr(
        sync_engine,
        "_collect_all_future_events",
        lambda config: ({e.event_id: e for e in events}, set(), [], set()),
    )
    monkeypatch.setattr(
        sync_engine, "_is_local_data_stale", lambda config, discord: False
    )


def _discord(*, marker=None) -> MagicMock:
    d = MagicMock()
    d.is_configured = True
    d.ensure_unarchived = MagicMock(return_value=True)
    d.find_event_threads = MagicMock(return_value=[])
    d.find_existing_thread = MagicMock(return_value=None)
    d.create_event_thread = MagicMock(
        return_value=(_THREAD, {"image_id": "img", "hash": "h", "sv_mtime": 0})
    )
    d.sync_thread_name = MagicMock(return_value=False)
    d.get_already_pinged_names = MagicMock(return_value={})
    d.ping_members = MagicMock(return_value={})
    d.find_ics_message = MagicMock(return_value=None)
    d.post_ics = MagicMock(return_value={"ics_id": "ics", "hash": "h"})
    d.update_ics = MagicMock(return_value={"ics_id": "ics", "hash": "h"})
    d.find_cancellation_marker = MagicMock(return_value=marker)
    d.post_notice = MagicMock(return_value=_snowflake(datetime.now(UTC)))
    d.delete_thread = MagicMock()
    return d


def _mapped(config, evt, **extra):
    """Map *evt* to an existing, up-to-date thread."""
    config.set(
        "discord_message_mapping",
        {
            evt.event_id: {
                "channel_id": _THREAD,
                "message_ids": {"image_id": "img", "hash": compute_event_hash(evt)},
                "pinged": {},
                "ics": {"ics_id": "ics", "hash": "h"},
                **extra,
            }
        },
    )


# --- Which events get a thread ---


class TestCollector:
    @pytest.fixture
    def collect(self, config, monkeypatch):
        def run(*events):
            monkeypatch.setattr(sync_engine, "parse_saved_variables", lambda p: {})
            monkeypatch.setattr(
                sync_engine, "extract_events", lambda db, key: list(events)
            )
            monkeypatch.setattr(
                sync_engine, "get_deleted_event_ids", lambda db, key: set()
            )
            return sync_engine._collect_all_future_events(config)[0]

        return run

    def test_raid_without_a_roster_gets_a_thread(self, collect):
        """The point of the change: planned is enough, no groups needed."""
        evt = _raid(participants=[Participant("Signy", Attendance.SIGNED, "", "")])
        assert evt.event_id in collect(evt)

    def test_non_raid_events_get_no_thread(self, collect):
        evt = _raid(event_type=EventType.MEETING)
        assert collect(evt) == {}

    def test_lookahead_covers_two_weeks(self, collect):
        inside = _raid(event_id="in", days_ahead=14)
        outside = _raid(event_id="out", days_ahead=15)
        assert set(collect(inside, outside)) == {"in"}

    def test_cancelled_raids_stay_in_the_set(self, collect):
        """Dropping them would read as "removed" and delete the thread at once,
        skipping the notice window."""
        evt = _raid(cancelled=True)
        assert evt.event_id in collect(evt)


# --- Planned raids ---


class TestPlannedRaid:
    def test_thread_is_created_without_pinging_anyone(self, config, monkeypatch):
        evt = _raid(participants=[Participant("Signy", Attendance.SIGNED, "", "")])
        _patch_collect(monkeypatch, evt)
        discord = _discord()

        sync_engine.execute_discord_sync(config, discord)

        discord.create_event_thread.assert_called_once()
        discord.ping_members.assert_not_called()

    def test_no_thread_is_created_once_the_raid_has_started(self, config, monkeypatch):
        """It would only expire a few hours later."""
        started = datetime.now(ZoneInfo("Europe/Berlin")) - timedelta(hours=2)
        evt = _raid(when=started.replace(tzinfo=None))
        _patch_collect(monkeypatch, evt)
        discord = _discord()

        sync_engine.execute_discord_sync(config, discord)
        plan = sync_engine.compute_discord_sync_plan(config, discord)

        discord.create_event_thread.assert_not_called()
        assert plan.entries == []

    def test_first_ping_in_a_thread_is_confirmed_not_newly(self, config, monkeypatch):
        """The thread predates the roster, but for the members this is their
        first confirmation — "newly" would be wrong."""
        evt = _raid()
        _patch_collect(monkeypatch, evt)
        _mapped(config, evt)
        discord = _discord()

        sync_engine.execute_discord_sync(config, discord)

        assert discord.ping_members.call_args.args[2] == t("discord.ping_confirmed")

    def test_later_pings_are_newly_confirmed(self, config, monkeypatch):
        evt = _raid(
            participants=[
                Participant("Confy", Attendance.CONFIRMED, "", ""),
                Participant("Late", Attendance.CONFIRMED, "", ""),
            ]
        )
        _patch_collect(monkeypatch, evt)
        _mapped(config, evt)
        discord = _discord()
        discord.get_already_pinged_names.return_value = {"Confy": "ping-1"}

        sync_engine.execute_discord_sync(config, discord)

        discord.ping_members.assert_called_once()
        args = discord.ping_members.call_args.args
        assert args[1] == {"Late"}
        assert args[2] == t("discord.ping_newly_confirmed")


# --- Cancellation ---


class TestCancellation:
    def test_notice_pings_everyone_who_signed_up(self, config, monkeypatch):
        evt = _raid(cancelled=True)
        _patch_collect(monkeypatch, evt)
        _mapped(config, evt)
        discord = _discord()

        sync_engine.execute_discord_sync(config, discord)

        discord.post_notice.assert_called_once()
        channel, label, names = discord.post_notice.call_args.args
        assert channel == _THREAD
        assert label == t("discord.cancelled_notice")
        assert names == {"Signy", "Confy", "Benchy"}  # not the declined one
        entry = config.get("discord_message_mapping")[evt.event_id]
        assert entry["cancelled"]["notice_id"] == discord.post_notice.return_value

    def test_cancelled_thread_is_frozen(self, config, monkeypatch):
        """No roster pings, image updates or renames for a cancelled raid."""
        evt = _raid(cancelled=True)
        _patch_collect(monkeypatch, evt)
        _mapped(config, evt, message_ids={"image_id": "img", "hash": "stale"})
        discord = _discord()

        sync_engine.execute_discord_sync(config, discord)

        discord.ping_members.assert_not_called()
        discord.update_event.assert_not_called()
        discord.post_event.assert_not_called()
        discord.sync_thread_name.assert_not_called()
        discord.delete_thread.assert_not_called()

    def test_no_thread_is_created_for_a_cancelled_raid(self, config, monkeypatch):
        evt = _raid(cancelled=True)
        _patch_collect(monkeypatch, evt)
        discord = _discord()

        sync_engine.execute_discord_sync(config, discord)

        discord.create_event_thread.assert_not_called()
        discord.post_notice.assert_not_called()
        assert evt.event_id not in config.get("discord_message_mapping")

    def test_existing_notice_is_not_repeated(self, config, monkeypatch):
        """Another client (or an earlier cycle) already told everyone."""
        posted_at = datetime.now(UTC) - timedelta(hours=1)
        notice = _snowflake(posted_at)
        evt = _raid(cancelled=True)
        _patch_collect(monkeypatch, evt)
        _mapped(config, evt)
        discord = _discord(marker=(CANCELLATION_MARKER_CANCELLED, notice))

        sync_engine.execute_discord_sync(config, discord)

        discord.post_notice.assert_not_called()
        entry = config.get("discord_message_mapping")[evt.event_id]
        assert entry["cancelled"]["notice_id"] == notice

    def test_recancelled_after_reinstatement_is_announced_again(
        self, config, monkeypatch
    ):
        evt = _raid(cancelled=True)
        _patch_collect(monkeypatch, evt)
        _mapped(config, evt)
        discord = _discord(marker=(CANCELLATION_MARKER_REINSTATED, "123"))

        sync_engine.execute_discord_sync(config, discord)

        discord.post_notice.assert_called_once()

    def test_failed_scan_does_not_ping_everyone_again(self, config, monkeypatch):
        """Not knowing whether a notice exists must never be read as "none"."""
        evt = _raid(cancelled=True)
        _patch_collect(monkeypatch, evt)
        _mapped(config, evt)
        discord = _discord()
        discord.find_cancellation_marker.side_effect = requests.HTTPError("429")

        result = sync_engine.execute_discord_sync(config, discord)

        discord.post_notice.assert_not_called()
        assert result.errors
        assert evt.event_id in config.get("discord_message_mapping")

    def test_raid_that_already_started_gets_no_notice(self, config, monkeypatch):
        started = datetime.now(ZoneInfo("Europe/Berlin")) - timedelta(hours=2)
        evt = _raid(cancelled=True, when=started.replace(tzinfo=None))
        _patch_collect(monkeypatch, evt)
        _mapped(config, evt)
        discord = _discord()

        sync_engine.execute_discord_sync(config, discord)

        discord.post_notice.assert_not_called()

    def test_thread_is_kept_during_the_notice_window(self, config, monkeypatch):
        notice = _snowflake(datetime.now(UTC) - timedelta(hours=23))
        evt = _raid(cancelled=True)
        _patch_collect(monkeypatch, evt)
        _mapped(config, evt)
        discord = _discord(marker=(CANCELLATION_MARKER_CANCELLED, notice))

        sync_engine.execute_discord_sync(config, discord)

        discord.delete_thread.assert_not_called()

    def test_thread_is_deleted_when_the_notice_window_ends(self, config, monkeypatch):
        """Even though the raid itself is still days away."""
        notice = _snowflake(datetime.now(UTC) - timedelta(hours=25))
        evt = _raid(cancelled=True, days_ahead=5)
        _patch_collect(monkeypatch, evt)
        _mapped(config, evt)
        discord = _discord(marker=(CANCELLATION_MARKER_CANCELLED, notice))

        sync_engine.execute_discord_sync(config, discord)

        discord.delete_thread.assert_called_once_with(_THREAD)
        assert evt.event_id not in config.get("discord_message_mapping")

    def test_deleted_thread_is_not_recreated(self, config, monkeypatch):
        """The raid is still cancelled in SavedVariables the cycle after."""
        evt = _raid(cancelled=True)
        _patch_collect(monkeypatch, evt)
        discord = _discord()

        sync_engine.execute_discord_sync(config, discord)
        sync_engine.execute_discord_sync(config, discord)

        discord.create_event_thread.assert_not_called()


class TestReinstatement:
    def test_members_are_told_the_raid_is_back_on(self, config, monkeypatch):
        evt = _raid()
        _patch_collect(monkeypatch, evt)
        _mapped(config, evt, cancelled={"notice_id": "111"})
        discord = _discord(marker=(CANCELLATION_MARKER_CANCELLED, "111"))

        sync_engine.execute_discord_sync(config, discord)

        discord.post_notice.assert_called_once()
        _channel, label, names = discord.post_notice.call_args.args
        assert label == t("discord.reinstated_notice")
        assert names == {"Signy", "Confy", "Benchy"}
        assert "cancelled" not in config.get("discord_message_mapping")[evt.event_id]

    def test_reinstatement_is_announced_once(self, config, monkeypatch):
        """Another client that also saw the cancellation already posted it."""
        evt = _raid()
        _patch_collect(monkeypatch, evt)
        _mapped(config, evt, cancelled={"notice_id": "111"})
        discord = _discord(marker=(CANCELLATION_MARKER_REINSTATED, "222"))

        sync_engine.execute_discord_sync(config, discord)

        discord.post_notice.assert_not_called()

    def test_uncancelled_raid_is_not_scanned_every_cycle(self, config, monkeypatch):
        evt = _raid()
        _patch_collect(monkeypatch, evt)
        _mapped(config, evt)
        discord = _discord()

        sync_engine.execute_discord_sync(config, discord)

        discord.find_cancellation_marker.assert_not_called()


# --- Dry-run ---


class TestPlan:
    def test_plan_shows_the_pending_notice(self, config, monkeypatch):
        evt = _raid(cancelled=True)
        _patch_collect(monkeypatch, evt)
        _mapped(config, evt)
        discord = _discord()

        plan = sync_engine.compute_discord_sync_plan(config, discord)

        infos = [e.participants_info for e in plan.entries]
        assert infos == ["cancelled, notify 3"]
        discord.post_notice.assert_not_called()

    def test_plan_shows_the_delete_after_the_window(self, config, monkeypatch):
        notice = _snowflake(datetime.now(UTC) - timedelta(hours=25))
        evt = _raid(cancelled=True)
        _patch_collect(monkeypatch, evt)
        _mapped(config, evt, cancelled={"notice_id": notice})
        discord = _discord(marker=(CANCELLATION_MARKER_CANCELLED, notice))

        plan = sync_engine.compute_discord_sync_plan(config, discord)

        assert [(e.action.value, e.participants_info) for e in plan.entries] == [
            ("delete", "cancelled")
        ]

    def test_plan_survives_a_failed_scan(self, config, monkeypatch):
        evt = _raid(cancelled=True)
        _patch_collect(monkeypatch, evt)
        _mapped(config, evt)
        discord = _discord()
        discord.find_cancellation_marker.side_effect = requests.HTTPError("429")

        plan = sync_engine.compute_discord_sync_plan(config, discord)

        assert plan.entries == []


# --- Discord client ---


@pytest.fixture
def poster():
    p = DiscordPoster("token", "forum-1", "guild-1")
    p._request = MagicMock()
    p._get_bot_user_id = MagicMock(return_value="bot")
    return p


def _msg(msg_id: str, content: str, author: str = "bot") -> dict:
    return {"id": msg_id, "content": content, "author": {"id": author}}


class TestCancellationMarker:
    def test_newest_marker_wins_regardless_of_listing_order(self, poster):
        poster._request.return_value = [
            _msg("100", f"{t('discord.cancelled_notice')}: <@1>"),
            _msg("300", f"{t('discord.cancelled_notice')}: <@1>"),
            _msg("200", f"{t('discord.reinstated_notice')}: <@1>"),
        ]
        assert poster.find_cancellation_marker(_THREAD) == (
            CANCELLATION_MARKER_CANCELLED,
            "300",
        )

    def test_every_language_is_recognised(self, poster):
        """A language switch must not re-announce a cancellation."""
        from fgc_sync import i18n

        for label in i18n.t_all("discord.cancelled_notice"):
            poster._request.return_value = [_msg("100", f"{label}:")]
            assert poster.find_cancellation_marker(_THREAD) == (
                CANCELLATION_MARKER_CANCELLED,
                "100",
            )

    def test_messages_from_members_are_ignored(self, poster):
        poster._request.return_value = [
            _msg("100", f"{t('discord.cancelled_notice')}: lol", author="someone")
        ]
        assert poster.find_cancellation_marker(_THREAD) is None

    def test_failed_listing_raises(self, poster):
        poster._request.side_effect = requests.HTTPError("boom")
        with pytest.raises(requests.HTTPError):
            poster.find_cancellation_marker(_THREAD)


class TestPostNotice:
    def test_posts_even_when_nobody_resolves(self, poster):
        """The notice is the record other clients read; it can't be skipped."""
        poster._find_member_id = MagicMock(return_value=None)
        poster._request.return_value = {"id": "n1"}

        assert poster.post_notice(_THREAD, "Label", {"Ghost"}) == "n1"
        payload = poster._request.call_args.kwargs["json"]
        assert payload["content"] == "Label:"

    def test_mentions_are_explicitly_allowed(self, poster):
        poster._find_member_id = MagicMock(side_effect=lambda n: {"A": "1"}.get(n))
        poster._request.return_value = {"id": "n1"}

        poster.post_notice(_THREAD, "Label", {"A", "Ghost"})

        payload = poster._request.call_args.kwargs["json"]
        assert payload["content"] == "Label: <@1>"
        assert payload["allowed_mentions"] == {"users": ["1"]}


def test_snowflake_datetime_reads_the_creation_time():
    # The weekly reply posted by the other client during the 2.17.0 ping-pong.
    assert snowflake_datetime("1552043409848537122") == datetime(
        2026, 9, 22, 19, 46, 24, 480000, tzinfo=UTC
    )
