// === ANCHOR: USE_QR_FULLSCREEN_SHORTCUT_START ===
const QR_WINDOW_LABEL = "qr-fullscreen";

export async function openQrWindow(viewerUrl: string) {
  if (!viewerUrl) return;

  if (hasTauriRuntime()) {
    const { WebviewWindow } = await import("@tauri-apps/api/webviewWindow");
    const existing = await WebviewWindow.getByLabel(QR_WINDOW_LABEL);
    if (existing) {
      await existing.close();
      return;
    }

    const { width, height } = await monitorEightyPercent();
    const qrWindow = new WebviewWindow(QR_WINDOW_LABEL, {
      url: qrWindowUrl(viewerUrl),
      title: "Viewer QR",
      decorations: false,
      resizable: true,
      center: true,
      width,
      height,
    });
    qrWindow.once("tauri://created", () => {
      void qrWindow.setFocus();
    });
    return;
  }

  const width = Math.round(window.screen.availWidth * 0.8);
  const height = Math.round(window.screen.availHeight * 0.8);
  const popup = window.open(qrWindowUrl(viewerUrl), QR_WINDOW_LABEL, `popup=yes,width=${width},height=${height}`);
  popup?.focus();
}

// 80% of the current monitor's LOGICAL size (physical / scaleFactor). Falls back to 1280x800 when no monitor is reported.
async function monitorEightyPercent(): Promise<{ width: number; height: number }> {
  const { currentMonitor } = await import("@tauri-apps/api/window");
  const mon = await currentMonitor();
  if (!mon) return { width: 1280, height: 800 };
  const logicalWidth = mon.size.width / mon.scaleFactor;
  const logicalHeight = mon.size.height / mon.scaleFactor;
  return {
    width: Math.round(0.8 * logicalWidth),
    height: Math.round(0.8 * logicalHeight),
  };
}

export function qrWindowParams(): { url: string } {
  const hash = window.location.hash;
  const query = hash.includes("?") ? hash.slice(hash.indexOf("?") + 1) : window.location.search.slice(1);
  const params = new URLSearchParams(query);
  return {
    url: params.get("url") ?? "",
  };
}

export function isQrWindowRoute(): boolean {
  return window.location.hash.startsWith("#/qr-window");
}

function qrWindowUrl(viewerUrl: string): string {
  return `#/qr-window?url=${encodeURIComponent(viewerUrl)}`;
}

export async function closeCurrentQrWindow() {
  if (hasTauriRuntime()) {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    await getCurrentWindow().close();
    return;
  }
  window.close();
}

export function hasTauriRuntime(): boolean {
  return typeof window !== "undefined" && Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
}

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tagName = target.tagName.toLowerCase();
  return tagName === "input" || tagName === "textarea" || tagName === "select" || target.isContentEditable;
}
// === ANCHOR: USE_QR_FULLSCREEN_SHORTCUT_END ===
