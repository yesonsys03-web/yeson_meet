// === ANCHOR: DESKTOP_CONSOLE_START ===
import { useEffect, useState } from "react";
import { installAppLogCapture } from "../diagnostics/appLog";
import { HelpManualPanel } from "../help/HelpManualPanel";
import { SettingsPanel } from "../settings/SettingsPanel";
import { SetupAssistant } from "../setup/SetupAssistant";
import { ConsoleNav } from "./ConsoleNav";
import { MeetingLifecyclePanel } from "./MeetingLifecyclePanel";
import { consoleStyles } from "./consoleStyles";
import type { ConsoleView } from "./types";

export function DesktopConsole() {
  const [activeView, setActiveView] = useState<ConsoleView>("setup");

  useEffect(() => {
    installAppLogCapture();
  }, []);

  return (
    <div style={consoleStyles.page}>
      <ConsoleNav activeView={activeView} onChange={setActiveView} />
      <main style={consoleStyles.content}>
        <section hidden={activeView !== "setup"}>
          <SetupAssistant />
        </section>
        <section hidden={activeView !== "meet"}>
          <MeetingLifecyclePanel />
        </section>
        <section hidden={activeView !== "help"}>
          <HelpManualPanel />
        </section>
        <section hidden={activeView !== "settings"}>
          <SettingsPanel />
        </section>
      </main>
    </div>
  );
}
// === ANCHOR: DESKTOP_CONSOLE_END ===
