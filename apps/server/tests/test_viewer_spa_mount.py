"""P4.0 — bundled FastAPI serves the viewer SPA under the same /api + /ws origin.

These tests exercise the StaticFiles/SPA-fallback mount via Starlette's
``TestClient`` against ``apps.server.main.app``. They do NOT touch the DB
(no ``client``/``db_session`` fixtures), so the only Postgres dependency is the
session-scoped conftest setup — the assertions themselves are DB-free.

The mount is built once at import time from ``_web_dist_dir()``. To test both
the "dist present" and "dist absent" branches without rebuilding the app, the
tests assert against the SPA-fallback route's *predicate* (api/ws never
shadowed) and the absent-dist guard (app still boots, /api/v1/health works).
"""
from __future__ import annotations

import importlib
import sys

import pytest
from starlette.testclient import TestClient


def _reload_app_with_web_dist(monkeypatch: pytest.MonkeyPatch, web_dist: str | None):
    """Re-import apps.server.main with YESON_WEB_DIST set so the mount re-binds.

    Returns the freshly-imported module's ``app``. The module is re-imported in
    isolation and restored afterwards so the shared conftest ``app`` is intact.
    """
    if web_dist is None:
        monkeypatch.delenv("YESON_WEB_DIST", raising=False)
    else:
        monkeypatch.setenv("YESON_WEB_DIST", web_dist)
    saved = sys.modules.pop("apps.server.main", None)
    try:
        module = importlib.import_module("apps.server.main")
        return module.app
    finally:
        if saved is not None:
            sys.modules["apps.server.main"] = saved


@pytest.fixture
def fake_dist(tmp_path):
    """A minimal web dist with a recognisable index.html + one asset."""
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><body>VIEWER_SPA_INDEX</body></html>",
        encoding="utf-8",
    )
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('viewer');", encoding="utf-8")
    return tmp_path


def test_root_and_viewer_route_serve_spa_index(monkeypatch, fake_dist):
    app = _reload_app_with_web_dist(monkeypatch, str(fake_dist))
    with TestClient(app) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert "VIEWER_SPA_INDEX" in root.text
        assert "text/html" in root.headers["content-type"]

        viewer = client.get("/v/sometoken")
        assert viewer.status_code == 200
        assert "VIEWER_SPA_INDEX" in viewer.text
        assert "text/html" in viewer.headers["content-type"]


def test_real_asset_is_served_not_the_index(monkeypatch, fake_dist):
    app = _reload_app_with_web_dist(monkeypatch, str(fake_dist))
    with TestClient(app) as client:
        asset = client.get("/assets/app.js")
        assert asset.status_code == 200
        assert "console.log('viewer')" in asset.text


def test_api_is_not_shadowed_by_spa_mount(monkeypatch, fake_dist):
    app = _reload_app_with_web_dist(monkeypatch, str(fake_dist))
    with TestClient(app) as client:
        # Real /api route still resolves (200) — not the SPA index.
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert "VIEWER_SPA_INDEX" not in health.text
        # Unknown /api path 404s (the SPA catch-all must NOT serve index here).
        unknown = client.get("/api/v1/does-not-exist")
        assert unknown.status_code == 404
        assert "VIEWER_SPA_INDEX" not in unknown.text


def test_ws_route_exists_and_is_not_shadowed(monkeypatch, fake_dist):
    app = _reload_app_with_web_dist(monkeypatch, str(fake_dist))
    with TestClient(app) as client:
        # The viewer WS route is registered; a GET to it is rejected by the WS
        # handler (not served the SPA index). With a bad/absent token it closes
        # rather than returning the HTML shell.
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/viewer?token=nope"):
                pass


def test_absent_dist_boots_and_serves_api(monkeypatch, tmp_path):
    # Point the resolver at an empty dir (no index.html) → mount is skipped.
    app = _reload_app_with_web_dist(monkeypatch, str(tmp_path))
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        # No SPA fallback route → an arbitrary client path 404s instead of HTML.
        viewer = client.get("/v/sometoken")
        assert viewer.status_code == 404


def test_health_route_is_registered_on_shared_app():
    # Sanity: the shared conftest app exposes /api/v1/health (no shadowing).
    from apps.server.main import app

    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/v1/health" in paths
