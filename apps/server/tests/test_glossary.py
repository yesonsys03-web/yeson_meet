"""Unit tests for the animation-production translation glossary."""
from __future__ import annotations

import importlib

import pytest

import apps.server.ai.glossary as glossary


def _fresh(monkeypatch, tmp_path, **env):
    """Reload the module with a clean cache and a controlled environment."""
    monkeypatch.delenv("YESON_GLOSSARY_PATH", raising=False)
    monkeypatch.delenv("STORAGE_ROOT", raising=False)
    # 명시 경로가 하나라도 남아 있으면 STORAGE_ROOT 기반 해석이 무시된다.
    monkeypatch.delenv("YESON_GLOSSARY_KO_PATH", raising=False)
    monkeypatch.delenv("YESON_GLOSSARY_DIALOGUE_PATH", raising=False)
    monkeypatch.delenv("YESON_GLOSSARY_KO_DIALOGUE_PATH", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    mod = importlib.reload(glossary)
    return mod


def test_default_glossary_fixes_cleanup(monkeypatch, tmp_path):
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    terms = dict((en.lower(), ko) for en, ko in mod.load_glossary())
    assert terms["cleanup"] == "클린업"  # not the literal "청소"
    assert terms["layout"] == "레이아웃"
    assert terms["coloring"] == "컬러"
    assert terms["line art"] == "라인아트"
    assert terms["element"] == "엘리먼트"  # not the literal "요소" (VFX sense)
    assert terms["push out"] == "밀림"  # schedule sense, not spatial
    assert terms["yeson"] == "예손"  # heard as "yes on" and dropped otherwise


def test_default_glossary_repairs_misheard_on_our_side(monkeypatch, tmp_path):
    """연음에서 "on our" /ɒn ˈaʊər/ 와 "an hour" /ən ˈaʊər/ 가 같은 소리라 3.5가
    더 흔한 쪽으로 적는다(실기 2026-08-04: "17 assets to receive on our side"가
    "...an hour side"로 전사돼 "한 시간 분량입니다"로 번역됐다).

    좌변이 3단어라 정상적인 "an hour"는 다치지 않는다 — "an hour"만 등록하면
    "in an hour"(한 시간 뒤)까지 망가진다.
    """
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    terms = {en.lower(): ko for en, ko in mod.load_glossary()}
    assert terms["an hour side"] == "저희 쪽"
    assert "an hour" not in terms


def test_ko_corrections_fix_report_awkwardness(monkeypatch, tmp_path):
    """실제 보고서에서 나온 어색한 문구가 교정되고, 정당한 표현은 안 다친다."""
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    fixed = mod.apply_ko_corrections("애니매틱을 딜리버리할 수 있어요")
    assert fixed == "애니매틱을 전달할 수 있어요"
    assert mod.apply_ko_corrections("2주간의 푸시 아웃을 보여줍니다") \
        == "2주간의 밀림을 보여줍니다"
    assert mod.apply_ko_corrections("일단 씨앗을 심어두고 다음 주에 얘기해요") \
        == "일단 주제로 던져두고 다음 주에 얘기해요"
    # 명사 "딜리버리"는 스튜디오 관례(delivery => 딜리버리)라 건드리면 안 된다.
    assert mod.apply_ko_corrections("딜리버리 일정 확인") == "딜리버리 일정 확인"


def test_block_lists_terms(monkeypatch, tmp_path):
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    block = mod.glossary_block()
    assert "cleanup → 클린업" in block
    assert "render → 렌더" in block


def test_parse_skips_comments_and_blanks():
    parsed = glossary.parse_glossary_file(
        "# comment\n\ncleanup => 청소대신클린업\n  spacing = 스페이싱x \n"
        "inbetween\t인비트윈x\nbad-line-no-sep\n"
    )
    assert parsed == [
        ("cleanup", "청소대신클린업"),
        ("spacing", "스페이싱x"),
        ("inbetween", "인비트윈x"),
    ]


def test_file_overrides_and_extends(monkeypatch, tmp_path):
    path = tmp_path / "glossary.txt"
    path.write_text("cleanup => 클린업오버라이드\nboarding => 보딩\n", encoding="utf-8")
    mod = _fresh(monkeypatch, tmp_path, YESON_GLOSSARY_PATH=str(path))
    terms = dict((en.lower(), ko) for en, ko in mod.load_glossary())
    assert terms["cleanup"] == "클린업오버라이드"  # overrides default
    assert terms["boarding"] == "보딩"  # appends new term
    assert terms["layout"] == "레이아웃"  # untouched default remains


def test_storage_root_default_location(monkeypatch, tmp_path):
    (tmp_path / "glossary.txt").write_text("retake => 리테이크x\n", encoding="utf-8")
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    terms = dict((en.lower(), ko) for en, ko in mod.load_glossary())
    assert terms["retake"] == "리테이크x"


def test_mtime_reload_without_restart(monkeypatch, tmp_path):
    path = tmp_path / "glossary.txt"
    path.write_text("cleanup => 버전1\n", encoding="utf-8")
    mod = _fresh(monkeypatch, tmp_path, YESON_GLOSSARY_PATH=str(path))
    assert dict(mod.load_glossary())["cleanup"] == "버전1"
    # Rewrite with a bumped mtime; the cache must invalidate and reload.
    import os

    stat = path.stat()
    path.write_text("cleanup => 버전2\n", encoding="utf-8")
    os.utime(path, (stat.st_atime, stat.st_mtime + 10))
    assert dict(mod.load_glossary())["cleanup"] == "버전2"


def test_glossary_disabled_returns_empty(monkeypatch, tmp_path):
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    monkeypatch.setenv("GEMINI_GLOSSARY_ENABLED", "0")
    assert mod.glossary_block() == ""
    monkeypatch.setenv("GEMINI_GLOSSARY_ENABLED", "1")
    assert "클린업" in mod.glossary_block()


def test_missing_file_falls_back_to_defaults(monkeypatch, tmp_path):
    mod = _fresh(
        monkeypatch, tmp_path, YESON_GLOSSARY_PATH=str(tmp_path / "nope.txt")
    )
    terms = dict((en.lower(), ko) for en, ko in mod.load_glossary())
    assert terms["cleanup"] == "클린업"


# --- 스코프(meeting / dialogue) ------------------------------------------
# 이 사전은 회의용으로 튜닝돼 있어서 자막 메이커(작품 대사)에 그대로 붙으면
# 평범한 대사가 깨진다. 아래는 그 분리를 잠그되, 반대 방향(회의 회귀)도 함께
# 잠근다 — 라이브 회의가 이 툴의 1순위 용도다.


def test_scope_default_is_meeting(monkeypatch, tmp_path):
    """기본 인자 = 오늘 동작. 라이브 호출부는 이 축을 몰라도 그대로여야 한다."""
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    assert mod.glossary_block() == mod.glossary_block(scope="meeting")
    assert mod.load_glossary() == mod.load_glossary(scope="meeting")
    assert mod.load_ko_corrections() == mod.load_ko_corrections(scope="meeting")


def test_meeting_scope_keeps_every_excluded_entry(monkeypatch, tmp_path):
    """반대 방향 회귀 방지 — 제외 6개는 회의 스코프에 전부 살아 있어야 한다."""
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    block = mod.glossary_block()
    assert "an hour side → 저희 쪽" in block
    assert "plant the seed" in block
    assert mod.apply_ko_corrections("청소팀이 왔어요") == "클린업팀이 왔어요"
    assert mod.apply_ko_corrections("청소 팀 회의") == "클린업 팀 회의"
    assert mod.apply_ko_corrections("청소 작업 일정") == "클린업 작업 일정"
    assert mod.apply_ko_corrections("씨앗을 심어 두고") == "주제로 던져 두고"


def test_dialogue_scope_leaves_ordinary_lines_alone(monkeypatch, tmp_path):
    """작품 대사에선 청소·씨앗이 문자 그대로여야 한다(= 이 축을 만든 이유)."""
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    assert mod.apply_ko_corrections("청소팀이 왔어요", scope="dialogue") \
        == "청소팀이 왔어요"
    assert mod.apply_ko_corrections("씨앗을 심어 두고", scope="dialogue") \
        == "씨앗을 심어 두고"
    # 대사에도 유효한 스튜디오 용어 교정은 상속돼 그대로 남는다.
    assert mod.apply_ko_corrections("연필 테스트 확인", scope="dialogue") \
        == "펜슬 테스트 확인"


def test_dialogue_block_drops_meeting_only_terms(monkeypatch, tmp_path):
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    block = mod.glossary_block(scope="dialogue")
    assert "an hour side" not in block
    assert "plant the seed" not in block
    assert "cleanup → 클린업" in block  # 나머지 파이프라인 용어는 상속


def test_dialogue_without_files_is_meeting_minus_exclusions(monkeypatch, tmp_path):
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    meeting = mod.load_glossary()
    dialogue = mod.load_glossary(scope="dialogue")
    assert dialogue == [
        (en, ko) for en, ko in meeting if en.lower() not in mod._DIALOGUE_EXCLUDE_EN
    ]
    meeting_ko = mod.load_ko_corrections()
    dialogue_ko = mod.load_ko_corrections(scope="dialogue")
    assert dialogue_ko == [
        (w, r) for w, r in meeting_ko if w not in mod._DIALOGUE_EXCLUDE_KO
    ]
    assert len(meeting) - len(dialogue) == 2
    assert len(meeting_ko) - len(dialogue_ko) == 4


def test_dialogue_file_overrides_inherited_entry(monkeypatch, tmp_path):
    """대사 전용 파일이 상속받은 항목을 마지막에 덮는다(= 운영자의 탈출구)."""
    (tmp_path / "glossary.txt").write_text("cleanup => 회의용\n", encoding="utf-8")
    (tmp_path / "glossary_dialogue.txt").write_text(
        "cleanup => 대사용\nboarding => 보딩\n", encoding="utf-8"
    )
    (tmp_path / "glossary_ko.txt").write_text("연필 테스트 => 회의교정\n", encoding="utf-8")
    (tmp_path / "glossary_ko_dialogue.txt").write_text(
        "연필 테스트 => 대사교정\n", encoding="utf-8"
    )
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    assert dict(mod.load_glossary())["cleanup"] == "회의용"
    assert dict(mod.load_glossary(scope="dialogue"))["cleanup"] == "대사용"
    assert dict(mod.load_glossary(scope="dialogue"))["boarding"] == "보딩"
    assert "boarding" not in dict(mod.load_glossary())  # 회의용은 오염되지 않는다
    assert mod.apply_ko_corrections("연필 테스트") == "회의교정"
    assert mod.apply_ko_corrections("연필 테스트", scope="dialogue") == "대사교정"


def test_operator_can_reinstate_excluded_entry(monkeypatch, tmp_path):
    """제외는 내장 기본값에만 적용한다 — 운영자가 공용 파일에 직접 다시 적었다면
    그건 운영자의 결정이므로 대사 스코프에서도 살린다."""
    (tmp_path / "glossary_ko.txt").write_text("청소팀 => 클린업팀\n", encoding="utf-8")
    (tmp_path / "glossary.txt").write_text(
        "plant the seed => 미리 던져 두다\n", encoding="utf-8"
    )
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    assert mod.apply_ko_corrections("청소팀이 왔어요", scope="dialogue") \
        == "클린업팀이 왔어요"
    assert "plant the seed" in mod.glossary_block(scope="dialogue")


def test_dialogue_mtime_reload_without_restart(monkeypatch, tmp_path):
    """dialogue는 파일 둘을 보므로 둘 다 재시작 없이 반영돼야 한다."""
    import os

    path = tmp_path / "glossary_dialogue.txt"
    path.write_text("cleanup => 버전1\n", encoding="utf-8")
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    assert dict(mod.load_glossary(scope="dialogue"))["cleanup"] == "버전1"
    stat = path.stat()
    path.write_text("cleanup => 버전2\n", encoding="utf-8")
    os.utime(path, (stat.st_atime, stat.st_mtime + 10))
    assert dict(mod.load_glossary(scope="dialogue"))["cleanup"] == "버전2"

    # 상속 원본(회의용 파일)이 바뀌어도 dialogue 캐시가 풀려야 한다.
    assert mod.apply_ko_corrections("보류중", scope="dialogue") == "보류중"
    (tmp_path / "glossary_ko.txt").write_text("보류중 => 홀드중\n", encoding="utf-8")
    assert mod.apply_ko_corrections("보류중", scope="dialogue") == "홀드중"


def test_unknown_scope_raises(monkeypatch, tmp_path):
    """조용히 meeting으로 떨어지면 오타 하나가 회의용 사전을 작품에 붙인다."""
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    for call in (
        lambda: mod.load_glossary("meetings"),
        lambda: mod.load_ko_corrections("dialog"),
        lambda: mod.glossary_block(scope="Dialogue"),
        lambda: mod.apply_ko_corrections("아무 말", scope=""),
        lambda: mod.glossary_file_path("nope"),
        lambda: mod.ko_corrections_file_path("nope"),
    ):
        with pytest.raises(ValueError):
            call()


def test_kill_switch_empties_both_scopes(monkeypatch, tmp_path):
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    monkeypatch.setenv("GEMINI_GLOSSARY_ENABLED", "0")
    assert mod.glossary_block() == ""
    assert mod.glossary_block(scope="dialogue") == ""
    monkeypatch.setenv("GEMINI_GLOSSARY_ENABLED", "1")
    assert "클린업" in mod.glossary_block(scope="dialogue")
