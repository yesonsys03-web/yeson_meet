#!/usr/bin/env python3
"""Compare baseline (BlackHole/Voicemeeter) vs native capture metrics.

Two input shapes are supported, auto-detected by ``schema_version``:
  * flat — same keys as baseline_collect.py default output (dev/TDD)
  * schema v1 — nested per docs/baselines/schema.md §2 (real artifacts)

For schema v1 the report uses the five §5 핵심 비교 키 (dotted paths).
Mismatched schema_version values are refused (schema.md §5).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FLAT_KEYS = [
    "subtitle_first_token_ms",
    "subtitle_full_p50_ms",
    "subtitle_full_p95_ms",
    "audio_queue_drop_count",
    "gemini_segment_count",
]

# schema.md §5 — the only five keys baseline_compare cares about for v1
V1_DOTTED_KEYS = [
    "user_perceived.first_speech_to_first_subtitle_ms_first",
    "ai.gemini_connect_to_first_subtitle_ms_p50",
    "capture.chunks_per_sec_sustained",
    "capture.audio_queue_drop_count",
    "delivery.server_to_viewer_ms_p50",
]


# === ANCHOR: BASELINE_COMPARE_LOOKUP_START ===
def _get(doc: dict, dotted: str):
    cur: object = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur
# === ANCHOR: BASELINE_COMPARE_LOOKUP_END ===


# === ANCHOR: BASELINE_COMPARE_MAIN_START ===
def render(baseline: dict, native: dict) -> str:
    schema = baseline.get("schema_version")
    keys = V1_DOTTED_KEYS if schema == 1 else FLAT_KEYS
    lines = [
        f"# Baseline vs Native — scenario `{baseline.get('scenario','?')}`",
        "",
        "| metric | baseline | native | delta |",
        "|---|---:|---:|---:|",
    ]
    for k in keys:
        b = _get(baseline, k) if "." in k else baseline.get(k)
        n = _get(native, k) if "." in k else native.get(k)
        if isinstance(b, (int, float)) and isinstance(n, (int, float)) and b:
            delta = f"{(n - b) / b * 100.0:+.1f}%"
        else:
            delta = "—"
        lines.append(f"| {k} | {b} | {n} | {delta} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--baseline", required=True, type=Path)
    p.add_argument("--native", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()
    baseline = json.loads(args.baseline.read_text())
    native = json.loads(args.native.read_text())
    b_schema = baseline.get("schema_version")
    n_schema = native.get("schema_version")
    if b_schema != n_schema:
        sys.exit(
            f"schema_version mismatch: baseline={b_schema!r} native={n_schema!r} — "
            "refusing to compare (see docs/baselines/schema.md §5)"
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(baseline, native))


if __name__ == "__main__":
    main()
# === ANCHOR: BASELINE_COMPARE_MAIN_END ===
