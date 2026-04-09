"""Render a raid roster card image for Discord embeds."""

from __future__ import annotations

import io
from collections import defaultdict
from importlib import resources

from PIL import Image, ImageDraw, ImageFont

from fgc_sync.models.enums import Attendance
from fgc_sync.models.events import CalendarEvent, Participant

# WoW class colors (official)
CLASS_COLORS = {
    "WARRIOR": (199, 156, 110),
    "PALADIN": (245, 140, 186),
    "HUNTER": (171, 212, 115),
    "ROGUE": (255, 245, 105),
    "PRIEST": (255, 255, 255),
    "SHAMAN": (0, 112, 222),
    "MAGE": (64, 199, 235),
    "WARLOCK": (148, 130, 201),
    "DRUID": (255, 125, 10),
    "DEATHKNIGHT": (196, 31, 59),
    "MONK": (0, 255, 150),
    "DEMONHUNTER": (163, 48, 201),
    "EVOKER": (51, 147, 127),
}

# Role display labels
ROLE_LABELS = {"TANK": "Tank", "HEALER": "Healer", "DAMAGER": "DD"}

# Palette
BG_COLOR = (32, 32, 40)
HEADER_BG = (44, 44, 56)
GROUP_BG = (38, 38, 50)
GROUP_HEADER_BG = (50, 50, 65)
BORDER_COLOR = (60, 60, 80)
TEXT_COLOR = (220, 220, 220)
SUBTEXT_COLOR = (160, 160, 170)
ACCENT_COLOR = (255, 183, 77)  # warm gold

# Scale factor for high-res output
SCALE = 2

# Layout constants (base values, multiplied by SCALE at render time)
CARD_WIDTH = 780 * SCALE
PADDING = 16 * SCALE
GROUP_COL_WIDTH = 186 * SCALE
GROUP_COLS = 4
ROW_HEIGHT = 22 * SCALE
GROUP_HEADER_HEIGHT = 26 * SCALE
ICON_SIZE = 16 * SCALE

