"""SessionEndOut must serialize ended_at as timezone-aware ISO (mirrors
SessionListItem). A naive value would be read by the client's ``new Date(iso)``
as LOCAL time, displaying the meeting end off by the UTC offset."""
from datetime import datetime, timezone
from uuid import uuid4

from apps.server.api.v1.sessions import SessionEndOut


def test_naive_ended_at_serializes_as_utc_aware():
    out = SessionEndOut(
        session_id=uuid4(),
        status="ended",
        ended_at=datetime(2026, 6, 25, 5, 58, 37, 52894),  # naive (DB convention)
        report_path="/x/report.md",
    )
    assert out.model_dump(mode="json")["ended_at"].endswith("+00:00")


def test_aware_ended_at_passes_through_with_offset():
    out = SessionEndOut(
        session_id=uuid4(),
        status="ended",
        ended_at=datetime(2026, 6, 25, 5, 58, 37, tzinfo=timezone.utc),
        report_path="/x/report.md",
    )
    assert out.model_dump(mode="json")["ended_at"].endswith("+00:00")
