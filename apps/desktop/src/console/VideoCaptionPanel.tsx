import { useCallback, useEffect, useRef, useState } from "react";
import { consoleStyles } from "./consoleStyles";
import {
  burnVideoJob, cancelAllVideoJobs, cancelVideoJob, createYoutubeJob, deleteVideoJob,
  deleteVideoModel, downloadGpuPack, downloadVideoModel, getGpuStatus, getVideoStorage,
  listTranslateEngines, listVideoJobs, listVideoModels, rebuildVideoJob, setGpuEnabled,
  uploadVideoJob, videoDownloadUrl, videoUploadUrl,
} from "./videoApi";
import type {
  BurnStyle, GpuStatus, TranslateEngineInfo, VideoJobSummary, VideoModelInfo,
  VideoStorageInfo,
} from "./videoApi";
import {
  abortBatchThenCancelAll, filterVideoFiles, uploadBatch, uploadBatchNative, VIDEO_EXT_LIST,
} from "./videoBatch";
import type { NativeVideoFile } from "./videoBatch";
import {
  actionableJobIds, canRebuild, captionedFileName, isSelectableStatus, overallProgress,
  partitionSelection,
} from "./videoBatchOps";
import { shouldShowCudaWarning } from "./videoReviewLogic";
import { VideoReviewView } from "./VideoReviewView";

const STATUS_LABEL: Record<string, string> = {
  queued: "대기 중", ingesting: "영상 가져오는 중", extracting: "오디오 추출 중",
  transcribing: "전사 중", translating: "번역 중", review: "검수 대기",
  burning: "영상 굽는 중", done: "완료", error: "오류", cancelled: "취소됨",
};

const INFLIGHT_STATUSES = ["queued", "ingesting", "extracting", "transcribing",
                           "translating", "burning"];

const PAGE_SIZE = 15;

// 일괄 굽기 공용 스타일 — 개별 검수 없이 굽는 대량 워크플로용 기본값(VideoReviewView 기본값과 동일).
const DEFAULT_BURN_STYLE: BurnStyle = { position: "bottom", margin_v: 40, font_size: 18, color: "#ffffff" };

type TauriGlobal = typeof globalThis & { __TAURI_INTERNALS__?: unknown };
function hasTauriRuntime(): boolean {
  return Boolean((globalThis as TauriGlobal).__TAURI_INTERNALS__);
}

type EngineOption = { value: string; label: string; available: boolean };

