# === ANCHOR: CAPTURE_TOKENS_START ===
"""세션 스코프 캡처 토큰(웹 캡처 /ws/capture 인증용) 인메모리 저장소.

영구 디바이스키를 터널에 노출하지 않기 위한 대체물: 회의(세션)마다 발급되고
세션 종료 시 즉시 폐기 + 안전상한 TTL. 평문은 응답으로만 나가고 서버에는
sha256 해시만 남는다. DB 무변경(단일 프로세스 인메모리); 서버 재시작 시
소실은 수용 — 회의도 끊기므로 클라가 JWT로 재발급받는다.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import UUID

DEFAULT_TTL = timedelta(hours=12)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _Entry:
    token_hash: str
    expires_at: datetime


@dataclass
class CaptureTokenStore:
    ttl: timedelta = DEFAULT_TTL
    now: Callable[[], datetime] = _utcnow
    _by_session: dict[UUID, _Entry] = field(default_factory=dict)

    def issue(self, session_uuid: UUID) -> tuple[str, datetime]:
        token = secrets.token_urlsafe(32)  # vibelign: allow-secret
        expires_at = self.now() + self.ttl
        self._by_session[session_uuid] = _Entry(_hash(token), expires_at)
        return token, expires_at

    def validate(self, token: str, session_uuid: UUID) -> bool:
        entry = self._by_session.get(session_uuid)
        if entry is None:
            return False
        if entry.expires_at <= self.now():
            del self._by_session[session_uuid]
            return False
        return secrets.compare_digest(entry.token_hash, _hash(token))

    def revoke_session(self, session_uuid: UUID) -> None:
        self._by_session.pop(session_uuid, None)

    def reset(self) -> None:
        self._by_session.clear()


capture_tokens = CaptureTokenStore()
# === ANCHOR: CAPTURE_TOKENS_END ===
