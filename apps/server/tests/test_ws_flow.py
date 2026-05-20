"""WebSocket flow tests: sidecar publish → viewer receive + REST backfill.

Uses synchronous TestClient (starlette) for WebSocket connections.
Data preparation (admin user, device, session) is done via REST through
a second sync TestClient so everything runs on the same event loop that
TestClient manages internally.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from apps.server.auth.password import hash_password
from apps.server.main import app

# ── psycopg sync connection helper (bypasses async ORM) ──────────────────────
_SYNC_DSN = (
    "postgresql://yeson:6fad32ad29a12088da075219fdeb809d"
    "@127.0.0.1:5432/yeson_meet_test"
)


def _sync_insert_admin() -> tuple[int, str]:
    """Insert an admin user; return (id, email)."""
    email = "ws-admin@test.example"
    pw_hash = hash_password("ws-admin-pw")
    with psycopg.connect(_SYNC_DSN) as conn:
        row = conn.execute(
            """
            INSERT INTO app_user (email, name, password_hash, role, is_active)
            VALUES (%s, %s, %s, 'admin', true)
            RETURNING id
            """,
            [email, "WS Admin", pw_hash],
        ).fetchone()
        conn.commit()
    return row[0], email


def _sync_count_utterances(session_pk: int) -> int:
    with psycopg.connect(_SYNC_DSN) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM utterance WHERE session_id = %s",
            [session_pk],
        ).fetchone()
    return row[0]


def _sync_get_session_pk(external_id: str) -> int:
    with psycopg.connect(_SYNC_DSN) as conn:
        row = conn.execute(
            "SELECT id FROM session WHERE external_id = %s::uuid",
            [external_id],
        ).fetchone()
    return row[0]


def _sync_get_session_status(external_id: str) -> str:
    with psycopg.connect(_SYNC_DSN) as conn:
        row = conn.execute(
            "SELECT status FROM session WHERE external_id = %s::uuid",
            [external_id],
        ).fetchone()
    return row[0]


def _create_session(tc: TestClient, auth_headers: dict[str, str], title: str) -> str:
    response = tc.post(
        "/api/v1/sessions",
        json={"title": title},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


# ── Utterance frame builder ───────────────────────────────────────────────────

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


# ── Test ──────────────────────────────────────────────────────────────────────

def test_ws_sidecar_viewer_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Full end-to-end flow:
    1. Admin creates device + session via REST.
    2. Viewer WS subscribes.
    3. Sidecar WS publishes 3 utterances.
    4. Viewer receives all 3.
    5. Duplicate seq=1 is rejected (DB row count stays 3).
    6. REST backfill /api/v1/viewer/utterances returns 3 items.
    """
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    with TestClient(app, raise_server_exceptions=True) as tc:
        # ── Step A: insert admin user ────────────────────────────────────────
        _admin_id, admin_email = _sync_insert_admin()

        # ── Step B: login → admin token ──────────────────────────────────────
        login_resp = tc.post(
            "/api/v1/auth/login",
            json={"email": admin_email, "password": "ws-admin-pw"},
        )
        assert login_resp.status_code == 200
        admin_token = login_resp.json()["access_token"]
        auth_headers = {"Authorization": f"Bearer {admin_token}"}

        # ── Step C: create device ─────────────────────────────────────────────
        dev_resp = tc.post(
            "/api/v1/devices",
            json={"name": "test-sidecar"},
            headers=auth_headers,
        )
        assert dev_resp.status_code == 201
        api_key = dev_resp.json()["api_key"]

        # ── Step D: create session ────────────────────────────────────────────
        sess_resp = tc.post(
            "/api/v1/sessions",
            json={"title": "WS Test Session"},
            headers=auth_headers,
        )
        assert sess_resp.status_code == 201
        sess_data = sess_resp.json()
        session_uuid = sess_data["session_id"]
        # viewer_url = "http://localhost:5173/v/<token>"
        viewer_token = sess_data["viewer_url"].rsplit("/", 1)[-1]

        session_pk = _sync_get_session_pk(session_uuid)

        # ── Step E: connect viewer/operator WS first (must subscribe before publish) ──
        with tc.websocket_connect(
            f"/ws/viewer?token={viewer_token}"
        ) as viewer_ws:
            with tc.websocket_connect(
                f"/ws/operator?access={admin_token}&session={session_uuid}"
            ) as operator_ws:
                # ── Step F: connect sidecar WS ───────────────────────────────
                with tc.websocket_connect(
                    f"/ws/sidecar?key={api_key}&session={session_uuid}"
                ) as sidecar_ws:
                    # Publish seq 1, 2, 3
                    for seq in (1, 2, 3):
                        sidecar_ws.send_text(_make_frame(session_uuid, seq))

                    # Receive 3 events on viewer and operator console
                    received = []
                    operator_received = []
                    for _ in range(3):
                        received.append(viewer_ws.receive_json())
                        operator_received.append(operator_ws.receive_json())

            # Sidecar WS closed — publish duplicate seq=1
            # We need a fresh sidecar connection to send the duplicate
            with tc.websocket_connect(
                f"/ws/sidecar?key={api_key}&session={session_uuid}"
            ) as sidecar_ws2:
                sidecar_ws2.send_text(_make_frame(session_uuid, 1))

        # ── Assertions ───────────────────────────────────────────────────────
        assert len(received) == 3
        seqs = sorted(m["seq"] for m in received)
        assert seqs == [1, 2, 3]
        operator_seqs = sorted(m["seq"] for m in operator_received)
        assert operator_seqs == [1, 2, 3]

        # DB row count must still be 3 (duplicate was suppressed by ON CONFLICT DO NOTHING)
        row_count = _sync_count_utterances(session_pk)
        assert row_count == 3, f"Expected 3 rows, got {row_count}"

        # ── Step G: REST backfill ─────────────────────────────────────────────
        backfill_resp = tc.get(
            f"/api/v1/viewer/utterances?token={viewer_token}"
        )
        assert backfill_resp.status_code == 200
        utterances = backfill_resp.json()["utterances"]
        assert len(utterances) == 3

        operator_backfill_resp = tc.get(
            f"/api/v1/sessions/{session_uuid}/utterances",
            headers=auth_headers,
        )
        assert operator_backfill_resp.status_code == 200
        assert len(operator_backfill_resp.json()["utterances"]) == 3

        second_session = _create_session(tc, auth_headers, "Second Live Session")
        with tc.websocket_connect(f"/ws/sidecar?key={api_key}&session={second_session}"):
            pass
        assert _sync_get_session_status(session_uuid) == "ended"
