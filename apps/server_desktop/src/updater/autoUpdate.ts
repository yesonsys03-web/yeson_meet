// === ANCHOR: AUTO_UPDATE_START ===
// Pure state machine for the server console's background auto-updater banner.
// Free of Tauri imports so it runs (and is unit-tested) in plain vitest; the
// hook in useAutoUpdate.ts is the only place that touches the @tauri-apps plugins.

export type UpdateStatus =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "downloading"; version: string; percent: number | null }
  | { kind: "ready"; version: string }
  | { kind: "up-to-date" }
  | { kind: "error"; message: string };

export type UpdateAction =
  | { type: "check-start" }
  | { type: "up-to-date" }
  | { type: "download-start"; version: string }
  | { type: "download-progress"; percent: number }
  | { type: "download-done"; version: string }
  | { type: "fail"; message: string };

export const initialUpdateStatus: UpdateStatus = { kind: "idle" };

export function updateReducer(state: UpdateStatus, action: UpdateAction): UpdateStatus {
  switch (action.type) {
    case "check-start":
      if (state.kind === "ready" || state.kind === "downloading") return state;
      return { kind: "checking" };
    case "up-to-date":
      if (state.kind === "ready" || state.kind === "downloading") return state;
      return { kind: "up-to-date" };
    case "download-start":
      return { kind: "downloading", version: action.version, percent: null };
    case "download-progress":
      if (state.kind !== "downloading") return state;
      return { kind: "downloading", version: state.version, percent: action.percent };
    case "download-done":
      return { kind: "ready", version: action.version };
    case "fail":
      if (state.kind === "ready") return state;
      return { kind: "error", message: action.message };
    default:
      return state;
  }
}
// === ANCHOR: AUTO_UPDATE_END ===
