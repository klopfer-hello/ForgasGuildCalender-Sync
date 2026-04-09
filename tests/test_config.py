"""Tests for fgc_sync.services.config."""

import json

from fgc_sync.services.config import Config, decode_setup_code, encode_setup_code

# --- encode_setup_code / decode_setup_code ---


class TestSetupCodeRoundTrip:
    def test_round_trip(self):
        data = {
            "discord_bot_token": "token123",
            "discord_guild_id": "guild456",
            "discord_forum_id": "forum789",
        }
        code = encode_setup_code(data)
        decoded = decode_setup_code(code)
        assert decoded == data

    def test_extra_keys_ignored(self):
        data = {
            "discord_bot_token": "t",
            "discord_guild_id": "g",
            "discord_forum_id": "f",
            "wow_path": "C:/Games/WoW",
            "calendar_id": "my-cal",
        }
        code = encode_setup_code(data)
        decoded = decode_setup_code(code)
        assert "wow_path" not in decoded
        assert decoded["discord_bot_token"] == "t"

    def test_code_starts_with_prefix(self):
        data = {
            "discord_bot_token": "t",
            "discord_guild_id": "g",
            "discord_forum_id": "f",
        }
        code = encode_setup_code(data)
        assert code.startswith("fgc1-")


class TestDecodeSetupCodeBadInput:
    def test_wrong_prefix(self):
        assert decode_setup_code("fgc2-abc") is None

    def test_empty_string(self):
        assert decode_setup_code("") is None

    def test_garbage_data(self):
        assert decode_setup_code("fgc1-!!!notbase64!!!") is None

    def test_valid_base64_but_not_zlib(self):
        import base64

        fake = "fgc1-" + base64.urlsafe_b64encode(b"not zlib").decode()
        assert decode_setup_code(fake) is None

    def test_missing_keys(self):
        """Valid encoding but with empty values should return None."""
        data = {
            "discord_bot_token": "",
            "discord_guild_id": "g",
            "discord_forum_id": "f",
        }
        code = encode_setup_code(data)
        assert decode_setup_code(code) is None

    def test_whitespace_stripped(self):
        data = {
            "discord_bot_token": "t",
            "discord_guild_id": "g",
            "discord_forum_id": "f",
        }
        code = encode_setup_code(data)
        assert decode_setup_code(f"  {code}  ") == data


# --- Config class ---


class TestConfigGetSet:
    def test_get_default(self, tmp_path):
        cfg = Config(tmp_path / "config.json")
        assert cfg.get("missing") is None
        assert cfg.get("missing", "fallback") == "fallback"

    def test_set_and_get(self, tmp_path):
        cfg = Config(tmp_path / "config.json")
        cfg.set("key", "value")
        assert cfg.get("key") == "value"

    def test_persists_to_disk(self, tmp_path):
        path = tmp_path / "config.json"
        cfg = Config(path)
        cfg.set("hello", "world")
        # Read back from a new instance
        cfg2 = Config(path)
        assert cfg2.get("hello") == "world"


class TestConfigLoadSave:
    def test_load_nonexistent_file(self, tmp_path):
        cfg = Config(tmp_path / "does_not_exist.json")
        assert cfg.get("anything") is None

    def test_load_existing_file(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"pre": "existing"}), encoding="utf-8")
        cfg = Config(path)
        assert cfg.get("pre") == "existing"

    def test_save_creates_file(self, tmp_path):
        path = tmp_path / "config.json"
        assert not path.exists()
        cfg = Config(path)
        cfg.set("x", 1)
        assert path.exists()


class TestConfigTransaction:
    def test_commit_persists(self, tmp_path):
        path = tmp_path / "config.json"
        cfg = Config(path)
        cfg.begin_transaction()
        cfg.set("key", "committed")
        cfg.commit_transaction()
        cfg2 = Config(path)
        assert cfg2.get("key") == "committed"

    def test_rollback_discards(self, tmp_path):
        path = tmp_path / "config.json"
        cfg = Config(path)
        cfg.set("key", "original")
        cfg.begin_transaction()
        cfg.set("key", "changed")
        assert cfg.get("key") == "changed"
        cfg.rollback_transaction()
        assert cfg.get("key") == "original"

    def test_transaction_defers_write(self, tmp_path):
        path = tmp_path / "config.json"
        cfg = Config(path)
        cfg.begin_transaction()
        cfg.set("key", "buffered")
        # File should not have been written yet
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            assert "key" not in raw
        cfg.commit_transaction()
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["key"] == "buffered"

    def test_rollback_without_begin_is_noop(self, tmp_path):
        cfg = Config(tmp_path / "config.json")
        cfg.set("key", "value")
        cfg.rollback_transaction()  # no-op
        assert cfg.get("key") == "value"


class TestConfigProperties:
    def test_is_setup_complete_false_when_empty(self, tmp_path):
        cfg = Config(tmp_path / "config.json")
        assert cfg.is_setup_complete is False

    def test_is_setup_complete_true(self, tmp_path):
        cfg = Config(tmp_path / "config.json")
        cfg.set("wow_path", "C:/WoW")
        cfg.set("account_folder", "12345")
        cfg.set("guild_key", "MyGuild")
        assert cfg.is_setup_complete is True

    def test_is_setup_complete_partial(self, tmp_path):
        cfg = Config(tmp_path / "config.json")
        cfg.set("wow_path", "C:/WoW")
        cfg.set("account_folder", "12345")
        # missing guild_key
        assert cfg.is_setup_complete is False

    def test_is_google_configured_false(self, tmp_path):
        cfg = Config(tmp_path / "config.json")
        assert cfg.is_google_configured is False

    def test_is_google_configured_true(self, tmp_path):
        cfg = Config(tmp_path / "config.json")
        cfg.set("calendar_id", "my-cal@group.calendar.google.com")
        assert cfg.is_google_configured is True

    def test_saved_variables_path_none_when_unconfigured(self, tmp_path):
        cfg = Config(tmp_path / "config.json")
        assert cfg.saved_variables_path is None

    def test_saved_variables_path_constructed(self, tmp_path):
        cfg = Config(tmp_path / "config.json")
        cfg.set("wow_path", "C:/WoW")
        cfg.set("account_folder", "12345")
        sv = cfg.saved_variables_path
        assert sv is not None
        assert sv.name == "ForgasGuildCalendar.lua"
        assert "WTF" in str(sv)
        assert "12345" in str(sv)

    def test_log_level_default(self, tmp_path):
        cfg = Config(tmp_path / "config.json")
        assert cfg.log_level == "ERROR"

    def test_log_level_custom(self, tmp_path):
        cfg = Config(tmp_path / "config.json")
        cfg.set("log_level", "debug")
        assert cfg.log_level == "DEBUG"  # uppercased

    def test_token_path(self, tmp_path):
        cfg = Config(tmp_path / "config.json")
        assert cfg.token_path == tmp_path / "token.json"

    def test_path_property(self, tmp_path):
        path = tmp_path / "config.json"
        cfg = Config(path)
        assert cfg.path == path

    def test_app_data_dir(self, tmp_path):
        path = tmp_path / "config.json"
        cfg = Config(path)
        assert cfg.app_data_dir == tmp_path
