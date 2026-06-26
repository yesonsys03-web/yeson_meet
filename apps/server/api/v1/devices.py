# === ANCHOR: DEVICES_START ===
"""Devices router stub. Body implemented in S1-L1 (POST /devices)."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.auth.deps import require_admin, require_operator
from apps.server.auth.device import generate_api_key, hash_api_key
from apps.server.db.models import AppUser, Device
from apps.server.db.session import get_session

router = APIRouter(tags=["devices"], prefix="/devices")


# === ANCHOR: DEVICES_DEVICECREATEIN_START ===
class DeviceCreateIn(BaseModel):
    name: str
# === ANCHOR: DEVICES_DEVICECREATEIN_END ===


# === ANCHOR: DEVICES_DEVICECREATEOUT_START ===
class DeviceCreateOut(BaseModel):
    id: int
    name: str
    api_key: str
# === ANCHOR: DEVICES_DEVICECREATEOUT_END ===


@router.post("", response_model=DeviceCreateOut, status_code=status.HTTP_201_CREATED)
# === ANCHOR: DEVICES_CREATE_DEVICE_START ===
async def create_device(
    body: DeviceCreateIn,
    _admin: Annotated[AppUser, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
# === ANCHOR: DEVICES_CREATE_DEVICE_END ===
) -> DeviceCreateOut:
    plaintext = generate_api_key()
    device = Device(
        name=body.name,
        api_key_hash=hash_api_key(plaintext),
        is_active=True,
    )
    db.add(device)
    await db.flush()
    await db.commit()
    return DeviceCreateOut(id=device.id, name=device.name, api_key=plaintext)


@router.post("/self-enroll", response_model=DeviceCreateOut, status_code=status.HTTP_201_CREATED)
async def self_enroll_device(
    body: DeviceCreateIn,
    _operator: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> DeviceCreateOut:
    # Self-enroll: an operator client provisions ITS OWN single device key.
    # Separate from create_device (require_admin) so the client never gains
    # device-admin (list/revoke). Issuance still happens server-side.
    plaintext = generate_api_key()
    device = Device(
        name=body.name,
        api_key_hash=hash_api_key(plaintext),
        is_active=True,
    )
    db.add(device)
    await db.flush()
    await db.commit()
    return DeviceCreateOut(id=device.id, name=device.name, api_key=plaintext)


# === ANCHOR: DEVICES_DEVICEOUT_START ===
class DeviceOut(BaseModel):
    id: int
    name: str
    is_active: bool
    created_at: datetime
# === ANCHOR: DEVICES_DEVICEOUT_END ===


@router.get("", response_model=list[DeviceOut])
# === ANCHOR: DEVICES_LIST_DEVICES_START ===
async def list_devices(
    _admin: Annotated[AppUser, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
# === ANCHOR: DEVICES_LIST_DEVICES_END ===
) -> list[DeviceOut]:
    rows = (
        await db.execute(select(Device).order_by(Device.id))
    ).scalars().all()
    # Serializer omits api_key_hash by construction (never exposed via any GET).
    return [
        DeviceOut(
            id=d.id, name=d.name, is_active=d.is_active, created_at=d.created_at
        )
        for d in rows
    ]


@router.post("/{device_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
# === ANCHOR: DEVICES_REVOKE_DEVICE_START ===
async def revoke_device(
    device_id: int,
    _admin: Annotated[AppUser, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
# === ANCHOR: DEVICES_REVOKE_DEVICE_END ===
) -> None:
    device = await db.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    # Connect-time-only revocation (PM-2 bound): takes effect at the next
    # device_from_key / /ws/sidecar connect; live sockets are not force-closed.
    device.is_active = False
    await db.commit()
# === ANCHOR: DEVICES_END ===
