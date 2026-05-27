#!/usr/bin/env python3
"""Aggregate Gemini Live latency/throughput metrics from server logs.

Parses lines emitted by ``apps.server.ai.gemini_live`` and ``apps.server.ws.sidecar``
(both INFO/WARNING). Outputs a single JSON file per scenario suitable for
direct comparison with the post-Phase-1 native run.

Two output shapes:
  * default (flat keys) — for fast TDD/dev use
  * --schema v1 (nested per docs/baselines/schema.md v1) — for real measurements
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path


# === ANCHOR: BASELINE_COLLECT_PATTERNS_START ===
FIRST_SUBTITLE_RE = re.compile(
    r"Gemini Live first subtitle yielded.*?gemini_connect_to_first_subtitle_ms=(\d+).*?gemini_segment=(\d+)"
)
DROP_RE = re.compile(r"dropped_chunks_total=(\d+)")
CONNECT_RE = re.compile(r"Gemini Live connect starting.*?gemini_segment=(\d+)")
# === ANCHOR: BASELINE_COLLECT_PATTERNS_END ===


# === ANCHOR: BASELINE_COLLECT_PARSE_START ===
def parse_log(path: Path, *, allow_empty: bool = False) -> dict[str, object]:
    first_subtitle_ms_list: list[int] = []
    drop_total = 0
    segment_count = 0
    for line in path.read_text(errors="replace").splitlines():
        m = FIRST_SUBTITLE_RE.search(line)
        if m:
            first_subtitle_ms_list.append(int(m.group(1)))
            continue
        m = DROP_RE.search(line)
        if m:
            drop_total = max(drop_total, int(m.group(1)))
            continue
        m = CONNECT_RE.search(line)
        if m:
            segment_count = max(segment_count, int(m.group(1)))
            continue
    if not first_subtitle_ms_list:
        if not allow_empty:
            raise SystemExit("no 'Gemini Live first subtitle yielded' lines found")
        return {
            "subtitle_first_token_ms": None,
            "subtitle_full_p50_ms": None,
            "subtitle_full_p95_ms": None,
            "audio_queue_drop_count": drop_total,
            "gemini_segment_count": segment_count,
            "empty_scenario": True,
        }
    p95 = (
        int(statistics.quantiles(first_subtitle_ms_list, n=20)[-1])
        if len(first_subtitle_ms_list) >= 2
        else first_subtitle_ms_list[0]
    )
    return {
        "subtitle_first_token_ms": first_subtitle_ms_list[0],
        "subtitle_full_p50_ms": int(statistics.median(first_subtitle_ms_list)),
        "subtitle_full_p95_ms": p95,
        "audio_queue_drop_count": drop_total,
        "gemini_segment_count": segment_count,
        "empty_scenario": False,
    }
# === ANCHOR: BASELINE_COLLECT_PARSE_END ===


# === ANCHOR: BASELINE_COLLECT_SCHEMA_V1_START ===
def to_schema_v1(parsed: dict[str, object], args: argparse.Namespace) -> dict[str, object]:
    """Wrap flat parse_log() output in the nested schema.md v1 shape.

    See docs/baselines/schema.md §2. Fields not derivable from the log are
    null; comparison script (baseline_compare.py) treats nulls as missing.
    """
    empty = bool(parsed["empty_scenario"])
    first_ms = parsed["subtitle_first_token_ms"]
    p50_ms = parsed["subtitle_full_p50_ms"]
    p95_ms = parsed["subtitle_full_p95_ms"]
    return {
        "schema_version": 1,
        "scenario": args.scenario,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_seconds": args.duration_seconds,
        "source_log": str(args.log),
        "env": {
            "provider": args.provider,
            "os": args.os,
            "os_version": args.os_version,
            "cpu_arch": args.cpu_arch,
            "device_model": args.device_model,
            "audio_route": args.audio_route,
            "permission_state": args.permission_state,
            "server_commit": args.server_commit,
            "client_commit": args.client_commit,
            "gemini_model": args.gemini_model,
            "gemini_response_modality": args.gemini_modality,
        },
        "capture": {
            "chunks_per_sec_sustained": None,
            "chunks_per_sec_p05": None,
            "audio_queue_drop_count": parsed["audio_queue_drop_count"],
            "first_chunk_after_speech_ms": None,
        },
        "ai": {
            "gemini_connect_to_first_subtitle_ms_first": first_ms,
            "gemini_connect_to_first_subtitle_ms_p50": p50_ms,
            "gemini_connect_to_first_subtitle_ms_p95": p95_ms,
            "gemini_segment_count": parsed["gemini_segment_count"],
            "gemini_segments_per_minute": None,
        },
        "delivery": {
            "server_to_viewer_ms_p50": None,
            "server_to_viewer_ms_p95": None,
            "client_timing_artifact": args.client_timing,
        },
        "user_perceived": {
            "first_speech_to_first_subtitle_ms_first": (
                args.speech_onset_unix_ms if args.speech_onset_unix_ms is not None and not empty else None
            ),
            "first_speech_to_final_subtitle_ms_p50": None,
            "first_speech_to_final_subtitle_ms_p95": None,
            "measurement_method": args.measurement_method,
        },
        "cost": {
            "input_tokens_total": None,
            "output_tokens_total": None,
            "usd_estimated": None,
        },
        "empty_scenario": empty,
        "notes": args.notes,
    }
# === ANCHOR: BASELINE_COLLECT_SCHEMA_V1_END ===


# === ANCHOR: BASELINE_COLLECT_MAIN_START ===
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--allow-empty", action="store_true",
                        help="silent scenario: succeed with nulls if no subtitle lines")
    parser.add_argument("--schema", choices=["flat", "v1"], default="flat",
                        help="output shape: flat keys (default) or schema.md v1 nested")
    # schema v1 env fields
    parser.add_argument("--provider", choices=["sounddevice", "native"])
    parser.add_argument("--os", choices=["macOS", "Windows"])
    parser.add_argument("--os-version")
    parser.add_argument("--cpu-arch", default=None)
    parser.add_argument("--device-model", default=None)
    parser.add_argument("--audio-route")
    parser.add_argument("--permission-state",
                        choices=["granted", "denied", "not_applicable", "not_determined"])
    parser.add_argument("--server-commit")
    parser.add_argument("--client-commit")
    parser.add_argument("--gemini-model")
    parser.add_argument("--gemini-modality", choices=["AUDIO", "TEXT"])
    parser.add_argument("--duration-seconds", type=int, default=None)
    parser.add_argument("--client-timing", default=None,
                        help="path to client subtitleTiming.ts JSON export")
    parser.add_argument("--speech-onset-unix-ms", type=int, default=None,
                        help="user_perceived: spoken-word onset in unix ms")
    parser.add_argument("--measurement-method",
                        choices=["manual_cue", "youtube_timestamp", "vad"],
                        default="manual_cue")
    parser.add_argument("--notes", default=None)
    args = parser.parse_args()

    parsed = parse_log(args.log, allow_empty=args.allow_empty)

    if args.schema == "v1":
        required_v1 = [
            "provider", "os", "os_version", "audio_route", "permission_state",
            "server_commit", "client_commit", "gemini_model", "gemini_modality",
        ]
        missing = [f"--{n.replace('_','-')}" for n in required_v1 if getattr(args, n) is None]
        if missing:
            parser.error(f"--schema v1 requires: {', '.join(missing)}")
        out = to_schema_v1(parsed, args)
    else:
        out = dict(parsed)
        out["scenario"] = args.scenario
        out["source_log"] = str(args.log)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
# === ANCHOR: BASELINE_COLLECT_MAIN_END ===


if __name__ == "__main__":
    main()
