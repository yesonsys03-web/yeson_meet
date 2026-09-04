// === ANCHOR: CONSOLE_NAV_START ===
import type { ReactNode } from "react";
import { consoleStyles } from "./consoleStyles";
import type { PdfFeatures } from "./pdfApi";
import type { ConsoleView } from "./types";

type ConsoleNavProps = {
  activeView: ConsoleView;
  onChange: (view: ConsoleView) => void;
  appVersion?: string;
  updateBanner?: ReactNode;
  pdfFeatures?: PdfFeatures;
};

type NavItem = { view: ConsoleView; label: string; disabled?: boolean };

const navItems: NavItem[] = [
  { view: "setup", label: "미팅 시작" },
  { view: "settings", label: "Settings" },
  { view: "help", label: "Help Manual" },
  { view: "history", label: "회의 기록" },
  { view: "video", label: "자막 메이커" },
  { view: "pdf", label: "스토리보드 번역" },
  { view: "xsheet", label: "Xsheet 번역" },
];

const DISABLED_TITLE = "서버 운영자가 비활성화한 기능입니다";

/** 운영자가 끈 PDF 포맷은 버튼을 남겨 두되 회색으로 잠근다(숨기지 않는다). */
function isDisabled(item: NavItem, pdfFeatures?: PdfFeatures): boolean {
  if (item.view === "pdf") return pdfFeatures?.storyboard === false;
  if (item.view === "xsheet") return pdfFeatures?.xsheet === false;
  return Boolean(item.disabled);
}

export function ConsoleNav({ activeView, onChange, appVersion, updateBanner,
  pdfFeatures }: ConsoleNavProps) {
  return (
    <aside style={consoleStyles.sidebar}>
      <p style={consoleStyles.brand}>yeson-meet operator</p>
      {appVersion ? <p style={consoleStyles.version}>v{appVersion}</p> : null}
      {updateBanner ?? null}
      <nav style={consoleStyles.nav} aria-label="Desktop console sections">
        {navItems.map((item) => {
          const disabled = isDisabled(item, pdfFeatures);
          return (
            // disabled 속성은 포인터 이벤트를 끊어 title 툴팁이 안 뜬다(Chromium·
            // WebView2) — aria-disabled + 클릭 무시로 잠그고 이유는 툴팁으로 남긴다.
            <button
              key={item.view}
              type="button"
              aria-disabled={disabled || undefined}
              title={disabled ? DISABLED_TITLE : undefined}
              onClick={() => { if (!disabled) onChange(item.view); }}
              style={{
                ...consoleStyles.navButton,
                ...(activeView === item.view ? consoleStyles.navButtonActive : null),
                ...(disabled ? consoleStyles.navButtonDisabled : null),
              }}
            >
              {item.label}
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
// === ANCHOR: CONSOLE_NAV_END ===
