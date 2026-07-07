import { useCallback, useEffect, useRef, useState } from "react";
import { consoleStyles } from "./consoleStyles";
import {
  createYoutubeJob, deleteVideoJob, deleteVideoModel, downloadVideoModel,
  getVideoStorage, listTranslateEngines, listVideoJobs, listVideoModels, uploadVideoJob,
} from "./videoApi";
import type {
  TranslateEngineInfo, VideoJobSummary, VideoModelInfo, VideoStorageInfo,
} from "./videoApi";
import { filterVideoFiles, uploadBatch } from "./videoBatch";
import { VideoReviewView } from "./VideoReviewView";

const STATUS_LABEL: Record<string, string> = {
  queued: "대기 중", ingesting: "영상 가져오는 중", extracting: "오디오 추출 중",
  transcribing: "전사 중", translating: "번역 중", review: "검수 대기",
  burning: "영상 굽는 중", done: "완료", error: "오류",
};

const INFLIGHT_STATUSES = ["queued", "ingesting", "extracting", "transcribing",
                           "translating", "burning"];

type EngineOption = { value: string; label: string; available: boolean };

// 서버 응답이 오기 전 첫 렌더용 폴백 — 깜빡임 방지 (서버 미설치 상태를 알기 전이므로 전부 available)
const DEFAULT_ENGINE_OPTIONS: EngineOption[] = [
  { value: "", label: "번역: Gemini (기본)", available: true },
  { value: "claude", label: "번역: Claude 구독", available: true },
  { value: "codex", label: "번역: Codex 구독", available: true },
  { value: "agy", label: "번역: Antigravity", available: true },
  { value: "opencode", label: "번역: OpenCode (딥시크 등)", available: true },
];

// 서버의 gemini(값 없음=기본)를 클라 상태값 ""와 맞추고, 미설치 엔진은 disabled 처리
function toEngineOptions(engines: TranslateEngineInfo[]): EngineOption[] {
  return engines.map((engine) => {
    const isGemini = engine.value === "gemini";
    let label = `번역: ${engine.label}`;
    if (!engine.available) {
      label += isGemini ? " (서버에 키 없음)" : " (서버에 미설치)";
    }
    return {
      value: isGemini ? "" : engine.value,
      label,
      available: isGemini ? true : engine.available, // 기본값이므로 gemini는 항상 선택 허용
    };
  });
}

const DEFAULT_OPENCODE_MODEL = "opencode/deepseek-v4-flash-free";

