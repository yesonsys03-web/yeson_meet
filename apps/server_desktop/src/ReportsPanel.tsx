// === ANCHOR: REPORTS_PANEL_START ===
// 서버 콘솔의 "보고서 관리" 패널. 회의가 끝나면 자막→보고서(회의록/요약)가 서버
// 스토리지에 쌓인다 — 파일이 서버에 있으니 조회/리뷰/익스포트/삭제도 서버
// control plane에서 한다(VideoJobsPanel/BackupPanel/DevicePanel과 동일 사상).
// 무인증 loopback REST라 로그인 게이트가 없다. 리뷰는 서버가 렌더한 HTML을
// 샌드박스 iframe으로 그대로 보여준다(마크업을 클라에서 재구성하지 않음).
// 익스포트는 바이트를 받아 네이티브 저장 다이얼로그로 기록한다 — 이 앱은
// tauri-plugin-dialog/tauri-plugin-fs를 쓰지 않는 plugin-free 컨벤션이라(
// backup_dialog::pick_backup_dir, diagnostics::save_app_log 참고) 전용 Rust
// 커맨드 `save_report_bytes`를 invoke한다. 삭제 확인은 window.confirm 대신
// 인라인 확인(WebView2에서 네이티브 다이얼로그가 막히거나 튀는 문제를 피함) —
// "파일 삭제"는 1단계, "세션 삭제"는 자막·발화 기록까지 영구 삭제되므로 2단계.
import type { CSSProperties } from "react";
import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import {
  deleteReportFiles,
  deleteReportSession,
  fetchReportBytes,
  getReportStorage,
  listReports,
  reportViewUrl,
  type ReportFmt,
  type ReportKind,
  type ReportRow,
  type ReportStorage,
} from "./reportsAdmin";

type Props = { serverPort: number | null; running: boolean };

