// === ANCHOR: DESKTOP_CONSOLE_START ===
import { useEffect, useState } from "react";
import { getVersion } from "@tauri-apps/api/app";
import { installAppLogCapture } from "../diagnostics/appLog";
import { hydrateServerAddressFromKeychain } from "../setup/credentials";
import { HelpManualPanel } from "../help/HelpManualPanel";
import { SettingsPanel } from "../settings/SettingsPanel";
import { SetupAssistant } from "../setup/SetupAssistant";
import { ConsoleNav } from "./ConsoleNav";
import { UpdateBanner } from "./UpdateBanner";
import { useAutoUpdate } from "../updater/useAutoUpdate";
import { KnowledgeRepositoryPanel } from "./KnowledgeRepositoryPanel";
import { NativeCaptureBanner } from "./NativeCaptureBanner";
import { VideoCaptionPanel } from "./VideoCaptionPanel";
import { PdfTranslatePanel } from "./PdfTranslatePanel";
import { consoleStyles } from "./consoleStyles";
import type { ConsoleView } from "./types";

export function DesktopConsole() {
  const [activeView, setActiveView] = useState<ConsoleView>("setup");
  // P2: gate first render until the keychain server address is hydrated into
  // localStorage, so every apiBase() consumer resolves the authoritative host
  // before any console interaction is possible.
  const [hydrated, setHydrated] = useState(false);
  // C5: operator JWT lifted to shared state so it survives view-switching.
  // NOT persisted to disk — a stolen laptop must not grant standing corpus access.
  const [operatorToken, setOperatorToken] = useState<string | null>(null);
  // App version from the Tauri bundle (tauri.conf.json). Best-effort: outside the
  // Tauri runtime (e.g. tests) getVersion rejects and the version line stays hidden.
  const [appVersion, setAppVersion] = useState<string>("");
  // Background auto-update: silent check/download, restart-to-apply banner.
  const update = useAutoUpdate();

  useEffect(() => {
    installAppLogCapture();
  }, []);

  useEffect(() => {
    getVersion()
      .then(setAppVersion)
      .catch(() => {
        /* version is a cosmetic footer; ignore failures */
      });
  }, []);

  useEffect(() => {
    void hydrateServerAddressFromKeychain().finally(() => setHydrated(true));
  }, []);

  if (!hydrated) {
    return (
      <div style={consoleStyles.page}>
        <main style={consoleStyles.content}>
          <p style={{ color: "#94a3b8", fontSize: 13 }}>설정을 불러오는 중... (Loading...)</p>
        </main>
      </div>
    );
  }

  return (
    <div style={consoleStyles.page}>
      <ConsoleNav
        activeView={activeView}
        onChange={setActiveView}
        appVersion={appVersion}
        updateBanner={
          <UpdateBanner status={update.status} onCheckNow={update.checkNow} onApplyNow={update.applyNow} />
        }
      />
      <main style={consoleStyles.content}>
        <NativeCaptureBanner />
        <section
          hidden={activeView !== "setup"}
          style={activeView === "setup" ? consoleStyles.sectionFill : undefined}
        >
          <SetupAssistant />
        </section>
        <section
          hidden={activeView !== "help"}
          style={activeView === "help" ? consoleStyles.sectionScroll : undefined}
        >
          <HelpManualPanel />
        </section>
        <section
          hidden={activeView !== "settings"}
          style={activeView === "settings" ? consoleStyles.sectionScroll : undefined}
        >
          <SettingsPanel />
        </section>
        <section
          hidden={activeView !== "history"}
          style={activeView === "history" ? consoleStyles.sectionFill : undefined}
        >
          <KnowledgeRepositoryPanel
            operatorToken={operatorToken}
            onTokenAcquired={setOperatorToken}
          />
        </section>
        <section hidden={activeView !== "video"}
          style={activeView === "video" ? consoleStyles.sectionScroll : undefined}>
          <VideoCaptionPanel
            active={activeView === "video"}
          />
        </section>
        <section hidden={activeView !== "pdf"}
          style={activeView === "pdf" ? consoleStyles.sectionScroll : undefined}>
          <PdfTranslatePanel active={activeView === "pdf"} />
        </section>
        <section hidden={activeView !== "xsheet"}
          style={activeView === "xsheet" ? consoleStyles.sectionScroll : undefined}>
          <PdfTranslatePanel active={activeView === "xsheet"} format="xsheet" />
        </section>
      </main>
    </div>
  );
}
// === ANCHOR: DESKTOP_CONSOLE_END ===
