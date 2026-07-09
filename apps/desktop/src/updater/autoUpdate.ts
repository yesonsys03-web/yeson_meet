// === ANCHOR: AUTO_UPDATE_START ===
// Pure state machine for the background auto-updater banner. Deliberately free of
// any Tauri imports so it runs (and is unit-tested) in plain vitest; the hook in
// useAutoUpdate.ts is the only place that touches the @tauri-apps plugins.

export type UpdateStatus =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "downloading"; version: string; percent: number | null }
  | { kind: "ready"; version: string }
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
      // A staged ("ready") or in-flight download must survive the next 4h poll.
      if (state.kind === "ready" || state.kind === "downloading") return state;
      return { kind: "checking" };
    case "up-to-date":
      // A background poll that finds nothing must not erase a ready banner.
      if (state.kind === "ready" || state.kind === "downloading") return state;
      return { kind: "idle" };
    case "download-start":
      return { kind: "downloading", version: action.version, percent: null };
    case "download-progress":
      if (state.kind !== "downloading") return state;
      return { kind: "downloading", version: state.version, percent: action.percent };
    case "download-done":
      return { kind: "ready", version: action.version };
    case "fail":
      // Never bury a usable "ready" banner under a later transient failure.
      if (state.kind === "ready") return state;
      return { kind: "error", message: action.message };
    default:
      return state;
  }
}

// navigator.platform / userAgent contain "Mac" inside the Tauri webview on macOS.
// Pure helper so the mac-only permission note stays unit-testable.
export function isMacOS(platform: string): boolean {
  return /mac/i.test(platform);
}
// === ANCHOR: AUTO_UPDATE_END ===