const FORMATS: ReportFmt[] = ["md", "html", "docx", "pdf"];

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function formatBytes(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)} GB`;
  if (n >= 1_000_000) return `${Math.round(n / 1_000_000)} MB`;
  if (n >= 1000) return `${Math.round(n / 1000)} KB`;
  return `${n} B`;
}

export default function ReportsPanel({ serverPort, running }: Props) {
  const [rows, setRows] = useState<ReportRow[]>([]);
  const [storage, setStorage] = useState<ReportStorage | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [viewKind, setViewKind] = useState<ReportKind>("report");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmFiles, setConfirmFiles] = useState<string | null>(null); // 행별 1단계 확인
  const [sessionConfirm, setSessionConfirm] = useState<{ id: string; step: 1 | 2 } | null>(null); // 행별 2단계 확인

  const refresh = useCallback(async () => {
    if (serverPort == null) return;
    try {
      const [r, st] = await Promise.all([listReports(serverPort), getReportStorage(serverPort)]);
      setRows(r);
      setStorage(st);
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, [serverPort]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onExport = useCallback(
    async (id: string, kind: ReportKind, fmt: ReportFmt) => {
      if (serverPort == null) return;
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const bytes = await fetchReportBytes(serverPort, id, kind, fmt);
        // 이 앱은 tauri-plugin-dialog/fs 대신 전용 Rust 커맨드로 저장 다이얼로그+쓰기를
        // 한 번에 처리한다(위 파일 헤더 설명 참고). bytes는 JSON 숫자 배열로 전달 —
        // Vec<u8> 단일 인자 raw-body 최적화는 default_name과 함께 못 쓰지만 보고서
        // 크기에서는 문제 없다.
        const path = await invoke<string | null>("save_report_bytes", {
          defaultName: `${kind}-${id}.${fmt}`,
          bytes: Array.from(bytes),
        });
        if (path) setNotice("저장됨");
      } catch (e) {
        setError(errText(e));
      } finally {
        setBusy(false);
      }
    },
    [serverPort],
  );

  const onDeleteFiles = useCallback(
    async (id: string) => {
      if (serverPort == null) return;
      setConfirmFiles(null);
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        await deleteReportFiles(serverPort, id);
        setNotice("보고서 파일 삭제됨");
        await refresh();
      } catch (e) {
        setError(errText(e));
      } finally {
        setBusy(false);
      }
    },
    [serverPort, refresh],
  );

  const onDeleteSession = useCallback(
    async (id: string) => {
      if (serverPort == null) return;
      setSessionConfirm(null);
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        await deleteReportSession(serverPort, id);
        if (selected === id) setSelected(null);
        setNotice("세션 전체 삭제됨");
        await refresh();
      } catch (e) {
        setError(errText(e));
      } finally {
        setBusy(false);
      }
    },
    [serverPort, refresh, selected],
  );

  if (!running || serverPort == null) {
    return (
      <div style={s.wrap}>
        <p style={s.hint}>서버를 먼저 시작하세요 — 보고서 관리는 실행 중인 서버에 연결합니다.</p>
      </div>
    );
  }

  return (
    <div style={s.wrap}>
      <div style={s.headRow}>
        <h2 style={s.title}>보고서 관리</h2>
        <button style={s.muted} onClick={() => void refresh()} disabled={busy}>새로고침</button>
      </div>

      {storage ? (
        <p style={s.hint}>
          총 사용량 <strong style={{ color: "var(--ys-text-strong)" }}>{formatBytes(storage.total_bytes)}</strong>
          {" · "}세션 {storage.session_count}개
        </p>
      ) : null}

      {notice ? <p style={s.success}>{notice}</p> : null}
      {error ? <p style={s.error}>{error}</p> : null}

      {rows.length === 0 ? (
        <p style={s.hint}>보고서가 없습니다.</p>
      ) : (
        <>
          <div style={s.headerRow}>
            <span style={s.colTitle}>제목</span>
            <span style={s.colStatus}>상태</span>
            <span style={s.colStarted}>시작</span>
            <span style={s.colSize}>크기</span>
            <span style={s.colActions}>동작</span>
          </div>
          {rows.map((r) => (
            <div key={r.session_id} style={{ ...s.reportRow, ...(selected === r.session_id ? s.reportRowSelected : {}) }}>
              <span style={{ ...s.colTitle, ...s.reportTitle }} title={r.title}>{r.title}</span>
              <span style={s.colStatus}>{r.report_ready ? "준비됨" : "미준비"}</span>
              <span style={s.colStarted}>{r.started_at ? new Date(r.started_at).toLocaleString() : "-"}</span>
              <span style={s.colSize}>{r.size_bytes != null ? formatBytes(r.size_bytes) : "-"}</span>
              <span style={s.colActions}>
                <button
                  style={s.muted}
                  onClick={() => { setSelected(r.session_id); setViewKind("report"); }}
                  disabled={busy}
                >
                  리뷰
                </button>

                {confirmFiles === r.session_id ? (
                  <>
                    <span style={s.warnInline}>파일 삭제?</span>
                    <button style={{ ...s.danger, ...(busy ? s.disabled : {}) }} onClick={() => void onDeleteFiles(r.session_id)} disabled={busy}>예</button>
                    <button style={s.muted} onClick={() => setConfirmFiles(null)} disabled={busy}>아니오</button>
                  </>
                ) : (
                  <button style={s.muted} onClick={() => setConfirmFiles(r.session_id)} disabled={busy}>파일 삭제</button>
                )}

                {sessionConfirm?.id === r.session_id && sessionConfirm.step === 1 ? (
                  <>
                    <span style={s.warnInline}>자막·발화 기록까지 영구 삭제됩니다</span>
                    <button style={{ ...s.danger, ...(busy ? s.disabled : {}) }} onClick={() => setSessionConfirm({ id: r.session_id, step: 2 })} disabled={busy}>다음</button>
                    <button style={s.muted} onClick={() => setSessionConfirm(null)} disabled={busy}>취소</button>
                  </>
                ) : sessionConfirm?.id === r.session_id && sessionConfirm.step === 2 ? (
                  <>
                    <span style={s.warnInline}>정말 영구 삭제할까요? 되돌릴 수 없습니다</span>
                    <button style={{ ...s.danger, ...(busy ? s.disabled : {}) }} onClick={() => void onDeleteSession(r.session_id)} disabled={busy}>영구 삭제</button>
                    <button style={s.muted} onClick={() => setSessionConfirm(null)} disabled={busy}>취소</button>
                  </>
                ) : (
                  <button style={{ ...s.danger, ...(busy ? s.disabled : {}) }} onClick={() => setSessionConfirm({ id: r.session_id, step: 1 })} disabled={busy}>세션 삭제</button>
                )}
              </span>
            </div>
          ))}
        </>
      )}

      {selected ? (
        <div style={s.reviewer}>
          <div style={s.reviewerTabs}>
            <button style={viewKind === "report" ? s.tabBtnActive : s.tabBtn} onClick={() => setViewKind("report")}>보고서</button>
            <button style={viewKind === "summary" ? s.tabBtnActive : s.tabBtn} onClick={() => setViewKind("summary")}>요약</button>
            <span style={s.spacer} />
            {FORMATS.map((f) => (
              <button key={f} style={{ ...s.muted, ...(busy ? s.disabled : {}) }} onClick={() => void onExport(selected, viewKind, f)} disabled={busy}>
                {f.toUpperCase()}
              </button>
            ))}
            <button style={s.muted} onClick={() => setSelected(null)}>닫기</button>
          </div>
          <iframe
            title="report-view"
            style={s.reviewerFrame}
            sandbox=""
            src={reportViewUrl(serverPort, selected, viewKind)}
          />
        </div>
      ) : null}
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  wrap: { padding: "14px 20px", display: "flex", flexDirection: "column", gap: 10 },
  headRow: { display: "flex", alignItems: "center", justifyContent: "space-between" },
  title: { fontSize: 15, fontWeight: 600, margin: 0, color: "var(--ys-text-strong)" },
  hint: { fontSize: 13, color: "var(--ys-text-faint)", margin: 0 },
  muted: { padding: "6px 12px", borderRadius: "var(--ys-radius-control)", border: "1px solid var(--ys-border-strong)", background: "transparent", color: "var(--ys-text-label)", cursor: "pointer", fontSize: 13 },
  danger: { padding: "5px 12px", borderRadius: "var(--ys-radius-control)", border: "1px solid var(--ys-danger)", background: "var(--ys-danger)", color: "#fff", fontWeight: 600, cursor: "pointer", fontSize: 12 },
  disabled: { opacity: 0.5, cursor: "not-allowed" },
  success: { color: "var(--ys-success-text)", fontSize: 13, margin: "4px 0 0" },
  error: { color: "var(--ys-danger-text)", fontSize: 13, margin: "4px 0 0" },
  warnInline: { fontSize: 12, color: "var(--ys-warning-text)" },
  headerRow: { display: "flex", alignItems: "center", gap: 12, padding: "4px 0", borderBottom: "1px solid var(--ys-border-strong)", fontSize: 12, fontWeight: 600, color: "var(--ys-text-faint)" },
  reportRow: { display: "flex", alignItems: "center", gap: 12, padding: "6px 0", borderBottom: "1px solid var(--ys-border-subtle)" },
  reportRowSelected: { background: "var(--ys-surface-hover, rgba(127,127,127,0.08))" },
  reportTitle: { fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  colTitle: { fontSize: 13, flex: "1 1 auto", minWidth: 120, maxWidth: 320 },
  colStatus: { fontSize: 12, color: "var(--ys-text-faint)", flex: "0 0 80px" },
  colStarted: { fontSize: 12, color: "var(--ys-text-faint)", flex: "0 0 150px" },
  colSize: { fontSize: 12, color: "var(--ys-text-faint)", flex: "0 0 70px" },
  colActions: { display: "flex", alignItems: "center", gap: 6, flex: "0 0 auto", flexWrap: "wrap" },
  reviewer: { display: "flex", flexDirection: "column", gap: 8, marginTop: 8, border: "1px solid var(--ys-border-strong)", borderRadius: "var(--ys-radius-md)", padding: 10 },
  reviewerTabs: { display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" },
  tabBtn: { padding: "5px 12px", borderRadius: "var(--ys-radius-control)", border: "1px solid var(--ys-border-strong)", background: "transparent", color: "var(--ys-text-label)", cursor: "pointer", fontSize: 12 },
  tabBtnActive: { padding: "5px 12px", borderRadius: "var(--ys-radius-control)", border: "1px solid var(--ys-border-strong)", background: "var(--ys-text-strong)", color: "var(--ys-surface, #fff)", fontWeight: 600, cursor: "pointer", fontSize: 12 },
  spacer: { flex: "1 1 auto" },
  reviewerFrame: { width: "100%", height: 480, border: "1px solid var(--ys-border-subtle)", borderRadius: "var(--ys-radius-md)", background: "#fff" },
};
// === ANCHOR: REPORTS_PANEL_END ===
