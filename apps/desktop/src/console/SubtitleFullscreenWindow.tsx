// === ANCHOR: SUBTITLE_FULLSCREEN_WINDOW_START ===
import { LiveSubtitlePreview } from "./LiveSubtitlePreview";
import { consoleStyles } from "./consoleStyles";
import { subtitleWindowParams } from "./useSubtitleFullscreenShortcut";

export function SubtitleFullscreenWindow() {
  const { sessionId, operatorToken } = subtitleWindowParams();

  return (
    <main style={consoleStyles.subtitleWindowPage}>
      <LiveSubtitlePreview operatorToken={operatorToken} sessionId={sessionId} windowMode />
    </main>
  );
}
// === ANCHOR: SUBTITLE_FULLSCREEN_WINDOW_END ===
