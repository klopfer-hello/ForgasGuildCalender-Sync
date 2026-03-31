"""Domain enumerations."""

from enum import IntEnum, StrEnum


class Attendance(IntEnum):
    SIGNED = 1
    CONFIRMED = 2
    DECLINED = 3
    BENCHED = 4

    @property
    def label(self) -> str:
        return self.name.capitalize()

    @classmethod
    def is_active(cls, value: int) -> bool:
        return value in (cls.SIGNED, cls.CONFIRMED)


class EventType(StrEnum):
    RAID = "raid"
    DUNGEON = "dungeon"
    PVP = "pvp"
    MEETING = "meeting"

    @property
    def label(self) -> str:
        return self.name.capitalize()


class SyncAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
