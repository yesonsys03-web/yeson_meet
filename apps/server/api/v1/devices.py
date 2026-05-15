"""Devices router stub. Body implemented in S1-L1 (POST /devices)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.auth.deps import require_admin
from apps.server.auth.device import generate_api_key, hash_api_key
from apps.server.db.models import AppUser, Device
from apps.server.db.session import get_session

router = APIRouter(tags=["devices"], prefix="/devices")


class DeviceCreateIn(BaseModel):
    name: str


class DeviceCreateOut(BaseModel):
    id: int
    name: str
    api_key: str


@router.post("", response_model=DeviceCreateOut, status_code=status.HTTP_201_CREATED)
async def create_device(
    body: DeviceCreateIn,
    _admin: Annotated[AppUser, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
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
