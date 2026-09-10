"""Render a weekly raid schedule as a school-timetable-style image."""

from __future__ import annotations

import hashlib
import io
from datetime import date, timedelta

from PIL import Image, ImageDraw

from fgc_sync import i18n
from fgc_sync.i18n import t, tl
from fgc_sync.models.events import CalendarEvent
from fgc_sync.services.roster_image import (
    ACCENT_COLOR,
    BG_COLOR,
    BORDER_COLOR,
    HEADER_BG,
    SUBTEXT_COLOR,
    TEXT_COLOR,
    _load_font,
)

#: Last-resort event length for the weekly grid, used only when an event
#: reaches the renderer without a resolved ``duration_minutes`` (the sync-engine
#: collectors always resolve one — see services.event_duration). Fractional
#: values are supported; everything flowing to canvas geometry is coerced to int.
WEEKLY_EVENT_DURATION_HOURS = 2.5


def _duration_minutes(evt: CalendarEvent) -> int:
    """Effective length of *evt* in whole minutes, for grid geometry."""
    return int(evt.duration_minutes or WEEKLY_EVENT_DURATION_HOURS * 60)


def get_weekly_thread_name() -> str:
    """Active-language name for the permanent weekly-overview forum thread."""
    return t("weekly.thread_name")


def candidate_weekly_thread_names() -> list[str]:
    """Every translation of the weekly-overview thread name (deduplicated).

    Used for cross-language adoption of an existing thread when the user
    switches the application language.
    """
    return i18n.t_all("weekly.thread_name")


# Layout
_SCALE = 2
_PADDING = 20 * _SCALE
_HEADER_HEIGHT = 96 * _SCALE
_DAY_HEADER_HEIGHT = 44 * _SCALE
_TIME_COL_WIDTH = 60 * _SCALE
_DAY_COL_WIDTH = 200 * _SCALE
_HOUR_HEIGHT = 44 * _SCALE
_DEFAULT_START_HOUR = 17
_DEFAULT_END_HOUR = 24  # exclusive

# Event cell palette (darker than header — still readable)
_EVENT_FILL = (56, 76, 110)
_EVENT_BORDER = (120, 160, 220)
# Full-raid palette (confirmed_count >= max roster size)
_EVENT_FILL_FULL = (40, 100, 60)
_EVENT_BORDER_FULL = (110, 200, 140)
#: How far the solid event colour is faded toward the grid for the part of a
#: card that extends past the raid's real end time. The card grows to fit its
#: label; only the region up to the true end time keeps the full colour, so the
#: colour boundary still reads as "the raid ends here".
_EVENT_TAIL_BLEND = 0.62

_GRID_FILL = (28, 28, 36)
_GRID_STRIPE = (36, 36, 46)

#: Pixel height of the six-line cell label (name, time, RL, confirmed, signed,
#: open) plus its padding, at the widest 1-lane tier — the tallest the label
#: ever gets, since narrower tiers shrink the fonts.
_CELL_LABEL_HEIGHT = 5 * _SCALE + 18 * _SCALE + 5 * (16 * _SCALE) + 4 * _SCALE

