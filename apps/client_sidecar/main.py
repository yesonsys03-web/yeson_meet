"""Sidecar entrypoint.

S0 부트스트랩: 콘솔에 startup 한 줄만 출력하고 종료. 실제 오디오 캡처/WS 연결은
Slice 1+ (transport), Slice 2+ (audio) 에서 채워짐.
"""
from __future__ import annotations

from apps.client_sidecar.config.constants import (
    SIDECAR_LOCAL_WS_HOST,
    SIDECAR_LOCAL_WS_PORT,
)


def run() -> None:
    print(
        f"sidecar started (dev mode, localhost ws will bind on "
        f"{SIDECAR_LOCAL_WS_HOST}:{SIDECAR_LOCAL_WS_PORT} in Slice 1+)"
    )


if __name__ == "__main__":
    run()
