// === ANCHOR: LIVE_SUBTITLE_PREVIEW_START ===
import { consoleStyles } from "./consoleStyles";
import { useLiveSubtitleStream } from "./useLiveSubtitleStream";

type LiveSubtitlePreviewProps = {
  operatorToken: string;
  sessionId: string | null;
};

export function LiveSubtitlePreview({ operatorToken, sessionId }: LiveSubtitlePreviewProps) {
  const stream = useLiveSubtitleStream(sessionId, operatorToken);

  if (!sessionId) {
    return (
      <section style={consoleStyles.subtitlePanel}>
        <strong>Live subtitles</strong>
        <p style={consoleStyles.subtitleEmpty}>회의를 시작하면 live subtitle preview가 연결됩니다.</p>
      </section>
    );
  }

  if (!operatorToken) {
    return (
      <section style={consoleStyles.subtitlePanel}>
        <strong>Live subtitles</strong>
        <p style={consoleStyles.subtitleEmpty}>Operator token이 있어야 live subtitle preview가 연결됩니다.</p>
      </section>
    );
  }

  return (
    <section style={consoleStyles.subtitlePanel}>
      <div style={consoleStyles.subtitleHeader}>
        <strong>Live subtitles</strong>
        <span style={stream.connected ? consoleStyles.liveBadge : consoleStyles.idleBadge}>
          {stream.ended ? "ended" : stream.connected ? "live" : "connecting"}
        </span>
      </div>
      {stream.latest ? (
        <div style={consoleStyles.subtitleText}>{stream.latest.text_ko || stream.latest.text_en}</div>
      ) : (
        <p style={consoleStyles.subtitleEmpty}>아직 수신한 자막이 없습니다.</p>
      )}
      {stream.error ? <p style={consoleStyles.subtitleError}>{stream.error}</p> : null}
      <p style={consoleStyles.subtitleMeta}>최근 {stream.utterances.length}개 발화 보관 · partial/final은 같은 seq로 교체</p>
    </section>
  );
}
// === ANCHOR: LIVE_SUBTITLE_PREVIEW_END ===
