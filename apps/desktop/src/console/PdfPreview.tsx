import { useEffect, useState } from "react";
import { pdfPageUrl, type PdfJobSummary } from "./pdfApi";

// 페이지 단위 lazy 이미지 프리뷰 — 1000페이지급도 현재 페이지 1장만 로드한다.
export function PdfPreview({ job, onClose }:
  { job: PdfJobSummary; onClose: () => void }) {
  const [page, setPage] = useState(0);
  const [variant, setVariant] = useState<"source" | "translated">(
    job.status === "done" ? "translated" : "source");
  const total = job.page_count ?? 1;

  useEffect(() => { setPage(0); }, [job.job_id]);

  const clamp = (n: number) => Math.max(0, Math.min(total - 1, n));
  return (
    <div style={{ marginTop: 8, borderTop: "1px solid #334155", paddingTop: 8 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12 }}>
        <button type="button" onClick={() => setPage((p) => clamp(p - 1))}
          disabled={page <= 0}>← 이전</button>
        <span>{page + 1} / {total}</span>
        <button type="button" onClick={() => setPage((p) => clamp(p + 1))}
          disabled={page >= total - 1}>다음 →</button>
        <label>
          <input type="radio" checked={variant === "source"}
            onChange={() => setVariant("source")} /> 원본
        </label>
        <label>
          <input type="radio" checked={variant === "translated"}
            disabled={job.status !== "done"}
            onChange={() => setVariant("translated")} /> 번역본
        </label>
        <button type="button" onClick={onClose} style={{ marginLeft: "auto" }}>
          닫기
        </button>
      </div>
      <img src={pdfPageUrl(job.job_id, page, variant)}
        alt={`${job.title} p${page + 1}`}
        style={{ maxWidth: "100%", marginTop: 8, border: "1px solid #1e293b" }} />
    </div>
  );
}
