"""Tests for DiscordPoster's ping/unping message handling.

Covers ``ping_members`` (now returns ``{name: message_id}``),
``remove_mentions`` (edits prior ping messages to strike out @mentions
without re-notifying others), and ``get_already_pinged_names`` (now
returns ``{name: message_id}`` from history).
"""

from unittest.mock import MagicMock

import pytest

from fgc_sync.services.discord_poster import DiscordPoster


@pytest.fixture
def poster():
    """A DiscordPoster with HTTP and member-resolution stubbed out."""
    p = DiscordPoster("token", "forum-1", "guild-1")
    p._request = MagicMock()
    # Pre-seed bot identity so ``get_already_pinged_names`` works without HTTP.
    p._bot_user_id = "bot-1"
    return p


def _stub_member_lookup(poster: DiscordPoster, mapping: dict[str, str]):
    """Make ``_find_member_id`` return user ids from *mapping* (name → id)."""
    poster._find_member_id = MagicMock(  # type: ignore[method-assign]
        side_effect=lambda name: mapping.get(name)
    )


# --- ping_members ---


class TestPingMembers:
    def test_returns_name_to_message_id_dict(self, poster):
        _stub_member_lookup(poster, {"Alice": "100001", "Bob": "200002"})
        poster._request.return_value = {"id": "msg-99"}

        result = poster.ping_members("ch-1", {"Alice", "Bob"}, label="Confirmed")

        assert result == {"Alice": "msg-99", "Bob": "msg-99"}
        poster._request.assert_called_once()
        # The single POSTed message includes both mentions
        args, kwargs = poster._request.call_args
        assert args[0] == "POST"
        assert "/channels/ch-1/messages" in args[1]
        content = kwargs["json"]["content"]
        assert content.startswith("Confirmed:")
        assert "<@100001>" in content
        assert "<@200002>" in content

    def test_unresolved_names_are_dropped(self, poster):
        _stub_member_lookup(poster, {"Alice": "100001"})  # Bob unresolved
        poster._request.return_value = {"id": "msg-1"}

        result = poster.ping_members("ch-1", {"Alice", "Bob"}, label="Confirmed")

        assert result == {"Alice": "msg-1"}
        assert "Bob" not in result

    def test_empty_when_no_one_resolves(self, poster):
        _stub_member_lookup(poster, {})
        result = poster.ping_members("ch-1", {"Alice"}, label="Confirmed")
        assert result == {}
        poster._request.assert_not_called()

    def test_empty_input_skips_post(self, poster):
        _stub_member_lookup(poster, {})
        result = poster.ping_members("ch-1", set(), label="Confirmed")
        assert result == {}
        poster._request.assert_not_called()


# --- remove_mentions ---


