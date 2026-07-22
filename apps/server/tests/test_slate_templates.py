from __future__ import annotations

from apps.server.domain.video_captions import slate_templates as st


def test_upsert_creates_then_updates_by_name(monkeypatch, tmp_path):
    """쇼 템플릿 = 슬레이트 구역 + 토큰 규칙. 같은 쇼의 다음 에피소드에서 그대로
    불러 쓰기 위한 저장소이므로 이름이 키다(같은 이름은 덮어쓴다)."""
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    st.upsert_template({
        "name": "HZBN307", "region": {"x": 0.02, "y": 0.03, "w": 0.5, "h": 0.08},
        "delimiters": ["_", "-"], "seq_tokens": [1], "scene_tokens": [2],
    })
    assert [t["name"] for t in st.list_templates()] == ["HZBN307"]
    st.upsert_template({
        "name": "HZBN307", "region": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.2},
        "delimiters": ["_"], "seq_tokens": [2], "scene_tokens": [],
    })
    got = st.list_templates()
    assert len(got) == 1, "같은 이름은 새로 만들지 않고 덮어쓴다"
    assert got[0]["region"]["h"] == 0.2
    assert got[0]["seq_tokens"] == [2]


def test_delete_removes_only_that_name(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    for name in ("A", "B"):
        st.upsert_template({
            "name": name, "region": {"x": 0, "y": 0, "w": 1, "h": 0.1},
            "delimiters": ["_"], "seq_tokens": [1], "scene_tokens": [2],
        })
    assert st.delete_template("A") is True
    assert [t["name"] for t in st.list_templates()] == ["B"]
    assert st.delete_template("없는이름") is False


def test_list_is_empty_and_survives_corrupt_file(monkeypatch, tmp_path):
    """저장소가 없거나 깨져도 빈 목록으로 시작한다 — 템플릿은 편의 기능이라
    파일 하나 때문에 씬 분할 화면이 죽으면 안 된다."""
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    assert st.list_templates() == []
    st.templates_path().parent.mkdir(parents=True, exist_ok=True)
    st.templates_path().write_text("{ not json", encoding="utf-8")
    assert st.list_templates() == []
