# === ANCHOR: LOGIN_RATE_LIMIT_START ===
"""계정(email) 기준 인메모리 로그인 rate-limit.

터널 경유 요청은 원 IP가 에지에 가려질 수 있어 IP 기준은 신뢰할 수 없다 —
계정 기준 연속 실패 잠금이 주 방어선이다. 번들 서버는 단일 프로세스 uvicorn이라
인메모리로 충분하며 DB 스키마를 건드리지 않는다(서버 재시작 시 카운터 소실 수용).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class _Entry:
    fails: int = 0
    locked_until: float = 0.0


@dataclass
class LoginRateLimiter:
    max_fails: int = 5
    lockout_seconds: float = 300.0
    clock: Callable[[], float] = time.monotonic
    _entries: dict[str, _Entry] = field(default_factory=dict)

    @staticmethod
    def _key(email: str) -> str:
        return email.strip().lower()

    def check(self, email: str) -> float | None:
        """잠금 중이면 남은 초(> 0), 허용이면 None."""
        entry = self._entries.get(self._key(email))
        if entry is None:
            return None
        now = self.clock()
        if entry.locked_until > now:
            return entry.locked_until - now
        if entry.locked_until:
            # 잠금 만료 — 카운터째 초기화
            del self._entries[self._key(email)]
        return None

    def record_failure(self, email: str) -> None:
        entry = self._entries.setdefault(self._key(email), _Entry())
        entry.fails += 1
        if entry.fails >= self.max_fails:
            entry.locked_until = self.clock() + self.lockout_seconds

    def record_success(self, email: str) -> None:
        self._entries.pop(self._key(email), None)

    def reset(self) -> None:
        self._entries.clear()


login_rate_limiter = LoginRateLimiter()
# === ANCHOR: LOGIN_RATE_LIMIT_END ===
