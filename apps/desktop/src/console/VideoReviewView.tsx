import { useCallback, useEffect, useRef, useState } from "react";
import { consoleStyles } from "./consoleStyles";
import {
  burnVideoJob, getVideoJob, patchSegments, videoDownloadUrl, videoMediaUrl,
} from "./videoApi";
import type { BurnStyle, VideoJobDetail } from "./videoApi";
import { activeSegmentIndex, overlayStyleFor } from "./videoReviewLogic";

type VideoReviewViewProps = {
  jobId: string;
  operatorToken: string;
  onBack: () => void;
};

function fmtMs(ms: number): string {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function VideoReviewView({ jobId, operatorToken, onBack }: VideoReviewViewProps) {
  const [job, setJob] = useState<VideoJobDetail | null>(null);
  const [edits, setEdits] = useState<Record<number, string>>({});
  const [style, setStyle] = useState<BurnStyle>({ position: "bottom", margin_v: 40, font_size: 18 });
  const [currentMs, setCurrentMs] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [videoHeight, setVideoHeight] = useState(0);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  // burn(ffmpeg subtitles 필터)이 SRT를 ASS로 변환할 때 PlayResY=288 기준
  // Fontsize/MarginV를 실제 렌더 높이로 스케일하므로, 미리보기도 실제 표시
  // 높이를 알아야 같은 좌표계로 오버레이를 배치할 수 있다.
  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const height = entries[0]?.contentRect.height;
      if (height) setVideoHeight(height);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const refresh = useCallback(async () => {
    try {
      setJob(await getVideoJob(jobId, operatorToken));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [jobId, operatorToken]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // burning 상태 동안 3초 폴링
  useEffect(() => {
    if (job?.status !== "burning") return;
    const timer = setInterval(() => void refresh(), 3000);
    return () => clearInterval(timer);
  }, [job?.status, refresh]);

  if (!job) {
    return (
      <div style={consoleStyles.panel}>
        {error ? <p style={{ color: "#e5484d" }}>{error}</p> : <p>불러오는 중…</p>}
        <button type="button" style={consoleStyles.mutedAction} onClick={onBack}>
          ← 목록으로
        </button>
      </div>
    );
  }

  const segments = job.segments;
  const koOf = (seq: number, fallback: string) => edits[seq] ?? fallback;
  const activeIdx = activeSegmentIndex(segments, currentMs);
  const activeSeg = activeIdx >= 0 ? segments[activeIdx] : undefined;
  const activeText = activeSeg ? koOf(activeSeg.seq, activeSeg.text_ko) : "";

  const saveEdits = async () => {
    const payload = Object.entries(edits).map(([seq, text_ko]) => ({
      seq: Number(seq), text_ko,
    }));
    if (payload.length === 0) return;
    await patchSegments(jobId, payload, operatorToken);
    await refresh();
    setEdits({});
  };

  const startBurn = async () => {
    setBusy(true);
    setError(null);
    try {
      await saveEdits();
      await burnVideoJob(jobId, style, operatorToken);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const download = async (kind: "video" | "srt") => {
    const response = await fetch(videoDownloadUrl(jobId, kind), {
      headers: { Authorization: `Bearer ${operatorToken}` },
    });
    if (!response.ok) {
      setError(`다운로드 실패: HTTP ${response.status}`);
      return;
    }
    const blob = await response.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = kind === "srt" ? `${job.title}.srt` : `${job.title}-captioned.mp4`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const burnDisabled = busy || job.status === "burning";

  return (
    <div style={{ ...consoleStyles.panel, display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <button type="button" style={consoleStyles.mutedAction} onClick={onBack}>
          ← 목록으로
        </button>
        <h2 style={{ ...consoleStyles.title, margin: 0, flex: 1 }}>{job.title}</h2>
        <span style={{ fontSize: 13, opacity: 0.75 }}>{job.whisper_model} 모델로 전사됨</span>
      </div>
      {error ? <p style={{ color: "#e5484d", margin: 0 }}>{error}</p> : null}

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
        {/* ---- 플레이어 + 자막 오버레이 ---- */}
        <div style={{ flex: "1 1 480px", minWidth: 360 }}>
          {/* 영상 전용 래퍼: 오버레이의 positioning context는 영상만 감싸야 함
              (컨트롤/버튼까지 포함하면 bottom/top 기준이 영상 프레임 밖으로 어긋남) */}
          <div style={{ position: "relative", lineHeight: 0 }}>
            <video
              ref={videoRef}
              src={videoMediaUrl(jobId)}
              controls
              style={{ width: "100%", borderRadius: 8, background: "#000" }}
              onTimeUpdate={(e) => setCurrentMs(e.currentTarget.currentTime * 1000)}
            />
            {activeText ? (
              <div style={overlayStyleFor(style, videoHeight)}>{activeText}</div>
            ) : null}
          </div>

          {/* ---- 자막 스타일 컨트롤 ---- */}
          <div style={{ display: "flex", gap: 16, marginTop: 8, alignItems: "center",
                        flexWrap: "wrap", fontSize: 13 }}>
            <label>
              위치{" "}
              <select value={style.position}
                onChange={(e) => setStyle({ ...style,
                  position: e.target.value as BurnStyle["position"] })}>
                <option value="bottom">하단</option>
                <option value="top">상단</option>
              </select>
            </label>
            <label>
              여백 {style.margin_v}px{" "}
              <input type="range" min={0} max={200} value={style.margin_v}
                onChange={(e) => setStyle({ ...style, margin_v: Number(e.target.value) })} />
            </label>
            <label>
              글자 크기 {style.font_size}px{" "}
              <input type="range" min={10} max={48} value={style.font_size}
                onChange={(e) => setStyle({ ...style, font_size: Number(e.target.value) })} />
            </label>
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
            <button type="button"
              style={{ ...consoleStyles.action, ...(burnDisabled ? consoleStyles.actionDisabled : null) }}
              disabled={burnDisabled}
              onClick={() => void startBurn()}>
              {job.status === "burning" ? "굽는 중…" : "이 스타일로 영상 굽기"}
            </button>
            {job.status === "done" ? (
              <>
                <button type="button" style={consoleStyles.mutedAction}
                  onClick={() => void download("video")}>
                  MP4 다운로드
                </button>
                <button type="button" style={consoleStyles.mutedAction}
                  onClick={() => void download("srt")}>
                  SRT 다운로드
                </button>
              </>
            ) : null}
          </div>
        </div>

        {/* ---- 세그먼트 편집 리스트 ---- */}
        <div style={{ flex: "1 1 360px", maxHeight: 520, overflowY: "auto",
                      display: "flex", flexDirection: "column", gap: 6 }}>
          {segments.map((seg, idx) => (
            <div key={seg.seq}
              style={{ padding: "6px 10px", borderRadius: 6,
                       border: `1px solid ${idx === activeIdx
                         ? "rgba(48,164,108,0.9)" : "rgba(255,255,255,0.12)"}` }}>
              <button type="button"
                style={{ background: "none", border: "none", color: "inherit",
                         cursor: "pointer", padding: 0, fontSize: 12, opacity: 0.7 }}
                onClick={() => {
                  if (videoRef.current) {
                    videoRef.current.currentTime = seg.start_ms / 1000;
                  }
                }}>
                {fmtMs(seg.start_ms)} → {fmtMs(seg.end_ms)}
              </button>
              <div style={{ fontSize: 12, opacity: 0.6 }}>{seg.text_en}</div>
              <textarea
                value={koOf(seg.seq, seg.text_ko)}
                onChange={(e) => setEdits({ ...edits, [seg.seq]: e.target.value })}
                rows={1}
                style={{ ...consoleStyles.input, width: "100%", resize: "vertical",
                         marginTop: 2 }}
              />
            </div>
          ))}
          {Object.keys(edits).length > 0 ? (
            <button type="button" style={consoleStyles.mutedAction}
              onClick={() => void saveEdits()}>
              수정 저장 ({Object.keys(edits).length}건)
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