function formatBytes(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}GB`;
  return `${Math.round(n / 1_000_000)}MB`;
}

type VideoCaptionPanelProps = {
  active: boolean;
};

export function VideoCaptionPanel({ active }: VideoCaptionPanelProps) {
  return <VideoCaptionInner active={active} />;
}

function VideoCaptionInner({ active }: { active: boolean }) {
  const [models, setModels] = useState<VideoModelInfo[]>([]);
  const [jobs, setJobs] = useState<VideoJobSummary[]>([]);
  const [storage, setStorage] = useState<VideoStorageInfo | null>(null);
  const [engineOptions, setEngineOptions] = useState<EngineOption[]>(DEFAULT_ENGINE_OPTIONS);
  const [selectedModel, setSelectedModel] = useState("base");
  const [translateProvider, setTranslateProvider] = useState("");
  const [cliModel, setCliModel] = useState(DEFAULT_OPENCODE_MODEL);
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reviewJobId, setReviewJobId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);
  const [batchStatus, setBatchStatus] = useState<string | null>(null);

  // webkitdirectory는 표준 타입에 없어 JSX 속성으로 못 준다 — 폴더 선택 input에
  // 직접 붙인다(WKWebView/WebView2 모두 지원). 폴더 안 모든 파일을 넘겨준다.
  useEffect(() => {
    const el = folderInputRef.current;
    if (el) {
      el.setAttribute("webkitdirectory", "");
      el.setAttribute("directory", "");
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [m, j, e, s] = await Promise.all(
        [listVideoModels(), listVideoJobs(), listTranslateEngines(), getVideoStorage()]);
      setModels(m);
      setJobs(j);
      setEngineOptions(toEngineOptions(e));
      setStorage(s);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  // 탭이 보이는 동안만 3초 폴링 (숨김 탭은 mount 유지되므로 active로 게이트)
  useEffect(() => {
    if (!active) return;
    void refresh();
    const timer = setInterval(() => void refresh(), 3000);
    return () => clearInterval(timer);
  }, [active, refresh]);

  const selectedInstalled = models.find((m) => m.name === selectedModel)?.downloaded;

  const submitYoutube = async () => {
    if (!youtubeUrl.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await createYoutubeJob({
        url: youtubeUrl.trim(), whisperModel: selectedModel,
        translateProvider: translateProvider || undefined,
        translateCliModel: translateProvider === "opencode" ? cliModel : undefined,
      });
      setYoutubeUrl("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  // 다중 파일/폴더 배치 업로드. 드롭다운의 모델·번역엔진을 전체에 동일 적용하고,
  // 순차로 업로드해 큐에 넣는다(실제 순차 처리는 서버 세마포어가 보장). 한 파일이
  // 실패해도 배치를 멈추지 않고 나머지를 계속 올린 뒤 결과를 요약해 보여준다.
  const submitFiles = async (files: File[]) => {
    if (files.length === 0) {
      setBatchStatus("업로드할 영상 파일이 없습니다.");
      return;
    }
    setBusy(true);
    setError(null);
    setBatchStatus(null);
    const cfg = {
      whisperModel: selectedModel,
      translateProvider: translateProvider || undefined,
      translateCliModel: translateProvider === "opencode" ? cliModel : undefined,
    };
    try {
      const res = await uploadBatch(files, cfg, uploadVideoJob, (done, total, current) => {
        setBatchStatus(done < total ? `업로드 중 ${done + 1}/${total} — ${current}` : null);
      });
      const parts = [`${res.ok}개 작업이 시작됐습니다 (순차 처리)`];
      if (res.failed.length) {
        parts.push(`${res.failed.length}개 실패: ${res.failed.map((x) => x.name).join(", ")}`);
      }
      setBatchStatus(parts.join(" · "));
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (reviewJobId) {
    return (
      <VideoReviewView jobId={reviewJobId}
        onBack={() => { setReviewJobId(null); void refresh(); }} />
    );
  }

  const youtubeDisabled = busy || !selectedInstalled || !youtubeUrl.trim();
  const fileDisabled = busy || !selectedInstalled;

  return (
    <div style={{ ...consoleStyles.panel, display: "flex", flexDirection: "column", gap: 20 }}>
      <h2 style={consoleStyles.title}>영상 자막</h2>
      <p style={consoleStyles.subtitle}>
        유튜브 URL 또는 로컬 동영상에 한국어 자막을 입혀 MP4로 익스포트합니다.
      </p>
      {error ? <p style={consoleStyles.statusError}>{error}</p> : null}

      {/* ---- 새 작업 ---- */}
      <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <h3 style={{ margin: 0 }}>새 작업</h3>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <input
            style={{ ...consoleStyles.input, flex: 1, minWidth: 260 }}
            placeholder="유튜브 URL 붙여넣기"
            value={youtubeUrl}
            onChange={(e) => setYoutubeUrl(e.target.value)}
          />
          <select
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
            style={{ ...consoleStyles.input, width: 160 }}
          >
            {models.map((m) => (
              <option key={m.name} value={m.name} disabled={!m.downloaded}>
                {m.name}{m.downloaded ? "" : " (미설치)"}
              </option>
            ))}
          </select>
          <select
            value={translateProvider}
            onChange={(e) => setTranslateProvider(e.target.value)}
            style={{ ...consoleStyles.input, width: 200 }}
          >
            {engineOptions.map((engine) => (
              <option key={engine.value} value={engine.value} disabled={!engine.available}>
                {engine.label}
              </option>
            ))}
          </select>
          {translateProvider === "opencode" ? (
            <input
              style={{ ...consoleStyles.input, width: 240 }}
              placeholder={DEFAULT_OPENCODE_MODEL}
              value={cliModel}
              onChange={(e) => setCliModel(e.target.value)}
            />
          ) : null}
          <button type="button"
            style={{ ...consoleStyles.action, ...(youtubeDisabled ? consoleStyles.actionDisabled : null) }}
            disabled={youtubeDisabled}
            onClick={() => void submitYoutube()}>
            유튜브로 시작
          </button>
          <button type="button"
            style={{ ...consoleStyles.mutedAction, ...(fileDisabled ? consoleStyles.actionDisabled : null) }}
            disabled={fileDisabled}
            onClick={() => fileInputRef.current?.click()}>
            로컬 파일 선택…
          </button>
          <button type="button"
            style={{ ...consoleStyles.mutedAction, ...(fileDisabled ? consoleStyles.actionDisabled : null) }}
            disabled={fileDisabled}
            onClick={() => folderInputRef.current?.click()}>
            폴더 선택…
          </button>
          {/* 다중 선택 지원 — 고른 파일 전부를 순차 업로드 */}
          <input ref={fileInputRef} type="file" accept="video/*" multiple hidden
            onChange={(e) => {
              void submitFiles(Array.from(e.target.files ?? []));
              e.target.value = "";
            }} />
          {/* 폴더 선택(webkitdirectory는 effect에서 부착) — 폴더 내 영상만 골라 순차 업로드 */}
          <input ref={folderInputRef} type="file" hidden
            onChange={(e) => {
              void submitFiles(filterVideoFiles(Array.from(e.target.files ?? [])));
              e.target.value = "";
            }} />
        </div>
        {batchStatus ? (
          <p style={{ margin: 0, fontSize: 13, opacity: 0.85 }}>{batchStatus}</p>
        ) : null}
        {!selectedInstalled ? (
          <p style={{ margin: 0, fontSize: 13, opacity: 0.75 }}>
            선택한 모델이 설치되어 있지 않습니다. 아래에서 먼저 다운로드하세요.
          </p>
        ) : null}
      </section>

      {/* ---- 작업 목록 ---- */}
      <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between",
                      gap: 12 }}>
          <h3 style={{ margin: 0 }}>작업 목록</h3>
          {storage ? (
            <span style={{ fontSize: 12, opacity: 0.7 }}
              title={`오래된 작업은 자동으로 정리되어 최근 ${storage.keep}개만 보관됩니다`}>
              스토리지 {formatBytes(storage.total_bytes)} · 최근 {storage.keep}개 보관
            </span>
          ) : null}
        </div>
        {jobs.length === 0 ? <p style={{ opacity: 0.7 }}>아직 작업이 없습니다.</p> : null}
        {jobs.map((job) => (
          <div key={job.job_id}
            style={{ display: "flex", alignItems: "center", gap: 12, padding: "8px 12px",
                     border: "1px solid rgba(255,255,255,0.12)", borderRadius: 8 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {job.title}
              </div>
              <div style={{ fontSize: 12, opacity: 0.7 }}>
                {job.source_type === "youtube" ? "유튜브" : "업로드"} · {job.whisper_model} 모델
                {job.translate_provider ? ` · 번역: ${job.translate_provider}` : ""}
                {job.status === "error" && job.error ? ` · ${job.error}` : ""}
              </div>
              {INFLIGHT_STATUSES.includes(job.status) ? (
                <div style={{ height: 4, borderRadius: 2, background: "rgba(255,255,255,0.12)",
                             marginTop: 6 }}>
                  <div style={{ height: 4, borderRadius: 2, width: `${job.progress}%`,
                               background: "#4a9eda", transition: "width 0.5s" }} />
                </div>
              ) : null}
            </div>
            <span style={{ fontSize: 13, whiteSpace: "nowrap" }}>
              {STATUS_LABEL[job.status] ?? job.status}
              {INFLIGHT_STATUSES.includes(job.status) ? ` ${job.progress}%` : ""}
            </span>
            {["review", "done"].includes(job.status) ? (
              <button type="button" style={consoleStyles.mutedAction}
                onClick={() => setReviewJobId(job.job_id)}>
                {job.status === "done" ? "결과 보기" : "검수하기"}
              </button>
            ) : null}
            {confirmDeleteId === job.job_id ? (
              <>
                <button type="button" style={consoleStyles.action}
                  onClick={() => {
                    setConfirmDeleteId(null);
                    void deleteVideoJob(job.job_id).then(refresh);
                  }}>
                  정말 삭제
                </button>
                <button type="button" style={consoleStyles.mutedAction}
                  onClick={() => setConfirmDeleteId(null)}>
                  취소
                </button>
              </>
            ) : (
              <button type="button" style={consoleStyles.mutedAction}
                onClick={() => setConfirmDeleteId(job.job_id)}>
                삭제
              </button>
            )}
          </div>
        ))}
      </section>

      {/* ---- whisper 모델 매니저 ---- */}
      <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <h3 style={{ margin: 0 }}>전사 모델 관리</h3>
        <p style={{ margin: 0, fontSize: 13, opacity: 0.75 }}>
          모델은 서버에 저장됩니다. 큰 모델일수록 정확하지만 전사가 느려집니다.
        </p>
        {models.map((m) => (
          <div key={m.name}
            style={{ display: "flex", alignItems: "center", gap: 12, padding: "6px 12px",
                     border: "1px solid rgba(255,255,255,0.12)", borderRadius: 8 }}>
            <div style={{ flex: 1 }}>
              <strong>{m.name}</strong>
              <span style={{ fontSize: 12, opacity: 0.7, marginLeft: 8 }}>
                {formatBytes(m.approx_bytes)} · {m.label}
              </span>
            </div>
            {m.downloading ? (
              <span style={{ fontSize: 13 }}>다운로드 중… {m.progress ?? 0}%</span>
            ) : m.downloaded ? (
              <>
                <span style={{ fontSize: 13, color: "#30a46c" }}>설치됨</span>
                <button type="button" style={consoleStyles.mutedAction}
                  onClick={() => void deleteVideoModel(m.name).then(refresh)}>
                  삭제
                </button>
              </>
            ) : (
              <button type="button" style={consoleStyles.mutedAction}
                onClick={() => void downloadVideoModel(m.name).then(refresh)}>
                다운로드
              </button>
            )}
          </div>
        ))}
      </section>
    </div>
  );
}
