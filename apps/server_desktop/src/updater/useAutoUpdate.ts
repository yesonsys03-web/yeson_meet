// === ANCHOR: USE_AUTO_UPDATE_START ===
import { useCallback, useEffect, useReducer, useRef } from "react";
import { check, type Update } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";
import { invoke } from "@tauri-apps/api/core";

import { initialUpdateStatus, updateReducer, type UpdateStatus } from "./autoUpdate";

// Background check on startup + every 4h. Silent download; the banner only asks
// to restart once an update is staged. Failures are swallowed (log only) so the
// updater can never block the console.
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
  const pending = useRef<Update | null>(null);
  const busy = useRef(false);
  const statusRef = useRef(status);
  statusRef.current = status;

  const runCheck = useCallback(async () => {
    if (!hasTauriRuntime() || busy.current) return;
    // A staged update is ready (or being applied) — don't re-check/re-download.
    if (statusRef.current.kind === "ready" || statusRef.current.kind === "applying") return;
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
      dispatch({ type: "apply-start", version: update.version });
      // Stop the bundled server + tunnel BEFORE swapping the bundle/exe. Unlike
      // the client, the console always has live children (server + tunnel)
      // holding the app bundle/exe open, which makes macOS installs slow and
      // Windows installs fail outright (locked binaries). Stopping first
      // mirrors the client's idle-at-update-time state. Best-effort: a stop
      // failure must not block the update, since the app is restarting anyway.
      try {
        await invoke("stop_tunnel_cmd");
      } catch (e) {
        console.warn("[auto-update] stop tunnel:", e);
      }
      try {
        await invoke("stop_server");
      } catch (e) {
        console.warn("[auto-update] stop server:", e);
      }
      try {
        await update.install();
        await relaunch();
      } catch (err) {
        console.warn("[auto-update] install/relaunch failed:", err);
        dispatch({
          type: "apply-fail",
          version: update.version,
          message: err instanceof Error ? err.message : String(err),
        });
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
