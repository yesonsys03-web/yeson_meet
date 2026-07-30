import { useCallback, useEffect, useRef, useState } from "react";
import { listTranslateEngines, type TranslateEngineInfo } from "./videoApi";
import {
  cancelPdfJob, deletePdfJob, isActivePdfStatus, listPdfJobs,
  pdfUploadUrl, uploadPdfJob, type PdfJobSummary,
} from "./pdfApi";

const STATUS_LABEL: Record<string, string> = {
  queued: "대기", extracting: "추출 중", translating: "번역 중",
  overlaying: "주석 생성 중", done: "완료", error: "오류", cancelled: "취소됨",
};

// VideoCaptionPanel.tsx:40-43과 동형 — Tauri 런타임 감지는 전 콘솔에서 이 형태로 통일.
type TauriGlobal = typeof globalThis & { __TAURI_INTERNALS__?: unknown };
function hasTauriRuntime(): boolean {
  return Boolean((globalThis as TauriGlobal).__TAURI_INTERNALS__);
}

export function PdfTranslatePanel({ active }: { active: boolean }) {
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
      });
    }
  }, [provider]);

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
        await uploadPdfJob(f, f.name, provider);
      }
      await refresh();
    } catch (e) {
      setMessage(`업로드 실패: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  }, [provider, refresh]);

  return (
    <div>
      <h2 style={{ fontSize: 16, marginBottom: 4 }}>스토리보드 번역</h2>
      <p style={{ color: "#94a3b8", fontSize: 12, marginBottom: 12 }}>
        납품 PDF(스토리보드)를 올리면 Dialog/Action Notes를 번역해 주석으로 입힌
        PDF를 만듭니다. 포맷은 자동 감지됩니다.
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
      <PdfJobList jobs={jobs} onChanged={refresh} />
    </div>
  );
}

function PdfJobList({ jobs, onChanged }:
  { jobs: PdfJobSummary[]; onChanged: () => Promise<void> }) {
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
            {isActivePdfStatus(j.status) ? (
              <button type="button" onClick={() => {
                void cancelPdfJob(j.job_id).then(onChanged);
              }}>취소</button>
            ) : (
              <button type="button" onClick={() => {
                void deletePdfJob(j.job_id).then(onChanged);
              }}>삭제</button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
