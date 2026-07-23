"""용어 사전 편집 API 테스트 (콘솔 '용어 사전' 탭 백엔드).

파일(STORAGE_ROOT/glossary.txt·glossary_ko.txt)이 단일 진실이고 API는 그
편집기다 — 저장하면 기존 mtime 감지로 다음 번역부터 즉시 반영되므로 별도
리로드 엔드포인트가 없다. parse_glossary_file은 잘못된 줄을 조용히 버리므로,
저장 검증이 그런 줄을 422로 알려줘야 오타 한 줄이 소리 없이 사전에서 빠지는
사고를 막는다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from apps.server.ai.glossary import DEFAULT_GLOSSARY, DEFAULT_KO_CORRECTIONS


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    # 명시 경로 오버라이드가 남아 있으면 STORAGE_ROOT를 무시하므로 제거.
    monkeypatch.delenv("YESON_GLOSSARY_PATH", raising=False)
    monkeypatch.delenv("YESON_GLOSSARY_KO_PATH", raising=False)


async def test_get_returns_empty_when_no_override_files(client, tmp_path):
    resp = await client.get("/api/v1/glossary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["glossary"]["content"] == ""
    assert data["glossary"]["terms"] == 0
    assert data["corrections"]["content"] == ""
    # 오버라이드가 없어도 내장 기본 사전은 적용 중이다.
    assert data["glossary"]["effective_terms"] == len(DEFAULT_GLOSSARY)
    assert data["corrections"]["effective_terms"] == len(DEFAULT_KO_CORRECTIONS)


async def test_put_writes_file_and_takes_effect(client, tmp_path):
    content = "# 소프트웨어\nToon Boom => 툰붐\nMaya => 마야\n"
    resp = await client.put(
        "/api/v1/glossary/glossary", json={"content": content}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["terms"] == 2
    assert body["effective_terms"] == len(DEFAULT_GLOSSARY) + 2
    assert (tmp_path / "glossary.txt").read_text(encoding="utf-8") == content

    resp = await client.get("/api/v1/glossary")
    assert resp.json()["glossary"]["content"] == content


async def test_put_corrections_file(client, tmp_path):
    content = "투붐 => 툰붐\n"
    resp = await client.put(
        "/api/v1/glossary/corrections", json={"content": content}
    )
    assert resp.status_code == 200
    assert resp.json()["terms"] == 1
    assert (tmp_path / "glossary_ko.txt").read_text(encoding="utf-8") == content


async def test_put_rejects_invalid_lines_with_line_numbers(client, tmp_path):
    content = "Toon Boom => 툰붐\n오타줄입니다\nMaya =>\n# 주석은 통과\n"
    resp = await client.put(
        "/api/v1/glossary/glossary", json={"content": content}
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    lines = {item["line"] for item in detail["invalid_lines"]}
    assert lines == {2, 3}
    # 저장이 거부됐으니 파일은 없어야 한다.
    assert not (tmp_path / "glossary.txt").exists()


async def test_put_unknown_name_404(client):
    resp = await client.put("/api/v1/glossary/nope", json={"content": ""})
    assert resp.status_code == 404
