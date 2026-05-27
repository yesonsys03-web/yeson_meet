"""Tests for scripts/baseline_collect.py."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "baseline_sample.log"
SCRIPT = Path(__file__).parents[2] / "scripts" / "baseline_collect.py"


def test_collect_extracts_first_subtitle_latency(tmp_path):
    out = tmp_path / "metrics.json"
    subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--log", str(FIXTURE),
            "--scenario", "fixture",
            "--out", str(out),
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.read_text())
    assert data["scenario"] == "fixture"
    assert data["subtitle_first_token_ms"] == 9988
    assert "subtitle_full_p50_ms" in data
    assert "subtitle_full_p95_ms" in data
    assert data["audio_queue_drop_count"] == 100
    assert data["gemini_segment_count"] == 3


def test_collect_empty_scenario_with_allow_empty(tmp_path):
    empty_log = tmp_path / "empty.log"
    empty_log.write_text("")
    out = tmp_path / "metrics.json"
    subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--log", str(empty_log),
            "--scenario", "silent",
            "--out", str(out),
            "--allow-empty",
        ],
        check=True,
    )
    data = json.loads(out.read_text())
    assert data["empty_scenario"] is True
    assert data["subtitle_first_token_ms"] is None


def test_to_schema_v1_wraps_flat_into_nested(tmp_path):
    """Schema Alignment preamble: parse_log emits flat keys, to_schema_v1 wraps
    them into the docs/baselines/schema.md v1 nested structure so real
    measurement artifacts can be produced by passing --provider/--os/etc."""
    out = tmp_path / "metrics.json"
    subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--log", str(FIXTURE),
            "--scenario", "fixture",
            "--out", str(out),
            "--schema", "v1",
            "--provider", "sounddevice",
            "--os", "macOS",
            "--os-version", "14.5",
            "--audio-route", "BlackHole 2ch + Multi-Output",
            "--permission-state", "not_applicable",
            "--server-commit", "b3c2f9b",
            "--client-commit", "b3c2f9b",
            "--gemini-model", "gemini-3.1-flash-live-preview",
            "--gemini-modality", "AUDIO",
        ],
        check=True,
    )
    data = json.loads(out.read_text())
    assert data["schema_version"] == 1
    assert data["scenario"] == "fixture"
    assert data["env"]["provider"] == "sounddevice"
    assert data["env"]["os"] == "macOS"
    assert data["env"]["os_version"] == "14.5"
    assert data["env"]["audio_route"] == "BlackHole 2ch + Multi-Output"
    assert data["env"]["permission_state"] == "not_applicable"
    assert data["env"]["server_commit"] == "b3c2f9b"
    assert data["env"]["gemini_model"] == "gemini-3.1-flash-live-preview"
    assert data["env"]["gemini_response_modality"] == "AUDIO"
    assert data["ai"]["gemini_connect_to_first_subtitle_ms_first"] == 9988
    assert "gemini_connect_to_first_subtitle_ms_p50" in data["ai"]
    assert "gemini_connect_to_first_subtitle_ms_p95" in data["ai"]
    assert data["ai"]["gemini_segment_count"] == 3
    assert data["capture"]["audio_queue_drop_count"] == 100
    assert data["delivery"]["client_timing_artifact"] is None
    assert data["user_perceived"]["first_speech_to_first_subtitle_ms_first"] is None
    assert data["empty_scenario"] is False
