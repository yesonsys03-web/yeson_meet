// === ANCHOR: APP_START ===
import { DesktopConsole } from "./console/DesktopConsole";
import { SubtitleFullscreenWindow } from "./console/SubtitleFullscreenWindow";
import { isSubtitleWindowRoute } from "./console/useSubtitleFullscreenShortcut";

export default function App() {
  if (isSubtitleWindowRoute()) return <SubtitleFullscreenWindow />;
  return <DesktopConsole />;
}
// === ANCHOR: APP_END ===
