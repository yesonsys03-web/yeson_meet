// === ANCHOR: USE_AUTO_UPDATE_START ===
import { useCallback, useEffect, useReducer, useRef } from "react";
import { check, type Update } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";

import { initialUpdateStatus, updateReducer, type UpdateStatus } from "./autoUpdate";

// Background check on startup + every 4h. Download is silent; the banner only
// asks the user to restart once an update is staged. Every failure is swallowed
// (log only) so the updater can never block the app.
const CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000;

function hasTauriRuntime(): boolean {
  return (
    typeof window !== "undefined" &&
    Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
  );
}

export type UseAutoUpdate = {
  status: UpdateStatus;
  checkNow: () => void;
  applyNow: () => void;
};

export function useAutoUpdate(): UseAutoUpdate {
  const [status, dispatch] = useReducer(updateReducer, initialUpdateStatus);
  // The staged Update handle: download() populates it, install() consumes it.
  const pending = useRef<Update | null>(null);
  const busy = useRef(false);
  const statusRef = useRef(status);
  statusRef.current = status;

  const runCheck = useCallback(async () => {
    if (!hasTauriRuntime() || busy.current) return;
    // A staged update is ready — don't re-check/re-download; the user just needs to restart.
    if (statusRef.current.kind === "ready") return;
    busy.current = true;
    dispatch({ type: "check-start" });
    try {
      const update = await check();
      if (!update) {
        dispatch({ type: "up-to-date" });
        return;
      }
      pending.current = update;
      dispatch({ type: "download-start", version: update.version });
      let downloaded = 0;
      let total = 0;
      await update.download((event) => {
        if (event.event === "Started") {
          total = event.data.contentLength ?? 0;
        } else if (event.event === "Progress") {
          downloaded += event.data.chunkLength;
          if (total > 0) {
            dispatch({ type: "download-progress", percent: Math.round((downloaded / total) * 100) });
          }
        }
      });
      dispatch({ type: "download-done", version: update.version });
    } catch (err) {
      // Network down, signature mismatch, or a 404 before the first updater-
      // enabled release ships — all non-fatal. Log and retry next cycle.
      console.warn("[auto-update] check/download failed:", err);
      dispatch({ type: "fail", message: err instanceof Error ? err.message : String(err) });
    } finally {
      busy.current = false;
    }
  }, []);

  const applyNow = useCallback(() => {
    void (async () => {
      const update = pending.current;
      if (!update) return;
      try {
        await update.install();
        await relaunch();
      } catch (err) {
        console.warn("[auto-update] install/relaunch failed:", err);
        dispatch({ type: "fail", message: err instanceof Error ? err.message : String(err) });
      }
    })();
  }, []);

  const checkNow = useCallback(() => {
    void runCheck();
  }, [runCheck]);

  useEffect(() => {
    void runCheck();
    const id = window.setInterval(() => void runCheck(), CHECK_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [runCheck]);

  return { status, checkNow, applyNow };
}
// === ANCHOR: USE_AUTO_UPDATE_END ===
