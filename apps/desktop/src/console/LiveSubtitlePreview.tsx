// === ANCHOR: LIVE_SUBTITLE_PREVIEW_START ===
import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";

import { CaptureLevelMeter } from "./CaptureLevelMeter";
import { CaptureStatusChip } from "./CaptureStatusChip";
import { useCaptureLevel } from "./captureLevel";
import { useCaptureStatus, type CaptureState } from "./captureStatus";
import { consoleStyles } from "./consoleStyles";
import type { UtteranceTranscribed } from "./types";
import { useLiveSubtitleStream } from "./useLiveSubtitleStream";
import { usePacedSubtitle } from "./usePacedSubtitle";
import { useSubtitleFullscreenShortcut } from "./useSubtitleFullscreenShortcut";

type LiveSubtitlePreviewProps = {
  operatorToken: string;
  sessionId: string | null;
  windowMode?: boolean;
  meetingStartLabel?: string;
  meetingEndLabel?: string;
};

// 표시 모드 — "sentence"(기본): 확정(final) 자막만 표시해 완성된 문장이 한 번에
// 떠서 머문다(읽기 안정). "live": 파셜 포함, 지금 말하는 문장이 실시간으로
// 자란다(빠르지만 읽던 줄이 계속 바뀜). 별도 창(F 전체화면)도 localStorage로
// 같은 모드를 공유한다.
type SubtitleDisplayMode = "sentence" | "live";
const DISPLAY_MODE_KEY = "yeson.subtitleDisplayMode";

function loadDisplayMode(): SubtitleDisplayMode {
  try {
    return localStorage.getItem(DISPLAY_MODE_KEY) === "live" ? "live" : "sentence";
  } catch {
    return "sentence";
  }
}

export function LiveSubtitlePreview({ operatorToken, sessionId, windowMode = false, meetingStartLabel, meetingEndLabel }: LiveSubtitlePreviewProps) {
  const stream = useLiveSubtitleStream(sessionId, operatorToken);
  const captureStatus = useCaptureStatus();
  const captureLevel = useCaptureLevel();
  const fullscreen = useSubtitleFullscreenShortcut({ operatorToken, sessionId, windowMode });
  const panelStyle = {
    ...consoleStyles.subtitlePanel,
    ...(fullscreen.isFullscreen ? consoleStyles.subtitlePanelFullscreen : null),
  };
  const [displayMode, setDisplayMode] = useState<SubtitleDisplayMode>(loadDisplayMode);
  const toggleDisplayMode = useCallback(() => {
    setDisplayMode((mode) => {
      const next: SubtitleDisplayMode = mode === "sentence" ? "live" : "sentence";
      try {
        localStorage.setItem(DISPLAY_MODE_KEY, next);
      } catch {
        // localStorage 불가 환경이면 이번 세션에만 적용
      }
      return next;
    });
  }, []);
  // sentence 모드는 확정 자막만 페이서에 공급 — partial의 제자리 갱신이 사라져
  // 완성 문장이 통째로 뜬다. live 모드는 기존처럼 전체(파셜 포함).
  const visibleUtterances =
    displayMode === "sentence"
      ? stream.utterances.filter((item) => item.is_final)
      : stream.utterances;
  // Paced display — 들어온 모든 발화(seq)를 순서대로, 글자수 비례 읽기시간만큼
  // 보여준다(누락 0). 밀리면 표시시간을 압축해 따라잡되 건너뛰지 않는다.
  const latest = usePacedSubtitle(visibleUtterances);
  const previous = previousSubtitle(visibleUtterances, latest?.seq ?? null);
  const subtitleText = latest?.text_ko || latest?.text_en || "";
  const previousSubtitleText = previous?.text_ko || previous?.text_en || "";
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
          captureStatus={captureStatus}
          level={captureLevel}
          meetingStartLabel={meetingStartLabel}
          meetingEndLabel={meetingEndLabel}
          displayMode={displayMode}
          onToggleDisplayMode={toggleDisplayMode}
        />
      ) : null}
      {stream.providerError ? (
        <p style={consoleStyles.subtitleError}>
          ⚠ AI 번역 서비스 오류 — 자막을 생성할 수 없습니다. Gemini 결제·사용량·API 키를 확인하세요. ({stream.providerError})
        </p>
      ) : null}
      {latest ? (
        <div style={consoleStyles.subtitleStack}>
          {previousSubtitleText ? (
            <div style={fullscreen.isFullscreen ? consoleStyles.subtitleContextFullscreen : consoleStyles.subtitleContext}>
              {previousSubtitleText}
            </div>
          ) : null}
          <div ref={subtitleFit.ref} style={textStyle}>{subtitleText}</div>
        </div>
      ) : (
        <p style={consoleStyles.subtitleEmpty}>아직 수신한 자막이 없습니다.</p>
      )}
      {stream.error ? <p style={consoleStyles.subtitleError}>{stream.error}</p> : null}
      {!fullscreen.isFullscreen ? <p style={consoleStyles.subtitleMeta}>최근 {stream.utterances.length}개 발화 보관 · partial/final은 같은 seq로 교체</p> : null}
    </section>
  );
}

