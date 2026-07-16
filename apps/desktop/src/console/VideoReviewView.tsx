import { useCallback, useEffect, useRef, useState } from "react";
import { consoleStyles } from "./consoleStyles";
import {
  burnVideoJob, getVideoJob, listTranslateEngines, patchSegments, retranslateSegments,
  videoDownloadUrl, videoMediaUrl,
} from "./videoApi";
import type { BurnStyle, TranslateEngineInfo, VideoJobDetail } from "./videoApi";
import { engineLabel } from "./VideoCaptionPanel";
import {
  activeSegmentIndex, isSourceCopy, overlayStyleFor, sanitizeFilename,
} from "./videoReviewLogic";
import { captionedFileName } from "./videoBatchOps";

type VideoReviewViewProps = {
  jobId: string;
  onBack: () => void;
};

function fmtMs(ms: number): string {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

type TauriGlobal = typeof globalThis & { __TAURI_INTERNALS__?: unknown };

function hasTauriRuntime(): boolean {
  return Boolean((globalThis as TauriGlobal).__TAURI_INTERNALS__);
}

export function VideoReviewView({ jobId, onBack }: VideoReviewViewProps) {
  const [job, setJob] = useState<VideoJobDetail | null>(null);
  const [edits, setEdits] = useState<Record<number, string>>({});
  const [style, setStyle] = useState<BurnStyle>({
    position: "bottom", margin_v: 40, font_size: 18, color: "#ffffff",
  });
  // 숫자 입력칸은 타이핑 중간값("2" 등)을 허용해야 하므로 문자열로 따로 들고,
  // 유효 범위 값일 때만 style에 반영하고 blur 시점에 클램프·정규화한다.
  const [marginText, setMarginText] = useState("40");
  const [fontSizeText, setFontSizeText] = useState("18");
  const [currentMs, setCurrentMs] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<null | "video" | "srt">(null);
  const [videoHeight, setVideoHeight] = useState(0);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [engines, setEngines] = useState<TranslateEngineInfo[]>([]);
  // 로컬 엔진 재시도는 같은 실패가 반복될 수 있으므로 기본값은 작업이 쓰던
  // 엔진이 아니라 Claude 구독이다.
  const [retProvider, setRetProvider] = useState("claude");
  const [retCliModel, setRetCliModel] = useState("");
  const [retranslating, setRetranslating] = useState(false);

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
      setJob(await getVideoJob(jobId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [jobId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // burning 상태 동안 3초 폴링
  useEffect(() => {
    if (job?.status !== "burning") return;
    const timer = setInterval(() => void refresh(), 3000);
    return () => clearInterval(timer);
  }, [job?.status, refresh]);

  useEffect(() => {
    void listTranslateEngines().then(setEngines).catch(() => setEngines([]));
  }, []);

  // 기본값 claude가 이 서버에 없으면 쓸 수 있는 첫 엔진으로 폴백 —
  // VideoCaptionPanel:201-207이 같은 이유로 하는 것(그쪽은 자기 기본값인
  // gemini "")의 결과보기 판이다. 없으면 사용자가 "Claude 구독 (서버에 미설치)"가
  // 선택된 채인 버튼을 눌러 서버 409만 받는다.
  // 폴백 대상이 gemini 고정이 아닌 이유: 이 엔드포인트는 provider를 그대로 받아
  // ""가 없고, gemini도 API 키가 없으면 미가용일 수 있다.
  useEffect(() => {
    if (engines.length === 0) return;
    const cur = engines.find((e) => e.value === retProvider);
    if (cur && cur.available) return;
    const first = engines.find((e) => e.available);
    if (first) setRetProvider(first.value);
  }, [engines, retProvider]);

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

  const untranslatedSeqs = new Set(
    segments.filter((s) => isSourceCopy(s.text_en, koOf(s.seq, s.text_ko)))
            .map((s) => s.seq));
  const retEngineOk = engines.some((e) => e.value === retProvider && e.available);
  const retranslateDisabled =
    untranslatedSeqs.size === 0 || retranslating || !retEngineOk;

  const saveEdits = async () => {
    const payload = Object.entries(edits).map(([seq, text_ko]) => ({
      seq: Number(seq), text_ko,
    }));
    if (payload.length === 0) return;
    await patchSegments(jobId, payload);
    await refresh();
    setEdits({});
  };

  const runRetranslate = async () => {
    setRetranslating(true);
    setError(null);
    setNotice(null);
    try {
      // ★파괴적 동작 전에 편집을 먼저 저장한다 — startBurn(148행)과 같은 이유.
      // 이게 없으면 (a) 미저장 편집이 refresh()로 조용히 날아가고,
      // (b) 배지는 koOf(편집 인식)라 방금 타이핑한 줄을 "처리됨"으로 보여주는데
      // 서버는 DB(text_ko == text_en)를 보므로 그 줄을 대상에 넣어 덮어쓴다.
      // 저장하면 DB == 클라 상태가 되어 배지와 서버 판정이 정확히 일치한다.
      // saveEdits는 편집이 없으면 즉시 반환하고, 내부에서 refresh + setEdits({})를 한다.
      await saveEdits();
      const r = await retranslateSegments(
        jobId, retProvider,
        retProvider === "opencode" ? retCliModel : undefined);
      setNotice(r.remaining > 0
        ? `${r.total}개 중 ${r.retranslated}개 해결, ${r.remaining}개 남음`
        : `${r.retranslated}개 재번역 완료`);
      // 서버가 text_ko를 바꿨으므로 다시 읽는다. refresh()는 이미 있는
      // useCallback — getVideoJob을 직접 부르지 않는다.
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRetranslating(false);
    }
  };

  const startBurn = async () => {
    setBusy(true);
    setError(null);
    try {
      await saveEdits();
      await burnVideoJob(jobId, style);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const download = async (kind: "video" | "srt") => {
    setError(null);
    setNotice(null);
    setDownloading(kind); // 즉시 클릭 피드백 (버튼 라벨/비활성화)
    // 전체를 try로 감싼다 — 예전엔 save()가 try 밖이라 저장 다이얼로그/권한 오류가
    // void로 삼켜져 "아무 반응 없음"으로 보였다. finally에서 상태를 항상 되돌린다.
    try {
      const safeTitle = sanitizeFilename(job.title);
      const suggestedName = kind === "srt" ? `${safeTitle}.srt` : captionedFileName(job.title);

      if (!hasTauriRuntime()) {
        // 브라우저 dev 폴백: 저장 위치를 고를 수 없는 기존 blob 다운로드 방식.
        const response = await fetch(videoDownloadUrl(jobId, kind));
        if (!response.ok) {
          setError(`다운로드 실패: HTTP ${response.status}`);
          return;
        }
        const blob = await response.blob();
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = suggestedName;
        a.click();
        URL.revokeObjectURL(a.href);
        setNotice(`다운로드됨: ${suggestedName}`);
        return;
      }

      // Tauri 경로: 저장 다이얼로그로 위치·파일명을 먼저 고른 뒤 파일을 내려받는다.
      const { save } = await import("@tauri-apps/plugin-dialog");
      const path = await save({
        defaultPath: suggestedName,
        filters: [{
          name: kind === "video" ? "MP4 video" : "SRT subtitle",
          extensions: [kind === "video" ? "mp4" : "srt"],
        }],
      });
      if (!path) {
        setNotice("저장이 취소되었습니다.");
        return;
      }

      // 대용량 MP4는 fetch+저장에 수 초 걸리므로 진행 중임을 알린다.
      setNotice(kind === "video" ? "영상을 내려받는 중…" : "자막을 내려받는 중…");
      const response = await fetch(videoDownloadUrl(jobId, kind));
      if (!response.ok) {
        setError(`다운로드 실패: HTTP ${response.status}`);
        return;
      }
      // 전체 파일을 메모리에 올린다(arrayBuffer). 수백 MB 수준까지는 무난하지만
      // 그 이상 대용량 영상은 v2에서 스트리밍 저장으로 개선 필요.
      const bytes = new Uint8Array(await response.arrayBuffer());
      const { writeFile } = await import("@tauri-apps/plugin-fs");
      await writeFile(path, bytes);
      setNotice(`저장됨: ${path}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDownloading(null);
    }
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
      {notice ? <p style={consoleStyles.statusInfo}>{notice}</p> : null}

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
              여백{" "}
              <input type="range" min={0} max={200} value={style.margin_v}
                onChange={(e) => {
                  setStyle({ ...style, margin_v: Number(e.target.value) });
                  setMarginText(e.target.value);
                }} />
              <input type="number" min={0} max={200} value={marginText}
                onChange={(e) => {
                  setMarginText(e.target.value);
                  const n = Number(e.target.value);
                  if (e.target.value !== "" && Number.isFinite(n) && n >= 0 && n <= 200) {
                    setStyle({ ...style, margin_v: Math.round(n) });
                  }
                }}
                onBlur={() => {
                  const n = Math.max(0, Math.min(200, Math.round(Number(marginText)) || 0));
                  setMarginText(String(n));
                  setStyle({ ...style, margin_v: n });
                }}
                style={{ ...consoleStyles.input, width: 64, marginLeft: 6, padding: "2px 6px" }} />
              px
            </label>
            <label>
              글자 크기{" "}
              <input type="range" min={10} max={48} value={style.font_size}
                onChange={(e) => {
                  setStyle({ ...style, font_size: Number(e.target.value) });
                  setFontSizeText(e.target.value);
                }} />
              <input type="number" min={10} max={48} value={fontSizeText}
                onChange={(e) => {
                  setFontSizeText(e.target.value);
                  const n = Number(e.target.value);
                  if (e.target.value !== "" && Number.isFinite(n) && n >= 10 && n <= 48) {
                    setStyle({ ...style, font_size: Math.round(n) });
                  }
                }}
                onBlur={() => {
                  const n = Math.max(10, Math.min(48, Math.round(Number(fontSizeText)) || 18));
                  setFontSizeText(String(n));
                  setStyle({ ...style, font_size: n });
                }}
                style={{ ...consoleStyles.input, width: 64, marginLeft: 6, padding: "2px 6px" }} />
              px
            </label>
            <label>
              색상{" "}
              <input type="color" value={style.color}
                onChange={(e) => setStyle({ ...style, color: e.target.value })} />
            </label>
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap", alignItems: "center" }}>
            <button type="button"
              style={{ ...consoleStyles.action, ...(burnDisabled ? consoleStyles.actionDisabled : null) }}
              disabled={burnDisabled}
              onClick={() => void startBurn()}>
              {job.status === "burning" ? `굽는 중… ${job.progress}%` : "이 스타일로 영상 굽기"}
            </button>
            {job.status === "burning" ? (
              <div style={{ flex: "1 1 160px", height: 4, borderRadius: 2,
                           background: "rgba(255,255,255,0.12)" }}>
                <div style={{ height: 4, borderRadius: 2, width: `${job.progress}%`,
                             background: "#4a9eda", transition: "width 0.5s" }} />
              </div>
            ) : null}
            {job.status === "done" ? (
              <>
                <button type="button"
                  style={{ ...consoleStyles.mutedAction, ...(downloading ? consoleStyles.actionDisabled : null) }}
                  disabled={downloading !== null}
                  onClick={() => void download("video")}>
                  {downloading === "video" ? "MP4 준비 중…" : "MP4 다운로드"}
                </button>
                <button type="button"
                  style={{ ...consoleStyles.mutedAction, ...(downloading ? consoleStyles.actionDisabled : null) }}
                  disabled={downloading !== null}
                  onClick={() => void download("srt")}>
                  {downloading === "srt" ? "SRT 준비 중…" : "SRT 다운로드"}
                </button>
              </>
            ) : null}
          </div>
        </div>

        {/* ---- 세그먼트 편집 리스트 ---- */}
        <div style={{ flex: "1 1 360px", maxHeight: 520, overflowY: "auto",
                      display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center",
                        flexWrap: "wrap", paddingBottom: 4 }}>
            <span style={{ fontSize: 12, opacity: 0.75 }}>
              {untranslatedSeqs.size > 0
                ? `영문 잔존 ${untranslatedSeqs.size}구간`
                : "영문 잔존 없음"}
            </span>
            <select
              style={{ ...consoleStyles.input, width: "auto", fontSize: 12 }}
              value={retProvider}
              disabled={retranslating}
              onChange={(e) => setRetProvider(e.target.value)}>
              {/* 라벨 규칙은 engineLabel이 단일 출처 — 여기서 다시 짜지 않는다.
                  gemini는 value를 ""로 바꾸지 않는다: toEngineOptions의 ""는
                  VideoCaptionPanel 상태 규약이고, 이 엔드포인트는 provider를
                  그대로 받으므로 ""는 검증 패턴에서 거부된다. */}
              {engines.map((e) => (
                <option key={e.value} value={e.value} disabled={!e.available}>
                  {engineLabel(e)}
                </option>
              ))}
            </select>
            {retProvider === "opencode" ? (
              <input
                style={{ ...consoleStyles.input, width: 180, fontSize: 12 }}
                value={retCliModel}
                disabled={retranslating}
                placeholder="모델명"
                onChange={(e) => setRetCliModel(e.target.value)} />
            ) : null}
            {/* disabled 조건에 엔진 가용성도 포함한다 — 서버가 미가용 엔진을
                409로 막지만(무인증 API라 서버 가드는 필수), UI가 누를 수 있게
                두면 사용자는 에러만 받는다. 폴백 effect가 보통 가용 엔진으로
                옮겨주므로 이 조건은 "가용 엔진이 하나도 없는 서버"의 backstop이다.
                스타일도 이 파일의 관례(굽기 317-322, 다운로드 332-343)대로
                actionDisabled를 병합한다. */}
            <button type="button"
              style={{ ...consoleStyles.mutedAction,
                       ...(retranslateDisabled ? consoleStyles.actionDisabled : null) }}
              disabled={retranslateDisabled}
              onClick={() => void runRetranslate()}>
              {retranslating ? "재번역 중…" : "영문 구간 일괄 재번역"}
            </button>
          </div>
          {segments.map((seg, idx) => (
            <div key={seg.seq}
              style={{ padding: "6px 10px", borderRadius: 6,
                       border: `1px solid ${
                         idx === activeIdx ? "rgba(48,164,108,0.9)"
                         : untranslatedSeqs.has(seg.seq) ? "rgba(230,145,56,0.9)"
                         : "rgba(255,255,255,0.12)"}` }}>
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
