// === ANCHOR: CONSOLE_NAV_START ===
import { consoleStyles } from "./consoleStyles";
import type { ConsoleView } from "./types";

type ConsoleNavProps = {
  activeView: ConsoleView;
  onChange: (view: ConsoleView) => void;
  appVersion?: string;
};

const navItems: Array<{ view: ConsoleView; label: string; disabled?: boolean }> = [
  { view: "setup", label: "미팅 시작" },
  { view: "settings", label: "Settings" },
  { view: "help", label: "Help Manual" },
  { view: "history", label: "회의 기록" },
  { view: "video", label: "자막 메이커" },
];

export function ConsoleNav({ activeView, onChange, appVersion }: ConsoleNavProps) {
  return (
    <aside style={consoleStyles.sidebar}>
      <p style={consoleStyles.brand}>yeson-meet operator</p>
      {appVersion ? <p style={consoleStyles.version}>v{appVersion}</p> : null}
      <nav style={consoleStyles.nav} aria-label="Desktop console sections">
        {navItems.map((item) => (
          <button
            key={item.view}
            type="button"
            disabled={item.disabled}
            onClick={() => onChange(item.view)}
            style={{
              ...consoleStyles.navButton,
              ...(activeView === item.view ? consoleStyles.navButtonActive : null),
              ...(item.disabled ? consoleStyles.navButtonDisabled : null),
            }}
          >
            {item.label}
          </button>
        ))}
      </nav>
    </aside>
  );
}
// === ANCHOR: CONSOLE_NAV_END ===
