"""Cross-client version coordination state (pure logic, no Discord/IO).

Multiple machines run FGC-Sync against the same guild. To stop an older client
from clobbering data a newer one wrote — and to nudge people to update — each
client publishes a small registry entry to a shared Discord control message:

    {"clients": {"<key>": {"version": "2.10.0",
                            "names": ["Klopfbernd"],
                            "last_seen": "<iso-8601 UTC>"}}}

This module owns parsing, merging and interpreting that structure. It performs
no IO: callers read/write the raw message content via ``DiscordPoster`` and the
serialize/deserialize helpers here. Pre-2.10.0 clients never read or write the
control message, so the scheme is fully backward-compatible.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

# Marks the bot message that carries the registry so it can be re-located if
# its id is lost. Kept on its own first line; the JSON follows in a code block.
REGISTRY_MARKER = "[FGC-SYNC-REGISTRY]"

# Tags a version-notice message so the same release isn't announced twice. The
# advertised version is appended directly after the marker, e.g.
# ``[FGC-SYNC-UPDATE]2.10.0``.
UPDATE_NOTICE_MARKER = "[FGC-SYNC-UPDATE]"

# Entries older than this are ignored for gating/notification — a client that
# stopped running should not block everyone else forever.
FRESHNESS_HOURS = 24


def parse_version(v: str) -> tuple[int, ...]:
    """Parse '2.10.0' into a comparable tuple; unparseable → ``(0,)``."""
    try:
        return tuple(int(x) for x in str(v).split("."))
    except (ValueError, TypeError):
        return (0,)


def upsert_client(
    registry: dict,
    key: str,
    version: str,
    names: list[str],
    now: datetime,
) -> dict:
    """Return a copy of *registry* with this client's entry refreshed."""
    clients = dict(registry.get("clients", {}))
    clients[key] = {
        "version": version,
        "names": list(names),
        "last_seen": now.astimezone(UTC).isoformat(),
    }
    return {"clients": clients}


def _is_fresh(entry: dict, now: datetime, freshness_hours: int) -> bool:
    raw = entry.get("last_seen")
    if not raw:
        return False
    try:
        seen = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    return now.astimezone(UTC) - seen <= timedelta(hours=freshness_hours)


def active_clients(
    registry: dict,
    now: datetime,
    *,
    freshness_hours: int = FRESHNESS_HOURS,
) -> dict:
    """Return only the registry entries seen within the freshness window."""
    return {
        key: entry
        for key, entry in (registry.get("clients", {}) or {}).items()
        if isinstance(entry, dict) and _is_fresh(entry, now, freshness_hours)
    }


def newer_client_active(
    registry: dict,
    my_version: str,
    now: datetime,
    *,
    exclude_key: str | None = None,
    freshness_hours: int = FRESHNESS_HOURS,
) -> bool:
    """True if a *fresh* client (other than *exclude_key*) runs a higher version."""
    mine = parse_version(my_version)
    for key, entry in active_clients(
        registry, now, freshness_hours=freshness_hours
    ).items():
        if key == exclude_key:
            continue
        if parse_version(entry.get("version", "0")) > mine:
            return True
    return False


def serialize(registry: dict) -> str:
    """Render the registry as the control message body (marker + JSON block)."""
    body = json.dumps(registry, ensure_ascii=False, sort_keys=True)
    return f"{REGISTRY_MARKER}\n```json\n{body}\n```"


def deserialize(content: str) -> dict:
    """Recover a registry from a control message body. Tolerant of junk → {}."""
    if not content or REGISTRY_MARKER not in content:
        return {}
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        data = json.loads(content[start : end + 1])
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}
