"""CAF warehouse location parsing and pick classification."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


FULL_LOCATION_PATTERN = re.compile(
    r"^\s*(?P<aisle>\d{1,3})\s*[-/ ]\s*"
    r"(?P<bay>\d{1,3})\s*[-/ ]\s*"
    r"(?P<vertical>[A-Za-z])"
    r"(?:\s*[-/ ]\s*(?P<lateral>\d{1,3}|[A-Za-z]))?\s*$"
)

SHORT_GROUND_LOCATION_PATTERN = re.compile(
    r"^\s*(?P<aisle>\d{1,3})\s*[-/ ]\s*(?P<bay>\d{1,3})\s*$"
)


@dataclass(frozen=True)
class ParsedLocation:
    raw_location: str
    aisle: int | None
    bay: int | None
    vertical: str | None
    lateral: str | None
    pick_zone: str
    exception_reason: str | None

    @property
    def route_key(self) -> tuple[Any, ...]:
        lateral_number = None
        if self.lateral and str(self.lateral).isdigit():
            lateral_number = int(str(self.lateral))

        return (
            self.aisle if self.aisle is not None else 9999,
            self.bay if self.bay is not None else 9999,
            self.vertical or "Z",
            lateral_number if lateral_number is not None else 9999,
            self.lateral or "",
        )


def parse_location(value: Any) -> ParsedLocation:
    """Parse CAF location strings such as 07-09-B-04."""

    if value is None:
        return _exception("", "Missing location")

    raw = str(value).strip()
    if not raw or raw.lower() in {"nan", "none"}:
        return _exception(raw, "Missing location")

    match = FULL_LOCATION_PATTERN.match(raw)
    if not match:
        short_match = SHORT_GROUND_LOCATION_PATTERN.match(raw)
        if short_match:
            aisle = int(short_match.group("aisle"))
            bay = int(short_match.group("bay"))
            if 1 <= aisle <= 10:
                return ParsedLocation(raw, aisle, bay, None, None, "Ground-level", None)
        return _exception(raw, "Unparseable location")

    aisle = int(match.group("aisle"))
    bay = int(match.group("bay"))
    vertical = match.group("vertical").upper()
    lateral = match.group("lateral")
    lateral = lateral.upper() if lateral and not lateral.isdigit() else lateral

    pick_zone = classify_location(aisle=aisle, vertical=vertical)
    if pick_zone == "Exceptions":
        return ParsedLocation(raw, aisle, bay, vertical, lateral, pick_zone, "Unclassifiable vertical level")

    return ParsedLocation(raw, aisle, bay, vertical, lateral, pick_zone, None)


def classify_location(aisle: int | None, vertical: str | None) -> str:
    """Classify parsed location into Ground-level, Height/FLT, or Exceptions."""

    if aisle is None or not vertical:
        return "Exceptions"

    if 1 <= aisle <= 10:
        return "Ground-level"

    vertical = vertical.upper()
    if vertical == "A":
        return "Ground-level"
    if "B" <= vertical <= "Z":
        return "Height/FLT"
    return "Exceptions"


def _exception(raw: str, reason: str) -> ParsedLocation:
    return ParsedLocation(
        raw_location=raw,
        aisle=None,
        bay=None,
        vertical=None,
        lateral=None,
        pick_zone="Exceptions",
        exception_reason=reason,
    )
