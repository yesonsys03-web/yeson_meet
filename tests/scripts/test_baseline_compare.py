"""Tests for scripts/baseline_compare.py."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "baseline_compare.py"


def test_compare_produces_markdown_with_deltas(tmp_path):
    """Flat schema (plan's original Task 4 example)."""
    baseline = tmp_path / "baseline.json"
    native = tmp_path / "native.json"
    baseline.write_text(json.dumps({
        "scenario": "zoom-1on1",
        "subtitle_first_token_ms": 10000,
        "subtitle_full_p50_ms": 9500,
        "subtitle_full_p95_ms": 11200,
        "audio_queue_drop_count": 50,
        "gemini_segment_count": 30,
    }))
    native.write_text(json.dumps({
        "scenario": "zoom-1on1",
        "subtitle_first_token_ms": 7800,
        "subtitle_full_p50_ms": 7200,
        "subtitle_full_p95_ms": 8500,
        "audio_queue_drop_count": 5,
        "gemini_segment_count": 30,
    }))
    out = tmp_path / "report.md"
    subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--baseline", str(baseline),
            "--native", str(native),
            "--out", str(out),
        ],
        check=True,
    )
    body = out.read_text()
    assert "zoom-1on1" in body
    assert "subtitle_first_token_ms" in body
    assert "-22.0%" in body or "-22%" in body  # 10000 → 7800


def test_compare_schema_v1_nested_uses_dotted_keys(tmp_path):
    """Schema v1 inputs → nested-path metric rows in the report."""
    baseline = tmp_path / "baseline.json"
    native = tmp_path / "native.json"
    baseline_doc = {
        "schema_version": 1,
        "scenario": "zoom-1on1",
        "ai": {
            "gemini_connect_to_first_subtitle_ms_first": 10000,
            "gemini_connect_to_first_subtitle_ms_p50": 9500,
            "gemini_connect_to_first_subtitle_ms_p95": 11200,
            "gemini_segment_count": 30,
        },
        "capture": {
            "chunks_per_sec_sustained": 49.8,
            "audio_queue_drop_count": 50,
        },
        "delivery": {"server_to_viewer_ms_p50": 5.0},
        "user_perceived": {"first_speech_to_first_subtitle_ms_first": 11000},
    }
    native_doc = {
        "schema_version": 1,
        "scenario": "zoom-1on1",
        "ai": {
            "gemini_connect_to_first_subtitle_ms_first": 7800,
            "gemini_connect_to_first_subtitle_ms_p50": 7200,
            "gemini_connect_to_first_subtitle_ms_p95": 8500,
            "gemini_segment_count": 30,
        },
        "capture": {
            "chunks_per_sec_sustained": 50.0,
            "audio_queue_drop_count": 5,
        },
        "delivery": {"server_to_viewer_ms_p50": 5.2},
        "user_perceived": {"first_speech_to_first_subtitle_ms_first": 7500},
    }
    baseline.write_text(json.dumps(baseline_doc))
    native.write_text(json.dumps(native_doc))
    out = tmp_path / "report.md"
    subprocess.run(
        [sys.executable, str(SCRIPT),
         "--baseline", str(baseline), "--native", str(native),
         "--out", str(out)],
        check=True,
    )
    body = out.read_text()
    # The five schema.md §5 핵심 비교 키
    assert "user_perceived.first_speech_to_first_subtitle_ms_first" in body
    assert "ai.gemini_connect_to_first_subtitle_ms_p50" in body
    assert "capture.chunks_per_sec_sustained" in body
    assert "capture.audio_queue_drop_count" in body
    assert "delivery.server_to_viewer_ms_p50" in body


def test_compare_refuses_mismatched_schema_versions(tmp_path):
    """schema.md §5: schema_version 다르면 비교 거부."""
    baseline = tmp_path / "baseline.json"
    native = tmp_path / "native.json"
    baseline.write_text(json.dumps({"schema_version": 1, "scenario": "x"}))
    native.write_text(json.dumps({"schema_version": 2, "scenario": "x"}))
    out = tmp_path / "report.md"
    result = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--baseline", str(baseline), "--native", str(native),
         "--out", str(out)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "schema" in (result.stderr + result.stdout).lower()
