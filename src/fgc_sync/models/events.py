"""Domain models for calendar events and participants."""

from __future__ import annotations

from dataclasses import dataclass, field

from fgc_sync.models.enums import Attendance, EventType


@dataclass
class Participant:
    name: str
    attendance: Attendance
    class_code: str
    role_code: str
    comment: str = ""
    group: int = 0
    slot: int = 0
    item_level: float = 0.0


@dataclass
class CalendarEvent:
    event_id: str
    title: str
    event_type: EventType
    raid: str
    date: str  # "YYYY-MM-DD"
    server_hour: int
    server_minute: int
    comment: str
    creator: str
    revision: int
    participants: list[Participant] = field(default_factory=list)

    @property
    def type_label(self) -> str:
        try:
            return EventType(self.event_type).label
        except ValueError:
            return str(self.event_type).capitalize()

    @property
    def confirmed_count(self) -> int:
        return sum(1 for p in self.participants if p.attendance == Attendance.CONFIRMED)

    @property
    def signed_count(self) -> int:
        return sum(1 for p in self.participants if p.attendance == Attendance.SIGNED)

    @property
    def time_str(self) -> str:
        return f"{self.server_hour:02d}:{self.server_minute:02d}"

    def summary_line(self, character_name: str = "") -> str:
        base = f"[{self.type_label}] {self.title}"
        if character_name:
            base += f" ({character_name})"
        return base

    def description_text(self) -> str:
        lines = []
        if self.comment:
            lines.append(self.comment)
            lines.append("")

        lines.append(
            f"Confirmed: {self.confirmed_count} | "
            f"Signed: {self.signed_count} | "
            f"Total: {len(self.participants)}"
        )
        lines.append("")

        for status in [
            Attendance.CONFIRMED,
            Attendance.SIGNED,
            Attendance.BENCHED,
            Attendance.DECLINED,
        ]:
            group = [p for p in self.participants if p.attendance == status]
            if group:
                lines.append(f"--- {status.label} ({len(group)}) ---")
                for p in group:
                    role = p.role_code.capitalize() if p.role_code else ""
                    entry = f"  {p.name} ({p.class_code}, {role})"
                    if p.comment:
                        entry += f" - {p.comment}"
                    lines.append(entry)
                lines.append("")

        return "\n".join(lines)
