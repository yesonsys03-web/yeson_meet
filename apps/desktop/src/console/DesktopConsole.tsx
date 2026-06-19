// === ANCHOR: DESKTOP_CONSOLE_START ===
import { useEffect, useState } from "react";
import { installAppLogCapture } from "../diagnostics/appLog";
import { loginOperator } from "./sessionApi";
import { hydrateServerAddressFromKeychain, loadOperatorLogin } from "../setup/credentials";
import { HelpManualPanel } from "../help/HelpManualPanel";
import { SettingsPanel } from "../settings/SettingsPanel";
import { SetupAssistant } from "../setup/SetupAssistant";
import { ConsoleNav } from "./ConsoleNav";
import { DeviceList } from "./DeviceList";
import { NativeCaptureBanner } from "./NativeCaptureBanner";
import { consoleStyles } from "./consoleStyles";
import type { ConsoleView } from "./types";

export function DesktopConsole() {
  const [activeView, setActiveView] = useState<ConsoleView>("setup");
  const [deviceAdminToken, setDeviceAdminToken] = useState<string | null>(null);
  const [deviceTokenError, setDeviceTokenError] = useState<string | null>(null);
  // P2: gate first render until the keychain server address is hydrated into
  // localStorage, so every apiBase() consumer (incl. the Devices-view login below)
  // resolves the authoritative host before any console interaction is possible.
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    installAppLogCapture();
  }, []);

  useEffect(() => {
    void hydrateServerAddressFromKeychain().finally(() => setHydrated(true));
  }, []);

  // Lazily acquire an admin token when the Devices view is opened.
  useEffect(() => {
    if (!hydrated || activeView !== "devices" || deviceAdminToken) return;
    setDeviceTokenError(null);
    loadOperatorLogin()
      .then((login) => loginOperator(login.email, login.password))
      .then((tokens) => setDeviceAdminToken(tokens.access_token))
      .catch((err: unknown) => setDeviceTokenError(err instanceof Error ? err.message : String(err)));
  }, [hydrated, activeView, deviceAdminToken]);

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
          hidden={activeView !== "devices"}
          style={activeView === "devices" ? consoleStyles.sectionScroll : undefined}
        >
          {/* === ANCHOR: DESKTOP_CONSOLE_DEVICELIST_START === */}
          <div style={consoleStyles.panel}>
            {deviceTokenError && (
              <p style={consoleStyles.statusError}>{deviceTokenError}</p>
            )}
            {!deviceTokenError && deviceAdminToken && (
              <DeviceList adminToken={deviceAdminToken} />
            )}
            {!deviceTokenError && !deviceAdminToken && (
              <p style={{ color: "#94a3b8", fontSize: 13 }}>로그인 중... (Authenticating...)</p>
            )}
          </div>
          {/* === ANCHOR: DESKTOP_CONSOLE_DEVICELIST_END === */}
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