class TestRemoveMentions:
    def test_strikes_out_mention_and_patches_with_no_parse(self, poster):
        _stub_member_lookup(poster, {"Alice": "100001", "Bob": "200002"})
        # GET returns the original ping message; PATCH returns nothing of interest.
        poster._request.side_effect = [
            {"content": "Confirmed: <@100001> <@200002>"},
            {},
        ]

        poster.remove_mentions("ch-1", {"Alice": "msg-1"})

        # Two calls: GET then PATCH
        assert poster._request.call_count == 2
        get_args, _ = poster._request.call_args_list[0]
        patch_args, patch_kwargs = poster._request.call_args_list[1]

        assert get_args[0] == "GET"
        assert "msg-1" in get_args[1]

        assert patch_args[0] == "PATCH"
        assert "msg-1" in patch_args[1]
        # Alice's mention is replaced; Bob's is untouched
        assert patch_kwargs["json"]["content"] == "Confirmed: ~~@Alice~~ <@200002>"
        # allowed_mentions belt-and-braces guard
        assert patch_kwargs["json"]["allowed_mentions"] == {"parse": []}

    def test_handles_nickname_mention_form(self, poster):
        # Discord has both <@id> and <@!id> for nickname mentions
        _stub_member_lookup(poster, {"Alice": "100001"})
        poster._request.side_effect = [
            {"content": "Confirmed: <@!100001>"},
            {},
        ]

        poster.remove_mentions("ch-1", {"Alice": "msg-1"})

        patch_args, patch_kwargs = poster._request.call_args_list[1]
        assert patch_kwargs["json"]["content"] == "Confirmed: ~~@Alice~~"

    def test_groups_multiple_removals_into_one_patch_per_message(self, poster):
        _stub_member_lookup(poster, {"Alice": "100001", "Bob": "200002"})
        poster._request.side_effect = [
            {"content": "Confirmed: <@100001> <@200002>"},
            {},
        ]

        poster.remove_mentions("ch-1", {"Alice": "msg-1", "Bob": "msg-1"})

        # GET once, PATCH once — not GET+PATCH per name
        assert poster._request.call_count == 2
        patch_args, patch_kwargs = poster._request.call_args_list[1]
        # Both mentions are gone in the single edit
        assert "<@100001>" not in patch_kwargs["json"]["content"]
        assert "<@200002>" not in patch_kwargs["json"]["content"]
        assert "~~@Alice~~" in patch_kwargs["json"]["content"]
        assert "~~@Bob~~" in patch_kwargs["json"]["content"]

    def test_two_message_ids_yield_two_get_patch_pairs(self, poster):
        _stub_member_lookup(poster, {"Alice": "100001", "Bob": "200002"})
        poster._request.side_effect = [
            {"content": "Confirmed: <@100001>"},
            {},
            {"content": "Newly confirmed: <@200002>"},
            {},
        ]

        poster.remove_mentions("ch-1", {"Alice": "msg-1", "Bob": "msg-2"})

        assert poster._request.call_count == 4

    def test_empty_message_id_is_skipped(self, poster):
        # Legacy-migrated entries have empty-string message ids
        poster.remove_mentions("ch-1", {"Alice": ""})
        poster._request.assert_not_called()

    def test_no_change_skips_patch(self, poster):
        # If Alice's mention isn't in the message, no PATCH should fire
        _stub_member_lookup(poster, {"Alice": "100001"})
        poster._request.return_value = {"content": "Confirmed: <@999999>"}

        poster.remove_mentions("ch-1", {"Alice": "msg-1"})

        # Only the GET fired, no PATCH
        assert poster._request.call_count == 1
        assert poster._request.call_args_list[0][0][0] == "GET"

    def test_404_on_get_is_silent(self, poster):
        import requests

        resp = MagicMock()
        resp.status_code = 404
        err = requests.HTTPError(response=resp)
        poster._request.side_effect = err

        # Should not raise
        poster.remove_mentions("ch-1", {"Alice": "msg-1"})


# --- get_already_pinged_names ---


class TestGetAlreadyPingedNames:
    def test_returns_name_to_message_id(self, poster, monkeypatch):
        _stub_member_lookup(poster, {"Alice": "100001", "Bob": "200002"})
        poster._request.return_value = [
            {
                "id": "msg-9",
                "author": {"id": "bot-1"},
                "content": "Confirmed: <@100001> <@200002>",
            }
        ]

        result = poster.get_already_pinged_names("ch-1", {"Alice", "Bob", "Carol"})

        assert result == {"Alice": "msg-9", "Bob": "msg-9"}

    def test_newest_message_id_wins_when_name_appears_in_two(self, poster):
        # Discord returns messages newest-first; first encounter should win
        _stub_member_lookup(poster, {"Alice": "100001"})
        poster._request.return_value = [
            {
                "id": "msg-newer",
                "author": {"id": "bot-1"},
                "content": "Newly confirmed: <@100001>",
            },
            {
                "id": "msg-older",
                "author": {"id": "bot-1"},
                "content": "Confirmed: <@100001>",
            },
        ]

        result = poster.get_already_pinged_names("ch-1", {"Alice"})

        assert result == {"Alice": "msg-newer"}

    def test_ignores_messages_from_other_authors(self, poster):
        _stub_member_lookup(poster, {"Alice": "100001"})
        poster._request.return_value = [
            {
                "id": "msg-1",
                "author": {"id": "someone-else"},
                "content": "Confirmed: <@100001>",
            }
        ]

        result = poster.get_already_pinged_names("ch-1", {"Alice"})

        assert result == {}

    def test_ignores_messages_without_ping_label_prefix(self, poster):
        _stub_member_lookup(poster, {"Alice": "100001"})
        poster._request.return_value = [
            {
                "id": "msg-1",
                "author": {"id": "bot-1"},
                "content": "Just chatting <@100001>",
            }
        ]

        result = poster.get_already_pinged_names("ch-1", {"Alice"})

        assert result == {}

    def test_recognises_german_label_prefix(self, poster):
        # Cross-language dedup: "Bestätigt:" should also count
        _stub_member_lookup(poster, {"Alice": "100001"})
        poster._request.return_value = [
            {
                "id": "msg-1",
                "author": {"id": "bot-1"},
                "content": "Bestätigt: <@100001>",
            }
        ]

        result = poster.get_already_pinged_names("ch-1", {"Alice"})

        assert result == {"Alice": "msg-1"}

    def test_returns_empty_when_no_bot_id(self, poster):
        poster._bot_user_id = None
        result = poster.get_already_pinged_names("ch-1", {"Alice"})
        assert result == {}
