// === ANCHOR: LIVE_SUBTITLE_PREVIEW_START ===
import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";

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
  const subtitleText = stream.latest?.text_ko || stream.latest?.text_en || "";
  const subtitleFit = useFullscreenSubtitleFit(subtitleText, fullscreen.isFullscreen);
  const textStyle = {
    ...consoleStyles.subtitleText,
    ...(fullscreen.isFullscreen ? consoleStyles.subtitleTextFullscreen : null),
    ...(fullscreen.isFullscreen ? subtitleFit.style : null),
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
      {!fullscreen.isFullscreen ? (
        <SubtitleHeader
          isFullscreen={fullscreen.isFullscreen}
          onToggleFullscreen={fullscreen.toggleFullscreen}
          status={stream.ended ? "ended" : stream.connected ? "live" : "connecting"}
        />
      ) : null}
      {stream.latest ? (
        <div ref={subtitleFit.ref} style={textStyle}>{subtitleText}</div>
      ) : (
        <p style={consoleStyles.subtitleEmpty}>아직 수신한 자막이 없습니다.</p>
      )}
      {stream.error ? <p style={consoleStyles.subtitleError}>{stream.error}</p> : null}
      {!fullscreen.isFullscreen ? <p style={consoleStyles.subtitleMeta}>최근 {stream.utterances.length}개 발화 보관 · partial/final은 같은 seq로 교체</p> : null}
    </section>
  );
}

function useFullscreenSubtitleFit(text: string, enabled: boolean): { ref: (node: HTMLDivElement | null) => void; style: CSSProperties | null } {
  const elementRef = useRef<HTMLDivElement | null>(null);
  const [fontSize, setFontSize] = useState<number | null>(null);
  const ref = useCallback((node: HTMLDivElement | null) => {
    elementRef.current = node;
  }, []);

  useLayoutEffect(() => {
    if (!enabled || !text) {
      setFontSize(null);
      return;
    }

    const element = elementRef.current;
    if (!element) return;

    let frame = 0;
    const fitText = () => {
      const widthLimit = window.innerWidth * 0.9;
      const heightLimit = window.innerHeight * 0.9;
      let low = 24;
      let high = Math.min(260, Math.max(96, window.innerHeight * 0.42));
      let best = low;
      const previousFontSize = element.style.fontSize;

      for (let index = 0; index < 9; index += 1) {
        const next = Math.floor((low + high) / 2);
        element.style.fontSize = `${next}px`;
        const fits = element.scrollWidth <= widthLimit + 1 && element.scrollHeight <= heightLimit + 1;
        if (fits) {
          best = next;
          low = next + 1;
        } else {
          high = next - 1;
        }
      }

      element.style.fontSize = previousFontSize;
      setFontSize(best);
    };

    const scheduleFit = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(fitText);
    };

    scheduleFit();
    window.addEventListener("resize", scheduleFit);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", scheduleFit);
    };
  }, [enabled, text]);

  if (!enabled || fontSize === null) return { ref, style: null };
  return {
    ref,
    style: {
      fontSize,
      lineHeight: fontSize >= 120 ? 1.08 : fontSize >= 72 ? 1.14 : 1.18,
    },
  };
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