#: The same height expressed in grid minutes: a card is never drawn shorter
#: than this, so the label always sits on its own backdrop instead of spilling
#: onto the grid. Short raids (gruul 30, gruul_mag 60, the 2h raids) would
#: otherwise overflow, which is what a duration-exact box costs once durations
#: stop being uniform. Derived from constants only — no font metrics — so every
#: client packs lanes identically.
_MIN_CARD_MINUTES = -(-_CELL_LABEL_HEIGHT * 60 // _HOUR_HEIGHT)


def _card_minutes(evt: CalendarEvent) -> int:
    """Height of *evt*'s card in grid minutes — its duration, or the label."""
    return max(_duration_minutes(evt), _MIN_CARD_MINUTES)


def _blend(colour: tuple, toward: tuple, amount: float) -> tuple:
    """Mix *colour* *amount* of the way toward *toward*."""
    return tuple(
        round(c + (t - c) * amount) for c, t in zip(colour, toward, strict=True)
    )


def format_weekly_summary(monday: date, num_events: int) -> str:
    """Build the text that accompanies the weekly overview image.

    Shown as the starter message / topic in the Discord forum thread.
    """
    sunday = monday + timedelta(days=6)
    iso = monday.isocalendar()
    date_range = (
        f"{monday.day:02d}.{monday.month:02d}.{monday.year} – "
        f"{sunday.day:02d}.{sunday.month:02d}.{sunday.year}"
    )
    return t(
        "weekly.summary",
        week=iso[1],
        year=iso[0],
        date_range=date_range,
        count=num_events,
    )


def week_bounds(
    today: date | None = None,
    *,
    week_offset: int = 0,
) -> tuple[date, date, str]:
    """Return (monday, sunday, week_key) for an ISO week relative to *today*.

    ``week_offset=0`` is the current week, ``1`` is next week, ``-1`` last
    week. The returned ``week_key`` is computed from the target monday's ISO
    calendar (not today's) so year/week numbers stay correct across rollovers.
    """
    today = today or date.today()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    sunday = monday + timedelta(days=6)
    iso = monday.isocalendar()
    week_key = f"{iso[0]}-W{iso[1]:02d}"
    return monday, sunday, week_key


def current_week_bounds(today: date | None = None) -> tuple[date, date, str]:
    """Return (monday, sunday, week_key) for the ISO week containing *today*."""
    return week_bounds(today, week_offset=0)


def next_week_bounds(today: date | None = None) -> tuple[date, date, str]:
    """Return (monday, sunday, week_key) for the ISO week after *today*'s."""
    return week_bounds(today, week_offset=1)


def collect_week_events(
    events: dict[str, CalendarEvent],
    today: date | None = None,
    *,
    week_offset: int = 0,
) -> list[CalendarEvent]:
    """Filter *events* to those in the requested ISO week (default: current)."""
    monday, sunday, _ = week_bounds(today, week_offset=week_offset)
    out: list[CalendarEvent] = []
    for evt in events.values():
        try:
            evt_date = date.fromisoformat(evt.date)
        except ValueError:
            continue
        if monday <= evt_date <= sunday:
            out.append(evt)
    out.sort(key=lambda e: (e.date, e.server_hour, e.server_minute))
    return out


def _short_raid_name(raid: str, title: str) -> str:
    # Imported lazily to avoid any circular-import risk at module load.
    from fgc_sync.services.discord_poster import _short_raid_name as short

    return short(raid) if raid else (title[:12] or "Event")


def _max_roster_size(raid: str) -> int:
    from fgc_sync.services.discord_poster import max_roster_size

    return max_roster_size(raid)


#: Bumped whenever the *rendering* changes in a way that must reach already
#: posted images even though the underlying event data is identical. The
#: per-message skip compares this hash, so without a bump a purely visual fix
#: never ships — the remote image stays stale forever. Same lever as
#: `_EVENT_FEATURE_VERSION` for Google event bodies.
#:   1 = uniform 2.5h cells
#:   2 = per-event duration; cards grown to fit their label, real end shown by
#:       the colour boundary (see _MIN_CARD_MINUTES / _EVENT_TAIL_BLEND)
_WEEKLY_RENDER_VERSION = 2


def compute_weekly_hash(events: list[CalendarEvent]) -> str:
    """Short hash over everything the image displays."""
    payload_parts = [f"render={_WEEKLY_RENDER_VERSION}"]
    for evt in sorted(events, key=lambda e: (e.date, e.server_hour, e.server_minute)):
        payload_parts.append(
            "|".join(
                [
                    evt.event_id,
                    evt.date,
                    f"{evt.server_hour:02d}:{evt.server_minute:02d}",
                    _short_raid_name(evt.raid, evt.title),
                    evt.creator or "",
                    str(evt.confirmed_count),
                    str(evt.signed_count),
                    str(_max_roster_size(evt.raid)),
                    str(_duration_minutes(evt)),
                ]
            )
        )
    payload = "\n".join(payload_parts).encode()
    return hashlib.sha256(payload).hexdigest()[:8]


#: Hash produced by :func:`compute_weekly_hash` for an empty event list. A
#: rendered week carrying this hash is blank — used to refuse overwriting a
#: populated remote overview with an empty local render (see sync_engine).
EMPTY_WEEK_HASH = compute_weekly_hash([])


def _determine_hour_range(events: list[CalendarEvent]) -> tuple[int, int]:
    if not events:
        return _DEFAULT_START_HOUR, _DEFAULT_END_HOUR
    start = min(evt.server_hour for evt in events)
    latest_end_minutes = max(
        evt.server_hour * 60 + evt.server_minute + _card_minutes(evt) for evt in events
    )
    # +1 so there's always a trailing labeled row past the latest event end,
    # anchoring the event's end time to a visible hour label.
    end_hour = (latest_end_minutes + 59) // 60 + 1
    # Keep at least a 4-hour visible range for readability.
    start = max(0, min(start, _DEFAULT_START_HOUR))
    end_hour = max(end_hour, start + 4)
    end_hour = min(end_hour, 27)  # cap at 03:00 next day
    return int(start), int(end_hour)


def _time_label(hour: int, minute: int) -> str:
    """HH:MM for an absolute wall-time point (hour may be >= 24)."""
    return f"{hour % 24:02d}:{minute:02d}"


def _end_time(evt: CalendarEvent) -> tuple[int, int]:
    """Return (end_hour, end_minute) for *evt* using its effective duration."""
    total_minutes = evt.server_hour * 60 + evt.server_minute + _duration_minutes(evt)
    return total_minutes // 60, total_minutes % 60


def render_weekly_overview(
    events: list[CalendarEvent],
    monday: date,
) -> bytes:
    """Render the week as a PNG and return the bytes."""
    sunday = monday + timedelta(days=6)
    font_title = _load_font(18 * _SCALE, bold=True)
    font_header = _load_font(12 * _SCALE, bold=True)
    font_body_bold = _load_font(11 * _SCALE, bold=True)
    font_small = _load_font(10 * _SCALE)

    start_hour, end_hour = _determine_hour_range(events)
    hour_span = end_hour - start_hour

    grid_width = _TIME_COL_WIDTH + 7 * _DAY_COL_WIDTH
    canvas_width = _PADDING * 2 + grid_width
    grid_height = _DAY_HEADER_HEIGHT + hour_span * _HOUR_HEIGHT
    canvas_height = _PADDING * 2 + _HEADER_HEIGHT + grid_height + 20 * _SCALE

    img = Image.new("RGB", (canvas_width, canvas_height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # -- Header --
    draw.rectangle(
        [(0, 0), (canvas_width, _HEADER_HEIGHT)],
        fill=HEADER_BG,
    )
    iso = monday.isocalendar()
    title = t("weekly.image_title", week=iso[1], year=iso[0])
    draw.text((_PADDING, 10 * _SCALE), title, fill=ACCENT_COLOR, font=font_title)

    date_range = (
        f"{monday.day:02d}.{monday.month:02d}.{monday.year} – "
        f"{sunday.day:02d}.{sunday.month:02d}.{sunday.year}"
    )
    draw.text((_PADDING, 40 * _SCALE), date_range, fill=TEXT_COLOR, font=font_header)

    subtitle = t("weekly.image_subtitle", count=len(events))
    draw.text((_PADDING, 68 * _SCALE), subtitle, fill=SUBTEXT_COLOR, font=font_small)

    grid_origin_x = _PADDING
    grid_origin_y = _PADDING + _HEADER_HEIGHT

    # -- Day column headers --
    weekdays = tl("discord.weekday_abbrev")
    for col in range(7):
        day = monday + timedelta(days=col)
        x = grid_origin_x + _TIME_COL_WIDTH + col * _DAY_COL_WIDTH
        draw.rectangle(
            [
                (x, grid_origin_y),
                (x + _DAY_COL_WIDTH, grid_origin_y + _DAY_HEADER_HEIGHT),
            ],
            fill=HEADER_BG,
            outline=BORDER_COLOR,
        )
        weekday = weekdays[col] if len(weekdays) == 7 else day.strftime("%a")
        date_str = f"{day.day:02d}.{day.month:02d}."
        draw.text(
            (x + 10 * _SCALE, grid_origin_y + 6 * _SCALE),
            weekday,
            fill=TEXT_COLOR,
            font=font_header,
        )
        draw.text(
            (x + 10 * _SCALE, grid_origin_y + 24 * _SCALE),
            date_str,
            fill=SUBTEXT_COLOR,
            font=font_small,
        )

    # -- Hour rows (time labels + background stripes) --
    body_top = grid_origin_y + _DAY_HEADER_HEIGHT
    for i in range(hour_span):
        row_y = body_top + i * _HOUR_HEIGHT
        stripe = _GRID_FILL if i % 2 == 0 else _GRID_STRIPE
        draw.rectangle(
            [
                (grid_origin_x, row_y),
                (grid_origin_x + grid_width, row_y + _HOUR_HEIGHT),
            ],
            fill=stripe,
        )
        hour = (start_hour + i) % 24
        draw.text(
            (grid_origin_x + 8 * _SCALE, row_y + 6 * _SCALE),
            f"{hour:02d}:00",
            fill=SUBTEXT_COLOR,
            font=font_small,
        )

    # -- Grid lines --
    for col in range(8):
        x = grid_origin_x + _TIME_COL_WIDTH + col * _DAY_COL_WIDTH
        draw.line(
            [(x, grid_origin_y), (x, body_top + hour_span * _HOUR_HEIGHT)],
            fill=BORDER_COLOR,
        )
    # Left edge + outer frame
    draw.line(
        [
            (grid_origin_x, grid_origin_y),
            (grid_origin_x, body_top + hour_span * _HOUR_HEIGHT),
        ],
        fill=BORDER_COLOR,
    )
    for i in range(hour_span + 1):
        y = body_top + i * _HOUR_HEIGHT
        draw.line(
            [(grid_origin_x, y), (grid_origin_x + grid_width, y)],
            fill=BORDER_COLOR,
        )

    # -- Assign lanes per day so parallel raids sit side-by-side --
    lanes_by_event: dict[str, int] = {}
    lane_count_by_day: dict[int, int] = {}
    by_day: dict[int, list[CalendarEvent]] = {}
    for evt in events:
        try:
            evt_date = date.fromisoformat(evt.date)
        except ValueError:
            continue
        col = (evt_date - monday).days
        if 0 <= col <= 6:
            by_day.setdefault(col, []).append(evt)

    for col, day_events in by_day.items():
        day_events.sort(key=lambda e: (e.server_hour, e.server_minute))
        lane_ends: list[int] = []
        for evt in day_events:
            start_m = evt.server_hour * 60 + evt.server_minute
            end_m = start_m + _card_minutes(evt)
            for i, lane_end in enumerate(lane_ends):
                if lane_end <= start_m:
                    lane_ends[i] = end_m
                    lanes_by_event[evt.event_id] = i
                    break
            else:
                lanes_by_event[evt.event_id] = len(lane_ends)
                lane_ends.append(end_m)
        lane_count_by_day[col] = max(len(lane_ends), 1)

    # -- Event cells --
    for evt in events:
        try:
            evt_date = date.fromisoformat(evt.date)
        except ValueError:
            continue
        col = (evt_date - monday).days
        if col < 0 or col > 6:
            continue
        start_minutes = (evt.server_hour - start_hour) * 60 + evt.server_minute

        lane = lanes_by_event.get(evt.event_id, 0)
        lanes = lane_count_by_day.get(col, 1)
        lane_width = _DAY_COL_WIDTH // lanes
        col_left = grid_origin_x + _TIME_COL_WIDTH + col * _DAY_COL_WIDTH
        x0 = col_left + lane * lane_width + 3
        x1 = col_left + (lane + 1) * lane_width - 3
        grid_bottom = body_top + hour_span * _HOUR_HEIGHT - 2
        y0 = body_top + int(start_minutes * _HOUR_HEIGHT / 60) + 2
        # The card is as tall as its label needs; the raid's real end sits
        # somewhere inside it. Everything past that end is faded toward the
        # grid, so the colour boundary still marks when the raid finishes
        # while the text keeps a readable backdrop underneath it.
        card_y1 = min(
            body_top
            + int((start_minutes + _card_minutes(evt)) * _HOUR_HEIGHT / 60)
            - 2,
            grid_bottom,
        )
        duration_y1 = min(
            body_top
            + int((start_minutes + _duration_minutes(evt)) * _HOUR_HEIGHT / 60)
            - 2,
            grid_bottom,
        )

        max_spots = _max_roster_size(evt.raid)
        is_full = evt.confirmed_count >= max_spots
        cell_fill = _EVENT_FILL_FULL if is_full else _EVENT_FILL
        cell_border = _EVENT_BORDER_FULL if is_full else _EVENT_BORDER
        tail_fill = _blend(cell_fill, _GRID_FILL, _EVENT_TAIL_BLEND)

        draw.rectangle([(x0, y0), (x1, card_y1)], fill=tail_fill, outline=cell_border)
        if duration_y1 > y0:
            draw.rectangle(
                [(x0, y0), (x1, duration_y1)], fill=cell_fill, outline=cell_border
            )

        # Pick cell fonts that fit the lane width. 1 lane = full column,
        # 2 lanes ≈ half, 3+ lanes ≈ third — shrink + abbreviate proportionally.
        if lanes >= 3:
            cell_title = _load_font(10 * _SCALE, bold=True)
            cell_text = _load_font(8 * _SCALE)
            title_h = 14 * _SCALE
            line_h = 12 * _SCALE
            leader_max = 8
            label_confirmed = t("weekly.label_confirmed_short")
            label_signed = t("weekly.label_signed_short")
            label_open = t("weekly.label_open_short")
        elif lanes == 2:
            cell_title = _load_font(11 * _SCALE, bold=True)
            cell_text = _load_font(9 * _SCALE)
            title_h = 16 * _SCALE
            line_h = 13 * _SCALE
            leader_max = 12
            label_confirmed = t("weekly.label_confirmed")
            label_signed = t("weekly.label_signed")
            label_open = t("weekly.label_open")
        else:
            cell_title = font_body_bold
            cell_text = font_small
            title_h = 18 * _SCALE
            line_h = 16 * _SCALE
            leader_max = 18
            label_confirmed = t("weekly.label_confirmed")
            label_signed = t("weekly.label_signed")
            label_open = t("weekly.label_open")

        short_name = _short_raid_name(evt.raid, evt.title)
        end_h, end_m = _end_time(evt)
        time_label = (
            f"{_time_label(evt.server_hour, evt.server_minute)}–"
            f"{_time_label(end_h, end_m)}"
        )
        leader = (evt.creator or "—")[:leader_max]
        open_count = max(0, max_spots - evt.confirmed_count)

        text_x = x0 + 6 * _SCALE
        text_y = y0 + 5 * _SCALE
        draw.text((text_x, text_y), short_name, fill=ACCENT_COLOR, font=cell_title)
        text_y += title_h
        draw.text((text_x, text_y), time_label, fill=TEXT_COLOR, font=cell_text)
        text_y += line_h
        draw.text((text_x, text_y), f"RL: {leader}", fill=SUBTEXT_COLOR, font=cell_text)
        text_y += line_h
        draw.text(
            (text_x, text_y),
            f"{label_confirmed}: {evt.confirmed_count}",
            fill=TEXT_COLOR,
            font=cell_text,
        )
        text_y += line_h
        draw.text(
            (text_x, text_y),
            f"{label_signed}: {evt.signed_count}",
            fill=SUBTEXT_COLOR,
            font=cell_text,
        )
        text_y += line_h
        draw.text(
            (text_x, text_y),
            f"{label_open}: {open_count}",
            fill=SUBTEXT_COLOR,
            font=cell_text,
        )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
