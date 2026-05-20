// === ANCHOR: USE_SUBTITLE_FULLSCREEN_SHORTCUT_START ===
import { useEffect, useState } from "react";

const SUBTITLE_WINDOW_LABEL = "subtitle-fullscreen";

type ShortcutOptions = {
  operatorToken: string;
  sessionId: string | null;
  windowMode?: boolean;
};

export function useSubtitleFullscreenShortcut({ operatorToken, sessionId, windowMode = false }: ShortcutOptions) {
  const [windowOpen, setWindowOpen] = useState(windowMode);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && windowMode) {
        event.preventDefault();
        void closeCurrentSubtitleWindow();
        return;
      }
      if (event.key.toLowerCase() !== "f") return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.repeat || isEditableTarget(event.target)) return;

      event.preventDefault();
      void toggleSubtitleWindow();
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  async function toggleSubtitleWindow() {
    if (windowMode) {
      await closeCurrentSubtitleWindow();
      return;
    }
    if (!sessionId || !operatorToken) return;

    if (hasTauriRuntime()) {
      const { WebviewWindow } = await import("@tauri-apps/api/webviewWindow");
      const existing = await WebviewWindow.getByLabel(SUBTITLE_WINDOW_LABEL);
      if (existing) {
        await existing.close();
        setWindowOpen(false);
        return;
      }

      const subtitleWindow = new WebviewWindow(SUBTITLE_WINDOW_LABEL, {
        url: subtitleWindowUrl(sessionId, operatorToken),
        title: "yeson-meet live subtitles",
        decorations: false,
        maximized: true,
        resizable: true,
      });
      subtitleWindow.once("tauri://created", () => {
        setWindowOpen(true);
        void subtitleWindow.setFocus();
      });
      subtitleWindow.once("tauri://destroyed", () => setWindowOpen(false));
      return;
    }

    const popup = window.open(subtitleWindowUrl(sessionId, operatorToken), SUBTITLE_WINDOW_LABEL, "popup=yes,width=1280,height=720");
    popup?.focus();
    setWindowOpen(Boolean(popup));
  }

  return {
    isFullscreen: windowOpen || windowMode,
    toggleFullscreen: toggleSubtitleWindow,
  };
}

export function subtitleWindowParams(): { sessionId: string | null; operatorToken: string } {
  const hash = window.location.hash;
  const query = hash.includes("?") ? hash.slice(hash.indexOf("?") + 1) : window.location.search.slice(1);
  const params = new URLSearchParams(query);
  return {
    sessionId: params.get("sessionId"),
    operatorToken: params.get("operatorToken") ?? "",
  };
}

export function isSubtitleWindowRoute(): boolean {
  return window.location.hash.startsWith("#/subtitle-window");
}

function subtitleWindowUrl(sessionId: string, operatorToken: string): string {
  const params = new URLSearchParams({ sessionId, operatorToken });
  return `#/subtitle-window?${params.toString()}`;
}

async function closeCurrentSubtitleWindow() {
  if (hasTauriRuntime()) {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    await getCurrentWindow().close();
    return;
  }
  window.close();
}

function hasTauriRuntime(): boolean {
  return typeof window !== "undefined" && Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tagName = target.tagName.toLowerCase();
  return tagName === "input" || tagName === "textarea" || tagName === "select" || target.isContentEditable;
}
// === ANCHOR: USE_SUBTITLE_FULLSCREEN_SHORTCUT_END ===
