# === ANCHOR: OPERATOR_ALERTS_START ===
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from apps.server.auth.deps import require_operator
from apps.server.db.models import AppUser
from apps.server.ops.alerts import operator_alerts

router = APIRouter(tags=["operator-alerts"], prefix="/operator")


# === ANCHOR: OPERATOR_ALERTS_OPERATORALERTOUT_START ===
class OperatorAlertOut(BaseModel):
    code: str
    severity: str
    message: str
    created_at: str
    last_seen_at: str
    count: int
    resolved_at: str | None
# === ANCHOR: OPERATOR_ALERTS_OPERATORALERTOUT_END ===


@router.get("/alerts", response_model=list[OperatorAlertOut])
# === ANCHOR: OPERATOR_ALERTS_LIST_OPERATOR_ALERTS_START ===
async def list_operator_alerts(
    _operator: Annotated[AppUser, Depends(require_operator)],
# === ANCHOR: OPERATOR_ALERTS_LIST_OPERATOR_ALERTS_END ===
) -> list[OperatorAlertOut]:
    return [OperatorAlertOut(**alert.as_dict()) for alert in operator_alerts.active()]
# === ANCHOR: OPERATOR_ALERTS_END ===
