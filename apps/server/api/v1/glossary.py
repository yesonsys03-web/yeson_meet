# === ANCHOR: API_GLOSSARY_START ===
"""용어 사전 편집 API — 콘솔 '용어 사전' 탭 백엔드.

파일(STORAGE_ROOT/glossary.txt·glossary_ko.txt)이 단일 진실이고 이 API는 그
편집기다. 로더가 mtime을 감지해 다음 번역부터 즉시 반영하므로 리로드
엔드포인트가 없다. 손 편집·파일 복사 워크플로와 병행 가능.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from apps.server.ai.glossary import (
    glossary_file_path,
    invalid_glossary_lines,
    ko_corrections_file_path,
    load_glossary,
    load_ko_corrections,
    parse_glossary_file,
)

router = APIRouter(prefix="/glossary", tags=["glossary"])


class GlossaryPutIn(BaseModel):
    content: str


def _paths() -> dict[str, Path]:
    return {
        "glossary": glossary_file_path(),
        "corrections": ko_corrections_file_path(),
    }


def _effective_terms(name: str) -> int:
    """내장 기본 + 오버라이드 병합 후 실제 적용 항목 수."""
    return len(load_glossary() if name == "glossary" else load_ko_corrections())


def _file_info(name: str, path: Path) -> dict:
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return {
        "content": content,
        "terms": len(parse_glossary_file(content)),
        "effective_terms": _effective_terms(name),
    }


@router.get("")
async def get_glossary() -> dict:
    return {name: _file_info(name, path) for name, path in _paths().items()}


@router.put("/{name}")
async def put_glossary(name: str, body: GlossaryPutIn) -> dict:
    paths = _paths()
    if name not in paths:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown glossary file")
    bad = invalid_glossary_lines(body.content)
    if bad:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "형식 오류 줄이 있어 저장하지 않았습니다",
                "invalid_lines": [{"line": i, "text": t} for i, t in bad],
            },
        )
    path = paths[name]
    path.parent.mkdir(parents=True, exist_ok=True)
    # 원자적 교체 — 번역 로더가 부분 쓰기 상태를 읽지 않게.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body.content, encoding="utf-8")
    os.replace(tmp, path)
    return {
        "saved": True,
        "terms": len(parse_glossary_file(body.content)),
        "effective_terms": _effective_terms(name),
    }
# === ANCHOR: API_GLOSSARY_END ===
