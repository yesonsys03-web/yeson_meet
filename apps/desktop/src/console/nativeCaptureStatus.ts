// === ANCHOR: NATIVE_CAPTURE_STATUS_START ===
import { useEffect, useState } from "react";

import { subscribeAppLogs, type AppLogEntry } from "../diagnostics/appLog";

// The sidecar prints `NATIVE_STATUS <reason>` to stdout when native capture
// fails (permission denied, helper start failure, crash). Rust forwards that
// line into the app log; we promote the latest one into a user-facing banner.
const MARKER = "NATIVE_STATUS ";

export type NativeCaptureStatus = { reason: string; id: number };

/** Extract the reason token from a `NATIVE_STATUS <reason>` message, else null. */
export function parseNativeStatusReason(message: string): string | null {
  if (!message.startsWith(MARKER)) return null;
  const reason = message.slice(MARKER.length).trim().split(/\s+/)[0];
  return reason || null;
}

/** Most recent native-capture failure in the log, or null if none. */
export function latestNativeStatus(entries: AppLogEntry[]): NativeCaptureStatus | null {
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    const item = entries[i];
    if (!item) continue;
    const reason = parseNativeStatusReason(item.message);
    if (reason) return { reason, id: item.id };
  }
  return null;
}

/** Subscribe to the app log and expose the latest native-capture failure. */
export function useNativeCaptureStatus(): NativeCaptureStatus | null {
  const [status, setStatus] = useState<NativeCaptureStatus | null>(null);
  useEffect(() => subscribeAppLogs((entries) => setStatus(latestNativeStatus(entries))), []);
  return status;
}
// === ANCHOR: NATIVE_CAPTURE_STATUS_END ===
