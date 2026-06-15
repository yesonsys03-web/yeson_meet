// === ANCHOR: CAPTURE_LEVEL_START ===
import { useEffect, useState } from "react";

import { listen } from "@tauri-apps/api/event";

// The sidecar emits `CAPTURE_LEVEL <dbfs>` ~1×/s. The Rust forwarder routes it
// to a dedicated `capture-level` Tauri event (NOT the app log) so the diagnostic
// log stays clean. We map dBFS → meter segments for the live loudness bar.

export const SEGMENTS = 6;
export const LEVEL_FLOOR_DBFS = -54; // empty bar at/below
export const LEVEL_CEIL_DBFS = -6; // full bar at/above
export const WARN_DBFS = -12; // a lit segment whose edge exceeds this → yellow
export const CLIP_DBFS = -6; // a lit segment whose edge exceeds this → red

/** Map a dBFS value to a count of filled segments in [0, segments]. */
export function dbfsToSegments(dbfs: number, segments: number = SEGMENTS): number {
  if (!Number.isFinite(dbfs)) return 0;
  const span = LEVEL_CEIL_DBFS - LEVEL_FLOOR_DBFS;
  const filled = Math.round(((dbfs - LEVEL_FLOOR_DBFS) / span) * segments);
  return Math.max(0, Math.min(segments, filled));
}

/** The dBFS at the top edge of segment `index` (0-based). Used for coloring. */
export function segmentEdgeDbfs(index: number, segments: number = SEGMENTS): number {
  const span = LEVEL_CEIL_DBFS - LEVEL_FLOOR_DBFS;
  return LEVEL_FLOOR_DBFS + ((index + 1) / segments) * span;
}

type CaptureLevelPayload = { dbfs: number };
type TauriWindow = Window & { __TAURI_INTERNALS__?: unknown };

function hasTauriRuntime(): boolean {
  return typeof window !== "undefined" && Boolean((window as TauriWindow).__TAURI_INTERNALS__);
}

/** Latest dBFS from the `capture-level` event, or null (no signal / non-Tauri). */
export function useCaptureLevel(): number | null {
  const [dbfs, setDbfs] = useState<number | null>(null);
  useEffect(() => {
    if (!hasTauriRuntime()) return;
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void listen<CaptureLevelPayload>("capture-level", (event) => {
      setDbfs(event.payload.dbfs);
    }).then((fn) => {
      if (cancelled) fn();
      else unlisten = fn;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);
  return dbfs;
}
// === ANCHOR: CAPTURE_LEVEL_END ===
