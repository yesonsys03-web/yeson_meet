// === ANCHOR: APP_START ===
import { DesktopConsole } from "./console/DesktopConsole";
import { QrFullscreenWindow } from "./console/QrFullscreenWindow";
import { SubtitleFullscreenWindow } from "./console/SubtitleFullscreenWindow";
import { isQrWindowRoute } from "./console/useQrFullscreenShortcut";
import { isSubtitleWindowRoute } from "./console/useSubtitleFullscreenShortcut";

export default function App() {
  if (isSubtitleWindowRoute()) return <SubtitleFullscreenWindow />;
  if (isQrWindowRoute()) return <QrFullscreenWindow />;
  return <DesktopConsole />;
}
// === ANCHOR: APP_END ===
