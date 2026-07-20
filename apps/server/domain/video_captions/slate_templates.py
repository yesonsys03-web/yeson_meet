"""쇼별 슬레이트 템플릿 저장소.

슬레이트 구역(프레임 대비 비율)과 토큰 규칙(구분자·SEQ/SCENE 인덱스)은 쇼마다
다르지만 같은 쇼 안에서는 에피소드가 바뀌어도 같다. 한 번 지정한 값을 쇼 이름으로
저장해 두고 다음 작품에서 골라 쓰기 위한 목록이다.

잡 디렉터리가 아니라 스토리지 루트에 둔다 — 잡은 보존 정책(RETENTION_KEEP)에 따라
지워지지만 템플릿은 남아야 한다.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .pipeline import video_jobs_root

logger = logging.getLogger("yeson.video.slate_templates")


def templates_path() -> Path:
    return video_jobs_root().parent / "slate_templates.json"


def list_templates() -> list[dict]:
    """저장된 템플릿 목록. 파일이 없거나 깨졌으면 빈 목록 — 템플릿은 편의 기능이라
    파일 하나 때문에 씬 분할 화면이 죽으면 안 된다."""
    path = templates_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.exception("slate templates unreadable: %s", path)
        return []
    items = data.get("templates") if isinstance(data, dict) else data
    return items if isinstance(items, list) else []


def _save(items: list[dict]) -> None:
    path = templates_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"templates": items}, ensure_ascii=False),
                    encoding="utf-8")


def upsert_template(template: dict) -> list[dict]:
    """이름을 키로 저장(같은 이름은 덮어쓴다). 갱신 후 전체 목록을 돌려준다."""
    name = str(template.get("name", "")).strip()
    if not name:
        raise ValueError("템플릿 이름이 필요합니다.")
    entry = {**template, "name": name}
    items = [t for t in list_templates() if t.get("name") != name]
    items.append(entry)
    items.sort(key=lambda t: t.get("name", ""))
    _save(items)
    return items


def delete_template(name: str) -> bool:
    """지웠으면 True, 없던 이름이면 False."""
    items = list_templates()
    kept = [t for t in items if t.get("name") != name]
    if len(kept) == len(items):
        return False
    _save(kept)
    return True
