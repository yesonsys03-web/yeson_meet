// === ANCHOR: CAPTURE_STATUS_START ===
import { useEffect, useState } from "react";

import { subscribeAppLogs, type AppLogEntry } from "../diagnostics/appLog";

// The sidecar prints `CAPTURE_STATUS <state>` on each capture-state transition
// (connecting/active/silent/transport_down). Rust forwards it into the app log;
// we promote the latest one into a live status chip. Mirrors nativeCaptureStatus.
const MARKER = "CAPTURE_STATUS ";

export type CaptureState = "connecting" | "active" | "silent" | "no_audio" | "transport_down";

const KNOWN: readonly CaptureState[] = ["connecting", "active", "silent", "no_audio", "transport_down"];

/** Extract a known capture state from a `CAPTURE_STATUS <state>` line, else null. */
export function parseCaptureStatus(message: string): CaptureState | null {
  if (!message.startsWith(MARKER)) return null;
  const token = message.slice(MARKER.length).trim().split(/\s+/)[0] ?? "";
  return (KNOWN as readonly string[]).includes(token) ? (token as CaptureState) : null;
}

/** Most recent capture state in the log, or null if none. */
export function latestCaptureStatus(entries: AppLogEntry[]): CaptureState | null {
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    const item = entries[i];
    if (!item) continue;
    const state = parseCaptureStatus(item.message);
    if (state) return state;
  }
  return null;
}

/** Subscribe to the app log and expose the latest capture state. */
export function useCaptureStatus(): CaptureState | null {
  const [state, setState] = useState<CaptureState | null>(null);
  useEffect(() => subscribeAppLogs((entries) => setState(latestCaptureStatus(entries))), []);
  return state;
}
// === ANCHOR: CAPTURE_STATUS_END ===
