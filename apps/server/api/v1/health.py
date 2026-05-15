# === ANCHOR: HEALTH_START ===
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
# === ANCHOR: HEALTH_HEALTH_START ===
async def health() -> dict[str, str]:
    return {"status": "ok"}
# === ANCHOR: HEALTH_HEALTH_END ===
# === ANCHOR: HEALTH_END ===
