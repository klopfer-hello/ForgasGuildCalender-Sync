"""Lightweight i18n loader.

Translation files live alongside this module as ``<code>.json`` (e.g.
``en-UK.json``). Drop a new file in this directory and it is picked up
automatically — no code changes required.

Each file has a ``_meta`` block with at least ``display_name``::

    {
        "_meta": {"display_name": "Deutsch"},
        "common": { ... }
    }

Lookup is dot-separated (``setup.wow.title``); missing keys fall back to
``REFERENCE_LANGUAGE`` and finally to the key itself, so a missing entry
never crashes the app. On first load each language file is validated
against the reference language and any missing keys are logged at WARNING.
"""

from __future__ import annotations

import json
import logging
from importlib import resources
from typing import Any

log = logging.getLogger(__name__)

# The reference language is the source of truth for which keys must exist.
# When a new language file is added, it is compared to this one and any
# missing keys are reported via the logger.
REFERENCE_LANGUAGE = "en-UK"

_loaded: dict[str, dict] = {}
_discovered: tuple[str, ...] | None = None
_validated: set[str] = set()
_current_language: str = REFERENCE_LANGUAGE


def _discover_languages() -> tuple[str, ...]:
    """Scan the package directory for ``*.json`` translation files.

    Returns a sorted tuple of language codes (filename stems). Cached after
    the first call — drop a new file and restart the app to pick it up.
    """
    global _discovered
    if _discovered is not None:
        return _discovered
    codes: list[str] = []
    try:
        for entry in resources.files(__package__).iterdir():
            name = entry.name
            if name.endswith(".json") and not name.startswith("_"):
                codes.append(name[:-5])
    except (FileNotFoundError, OSError) as e:
        log.error("i18n: could not enumerate translation files: %s", e)
    _discovered = tuple(sorted(codes))
    if not _discovered:
        log.error("i18n: no translation files found in package")
    elif REFERENCE_LANGUAGE not in _discovered:
        log.error(
            "i18n: reference language %s is missing — fallback will be unavailable",
            REFERENCE_LANGUAGE,
        )
    return _discovered


def _flatten_keys(data: Any, prefix: str = "") -> set[str]:
    """Return the set of leaf paths in a nested dict.

    A leaf is any non-dict value. Lists count as leaves (they are
    semantically a single translation entry — e.g. weekday arrays).
    """
    keys: set[str] = set()
    if not isinstance(data, dict):
        return keys
    for k, v in data.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys.update(_flatten_keys(v, path))
        else:
            keys.add(path)
    return keys


def _validate(code: str, data: dict) -> None:
    """Compare *data* against the reference language and log missing keys."""
    if code in _validated or code == REFERENCE_LANGUAGE:
        _validated.add(code)
        return
    reference = _load_file(REFERENCE_LANGUAGE)
    if not reference:
        # Reference itself failed to load; can't validate
        _validated.add(code)
        return
    ref_keys = _flatten_keys(reference) - {"_meta.display_name", "_meta.native_name"}
    own_keys = _flatten_keys(data)
    missing = sorted(ref_keys - own_keys)
    if missing:
        log.warning(
            "i18n: language %s is missing %d key(s) (will fall back to %s): %s",
            code,
            len(missing),
            REFERENCE_LANGUAGE,
            ", ".join(missing[:10]) + ("..." if len(missing) > 10 else ""),
        )
    extra = sorted(own_keys - ref_keys)
    if extra:
        log.debug(
            "i18n: language %s has %d extra key(s): %s", code, len(extra), extra[:5]
        )
    if "_meta" not in data or "display_name" not in (data.get("_meta") or {}):
        log.warning(
            "i18n: language %s is missing _meta.display_name — code will be shown instead",
            code,
        )
    _validated.add(code)


def _load_file(code: str) -> dict:
    if code in _loaded:
        return _loaded[code]
    try:
        text = resources.files(__package__).joinpath(f"{code}.json").read_text("utf-8")
        data = json.loads(text)
    except (FileNotFoundError, OSError):
        log.warning("i18n: language file %s.json not found", code)
        data = {}
    except json.JSONDecodeError as e:
        log.error("i18n: failed to parse %s.json: %s", code, e)
        data = {}
    _loaded[code] = data
    if data:
        _validate(code, data)
    return data


