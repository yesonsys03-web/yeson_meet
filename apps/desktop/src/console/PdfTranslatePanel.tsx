import { useCallback, useEffect, useRef, useState } from "react";
import { listTranslateEngines, type TranslateEngineInfo } from "./videoApi";
import {
  cancelPdfJob, deletePdfJob, isActivePdfStatus, listPdfJobs,
  pdfDownloadUrl, pdfUploadUrl, uploadPdfJob, type PdfJobSummary,
} from "./pdfApi";
import { PdfLabelEditor } from "./PdfLabelEditor";
import { PdfPreview } from "./PdfPreview";

const STATUS_LABEL: Record<string, string> = {
  queued: "대기", extracting: "추출 중", translating: "번역 중",
  overlaying: "주석 생성 중", done: "완료", error: "오류", cancelled: "취소됨",
};

// VideoCaptionPanel.tsx:40-43과 동형 — Tauri 런타임 감지는 전 콘솔에서 이 형태로 통일.
type TauriGlobal = typeof globalThis & { __TAURI_INTERNALS__?: unknown };
function hasTauriRuntime(): boolean {
  return Boolean((globalThis as TauriGlobal).__TAURI_INTERNALS__);
}

// 번역 완료 PDF 저장 — VideoCaptionPanel.tsx의 download_to_file 호출부(url·path
// 인자, camelCase 그대로)를 그대로 따른다. 브라우저 dev 폴백은 위치 선택 없는
// 앵커 다운로드. 주의: 전체 버퍼링(비스트리밍)이라 대용량 PDF도 메모리를 그만큼 쓴다.
// VideoReviewView.tsx:190-192와 동형 — save()를 try 밖에 두면 다이얼로그/전송 오류가
// void로 삼켜져 "클릭했는데 아무 반응 없음"이 된다. 전체를 try/catch로 감싸 onError로 표면화.
async function downloadPdf(
  job: PdfJobSummary, onError: (msg: string) => void,
): Promise<void> {
  const name = `${job.source_ref.replace(/\.pdf$/i, "")}_번역.pdf`;
  try {
    if (hasTauriRuntime()) {
      const { save } = await import("@tauri-apps/plugin-dialog");
      const dest = await save({ defaultPath: name,
        filters: [{ name: "PDF", extensions: ["pdf"] }] });
      if (!dest) return;
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("download_to_file", { url: pdfDownloadUrl(job.job_id), path: dest });
    } else {
      const a = document.createElement("a");
      a.href = pdfDownloadUrl(job.job_id);
      a.download = name;
      a.click();
    }
  } catch (e) {
    onError(`저장 실패: ${String(e)}`);
  }
}

type PdfFormat = "storyboard" | "xsheet";

// 두 탭(스토리보드/Xsheet)은 문구·format_hint·목록 필터만 다르고 화면은
// 동일하다 — 통일성 요구(2026-08-20)로 패널을 매개변수화해 통째로 공유한다.
const FORMAT_COPY: Record<PdfFormat, { heading: string; desc: string }> = {
  storyboard: {
    heading: "스토리보드 번역",
    desc: "납품 PDF(스토리보드)를 올리면 Dialog/Action Notes를 번역해 주석으로 "
      + "입힌 PDF를 만듭니다. 포맷은 자동 감지됩니다.",
  },
  xsheet: {
    heading: "Xsheet 번역",
    desc: "엑스시트 스캔 PDF를 올리면 손글씨 노트를 판독·번역해 원문 옆에 "
      + "주석으로 병기합니다. 손글씨 판독(전사)은 서버의 Antigravity CLI를 씁니다.",
  },
};