# Class icon package resource
_CLASS_ICONS_PACKAGE = "fgc_sync.resources.class_icons"
_icon_cache: dict[str, Image.Image] = {}
_role_icon_cache: dict[str, Image.Image] = {}


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    import os

    candidates = []
    if os.name == "nt":
        candidates = [
            "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        ]
    else:
        if bold:
            candidates = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
            ]
        else:
            candidates = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/TTF/DejaVuSans.ttf",
                "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
            ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _get_class_icon(class_code: str) -> Image.Image | None:
    """Load and cache a class icon PNG."""
    if class_code in _icon_cache:
        return _icon_cache[class_code]
    try:
        ref = resources.files(_CLASS_ICONS_PACKAGE).joinpath(f"{class_code}.png")
        with resources.as_file(ref) as icon_path:
            icon = Image.open(icon_path).convert("RGBA")
            if icon.size != (ICON_SIZE, ICON_SIZE):
                icon = icon.resize((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)
            _icon_cache[class_code] = icon
            return icon
    except (FileNotFoundError, ModuleNotFoundError, TypeError):
        _icon_cache[class_code] = None
        return None


def _paste_icon(img: Image.Image, icon: Image.Image, x: int, y: int):
    """Paste an RGBA icon onto an RGB image with alpha compositing."""
    region = img.crop((x, y, x + icon.width, y + icon.height)).convert("RGBA")
    composited = Image.alpha_composite(region, icon)
    img.paste(composited.convert("RGB"), (x, y))


def _get_role_icon(role_code: str) -> Image.Image:
    """Render a small role icon: shield (tank), cross (healer), sword (dps)."""
    if role_code in _role_icon_cache:
        return _role_icon_cache[role_code]

    size = ICON_SIZE
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    cx, cy = size // 2, size // 2

    if role_code == "TANK":
        # Shield shape
        pts = [
            (cx, 2),
            (size - 2, 4),
            (size - 3, cy + 2),
            (cx, size - 2),
            (3, cy + 2),
            (2, 4),
        ]
        draw.polygon(pts, fill=(80, 130, 220, 230), outline=(140, 180, 255, 255))
    elif role_code == "HEALER":
        # Plus/cross
        t = 3  # thickness
        draw.rectangle([(cx - t, 2), (cx + t, size - 3)], fill=(80, 200, 80, 230))
        draw.rectangle([(2, cy - t), (size - 3, cy + t)], fill=(80, 200, 80, 230))
    else:
        # Schematic sword pointing up — blade, guard, grip
        s = size
        blade_color = (200, 80, 80, 240)
        guard_color = (200, 80, 80, 200)
        grip_color = (160, 60, 60, 200)
        # Blade (vertical line, top to center)
        bw = max(s // 8, 1)  # blade half-width
        draw.polygon(
            [
                (cx - bw, s * 2 // 10),  # left top of blade
                (cx, s * 1 // 10),  # tip
                (cx + bw, s * 2 // 10),  # right top of blade
                (cx + bw, s * 6 // 10),  # right bottom of blade
                (cx - bw, s * 6 // 10),  # left bottom of blade
            ],
            fill=blade_color,
        )
        # Guard (horizontal bar)
        gw = s * 3 // 10  # guard half-width
        gh = max(s // 10, 1)
        gy = s * 6 // 10
        draw.rectangle([(cx - gw, gy), (cx + gw, gy + gh)], fill=guard_color)
        # Grip
        draw.rectangle([(cx - bw, gy + gh), (cx + bw, s * 85 // 100)], fill=grip_color)
        # Pommel
        pr = max(s // 8, 1)
        draw.ellipse(
            [(cx - pr, s * 83 // 100), (cx + pr, s * 95 // 100)], fill=grip_color
        )

    _role_icon_cache[role_code] = icon
    return icon


def _draw_participant(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    p: Participant,
    x: int,
    y: int,
    font: ImageFont.FreeTypeFont,
):
    """Draw a single participant: role icon + class icon + name."""
    role_icon = _get_role_icon(p.role_code)
    _paste_icon(img, role_icon, x + 3 * SCALE, y + 3 * SCALE)
    class_icon = _get_class_icon(p.class_code)
    if class_icon:
        _paste_icon(img, class_icon, x + 21 * SCALE, y + 3 * SCALE)
    color = CLASS_COLORS.get(p.class_code, TEXT_COLOR)
    draw.text((x + 40 * SCALE, y + 3 * SCALE), p.name, fill=color, font=font)


def render_roster(event: CalendarEvent, timezone: str) -> bytes:
    """Render a roster card and return PNG bytes."""
    font = _load_font(13 * SCALE)
    font_title = _load_font(16 * SCALE, bold=True)
    font_header = _load_font(11 * SCALE, bold=True)
    font_small = _load_font(11 * SCALE)

    confirmed = [p for p in event.participants if p.attendance == Attendance.CONFIRMED]
    signed = [p for p in event.participants if p.attendance == Attendance.SIGNED]
    benched = [p for p in event.participants if p.attendance == Attendance.BENCHED]

    # Build groups from confirmed participants
    groups: dict[int, list[Participant]] = defaultdict(list)
    ungrouped_confirmed: list[Participant] = []
    for p in confirmed:
        if p.group > 0:
            groups[p.group].append(p)
        else:
            ungrouped_confirmed.append(p)

    # Sort within groups by slot
    for g in groups:
        groups[g].sort(key=lambda p: p.slot)

    max_group = max(groups.keys()) if groups else 0
    group_row_count = (max_group + GROUP_COLS - 1) // GROUP_COLS if max_group > 0 else 0

    # Calculate height
    y = 0
    y += 70 * SCALE  # header block
    if group_row_count > 0:
        for row_idx in range(group_row_count):
            start_g = row_idx * GROUP_COLS + 1
            end_g = min(start_g + GROUP_COLS, max_group + 1)
            max_slots = max(len(groups.get(g, [])) for g in range(start_g, end_g))
            max_slots = max(max_slots, 1)
            y += GROUP_HEADER_HEIGHT + max_slots * ROW_HEIGHT + 8
    if ungrouped_confirmed:
        rows = (len(ungrouped_confirmed) + GROUP_COLS - 1) // GROUP_COLS
        y += GROUP_HEADER_HEIGHT + rows * ROW_HEIGHT + 8
    if signed:
        rows = (len(signed) + GROUP_COLS - 1) // GROUP_COLS
        y += GROUP_HEADER_HEIGHT + rows * ROW_HEIGHT + 8
    if benched:
        rows = (len(benched) + GROUP_COLS - 1) // GROUP_COLS
        y += GROUP_HEADER_HEIGHT + rows * ROW_HEIGHT + 8
    y += 50 * SCALE  # footer
    total_height = y + PADDING

    img = Image.new("RGB", (CARD_WIDTH, total_height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    y = 0

    # -- Header --
    draw.rectangle([(0, 0), (CARD_WIDTH, 68 * SCALE)], fill=HEADER_BG)

    try:
        from datetime import date as date_cls

        dt = date_cls.fromisoformat(event.date)
        day_names = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        day_name = day_names[dt.weekday()]
        date_str = f"{day_name}, {event.date}    {event.time_str}"
    except ValueError:
        date_str = f"{event.date}  {event.time_str}"

    title_line = f"{date_str}    {event.title}"
    draw.text((PADDING, 10 * SCALE), title_line, fill=ACCENT_COLOR, font=font_title)

    location = event.raid.replace("_", " ").title() if event.raid else ""
    if location:
        draw.text((PADDING, 30 * SCALE), location, fill=SUBTEXT_COLOR, font=font)

    stats = (
        f"Confirmed: {len(confirmed)}    "
        f"Signed: {len(signed)}    "
        f"Bench: {len(benched)}    "
        f"Planned: {len(confirmed) + len(benched)}"
    )
    draw.text((PADDING, 48 * SCALE), stats, fill=SUBTEXT_COLOR, font=font_small)

    y = 70 * SCALE

    # -- Group grids --
    if group_row_count > 0:
        for row_idx in range(group_row_count):
            start_g = row_idx * GROUP_COLS + 1
            end_g = min(start_g + GROUP_COLS, max_group + 1)
            max_slots = 0
            for g in range(start_g, end_g):
                max_slots = max(max_slots, len(groups.get(g, [])))
            max_slots = max(max_slots, 1)

            for col, g in enumerate(range(start_g, end_g)):
                x = PADDING + col * GROUP_COL_WIDTH
                draw.rectangle(
                    [(x, y), (x + GROUP_COL_WIDTH - 4, y + GROUP_HEADER_HEIGHT - 2)],
                    fill=GROUP_HEADER_BG,
                )
                draw.text(
                    (x + 6 * SCALE, y + 5 * SCALE),
                    f"Group {g}",
                    fill=TEXT_COLOR,
                    font=font_header,
                )

                for si, p in enumerate(groups.get(g, [])):
                    sy = y + GROUP_HEADER_HEIGHT + si * ROW_HEIGHT
                    if si % 2 == 0:
                        draw.rectangle(
                            [
                                (x, sy),
                                (x + GROUP_COL_WIDTH - 4 * SCALE, sy + ROW_HEIGHT - 1),
                            ],
                            fill=GROUP_BG,
                        )
                    _draw_participant(img, draw, p, x, sy, font)

            y += GROUP_HEADER_HEIGHT + max_slots * ROW_HEIGHT + 8 * SCALE

    # -- Ungrouped confirmed --
    if ungrouped_confirmed:
        y = _draw_section(
            img,
            draw,
            ungrouped_confirmed,
            "Confirmed (unassigned)",
            y,
            font,
            font_header,
        )

    # -- Signed --
    if signed:
        y = _draw_section(
            img, draw, signed, f"Signed ({len(signed)})", y, font, font_header
        )

    # -- Benched --
    if benched:
        y = _draw_section(
            img, draw, benched, f"Bench ({len(benched)})", y, font, font_header
        )

    # -- Footer: role & class counts --
    draw.line([(PADDING, y), (CARD_WIDTH - PADDING, y)], fill=BORDER_COLOR)
    y += 6 * SCALE

    role_counts: dict[str, int] = defaultdict(int)
    class_counts: dict[str, int] = defaultdict(int)
    for p in confirmed:
        role_counts[p.role_code] += 1
        class_counts[p.class_code] += 1

    role_text_parts = []
    for role in ("TANK", "HEALER", "DAMAGER"):
        if role_counts.get(role, 0) > 0:
            role_text_parts.append(
                f"{ROLE_LABELS.get(role, role)}s ({role_counts[role]})"
            )
    role_text = "    ".join(role_text_parts)
    draw.text((PADDING, y), role_text, fill=SUBTEXT_COLOR, font=font_small)
    y += 18 * SCALE

    # Class counts with icons
    x = PADDING
    for cls in sorted(class_counts, key=lambda c: -class_counts[c]):
        icon = _get_class_icon(cls)
        if icon:
            _paste_icon(img, icon, x, y)
            x += ICON_SIZE + 3 * SCALE
        label = f"{cls.capitalize()} ({class_counts[cls]})"
        color = CLASS_COLORS.get(cls, TEXT_COLOR)
        draw.text((x, y), label, fill=color, font=font_small)
        bbox = font_small.getbbox(label)
        x += bbox[2] - bbox[0] + 12 * SCALE

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _draw_section(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    participants: list[Participant],
    title: str,
    y: int,
    font: ImageFont.FreeTypeFont,
    font_header: ImageFont.FreeTypeFont,
) -> int:
    """Draw a section of participants in a 4-column layout. Returns new y."""
    draw.rectangle(
        [(PADDING, y), (CARD_WIDTH - PADDING, y + GROUP_HEADER_HEIGHT - 2)],
        fill=GROUP_HEADER_BG,
    )
    draw.text(
        (PADDING + 6 * SCALE, y + 5 * SCALE), title, fill=TEXT_COLOR, font=font_header
    )
    y += GROUP_HEADER_HEIGHT

    cols = GROUP_COLS
    for i, p in enumerate(participants):
        col = i % cols
        row = i // cols
        x = PADDING + col * GROUP_COL_WIDTH
        sy = y + row * ROW_HEIGHT
        if row % 2 == 0:
            draw.rectangle(
                [(x, sy), (x + GROUP_COL_WIDTH - 4 * SCALE, sy + ROW_HEIGHT - 1)],
                fill=GROUP_BG,
            )
        _draw_participant(img, draw, p, x, sy, font)

    total_rows = (len(participants) + cols - 1) // cols
    y += total_rows * ROW_HEIGHT + 8 * SCALE
    return y