function previousSubtitle(utterances: UtteranceTranscribed[], latestSeq: number | null): UtteranceTranscribed | null {
  if (utterances.length < 2) return null;
  if (latestSeq === null) return null;
  for (let index = utterances.length - 2; index >= 0; index -= 1) {
    const item = utterances[index];
    if (item && item.seq !== latestSeq) return item;
  }
  return null;
}

// Fixed fullscreen caption size (≈40pt at 96dpi). Stable per-line size — no
// per-caption resizing — for readability across a meeting room (~3m). Long
// captions wrap to more lines at this size; the fit logic only shrinks BELOW it
// when even wrapping would overflow the screen, so a caption is never clipped.
const FULLSCREEN_SUBTITLE_TARGET_PX = 80;
const FULLSCREEN_SUBTITLE_MIN_PX = 24;

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
      // Fixed target; only search DOWNWARD from it so the size stays constant for
      // normal captions and merely shrinks for the rare caption too long to fit.
      let low = FULLSCREEN_SUBTITLE_MIN_PX;
      let high = FULLSCREEN_SUBTITLE_TARGET_PX;
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
  captureStatus = null,
  level = null,
  meetingStartLabel,
  meetingEndLabel,
  displayMode,
  onToggleDisplayMode,
}: {
  isFullscreen: boolean;
  onToggleFullscreen: () => Promise<void>;
  status: "idle" | "connecting" | "live" | "ended";
  captureStatus?: CaptureState | null;
  level?: number | null;
  meetingStartLabel?: string;
  meetingEndLabel?: string;
  displayMode?: SubtitleDisplayMode;
  onToggleDisplayMode?: () => void;
}) {
  return (
    <div style={consoleStyles.subtitleHeader}>
      <div style={consoleStyles.subtitleHeadingGroup}>
        <strong>Live subtitles</strong>
        <span style={consoleStyles.subtitleShortcutHint}>F 자막 전용 전체화면 · Esc/F 종료</span>
        {meetingStartLabel && meetingStartLabel !== "-" ? (
          <span style={consoleStyles.subtitleMeetingTimes}>
            시작 {meetingStartLabel} · 종료 {meetingEndLabel ?? "-"}
          </span>
        ) : null}
      </div>
      <div style={consoleStyles.subtitleHeaderActions}>
        {captureStatus ? <CaptureStatusChip state={captureStatus} /> : null}
        {captureStatus ? <CaptureLevelMeter dbfs={level} state={captureStatus} /> : null}
        {displayMode && onToggleDisplayMode ? (
          <button
            type="button"
            onClick={onToggleDisplayMode}
            style={consoleStyles.subtitleFullscreenButton}
            title="문장 단위: 완성된 문장만 표시(읽기 안정) · 라이브: 말하는 도중 자막이 실시간으로 자람"
          >
            {displayMode === "sentence" ? "표시: 문장 단위" : "표시: 라이브"}
          </button>
        ) : null}
        <button type="button" onClick={() => void onToggleFullscreen()} style={consoleStyles.subtitleFullscreenButton}>
          {isFullscreen ? "전체화면 종료" : "전체화면"}
        </button>
        <span style={status === "live" ? consoleStyles.liveBadge : consoleStyles.idleBadge}>{status}</span>
      </div>
    </div>
  );
}
// === ANCHOR: LIVE_SUBTITLE_PREVIEW_END ===
