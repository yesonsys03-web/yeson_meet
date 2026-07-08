// === ANCHOR: VIDEO_JOBS_PANEL_START ===
// 서버 콘솔의 "자막메이커 데이터 관리" 패널. 자막 메이커는 작업마다 원본/preview/
// burned mp4를 서버 스토리지에 쌓는다 — 파일이 서버에 있으니 관리(조회/용량/삭제)도
// 서버 control plane에서 한다(deviceAdmin/BackupPanel과 동일 사상). 무인증 loopback
// REST라 로그인 게이트가 없다. 자동 리텐션(최근 N개 유지)의 보완: 운영자가 특정
// 작업이나 완료분을 즉시 비운다. 확인은 window.confirm 대신 인라인 확인(클라이언트
// VideoCaptionPanel과 동일 패턴) — WebView2(Windows)에서 네이티브 다이얼로그가
// 막히거나 튀는 문제를 피한다.
import type { CSSProperties } from "react";
import { useCallback, useEffect, useState } from "react";

import { deleteVideoJob, getStorage, listVideoJobs, type StorageInfo, type VideoJobRow } from "./videoJobsAdmin";

type Props = { serverPort: number | null; running: boolean };

const STATUS_LABEL: Record<string, string> = {
  queued: "대기 중", ingesting: "가져오는 중", extracting: "오디오 추출 중",
  transcribing: "전사 중", translating: "번역 중", review: "검수 대기",
  burning: "굽는 중", done: "완료", error: "오류",
};
// 진행 중(입력 파일이 살아있어야 하는) 상태 — 삭제 시 경고. 자동 리텐션과 동일 개념.
const INFLIGHT = new Set(["queued", "ingesting", "extracting", "transcribing", "translating", "burning"]);
const COMPLETED = new Set(["done", "error"]);
const PAGE_SIZE = 15;

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function formatBytes(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)} GB`;
  if (n >= 1_000_000) return `${Math.round(n / 1_000_000)} MB`;
  if (n >= 1000) return `${Math.round(n / 1000)} KB`;
  return `${n} B`;
}

export default function VideoJobsPanel({ serverPort, running }: Props) {
  const [jobs, setJobs] = useState<VideoJobRow[]>([]);
  const [storage, setStorage] = useState<StorageInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null); // 행별 인라인 확인
  const [confirmBulk, setConfirmBulk] = useState(false); // 일괄 삭제 인라인 확인
  const [page, setPage] = useState(0);

  const refresh = useCallback(async () => {
    if (serverPort == null) return;
    try {
      const [j, st] = await Promise.all([listVideoJobs(serverPort), getStorage(serverPort)]);
      setJobs(j);
      setStorage(st);
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, [serverPort]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const performDelete = useCallback(
    async (jobId: string) => {
      if (serverPort == null) return;
      setConfirmId(null);
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        await deleteVideoJob(serverPort, jobId);
        setNotice("삭제됨");
        await refresh();
      } catch (e) {
        setError(errText(e));
      } finally {
        setBusy(false);
      }
    },
    [serverPort, refresh],
  );

  const completed = jobs.filter((j) => COMPLETED.has(j.status));

  const totalPages = Math.max(1, Math.ceil(jobs.length / PAGE_SIZE));
  const curPage = Math.min(page, totalPages - 1); // jobs 축소(삭제) 시 자동 클램프
  const pagedJobs = jobs.slice(curPage * PAGE_SIZE, curPage * PAGE_SIZE + PAGE_SIZE);

  const performDeleteCompleted = useCallback(async () => {
    if (serverPort == null || completed.length === 0) return;
    setConfirmBulk(false);
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      // 별도 벌크 엔드포인트 없이 검증된 DELETE 경로를 순회 — keep=N 규모라 충분.
      let ok = 0;
      for (const j of completed) {
        await deleteVideoJob(serverPort, j.job_id);
        ok += 1;
      }
      setNotice(`${ok}개 삭제됨`);
      await refresh();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }, [serverPort, completed, refresh]);

  if (!running || serverPort == null) {
    return (
      <div style={s.wrap}>
        <p style={s.hint}>서버를 먼저 시작하세요 — 자막 메이커 데이터 관리는 실행 중인 서버에 연결합니다.</p>
      </div>
    );
  }

  return (
    <div style={s.wrap}>
      <div style={s.headRow}>
        <h2 style={s.title}>자막메이커 데이터 관리</h2>
        <button style={s.muted} onClick={() => void refresh()} disabled={busy}>새로고침</button>
      </div>

      {storage ? (
        <p style={s.hint}>
          총 사용량 <strong style={{ color: "var(--ys-text-strong)" }}>{formatBytes(storage.total_bytes)}</strong>
          {" · "}작업 {storage.job_count}개 · 자동 보관 최근 {storage.keep}개(초과분 자동 삭제)
        </p>
      ) : null}

      {/* 완료·오류 작업 일괄 삭제 — 무엇이 지워질지 목록으로 명시한 뒤 확인.
          "선택 삭제 후 남은 것 = 지키려는 것"인데 일괄 삭제가 그걸 쓸어버리는
          실수를 막기 위해, 삭제 대상 제목/용량을 펼쳐 보여준다. */}
      {confirmBulk ? (
        <div style={s.confirmBox}>
          <div style={s.confirmMsg}>
            아래 완료·오류 작업 {completed.length}개가 삭제됩니다. 되돌릴 수 없습니다:
          </div>
          <ul style={s.confirmList}>
            {completed.map((j) => (
              <li key={j.job_id}>
                {j.title}
                {j.size_bytes != null ? ` — ${formatBytes(j.size_bytes)}` : ""}
                {` (${STATUS_LABEL[j.status] ?? j.status})`}
              </li>
            ))}
          </ul>
          <div style={s.row}>
            <button style={{ ...s.danger, ...(busy ? s.disabled : {}) }} onClick={() => void performDeleteCompleted()} disabled={busy}>
              위 {completed.length}개 삭제
            </button>
            <button style={s.muted} onClick={() => setConfirmBulk(false)} disabled={busy}>취소</button>
          </div>
        </div>
      ) : (
        <div style={s.row}>
          <button
            style={{ ...s.danger, ...(busy || completed.length === 0 ? s.disabled : {}) }}
            onClick={() => setConfirmBulk(true)}
            disabled={busy || completed.length === 0}
          >
            완료·오류 작업 일괄 삭제{completed.length ? ` (${completed.length})` : ""}
          </button>
        </div>
      )}

      {notice ? <p style={s.success}>{notice}</p> : null}
      {error ? <p style={s.error}>{error}</p> : null}

      {jobs.length === 0 ? (
        <p style={s.hint}>자막 메이커 작업이 없습니다.</p>
      ) : (
        <>
          {jobs.length > PAGE_SIZE ? (
            <div style={s.pager}>
              <button style={{ ...s.muted, ...(curPage === 0 ? s.disabled : {}) }} onClick={() => setPage(0)} disabled={curPage === 0}>« 처음</button>
              <button style={{ ...s.muted, ...(curPage === 0 ? s.disabled : {}) }} onClick={() => setPage(curPage - 1)} disabled={curPage === 0}>‹ 이전</button>
              <span style={s.pagerInfo}>{curPage + 1} / {totalPages} 페이지 · 총 {jobs.length}개</span>
              <button style={{ ...s.muted, ...(curPage >= totalPages - 1 ? s.disabled : {}) }} onClick={() => setPage(curPage + 1)} disabled={curPage >= totalPages - 1}>다음 ›</button>
              <button style={{ ...s.muted, ...(curPage >= totalPages - 1 ? s.disabled : {}) }} onClick={() => setPage(totalPages - 1)} disabled={curPage >= totalPages - 1}>마지막 »</button>
            </div>
          ) : null}
          {pagedJobs.map((j) => (
          <div key={j.job_id} style={{ ...s.jobRow, ...(INFLIGHT.has(j.status) ? { opacity: 0.75 } : {}) }}>
            <span style={s.jobTitle} title={j.title}>{j.title}</span>
            <span style={s.jobMeta}>
              {STATUS_LABEL[j.status] ?? j.status}
              {j.size_bytes != null ? ` · ${formatBytes(j.size_bytes)}` : ""}
              {j.created_at ? ` · ${new Date(j.created_at).toLocaleString()}` : ""}
            </span>
            {confirmId === j.job_id ? (
              <>
                {INFLIGHT.has(j.status) ? (
                  <span style={s.warnInline}>처리 중 — 삭제 시 진행이 중단됩니다</span>
                ) : null}
                <button style={{ ...s.danger, ...(busy ? s.disabled : {}) }} onClick={() => void performDelete(j.job_id)} disabled={busy}>
                  정말 삭제
                </button>
                <button style={s.muted} onClick={() => setConfirmId(null)} disabled={busy}>취소</button>
              </>
            ) : (
              <button style={s.muted} onClick={() => setConfirmId(j.job_id)} disabled={busy}>삭제</button>
            )}
          </div>
          ))}
        </>
      )}
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  wrap: { padding: "14px 20px", display: "flex", flexDirection: "column", gap: 10 },
  headRow: { display: "flex", alignItems: "center", justifyContent: "space-between" },
  title: { fontSize: 15, fontWeight: 600, margin: 0, color: "var(--ys-text-strong)" },
  hint: { fontSize: 13, color: "var(--ys-text-faint)", margin: 0 },
  row: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" },
  muted: { padding: "6px 12px", borderRadius: "var(--ys-radius-control)", border: "1px solid var(--ys-border-strong)", background: "transparent", color: "var(--ys-text-label)", cursor: "pointer", fontSize: 13 },
  danger: { padding: "5px 12px", borderRadius: "var(--ys-radius-control)", border: "1px solid var(--ys-danger)", background: "var(--ys-danger)", color: "#fff", fontWeight: 600, cursor: "pointer", fontSize: 12 },
  disabled: { opacity: 0.5, cursor: "not-allowed" },
  success: { color: "var(--ys-success-text)", fontSize: 13, margin: "4px 0 0" },
  error: { color: "var(--ys-danger-text)", fontSize: 13, margin: "4px 0 0" },
  confirmBox: { padding: "10px 14px", borderRadius: "var(--ys-radius-md)", border: "1px solid var(--ys-warning-border)", background: "var(--ys-warning-bg)", display: "flex", flexDirection: "column", gap: 8 },
  confirmMsg: { fontSize: 13, color: "var(--ys-warning-text)", fontWeight: 600 },
  confirmList: { margin: 0, paddingLeft: 18, maxHeight: 180, overflowY: "auto", fontSize: 12, color: "var(--ys-text-body)", display: "flex", flexDirection: "column", gap: 2 },
  warnInline: { fontSize: 12, color: "var(--ys-warning-text)" },
  jobRow: { display: "flex", alignItems: "center", gap: 12, padding: "6px 0", borderBottom: "1px solid var(--ys-border-subtle)" },
  // 파일명은 잘라내지 않고 전부 보여준다(…말줄임 금지) — 긴 이름은 줄바꿈.
  // 언더스코어 파일명은 공백이 없어 breakAll이 필요하다.
  jobTitle: { fontSize: 13, fontWeight: 600, minWidth: 160, wordBreak: "break-all" },
  jobMeta: { fontSize: 12, color: "var(--ys-text-faint)", flex: "1 1 auto" },
  pager: { display: "flex", alignItems: "center", justifyContent: "center", gap: 8, margin: "2px 0 8px", flexWrap: "wrap" },
  pagerInfo: { fontSize: 12, color: "var(--ys-text-faint)", minWidth: 150, textAlign: "center" },
};
// === ANCHOR: VIDEO_JOBS_PANEL_END ===
