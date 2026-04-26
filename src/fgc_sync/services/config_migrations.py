"""Forward-only schema migrations for ``config.json``.

The config carries a ``schema_version`` integer. On load, ``apply_all`` runs
every migration whose target version is greater than the stored version, in
order, and bumps ``schema_version`` after each.

Adding a new migration:

1. Increment :data:`CURRENT_SCHEMA_VERSION`.
2. Define a ``_migrate_to_vN(data)`` function that mutates *data* in place.
   It must be idempotent — running it twice on the same data should be a
   no-op the second time.
3. Append ``(N, _migrate_to_vN)`` to :data:`_MIGRATIONS`.
4. Never modify an existing migration after release — it's a frozen snapshot
   of how to upgrade out of that version.

Configs that predate schema versioning have no ``schema_version`` field and
are treated as version 0, so every migration runs.
"""

from __future__ import annotations

from collections.abc import Callable

CURRENT_SCHEMA_VERSION = 2


def _migrate_to_v1(data: dict) -> None:
    """v1: introduce the ``language`` key for i18n.

    Existing configs predate language support — default to English so the
    Settings dialog shows a value and so `t()` calls behave consistently.
    """
    if "language" not in data:
        # Hardcoded so this migration is stable even if i18n defaults change.
        data["language"] = "en-UK"


def _migrate_to_v2(data: dict) -> None:
    """v2: turn ``discord_message_mapping[*].pinged`` from ``list[name]`` into
    ``{name: message_id}``.

    The new shape lets us locate the original ping message when a member is
    removed from the roster, so we can edit the @mention away (Discord does
    not re-notify on edits). Pre-v2 entries don't know which message contained
    each ping, so they migrate with an empty-string sentinel — those names are
    still treated as already-pinged (no re-ping) but the @mention can't be
    edited away if they leave; the visual artifact is acceptable for legacy
    rows. Also folds the even-older ``confirmed`` field into ``pinged`` for
    configs that never went through the rename in f438501.
    """
    mapping = data.get("discord_message_mapping")
    if not isinstance(mapping, dict):
        return
    for entry in mapping.values():
        if not isinstance(entry, dict):
            continue
        if "pinged" not in entry and "confirmed" in entry:
            entry["pinged"] = entry.pop("confirmed")
        pinged = entry.get("pinged")
        if isinstance(pinged, list):
            entry["pinged"] = {name: "" for name in pinged}


# Each entry is (target_version, migration_fn). Applied in order on configs
# whose stored version is less than target_version.
_MIGRATIONS: tuple[tuple[int, Callable[[dict], None]], ...] = (
    (1, _migrate_to_v1),
    (2, _migrate_to_v2),
)


def apply_all(data: dict) -> bool:
    """Bring *data* up to :data:`CURRENT_SCHEMA_VERSION`.

    Returns ``True`` if anything changed (so the caller knows to flush to
    disk). Configs already at or beyond the current version are left alone
    — this preserves forward compatibility when a user downgrades the app.
    """
    current_raw = data.get("schema_version", 0)
    try:
        current = int(current_raw)
    except (TypeError, ValueError):
        # Corrupted version field — treat as v0. Migrations are idempotent
        # so the worst case is rewriting fields that are already correct.
        current = 0

    if current >= CURRENT_SCHEMA_VERSION:
        return False

    for version, migration in _MIGRATIONS:
        if version > current:
            migration(data)
            data["schema_version"] = version
    return True
