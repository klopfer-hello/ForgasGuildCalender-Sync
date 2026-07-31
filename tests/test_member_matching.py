"""Tests for DiscordPoster member matching (``_find_member_id``).

Covers the plain substring match (character name in nickname / display
name / username) and the wildcard patterns guild members can embed in
their **server nickname** to map several twinks to one Discord account
(e.g. ``Exo* | Maximilian``, ``-Pieps / Krissi``). Wildcard characters
are ``- _ . * + ~ ?``; each stands for any run of characters, including
none, and a wildcard segment must match the whole character name.
"""

from unittest.mock import MagicMock

import pytest

from fgc_sync.services.discord_poster import DiscordPoster


def _member(
    user_id: str,
    nick: str | None = None,
    global_name: str | None = None,
    username: str = "",
) -> dict:
    return {
        "nick": nick,
        "user": {"id": user_id, "global_name": global_name, "username": username},
    }


def _poster(members: list[dict]) -> DiscordPoster:
    p = DiscordPoster("token", "forum-1", "guild-1")
    p._request = MagicMock()
    p._members_cache = members
    return p


# --- plain substring matching (pre-existing behaviour) ---


class TestSubstringMatching:
    def test_matches_nickname_substring(self):
        p = _poster([_member("1", nick="Klopfbernd der Große")])
        assert p._find_member_id("Klopfbernd") == "1"

    def test_matches_global_name_and_username(self):
        p = _poster(
            [
                _member("1", global_name="Alicechar"),
                _member("2", username="bobchar"),
            ]
        )
        assert p._find_member_id("Alicechar") == "1"
        assert p._find_member_id("Bobchar") == "2"

    def test_no_match_returns_none(self):
        p = _poster([_member("1", nick="Somebody")])
        assert p._find_member_id("Nomatch") is None

    def test_plain_segment_in_separated_nickname_still_matches(self):
        # "Exomer | Exo* | Maximilian" — the verbatim twink name matches
        # via the ordinary substring check, separators untouched.
        p = _poster([_member("1", nick="Exomer | Exo* | Maximilian")])
        assert p._find_member_id("Exomer") == "1"


# --- wildcard matching in server nicknames ---


class TestWildcardMatching:
    def test_star_suffix_matches_all_twinks(self):
        p = _poster([_member("1", nick="Exo* | Maximilian")])
        for name in ("Exototem", "Exomer", "Exower", "Exonova"):
            assert p._find_member_id(name) == "1", name

    def test_hyphen_prefix_matches_all_twinks(self):
        p = _poster([_member("1", nick="-Pieps / Krissi")])
        for name in ("Schampieps", "Dosenpieps", "Profitpieps"):
            assert p._find_member_id(name) == "1", name

    def test_dot_matches_zero_or_more_characters(self):
        p = _poster([_member("1", nick="Vonda.i")])
        assert p._find_member_id("Vondai") == "1"
        assert p._find_member_id("Vondaai") == "1"

    def test_all_wildcard_characters_are_equivalent(self):
        for wc in "-_.*+~?":
            p = _poster([_member("1", nick=f"Exo{wc}")])
            assert p._find_member_id("Exototem") == "1", wc

    def test_wildcard_is_case_insensitive(self):
        p = _poster([_member("1", nick="exo*")])
        assert p._find_member_id("EXOTOTEM") == "1"

    def test_wildcard_must_match_whole_name(self):
        # "Exo*" anchors at the start — a name merely *containing* "exo"
        # elsewhere must not match.
        p = _poster([_member("1", nick="Exo*")])
        assert p._find_member_id("Totemexo") is None

    def test_pattern_without_wildcard_is_not_reverse_matched(self):
        # Plain "Pieps" is not a pattern — it must not match Schampieps.
        p = _poster([_member("1", nick="Pieps / Krissi")])
        assert p._find_member_id("Schampieps") is None

    def test_only_wildcards_never_match(self):
        # A segment of pure wildcards would match everyone — ignored.
        p = _poster([_member("1", nick="*"), _member("2", nick="- / ~")])
        assert p._find_member_id("Anychar") is None

    def test_wildcards_only_apply_to_nickname(self):
        # Usernames routinely contain "." and "_" without wildcard intent.
        p = _poster(
            [
                _member("1", username="scham.pieps"),
                _member("2", global_name="Scham*"),
            ]
        )
        assert p._find_member_id("Schamxpieps") is None
        assert p._find_member_id("Schampieps") is None

    def test_substring_match_wins_over_wildcard(self):
        # A member whose name contains the character verbatim beats an
        # earlier member's wildcard pattern.
        p = _poster(
            [
                _member("1", nick="Exo*"),
                _member("2", nick="Exototem"),
            ]
        )
        assert p._find_member_id("Exototem") == "2"

    def test_consecutive_wildcards_collapse(self):
        p = _poster([_member("1", nick="Exo**~")])
        assert p._find_member_id("Exototem") == "1"

    def test_regex_metacharacters_in_nickname_are_literal(self):
        # "(" would crash a naive regex build; it must be escaped and
        # matched literally.
        p = _poster([_member("1", nick="Exo(*")])
        assert p._find_member_id("Exototem") is None
        assert p._find_member_id("Exo(totem") == "1"

    def test_missing_nick_is_handled(self):
        p = _poster([_member("1", nick=None, username="someone")])
        assert p._find_member_id("Unrelated") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