def available_languages() -> tuple[str, ...]:
    """Return the language codes shipped with the application."""
    return _discover_languages()


def display_name(code: str) -> str:
    """Return the human-readable name for *code* (from its ``_meta`` block).

    Falls back to the code itself when the file or meta block is missing.
    """
    data = _load_file(code)
    meta = data.get("_meta") if isinstance(data, dict) else None
    if isinstance(meta, dict):
        for key in ("display_name", "native_name", "name"):
            value = meta.get(key)
            if isinstance(value, str) and value:
                return value
    return code


def get_language() -> str:
    """Return the currently active language code."""
    return _current_language


def set_language(code: str | None) -> None:
    """Activate *code* as the current language. Falls back to default if unknown."""
    global _current_language
    languages = _discover_languages()
    if code in languages:
        _current_language = code
    else:
        if code:
            log.warning(
                "i18n: language %s not available, using %s",
                code,
                REFERENCE_LANGUAGE,
            )
        _current_language = REFERENCE_LANGUAGE
    _load_file(_current_language)
    if _current_language != REFERENCE_LANGUAGE:
        _load_file(REFERENCE_LANGUAGE)


def _lookup(data: dict, key: str) -> Any:
    node: Any = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def t(key: str, **kwargs: Any) -> str:
    """Translate *key* in the active language.

    Format placeholders are filled via ``str.format(**kwargs)`` when the value
    is a string. If the key resolves to a non-string (e.g. a list), call ``tl``
    instead.
    """
    value = _lookup(_load_file(_current_language), key)
    if value is None and _current_language != REFERENCE_LANGUAGE:
        value = _lookup(_load_file(REFERENCE_LANGUAGE), key)
    if value is None:
        log.debug("i18n: missing key %r", key)
        return key
    if not isinstance(value, str):
        return str(value)
    if not kwargs:
        return value
    try:
        return value.format(**kwargs)
    except (KeyError, IndexError, ValueError) as e:
        log.error("i18n: failed to format %r: %s", key, e)
        return value


def tl(key: str) -> list:
    """Translate *key* expecting a list value (e.g. weekday arrays)."""
    value = _lookup(_load_file(_current_language), key)
    if value is None and _current_language != REFERENCE_LANGUAGE:
        value = _lookup(_load_file(REFERENCE_LANGUAGE), key)
    if isinstance(value, list):
        return value
    log.debug("i18n: missing list key %r", key)
    return []


def t_for(code: str, key: str, **kwargs: Any) -> str:
    """Translate *key* in a specific language (used for cross-language matching).

    Returns the value formatted with kwargs, falling back through
    ``REFERENCE_LANGUAGE`` and finally the key itself.
    """
    value = _lookup(_load_file(code), key)
    if value is None and code != REFERENCE_LANGUAGE:
        value = _lookup(_load_file(REFERENCE_LANGUAGE), key)
    if value is None:
        return key
    if not isinstance(value, str):
        return str(value)
    if not kwargs:
        return value
    try:
        return value.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return value


def tl_for(code: str, key: str) -> list:
    """List-valued counterpart to :func:`t_for`."""
    value = _lookup(_load_file(code), key)
    if value is None and code != REFERENCE_LANGUAGE:
        value = _lookup(_load_file(REFERENCE_LANGUAGE), key)
    if isinstance(value, list):
        return value
    return []


def t_all(key: str, **kwargs: Any) -> list[str]:
    """Return *key*'s value formatted in every available language (deduplicated).

    Used for cross-language matching — e.g. so a Discord thread named in
    German is still discoverable after the user switches to English.
    """
    seen: set[str] = set()
    out: list[str] = []
    for code in _discover_languages():
        val = t_for(code, key, **kwargs)
        if val not in seen:
            seen.add(val)
            out.append(val)
    return out
