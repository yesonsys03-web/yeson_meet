// === ANCHOR: DESKTOP_CONSOLE_START ===
import { useEffect, useState } from "react";
import { installAppLogCapture } from "../diagnostics/appLog";
import { hydrateServerAddressFromKeychain } from "../setup/credentials";
import { HelpManualPanel } from "../help/HelpManualPanel";
import { SettingsPanel } from "../settings/SettingsPanel";
import { SetupAssistant } from "../setup/SetupAssistant";
import { ConsoleNav } from "./ConsoleNav";
import { NativeCaptureBanner } from "./NativeCaptureBanner";
import { consoleStyles } from "./consoleStyles";
import type { ConsoleView } from "./types";

export function DesktopConsole() {
  const [activeView, setActiveView] = useState<ConsoleView>("setup");
  // P2: gate first render until the keychain server address is hydrated into
  // localStorage, so every apiBase() consumer resolves the authoritative host
  // before any console interaction is possible.
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    installAppLogCapture();
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
      <ConsoleNav activeView={activeView} onChange={setActiveView} />
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
      </main>
    </div>
  );
}
// === ANCHOR: DESKTOP_CONSOLE_END ===
