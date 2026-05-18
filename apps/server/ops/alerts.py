# === ANCHOR: ALERTS_START ===
"""In-memory operator alerts for MVP-alpha operational health."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

Severity = Literal["warning", "critical"]


@dataclass
# === ANCHOR: ALERTS_OPERATORALERT_START ===
class OperatorAlert:
    code: str
    severity: Severity
    message: str
    created_at: datetime
    last_seen_at: datetime
    count: int = 1
    resolved_at: datetime | None = None

    # === ANCHOR: ALERTS_AS_DICT_START ===
    def as_dict(self) -> dict[str, str | int | None]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "created_at": self.created_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
            "count": self.count,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
# === ANCHOR: ALERTS_OPERATORALERT_END ===
        }
    # === ANCHOR: ALERTS_AS_DICT_END ===


# === ANCHOR: ALERTS_OPERATORALERTSTORE_START ===
class OperatorAlertStore:
    """Small process-local alert buffer until Slice 4 operator WS lands."""

    # === ANCHOR: ALERTS___INIT___START ===
    def __init__(self) -> None:
        self._alerts: dict[str, OperatorAlert] = {}
    # === ANCHOR: ALERTS___INIT___END ===

    # === ANCHOR: ALERTS_RAISE_ALERT_START ===
    def raise_alert(self, code: str, severity: Severity, message: str) -> OperatorAlert:
        now = datetime.now(timezone.utc)
        alert = self._alerts.get(code)
        if alert is None:
            alert = OperatorAlert(
                code=code,
                severity=severity,
                message=message,
                created_at=now,
                last_seen_at=now,
            )
            self._alerts[code] = alert
            return alert

        alert.severity = severity
        alert.message = message
        alert.last_seen_at = now
        alert.count += 1
        alert.resolved_at = None
        return alert
    # === ANCHOR: ALERTS_RAISE_ALERT_END ===

    # === ANCHOR: ALERTS_RESOLVE_START ===
    def resolve(self, code: str) -> None:
        alert = self._alerts.get(code)
        if alert is not None and alert.resolved_at is None:
            alert.resolved_at = datetime.now(timezone.utc)
    # === ANCHOR: ALERTS_RESOLVE_END ===
# === ANCHOR: ALERTS_OPERATORALERTSTORE_END ===

    # === ANCHOR: ALERTS_ACTIVE_START ===
    def active(self) -> list[OperatorAlert]:
        return [alert for alert in self._alerts.values() if alert.resolved_at is None]
    # === ANCHOR: ALERTS_ACTIVE_END ===

    # === ANCHOR: ALERTS_RESET_START ===
    def reset(self) -> None:
        self._alerts.clear()
    # === ANCHOR: ALERTS_RESET_END ===


GEMINI_API_KEY_MISSING = "gemini_api_key_missing"
MEETING_MAX_DURATION_EXCEEDED = "meeting_max_duration_exceeded"
operator_alerts = OperatorAlertStore()


# === ANCHOR: ALERTS_SYNC_GEMINI_CONFIG_ALERT_START ===
def sync_gemini_config_alert(configured: bool) -> None:
    """Raise or resolve the non-secret operator alert for Gemini config health."""
    if configured:
        operator_alerts.resolve(GEMINI_API_KEY_MISSING)
        return

    _ = operator_alerts.raise_alert(
        code=GEMINI_API_KEY_MISSING,
        severity="critical",
        message="Gemini Live disabled: GEMINI_API_KEY is not configured on the server.",
    )
# === ANCHOR: ALERTS_SYNC_GEMINI_CONFIG_ALERT_END ===


# === ANCHOR: ALERTS_RAISE_MEETING_MAX_DURATION_ALERT_START ===
def raise_meeting_max_duration_alert(session_id: str) -> None:
    """Raise a non-secret alert when a meeting is force-ended for cost safety."""
    _ = operator_alerts.raise_alert(
        code=f"{MEETING_MAX_DURATION_EXCEEDED}:{session_id}",
        severity="critical",
        message=(
            "Meeting exceeded YESON_MEETING_MAX_DURATION_HOURS and was "
            f"automatically ended: session={session_id}."
        ),
    )
# === ANCHOR: ALERTS_RAISE_MEETING_MAX_DURATION_ALERT_END ===
# === ANCHOR: ALERTS_END ===