// 서버 응답이 오기 전 첫 렌더용 폴백 — 깜빡임 방지 (서버 미설치 상태를 알기 전이므로 전부 available)
const DEFAULT_ENGINE_OPTIONS: EngineOption[] = [
  { value: "", label: "번역: Gemini", available: true },
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
  const [gpu, setGpu] = useState<GpuStatus | null>(null);
  const [jobs, setJobs] = useState<VideoJobSummary[]>([]);
  const [storage, setStorage] = useState<VideoStorageInfo | null>(null);
  const [engineOptions, setEngineOptions] = useState<EngineOption[]>(DEFAULT_ENGINE_OPTIONS);
  const [selectedModel, setSelectedModel] = useState("base");
  // 번역 기본값=Claude 구독(사용자 결정 2026-07-08) — 최초 렌더 시 폴백.
  // 서버에서 apple_hifi(Apple 온디바이스 고품질)를 쓸 수 있으면 첫 refresh 때
  // 품질 우선 기본값으로 전환한다(사용자 결정 2026-07-11, 고속은 opt-in).
  // claude CLI도 없으면 아래 effect가 Gemini("")로 폴백한다.
  const [translateProvider, setTranslateProvider] = useState("claude");
  const [cliModel, setCliModel] = useState(DEFAULT_OPENCODE_MODEL);
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reviewJobId, setReviewJobId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [confirmRebuildId, setConfirmRebuildId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);
  // "전체 취소" 클릭 시 true — 진행 중인 업로드 루프가 다음 파일 시작 전에 확인해
  // 멈춘다(이미 시작한 업로드는 끝까지 둔다). 각 배치 시작 시 false로 리셋.
  const batchCancelRef = useRef(false);
  // 진행 중인 배치의 promise — 전체 취소가 cancel-all 호출 전에 이 promise의
  // settle을 기다려, 취소 직후 완료된 업로드가 취소를 비껴가는 누락을 막는다.
  const batchPromiseRef = useRef<Promise<unknown> | null>(null);
  const [batchStatus, setBatchStatus] = useState<string | null>(null);
  const [modelMgmtOpen, setModelMgmtOpen] = useState(false);
  const [selectedJobs, setSelectedJobs] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(0);
  const [confirmBatchBurn, setConfirmBatchBurn] = useState(false);
  const [confirmBatchRebuild, setConfirmBatchRebuild] = useState(false);
  // 최초 엔진 목록 응답에서 apple_hifi 기본 전환을 1회만 시도(사용자가 이후
  // 직접 고른 선택을 다시 덮어쓰지 않기 위함 — 재조회 폴링마다 재실행 방지).
  const appleHifiDefaultTriedRef = useRef(false);

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
      const [m, j, e, s, g] = await Promise.all([
        listVideoModels(), listVideoJobs(), listTranslateEngines(), getVideoStorage(),
        // 구버전 서버 번들에는 /gpu 라우트가 없다 — GPU 카드만 숨기고 패널은 살린다
        getGpuStatus().catch(() => null),
      ]);
      setModels(m);
      setJobs(j);
      const options = toEngineOptions(e);
      setEngineOptions(options);
      if (!appleHifiDefaultTriedRef.current) {
        appleHifiDefaultTriedRef.current = true;
        const appleHifi = options.find((o) => o.value === "apple_hifi");
        if (appleHifi?.available) setTranslateProvider("apple_hifi");
      }
      setStorage(s);
      setGpu(g);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  // 선택된 번역 엔진이 서버에 없으면(미설치) Gemini("")로 폴백 — 기본값이
  // claude라서 claude CLI 없는 서버에서도 업로드가 막히지 않게 한다.
  useEffect(() => {
    if (!translateProvider) return;
    const opt = engineOptions.find((o) => o.value === translateProvider);
    if (opt && !opt.available) setTranslateProvider("");
  }, [engineOptions, translateProvider]);

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
    batchCancelRef.current = false;
    const cfg = {
      whisperModel: selectedModel,
      translateProvider: translateProvider || undefined,
      translateCliModel: translateProvider === "opencode" ? cliModel : undefined,
    };
    try {
      const batch = uploadBatch(files, cfg, uploadVideoJob, (done, total, current) => {
        setBatchStatus(done < total ? `업로드 중 ${done + 1}/${total} — ${current}` : null);
      }, { isCancelled: () => batchCancelRef.current });
      batchPromiseRef.current = batch;
      const res = await batch;
      const parts = [`${res.ok}개 작업이 시작됐습니다 (순차 처리)`];
      if (res.skipped) parts.push(`${res.skipped}개 취소로 건너뜀`);
      if (res.failed.length) {
        parts.push(`${res.failed.length}개 실패: ${res.failed.map((x) => x.name).join(", ")}`);
      }
      setBatchStatus(parts.join(" · "));
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      batchPromiseRef.current = null;
      setBusy(false);
    }
  };

  // 네이티브 폴더 선택 — <input webkitdirectory>는 WebView2 런타임 버전에 따라
  // 폴더 피커가 안 열리는 회귀가 있어(2026-07-08 Windows), Tauri에서는 네이티브
  // 다이얼로그로 폴더를 고르고 열거·업로드를 Rust 커맨드에 맡긴다(스트리밍).
  // 브라우저 dev에서만 기존 webkitdirectory input으로 폴백.
  const pickFolder = async () => {
    if (!hasTauriRuntime()) {
      folderInputRef.current?.click();
      return;
    }
    setError(null);
    setBatchStatus(null);
    const { open } = await import("@tauri-apps/plugin-dialog");
    // Windows 폴더 선택 모드(FOS_PICKFOLDERS)는 OS가 파일을 숨겨 폴더 내용물을
    // 확인할 수 없다(예전 webkitdirectory 다이얼로그도 동일한 OS 창). Windows에서는
    // 파일이 보이는 파일 다이얼로그에서 폴더 안 영상 하나를 고르게 하고, 그 부모
    // 폴더를 대상 폴더로 쓴다. macOS 폴더 선택창은 파일을 회색으로 보여주므로 유지.
    let dir: string;
    if (navigator.userAgent.includes("Windows")) {
      const file = await open({
        title: "영상 폴더 선택 — 폴더 안 영상 파일 하나를 고르세요 (폴더 전체 처리)",
        filters: [{ name: "영상 파일", extensions: VIDEO_EXT_LIST }],
      });
      if (typeof file !== "string") return;
      const { dirname } = await import("@tauri-apps/api/path");
      dir = await dirname(file);
    } else {
      const picked = await open({ directory: true, title: "영상 폴더 선택" });
      if (typeof picked !== "string") return;
      dir = picked;
    }
    setBusy(true);
    batchCancelRef.current = false;
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      const entries = await invoke<NativeVideoFile[]>("list_video_files", { dir });
      if (entries.length === 0) {
        setBatchStatus("선택한 폴더에 영상 파일이 없습니다.");
        return;
      }
      const cfg = {
        whisperModel: selectedModel,
        translateProvider: translateProvider || undefined,
        translateCliModel: translateProvider === "opencode" ? cliModel : undefined,
      };
      const batch = uploadBatchNative(entries, cfg, (entry, c) =>
        invoke("upload_video_file", {
          uploadUrl: videoUploadUrl(),
          path: entry.path,
          whisperModel: c.whisperModel,
          title: entry.name,
          translateProvider: c.translateProvider ?? null,
          translateCliModel: c.translateCliModel ?? null,
        }), (done, total, current) => {
          setBatchStatus(done < total ? `업로드 중 ${done + 1}/${total} — ${current}` : null);
        }, { isCancelled: () => batchCancelRef.current });
      batchPromiseRef.current = batch;
      const res = await batch;
      const parts = [`${res.ok}개 작업이 시작됐습니다 (순차 처리)`];
      if (res.skipped) parts.push(`${res.skipped}개 취소로 건너뜀`);
      if (res.failed.length) {
        parts.push(`${res.failed.length}개 실패: ${res.failed.map((x) => x.name).join(", ")}`);
      }
      setBatchStatus(parts.join(" · "));
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      batchPromiseRef.current = null;
      setBusy(false);
    }
  };

  const toggleJob = useCallback((id: string) => {
    setSelectedJobs((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAll = useCallback(() => {
    setSelectedJobs((prev) => {
      const ids = actionableJobIds(jobs);
      const allSel = ids.length > 0 && ids.every((id) => prev.has(id));
      return allSel ? new Set() : new Set(ids);
    });
  }, [jobs]);

  // 선택 굽기 — 검수 없이 공용 기본 스타일로 굽는다. 한 건 실패해도 나머지 계속.
  const runBatchBurn = useCallback(async () => {
    const { burnable } = partitionSelection(jobs, selectedJobs);
    if (burnable.length === 0) return;
    setConfirmBatchBurn(false);
    setBusy(true);
    setError(null);
    setBatchStatus(null);
    let ok = 0;
    const failed: string[] = [];
    for (const j of burnable) {
      try {
        await burnVideoJob(j.job_id, DEFAULT_BURN_STYLE);
        ok += 1;
      } catch {
        failed.push(j.title);
      }
    }
    const parts = [`${ok}개 굽기를 시작했습니다`];
    if (failed.length) parts.push(`${failed.length}개 실패: ${failed.join(", ")}`);
    setBatchStatus(parts.join(" · "));
    setSelectedJobs(new Set());
    await refresh();
    setBusy(false);
  }, [jobs, selectedJobs, refresh]);

  // 선택 재생성 — 오류/취소됨 작업만 처음부터 다시 처리(서버가 순차 큐잉).
  const runBatchRebuild = useCallback(async () => {
    const { rebuildable } = partitionSelection(jobs, selectedJobs);
    if (rebuildable.length === 0) return;
    setConfirmBatchRebuild(false);
    setBusy(true);
    setError(null);
    setBatchStatus(null);
    let ok = 0;
    const failed: string[] = [];
    for (const j of rebuildable) {
      try {
        await rebuildVideoJob(j.job_id);
        ok += 1;
      } catch {
        failed.push(j.title);
      }
    }
    const parts = [`${ok}개 재생성을 시작했습니다`];
    if (failed.length) parts.push(`${failed.length}개 실패: ${failed.join(", ")}`);
    setBatchStatus(parts.join(" · "));
    setSelectedJobs(new Set());
    await refresh();
    setBusy(false);
  }, [jobs, selectedJobs, refresh]);

  // 선택 다운로드 — 폴더 하나를 고른 뒤 완료 작업의 mp4를 그 안에 {제목}-captioned.mp4로 저장.
  const runBatchDownload = useCallback(async () => {
    const { downloadable } = partitionSelection(jobs, selectedJobs);
    if (downloadable.length === 0) return;
    setError(null);
    setBatchStatus(null);

    if (!hasTauriRuntime()) {
      // 브라우저 dev 폴백: 폴더 지정 없이 순차 blob 다운로드.
      for (const j of downloadable) {
        try {
          const res = await fetch(videoDownloadUrl(j.job_id, "video"));
          if (!res.ok) continue;
          const blob = await res.blob();
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = captionedFileName(j.title);
          a.click();
          URL.revokeObjectURL(a.href);
        } catch { /* skip */ }
      }
      setBatchStatus(`${downloadable.length}개 다운로드 시작`);
      return;
    }

    setBusy(true);
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const dir = await open({ directory: true, title: "다운로드 폴더 선택" });
      if (typeof dir !== "string") {
        setBatchStatus("저장이 취소되었습니다.");
        return;
      }
      const { join } = await import("@tauri-apps/api/path");
      const { invoke } = await import("@tauri-apps/api/core");
      let ok = 0;
      const failed: string[] = [];
      for (const j of downloadable) {
        try {
          setBatchStatus(`내려받는 중 ${ok + 1}/${downloadable.length} — ${j.title}`);
          const path = await join(dir, captionedFileName(j.title));
          // 받기+쓰기를 Rust에서 처리 — fs 스코프 무관(다른 드라이브 OK) + 대용량 IPC 회피.
          await invoke("download_to_file", { url: videoDownloadUrl(j.job_id, "video"), path });
          ok += 1;
        } catch {
          failed.push(j.title);
        }
      }
      const parts = [`${ok}개 저장됨 → ${dir}`];
      if (failed.length) parts.push(`${failed.length}개 실패: ${failed.join(", ")}`);
      setBatchStatus(parts.join(" · "));
      setSelectedJobs(new Set());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [jobs, selectedJobs]);

  // 전체 취소 — ①업로드 루프 abort 플래그 → ②진행 중 배치의 settle 대기 →
  // ③서버 전체 취소(대기열 선취소 후 활성 작업 취소). 이 순서라 cancel-all
  // 시점에 업로드가 in-flight일 수 없어, 방금 완료된 업로드가 취소를 비껴가는
  // 누락이 없다.
  const cancelAllBatch = useCallback(async () => {
    setError(null);
    try {
      await abortBatchThenCancelAll(
        () => { batchCancelRef.current = true; },
        batchPromiseRef.current,
        cancelAllVideoJobs,
      );
      setBatchStatus("전체 취소를 요청했습니다.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [refresh]);

  if (reviewJobId) {
    return (
      <VideoReviewView jobId={reviewJobId}
        onBack={() => { setReviewJobId(null); void refresh(); }} />
    );
  }

  const totalPages = Math.max(1, Math.ceil(jobs.length / PAGE_SIZE));
  const curPage = Math.min(page, totalPages - 1); // jobs 축소(삭제/프루닝) 시 자동 클램프
  const pagedJobs = jobs.slice(curPage * PAGE_SIZE, curPage * PAGE_SIZE + PAGE_SIZE);
  const { burnable: burnableSel, downloadable: downloadableSel, rebuildable: rebuildableSel } =
    partitionSelection(jobs, selectedJobs);
  const actionableIds = actionableJobIds(jobs);
  const allActionableSelected = actionableIds.length > 0 && actionableIds.every((id) => selectedJobs.has(id));
  // 로컬 배치 업로드가 진행 중이거나(busy), 서버에 대기/실행 중인 작업이 하나라도
  // 있으면 "전체 취소" 버튼을 노출한다.
  const showCancelAll = busy || jobs.some((j) => INFLIGHT_STATUSES.includes(j.status));

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
            {models.map((m) => {
              const unavailable = m.available === false;
              const suffix = unavailable ? " (실리콘맥 전용)" : m.downloaded ? "" : " (미설치)";
              return (
                <option key={m.name} value={m.name} disabled={unavailable || !m.downloaded}>
                  {m.name}{suffix}
                </option>
              );
            })}
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
            onClick={() => void pickFolder()}>
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
        <p style={{ margin: 0, fontSize: 12, opacity: 0.6 }}>
          Windows에서 &apos;폴더 선택…&apos;은 파일이 보이는 창을 엽니다 — 폴더 안 영상
          하나를 고르면 그 폴더의 모든 영상을 일괄 처리합니다. 특정 파일만 처리하려면
          &apos;로컬 파일 선택…&apos;을 쓰세요.
        </p>
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
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            {showCancelAll ? (
              <button type="button" style={consoleStyles.mutedAction}
                title="진행 중인 업로드 루프를 멈추고, 서버에 대기/실행 중인 모든 작업을 취소합니다"
                onClick={() => void cancelAllBatch()}>
                전체 취소
              </button>
            ) : null}
            {storage ? (
              <span style={{ fontSize: 12, opacity: 0.7 }}
                title={`오래된 작업은 자동으로 정리되어 최근 ${storage.keep}개만 보관됩니다`}>
                스토리지 {formatBytes(storage.total_bytes)} · 최근 {storage.keep}개 보관
              </span>
            ) : null}
          </div>
        </div>
        {jobs.length === 0 ? <p style={{ opacity: 0.7 }}>아직 작업이 없습니다.</p> : null}

        {/* 일괄 작업 바 — 체크박스로 고른 검수 대기 작업을 공용 스타일로 일괄 굽기,
            완료 작업을 폴더 하나에 일괄 다운로드. 개별 검수/다운로드는 각 행에서 계속 가능. */}
        {jobs.length > 0 ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, cursor: "pointer" }}>
              <input type="checkbox" checked={allActionableSelected} onChange={toggleAll}
                disabled={busy || actionableIds.length === 0} />
              전체 선택
            </label>
            {confirmBatchBurn ? (
              <>
                <span style={{ fontSize: 13, opacity: 0.85 }}>
                  {burnableSel.length}개를 검수 없이 바로 굽습니다.
                </span>
                <button type="button" style={consoleStyles.action}
                  disabled={busy} onClick={() => void runBatchBurn()}>확인</button>
                <button type="button" style={consoleStyles.mutedAction}
                  disabled={busy} onClick={() => setConfirmBatchBurn(false)}>취소</button>
              </>
            ) : (
              <button type="button"
                title="선택한 작업 중 '검수 대기' 상태만 검수 없이 공용 스타일로 굽습니다 (완료된 작업은 제외)"
                style={{ ...consoleStyles.action, ...(busy || burnableSel.length === 0 ? consoleStyles.actionDisabled : null) }}
                disabled={busy || burnableSel.length === 0}
                onClick={() => setConfirmBatchBurn(true)}>
                선택 굽기{burnableSel.length ? ` (${burnableSel.length})` : ""}
              </button>
            )}
            <button type="button"
              title="선택한 작업 중 '완료' 상태의 mp4만 폴더 하나를 골라 일괄 저장합니다 (아직 안 구워진 작업은 제외)"
              style={{ ...consoleStyles.mutedAction, ...(busy || downloadableSel.length === 0 ? consoleStyles.actionDisabled : null) }}
              disabled={busy || downloadableSel.length === 0}
              onClick={() => void runBatchDownload()}>
              선택 다운로드{downloadableSel.length ? ` (${downloadableSel.length})` : ""}
            </button>
            {confirmBatchRebuild ? (
              <>
                <span style={{ fontSize: 13, opacity: 0.85 }}>
                  {rebuildableSel.length}개를 처음부터 다시 처리합니다.
                </span>
                <button type="button" style={consoleStyles.action}
                  disabled={busy} onClick={() => void runBatchRebuild()}>확인</button>
                <button type="button" style={consoleStyles.mutedAction}
                  disabled={busy} onClick={() => setConfirmBatchRebuild(false)}>취소</button>
              </>
            ) : (
              <button type="button"
                title="선택한 작업 중 '오류/취소됨' 상태만 처음부터 다시 처리합니다 (검수 대기·완료는 제외)"
                style={{ ...consoleStyles.mutedAction, ...(busy || rebuildableSel.length === 0 ? consoleStyles.actionDisabled : null) }}
                disabled={busy || rebuildableSel.length === 0}
                onClick={() => setConfirmBatchRebuild(true)}>
                선택 재생성{rebuildableSel.length ? ` (${rebuildableSel.length})` : ""}
              </button>
            )}
            <span style={{ flex: 1 }} />
            {selectedJobs.size > 0 ? (
              <span style={{ fontSize: 12, opacity: 0.7 }}>{selectedJobs.size}개 선택됨</span>
            ) : null}
          </div>
        ) : null}

        {/* 페이지 네비게이션 — 15개/페이지, 상단에서 바로 이동 */}
        {jobs.length > PAGE_SIZE ? (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8, flexWrap: "wrap" }}>
            <button type="button" style={{ ...consoleStyles.mutedAction, ...(curPage === 0 ? consoleStyles.actionDisabled : null) }} disabled={curPage === 0} onClick={() => setPage(0)}>« 처음</button>
            <button type="button" style={{ ...consoleStyles.mutedAction, ...(curPage === 0 ? consoleStyles.actionDisabled : null) }} disabled={curPage === 0} onClick={() => setPage(curPage - 1)}>‹ 이전</button>
            <span style={{ fontSize: 12, opacity: 0.7, minWidth: 150, textAlign: "center" }}>{curPage + 1} / {totalPages} 페이지 · 총 {jobs.length}개</span>
            <button type="button" style={{ ...consoleStyles.mutedAction, ...(curPage >= totalPages - 1 ? consoleStyles.actionDisabled : null) }} disabled={curPage >= totalPages - 1} onClick={() => setPage(curPage + 1)}>다음 ›</button>
            <button type="button" style={{ ...consoleStyles.mutedAction, ...(curPage >= totalPages - 1 ? consoleStyles.actionDisabled : null) }} disabled={curPage >= totalPages - 1} onClick={() => setPage(totalPages - 1)}>마지막 »</button>
          </div>
        ) : null}

        {pagedJobs.map((job) => (
          <div key={job.job_id}
            style={{ display: "flex", alignItems: "center", gap: 12, padding: "8px 12px",
                     border: "1px solid rgba(255,255,255,0.12)", borderRadius: 8 }}>
            <input type="checkbox"
              checked={selectedJobs.has(job.job_id)}
              disabled={busy || !isSelectableStatus(job.status)}
              onChange={() => toggleJob(job.job_id)}
              style={isSelectableStatus(job.status) ? undefined : { visibility: "hidden" }}
              title="일괄 작업 선택" />
            <div style={{ flex: 1, minWidth: 0 }}>
              {/* 파일명은 잘라내지 않는다 — 언더스코어 이름은 공백이 없어 breakAll 줄바꿈 */}
              <div style={{ wordBreak: "break-all" }}>
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
                  <div style={{ height: 4, borderRadius: 2,
                               width: `${overallProgress(job.status, job.progress)}%`,
                               background: "#4a9eda", transition: "width 0.5s" }} />
                </div>
              ) : null}
            </div>
            <span style={{ fontSize: 13, whiteSpace: "nowrap" }}>
              {STATUS_LABEL[job.status] ?? job.status}
              {INFLIGHT_STATUSES.includes(job.status)
                ? ` ${overallProgress(job.status, job.progress)}%` : ""}
            </span>
            {["review", "done"].includes(job.status) ? (
              <button type="button" style={consoleStyles.mutedAction}
                onClick={() => setReviewJobId(job.job_id)}>
                {job.status === "done" ? "결과 보기" : "검수하기"}
              </button>
            ) : null}
            {INFLIGHT_STATUSES.includes(job.status) ? (
              <button type="button" style={consoleStyles.mutedAction}
                onClick={() => void cancelVideoJob(job.job_id).then(refresh)}>
                취소
              </button>
            ) : null}
            {canRebuild(job.status) ? (
              confirmRebuildId === job.job_id ? (
                <>
                  <button type="button" style={consoleStyles.action}
                    onClick={() => {
                      setConfirmRebuildId(null);
                      void rebuildVideoJob(job.job_id).then(refresh);
                    }}>
                    정말 재생성
                  </button>
                  <button type="button" style={consoleStyles.mutedAction}
                    onClick={() => setConfirmRebuildId(null)}>
                    취소
                  </button>
                </>
              ) : (
                <button type="button" style={consoleStyles.mutedAction}
                  title="같은 소스·같은 옵션으로 전사/번역을 다시 실행합니다 (기존 검수 편집은 사라짐)"
                  onClick={() => setConfirmRebuildId(job.job_id)}>
                  재생성
                </button>
              )
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
        <button type="button"
          onClick={() => setModelMgmtOpen((v) => !v)}
          style={{ display: "flex", alignItems: "center", gap: 8, background: "none",
                   border: 0, padding: 0, cursor: "pointer", color: "inherit", font: "inherit",
                   alignSelf: "flex-start" }}>
          <span aria-hidden style={{ fontSize: 12, opacity: 0.7, width: 12, display: "inline-block" }}>
            {modelMgmtOpen ? "▾" : "▸"}
          </span>
          <h3 style={{ margin: 0 }}>전사 모델 관리</h3>
        </button>
        {modelMgmtOpen ? (
        <>
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
                {m.builtin
                  ? (m.available === false ? "실리콘맥 전용" : "내장")
                  : formatBytes(m.approx_bytes)} · {m.label}
              </span>
            </div>
            {m.builtin ? (
              m.available === false ? (
                <span style={{ fontSize: 13, opacity: 0.6 }}>이 기기에서 사용 불가</span>
              ) : (
                <span style={{ fontSize: 13, color: "#30a46c" }}>내장</span>
              )
            ) : m.downloading ? (
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
        {gpu?.supported ? (
          <div
            style={{ display: "flex", alignItems: "center", gap: 12, padding: "6px 12px",
                     border: "1px solid rgba(255,255,255,0.12)", borderRadius: 8 }}>
            <div style={{ flex: 1 }}>
              <strong>GPU 전사 (NVIDIA CUDA)</strong>
              <span style={{ fontSize: 12, opacity: 0.7, marginLeft: 8 }}>
                {gpu.gpu_name ?? "GPU 미감지"} · GPU 팩 {formatBytes(gpu.approx_bytes)}
              </span>
            </div>
            {gpu.downloading ? (
              <span style={{ fontSize: 13 }}>GPU 팩 다운로드 중… {gpu.progress ?? 0}%</span>
            ) : !gpu.installed ? (
              <>
                {gpu.last_error ? (
                  <span style={{ fontSize: 12, color: "#e5484d", maxWidth: 320 }}
                    title={gpu.last_error}>
                    다운로드 실패: {gpu.last_error}
                  </span>
                ) : null}
                <button type="button" style={consoleStyles.mutedAction}
                  onClick={() => void downloadGpuPack().then(refresh)
                    .catch((e) => setError(e instanceof Error ? e.message : String(e)))}>
                  {gpu.last_error ? "다시 시도" : "GPU 팩 다운로드"}
                </button>
              </>
            ) : (
              <>
                <span style={{ fontSize: 13,
                               color: gpu.cuda_available ? "#30a46c" : "#e5484d" }}>
                  {gpu.cuda_available ? "CUDA 인식됨" : "CUDA 미인식"}
                </span>
                <button type="button" style={consoleStyles.mutedAction}
                  onClick={() => void setGpuEnabled(!gpu.enabled).then(refresh)
                    .catch((e) => setError(e instanceof Error ? e.message : String(e)))}>
                  {gpu.enabled ? "GPU 사용 끄기" : "GPU 사용 켜기"}
                </button>
              </>
            )}
          </div>
        ) : null}
        {gpu?.supported && shouldShowCudaWarning(gpu) ? (
          <div style={{ fontSize: 12, opacity: 0.75, padding: "2px 12px" }}>
            전사 GPU 사용 불가 ({gpu.cuda_reason ?? "사유 미상"}) — 전사는 CPU로 진행됩니다
          </div>
        ) : null}
        {gpu?.supported ? (
          <div style={{ fontSize: 12, opacity: 0.6, padding: "2px 12px" }}>
            GPU 가속은 전사에 적용됩니다. 굽기는 CPU 인코딩이 더 빨라 항상 CPU로, 번역은 외부 AI(Claude/Gemini)라 GPU와 무관합니다.
          </div>
        ) : null}
        </>
        ) : null}
      </section>
    </div>
  );
}
