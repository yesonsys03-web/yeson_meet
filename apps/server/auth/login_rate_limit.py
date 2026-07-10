# === ANCHOR: LOGIN_RATE_LIMIT_START ===
"""계정(email) 기준 인메모리 로그인 rate-limit.

터널 경유 요청은 원 IP가 에지에 가려질 수 있어 IP 기준은 신뢰할 수 없다 —
계정 기준 연속 실패 잠금이 주 방어선이다. 번들 서버는 단일 프로세스 uvicorn이라
인메모리로 충분하며 DB 스키마를 건드리지 않는다(서버 재시작 시 카운터 소실 수용).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


def _mask_email(email: str) -> str:
    """관측 로그용 이메일 마스킹 — 로컬파트 앞 3자 + 도메인만 노출."""
    local, sep, domain = email.partition("@")
    if not sep:
        return "***"
    return f"{local[:3]}***@{domain}"


@dataclass
class _Entry:
    fails: int = 0
    locked_until: float = 0.0


@dataclass
class LoginRateLimiter:
    max_fails: int = 5
    lockout_seconds: float = 300.0
    max_entries: int = 10_000
    clock: Callable[[], float] = time.monotonic
    _entries: dict[str, _Entry] = field(default_factory=dict, init=False, repr=False)

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
        key = self._key(email)
        entry = self._entries.get(key)
        if entry is None:
            self._evict_if_full()
            entry = self._entries.setdefault(key, _Entry())
        entry.fails += 1
        if entry.fails >= self.max_fails and entry.locked_until == 0.0:
            entry.locked_until = self.clock() + self.lockout_seconds
            logger.warning(
                "Login rate limiter locked account after repeated failures: %s",
                _mask_email(email),
            )

    def record_success(self, email: str) -> None:
        self._entries.pop(self._key(email), None)

    def reset(self) -> None:
        self._entries.clear()

    def _evict_if_full(self) -> None:
        """터널 노출 로그인에 대한 무한 메모리 성장 차단.

        엔트리 수가 상한에 도달하면 잠기지 않은 엔트리 하나를 제거해 공간을
        확보한다. 잠긴(locked_until != 0.0) 엔트리는 활성 방어선이므로 제거하지
        않는다 — 모든 엔트리가 잠긴 극단적 상황에서는 상한을 일시적으로 초과
        허용한다(공격 완화가 메모리 상한보다 우선).
        """
        if len(self._entries) < self.max_entries:
            return
        for key, entry in self._entries.items():
            if entry.locked_until == 0.0:
                del self._entries[key]
                return


login_rate_limiter = LoginRateLimiter()
# === ANCHOR: LOGIN_RATE_LIMIT_END ===
