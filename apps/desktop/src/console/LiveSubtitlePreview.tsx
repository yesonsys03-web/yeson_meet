// === ANCHOR: LIVE_SUBTITLE_PREVIEW_START ===
import { consoleStyles } from "./consoleStyles";
import { useLiveSubtitleStream } from "./useLiveSubtitleStream";
import { useSubtitleFullscreenShortcut } from "./useSubtitleFullscreenShortcut";

type LiveSubtitlePreviewProps = {
  operatorToken: string;
  sessionId: string | null;
  windowMode?: boolean;
};

export function LiveSubtitlePreview({ operatorToken, sessionId, windowMode = false }: LiveSubtitlePreviewProps) {
  const stream = useLiveSubtitleStream(sessionId, operatorToken);
  const fullscreen = useSubtitleFullscreenShortcut({ operatorToken, sessionId, windowMode });
  const panelStyle = {
    ...consoleStyles.subtitlePanel,
    ...(fullscreen.isFullscreen ? consoleStyles.subtitlePanelFullscreen : null),
  };
  const textStyle = {
    ...consoleStyles.subtitleText,
    ...(fullscreen.isFullscreen ? consoleStyles.subtitleTextFullscreen : null),
  };

  if (!sessionId) {
    return (
      <section style={panelStyle}>
        <SubtitleHeader isFullscreen={fullscreen.isFullscreen} onToggleFullscreen={fullscreen.toggleFullscreen} status="idle" />
        <p style={consoleStyles.subtitleEmpty}>회의를 시작하면 live subtitle preview가 연결됩니다.</p>
      </section>
    );
  }

  if (!operatorToken) {
    return (
      <section style={panelStyle}>
        <SubtitleHeader isFullscreen={fullscreen.isFullscreen} onToggleFullscreen={fullscreen.toggleFullscreen} status="idle" />
        <p style={consoleStyles.subtitleEmpty}>Operator token이 있어야 live subtitle preview가 연결됩니다.</p>
      </section>
    );
  }

  return (
    <section style={panelStyle}>
      <SubtitleHeader
        isFullscreen={fullscreen.isFullscreen}
        onToggleFullscreen={fullscreen.toggleFullscreen}
        status={stream.ended ? "ended" : stream.connected ? "live" : "connecting"}
      />
      {stream.latest ? (
        <div style={textStyle}>{stream.latest.text_ko || stream.latest.text_en}</div>
      ) : (
        <p style={consoleStyles.subtitleEmpty}>아직 수신한 자막이 없습니다.</p>
      )}
      {stream.error ? <p style={consoleStyles.subtitleError}>{stream.error}</p> : null}
      <p style={consoleStyles.subtitleMeta}>최근 {stream.utterances.length}개 발화 보관 · partial/final은 같은 seq로 교체</p>
    </section>
  );
}

function SubtitleHeader({
  isFullscreen,
  onToggleFullscreen,
  status,
}: {
  isFullscreen: boolean;
  onToggleFullscreen: () => Promise<void>;
  status: "idle" | "connecting" | "live" | "ended";
}) {
  return (
    <div style={consoleStyles.subtitleHeader}>
      <div style={consoleStyles.subtitleHeadingGroup}>
        <strong>Live subtitles</strong>
        <span style={consoleStyles.subtitleShortcutHint}>F 자막 전용 전체화면 · Esc/F 종료</span>
      </div>
      <div style={consoleStyles.subtitleHeaderActions}>
        <button type="button" onClick={() => void onToggleFullscreen()} style={consoleStyles.subtitleFullscreenButton}>
          {isFullscreen ? "전체화면 종료" : "전체화면"}
        </button>
        <span style={status === "live" ? consoleStyles.liveBadge : consoleStyles.idleBadge}>{status}</span>
      </div>
    </div>
  );
}
// === ANCHOR: LIVE_SUBTITLE_PREVIEW_END ===