export function PdfTranslatePanel({ active, format = "storyboard" }: {
  active: boolean; format?: PdfFormat;
}) {
  const [engines, setEngines] = useState<TranslateEngineInfo[]>([]);
  const [provider, setProvider] = useState<string>("gemini");
  const [jobs, setJobs] = useState<PdfJobSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string>("");
  const fileInput = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      setJobs(await listPdfJobs());
    } catch (e) {
      setMessage(String(e));
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    void refresh();
    void listTranslateEngines().then(setEngines).catch(() => {});
  }, [active, refresh]);

  // 활성 작업이 있을 때만 1.5초 폴링
  useEffect(() => {
    if (!active || !jobs.some((j) => isActivePdfStatus(j.status))) return;
    const t = setInterval(() => void refresh(), 1500);
    return () => clearInterval(t);
  }, [active, jobs, refresh]);

  const uploadPaths = useCallback(async (paths: string[]) => {
    const { invoke } = await import("@tauri-apps/api/core");
    for (const p of paths) {
      const name = p.split(/[\\/]/).pop() ?? "upload.pdf";
      await invoke<string>("upload_pdf_file", {
        uploadUrl: pdfUploadUrl(), path: p, title: name,
        translateProvider: provider, translateCliModel: null,
        formatHint: format,
      });
    }
  }, [provider, format]);

  const pickAndUpload = useCallback(async () => {
    setMessage("");
    setBusy(true);
    try {
      if (hasTauriRuntime()) {
        const { open } = await import("@tauri-apps/plugin-dialog");
        const picked = await open({
          multiple: true, filters: [{ name: "PDF", extensions: ["pdf"] }],
          title: "번역할 PDF 선택",
        });
        if (!picked) return;
        await uploadPaths(Array.isArray(picked) ? picked : [picked]);
      } else {
        fileInput.current?.click();
        return;
      }
      await refresh();
    } catch (e) {
      setMessage(`업로드 실패: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  }, [refresh, uploadPaths]);

  const onBrowserFiles = useCallback(async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    try {
      for (const f of Array.from(files)) {
        await uploadPdfJob(f, f.name, provider, undefined, format);
      }
      await refresh();
    } catch (e) {
      setMessage(`업로드 실패: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  }, [provider, refresh, format]);

  // 탭별 잡 분리 — format_hint가 업로드 시점에 job.format을 선기록하므로
  // queued여도 결정적으로 갈린다. 힌트 없는 옛 잡(format null)은 감지 전까지
  // 스토리보드 탭 몫으로 둔다(이 기능 전 잡은 전부 스토리보드였다).
  const visibleJobs = jobs.filter((j) => (j.format ?? "storyboard") === format);

  return (
    <div>
      <h2 style={{ fontSize: 16, marginBottom: 4 }}>{FORMAT_COPY[format].heading}</h2>
      <p style={{ color: "#94a3b8", fontSize: 12, marginBottom: 12 }}>
        {FORMAT_COPY[format].desc}
      </p>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
        <label style={{ fontSize: 12 }}>번역 엔진</label>
        <select value={provider} onChange={(e) => setProvider(e.target.value)}
          style={{ fontSize: 12 }}>
          {engines.map((eng) => (
            <option key={eng.value} value={eng.value} disabled={!eng.available}>
              {eng.label}{eng.available ? "" : " (사용 불가)"}
            </option>
          ))}
        </select>
        <button type="button" onClick={() => void pickAndUpload()} disabled={busy}>
          {busy ? "업로드 중..." : "PDF 업로드"}
        </button>
        <input ref={fileInput} type="file" accept="application/pdf" multiple
          style={{ display: "none" }}
          onChange={(e) => void onBrowserFiles(e.target.files)} />
      </div>
      {message ? <p style={{ color: "#f87171", fontSize: 12 }}>{message}</p> : null}
      <PdfJobList jobs={visibleJobs} onChanged={refresh} onError={setMessage} />
    </div>
  );
}

function PdfJobList({ jobs, onChanged, onError }: {
  jobs: PdfJobSummary[]; onChanged: () => Promise<void>;
  onError: (msg: string) => void;
}) {
  const [previewId, setPreviewId] = useState<string | null>(null);
  // 편집 뷰는 **열려 있는 동안 언마운트하지 않는다.** 재굽기를 시작하면 상태가
  // overlaying이 되어 아래 `status === "done"` 분기로는 버튼이 사라지는데,
  // 그때 화면까지 닫히면 사람이 방금 무엇을 눌렀는지 잃는다.
  const [editorId, setEditorId] = useState<string | null>(null);
  if (!jobs.length) {
    return <p style={{ color: "#64748b", fontSize: 12 }}>작업이 없습니다.</p>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {jobs.map((j) => (
        <div key={j.job_id}
          style={{ border: "1px solid #334155", borderRadius: 6, padding: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <strong style={{ fontSize: 13 }}>{j.title}</strong>
            <span style={{ fontSize: 12, color: "#94a3b8" }}>
              {STATUS_LABEL[j.status] ?? j.status}
              {isActivePdfStatus(j.status) ? ` ${j.progress}%` : ""}
            </span>
          </div>
          {j.error ? (
            <p style={{ color: "#f87171", fontSize: 12 }}>{j.error}</p>
          ) : null}
          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            {/* .catch 필수 — 취소는 409(폴링 1.5초 주기 안에서 작업이 끝나면
                화면엔 아직 "취소" 버튼이 있다), 삭제는 404를 실제로 던진다.
                catch가 없으면 프로미스가 reject되고 onChanged도 안 돌아
                "눌렀는데 아무 일도 안 일어남"이 된다 — 위 downloadPdf가
                Task 10 리뷰에서 정확히 이 이유로 고쳐진 결함 클래스다. */}
            {isActivePdfStatus(j.status) ? (
              <button type="button" onClick={() => {
                void cancelPdfJob(j.job_id).then(onChanged)
                  .catch((e) => onError(`취소 실패: ${String(e)}`));
              }}>취소</button>
            ) : (
              <button type="button" onClick={() => {
                void deletePdfJob(j.job_id).then(onChanged)
                  .catch((e) => onError(`삭제 실패: ${String(e)}`));
              }}>삭제</button>
            )}
            <button type="button" onClick={() =>
              setPreviewId((cur) => (cur === j.job_id ? null : j.job_id))
            }>프리뷰</button>
            {j.status === "done" ? (
              <>
                <button type="button" onClick={() => void downloadPdf(j, onError)}>
                  번역 PDF 저장
                </button>
                <button type="button" onClick={() =>
                  setEditorId((cur) => (cur === j.job_id ? null : j.job_id))
                }>
                  라벨 편집{j.has_edits ? " ✎" : ""}{j.stale ? " (다시 굽기 필요)" : ""}
                </button>
              </>
            ) : null}
          </div>
          {previewId === j.job_id ? (
            <PdfPreview job={j} onClose={() => setPreviewId(null)} />
          ) : null}
          {editorId === j.job_id ? (
            <PdfLabelEditor job={j} onClose={() => setEditorId(null)} />
          ) : null}
        </div>
      ))}
    </div>
  );
}
