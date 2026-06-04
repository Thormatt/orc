"""Injectable wall clock. Tests can monkeypatch `now_iso` for determinism."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def now_iso() -> str:
    """ISO 8601 UTC with millisecond precision and a `Z` suffix."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now_plus_seconds_iso(seconds: float) -> str:
    """now_iso() shifted by `seconds` (may be negative). Same format, so results
    sort lexicographically against now_iso()."""
    stamp = datetime.now(UTC) + timedelta(seconds=seconds)
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")
