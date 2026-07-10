"""/ws/capture 첫 메시지 인증 계약 테스트 (test_ws_flow.py의 sync TestClient 패턴)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from starlette.testclient import TestClient

from apps.server.auth.capture_tokens import capture_tokens
from apps.server.auth.password import hash_password
from apps.server.main import app

_SYNC_DSN = (
    "postgresql://yeson:6fad32ad29a12088da075219fdeb809d"  # vibelign: allow-secret — 기존 테스트 파일과 동일한 로컬 테스트 DB
    "@127.0.0.1:5432/yeson_meet_test"
)


@pytest.fixture(autouse=True)
def _reset_capture_tokens():
    capture_tokens.reset()
    yield
    capture_tokens.reset()


def _sync_insert_admin() -> tuple[int, str]:
    email = "capture-admin@test.example"
    pw_hash = hash_password("capture-admin-pw")
    with psycopg.connect(_SYNC_DSN) as conn:
        row = conn.execute(
            """
            INSERT INTO app_user (email, name, password_hash, role, is_active)
            VALUES (%s, %s, %s, 'admin', true)
            ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash
            RETURNING id
            """,
            [email, "Capture Admin", pw_hash],
        ).fetchone()
        conn.commit()
    return row[0], email


def _sync_count_utterances_by_external(external_id: str) -> int:
    with psycopg.connect(_SYNC_DSN) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM utterance u
            JOIN session s ON s.id = u.session_id
            WHERE s.external_id = %s::uuid
            """,
            [external_id],
        ).fetchone()
    return row[0]


def _make_frame(session_id: str, seq: int) -> str:
    now = datetime.now(timezone.utc).isoformat()
    return json.dumps(
        {
            "type": "utterance.transcribed",
            "session_id": session_id,
            "occurred_at": now,
            "seq": seq,
            "text_en": f"Hello seq {seq}",
            "text_ko": f"안녕 seq {seq}",
            "started_at": now,
            "ended_at": now,
            "is_final": True,
        }
    )


def _login_and_create_session(tc: TestClient) -> tuple[dict[str, str], str]:
    _admin_id, email = _sync_insert_admin()
    login = tc.post(
        "/api/v1/auth/login", json={"email": email, "password": "capture-admin-pw"}
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    created = tc.post("/api/v1/sessions", json={"title": "capture-ws-test"}, headers=headers)
    assert created.status_code == 201
    return headers, created.json()["session_id"]


def _auth_msg(token: str, session_id: str) -> str:
    return json.dumps({"type": "auth", "token": token, "session": session_id})


def test_capture_ws_rejects_bad_token() -> None:
    with TestClient(app, raise_server_exceptions=True) as tc:
        _headers, sid = _login_and_create_session(tc)
        with pytest.raises(Exception):  # 서버가 close 1008 → 클라이언트 측 예외
            with tc.websocket_connect("/ws/capture") as ws:
                ws.send_text(_auth_msg("wrong-token", sid))
                ws.receive_text()  # auth.ok 대신 close → 예외


def test_capture_ws_rejects_non_auth_first_message() -> None:
    with TestClient(app, raise_server_exceptions=True) as tc:
        _headers, sid = _login_and_create_session(tc)
        token, _ = capture_tokens.issue(UUID(sid))
        assert token
        with pytest.raises(Exception):
            with tc.websocket_connect("/ws/capture") as ws:
                ws.send_text(json.dumps({"type": "audio.started", "sample_rate": 16000}))
                ws.receive_text()


def test_capture_ws_rejects_ended_session() -> None:
    with TestClient(app, raise_server_exceptions=True) as tc:
        headers, sid = _login_and_create_session(tc)
        token, _ = capture_tokens.issue(UUID(sid))
        tc.post(f"/api/v1/sessions/{sid}/end", headers=headers)
        # end가 capture_tokens.revoke_session을 호출하므로 validate부터 실패한다
        with pytest.raises(Exception):
            with tc.websocket_connect("/ws/capture") as ws:
                ws.send_text(_auth_msg(token, sid))
                ws.receive_text()


def test_capture_ws_happy_path_streams_utterance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    with TestClient(app, raise_server_exceptions=True) as tc:
        headers, sid = _login_and_create_session(tc)
        token, _ = capture_tokens.issue(UUID(sid))
        # test_ws_flow.py와 동일한 대기 방식: 뷰어 WS로 fan-out을 받아야 프레임이
        # 실제로 DB에 반영된 뒤임이 보장된다(구독은 발행 전에 먼저 이뤄져야 함).
        viewer_url = tc.get(f"/api/v1/sessions/{sid}/viewer-url", headers=headers).json()[
            "viewer_url"
        ]
        viewer_token = viewer_url.rsplit("/", 1)[-1]  # vibelign: allow-secret — 토큰 파싱, 하드코딩 비밀 아님
        with tc.websocket_connect(f"/ws/viewer?token={viewer_token}") as viewer_ws:
            with tc.websocket_connect("/ws/capture") as ws:
                ws.send_text(_auth_msg(token, sid))
                ack = json.loads(ws.receive_text())
                assert ack == {"type": "auth.ok"}
                # 기존 사이드카 계약 그대로: S1 fixture 텍스트 프레임이 DB에 도달
                ws.send_text(_make_frame(sid, 1))
                ws.send_text(_make_frame(sid, 2))
                received = [viewer_ws.receive_json(), viewer_ws.receive_json()]
        assert sorted(m["seq"] for m in received) == [1, 2]
        assert _sync_count_utterances_by_external(sid) == 2
