"""Unit tests for the animation-production translation glossary."""
from __future__ import annotations

import importlib

import apps.server.ai.glossary as glossary


def _fresh(monkeypatch, tmp_path, **env):
    """Reload the module with a clean cache and a controlled environment."""
    monkeypatch.delenv("YESON_GLOSSARY_PATH", raising=False)
    monkeypatch.delenv("STORAGE_ROOT", raising=False)
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


def test_ko_corrections_fix_report_awkwardness(monkeypatch, tmp_path):
    """실제 보고서에서 나온 어색한 문구가 교정되고, 정당한 표현은 안 다친다."""
    mod = _fresh(monkeypatch, tmp_path, STORAGE_ROOT=str(tmp_path))
    fixed = mod.apply_ko_corrections("애니매틱을 딜리버리할 수 있어요")
    assert fixed == "애니매틱을 전달할 수 있어요"
    assert mod.apply_ko_corrections("2주간의 푸시 아웃을 보여줍니다") \
        == "2주간의 밀림을 보여줍니다"
    assert mod.apply_ko_corrections("일단 씨앗을 심어두고 다음 주에 얘기해요") \
        == "일단 미리 말을 꺼내두고 다음 주에 얘기해요"
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
