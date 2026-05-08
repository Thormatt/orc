"""Injectable wall clock. Tests can monkeypatch `now_iso` for determinism."""

from __future__ import annotations

from datetime import UTC, datetime


def now_iso() -> str:
    """ISO 8601 UTC with millisecond precision and a `Z` suffix."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
