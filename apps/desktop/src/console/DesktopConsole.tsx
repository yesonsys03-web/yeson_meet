// === ANCHOR: DESKTOP_CONSOLE_START ===
import { useState } from "react";
import { HelpManualPanel } from "../help/HelpManualPanel";
import { SetupAssistant } from "../setup/SetupAssistant";
import { ConsoleNav } from "./ConsoleNav";
import { MeetingLifecyclePanel } from "./MeetingLifecyclePanel";
import { consoleStyles } from "./consoleStyles";
import type { ConsoleView } from "./types";

export function DesktopConsole() {
  const [activeView, setActiveView] = useState<ConsoleView>("setup");

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
      </main>
    </div>
  );
}
// === ANCHOR: DESKTOP_CONSOLE_END ===
