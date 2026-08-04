// 판넬 라벨 편집 화면.
//
// 배경은 **원본 PNG**다. 번역본 위에 그리면 이미 구워진 주석 텍스트와 여기서
// 그리는 박스가 이중으로 보이고, 라벨을 지워도 배경에 그대로 남아 "지웠는데
// 그대로"가 된다. 번역본은 확인용 토글로만 둔다.
//
// 판정 로직(선택 전이·제출값·드래그 커밋·좌표 변환)은 전부 순수 모듈에 있다
// (`pdfLabelEditorState.ts`, `pdfLabelGeom.ts`). 이 파일은 그것들을 통과시키고
// 그리는 일만 한다 — 이 리포에 컴포넌트 테스트 인프라가 없기 때문이다.
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import {
  createPdfLabel, decodePanelLabel, deletePdfLabel, explainPdfError,
  getPdfPanels, listPdfLabels, patchPdfLabel, pdfPageUrl, purgeDanglingLabels,
  rebakePdfJob, repointPdfLabel, retranslatePdfJob,
  type PdfJobSummary, type PdfLabelItem, type PdfLabelsResponse,
  type PdfPanelsResponse,
} from "./pdfApi";
import {
  dragCommitRect, editorReducer, initialEditorState, resolveSubmitText,
  rowsOnPage, type EditorState, type LabelRow,
} from "./pdfLabelEditorState";
import {
  clientPointToPt, hitTestPanel, pxToPt, rectToStyle,
  type ImageBox, type Rect,
} from "./pdfLabelGeom";

const PAGE_ROWS = 200;

type Draft = {
  panelIndex: number;
  rel: [number, number];
  english: string;
  korean: string;
  decoded: string[] | null;
};

function boxOf(img: HTMLImageElement | null): ImageBox | null {
  if (!img || !img.naturalWidth) return null;
  const r = img.getBoundingClientRect();
  return { left: r.left, top: r.top, width: r.width, naturalWidth: img.naturalWidth };
}

export function PdfLabelEditor({ job, onClose }: {
  job: PdfJobSummary; onClose: () => void;
}) {
  const pageCount = job.page_count ?? 1;
  const [state, rawDispatch] = useReducer(
    (s: EditorState, a: Parameters<typeof editorReducer>[1]) =>
      editorReducer(s, a, pageCount), initialEditorState);
  const dispatch = rawDispatch as (a: Parameters<typeof editorReducer>[1]) => void;

  const [labels, setLabels] = useState<PdfLabelsResponse | null>(null);
  const [panels, setPanels] = useState<PdfPanelsResponse | null>(null);
  const [variant, setVariant] = useState<"source" | "translated">("source");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [drag, setDrag] = useState<{ id: string; dx: number; dy: number } | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  const reload = useCallback(async () => {
    try {
      // 전량을 받아 목록표·오버레이가 같은 데이터를 본다(1037p 문서에서
      // 항목 1321개 = 수백 KB 수준이라 페이지네이션은 표시에만 쓴다).
      setLabels(await listPdfLabels(job.job_id, { kind: "all", limit: 500 }));
    } catch (e) {
      setMessage(explainPdfError(e));
    }
  }, [job.job_id]);

  useEffect(() => { void reload(); }, [reload]);

  useEffect(() => {
    let alive = true;
    getPdfPanels(job.job_id, state.page)
      .then((p) => { if (alive) setPanels(p); })
      .catch((e) => { if (alive) { setPanels(null); setMessage(explainPdfError(e)); } });
    return () => { alive = false; };
  }, [job.job_id, state.page]);

  // 영문 입력 → 해독 미리보기(300ms 디바운스). 자동 라벨과 **같은 함수**를 쓰므로
  // 표기가 갈라지지 않는다.
  useEffect(() => {
    const english = draft?.english.trim();
    if (!english) return;
    const timer = setTimeout(() => {
      void decodePanelLabel(english.split("\n"))
        .then((r) => setDraft((d) => (d ? { ...d, decoded: r.lines } : d)))
        .catch(() => setDraft((d) => (d ? { ...d, decoded: null } : d)));
    }, 300);
    return () => clearTimeout(timer);
  }, [draft?.english]);

  const rows: LabelRow[] = useMemo(() => (labels?.items ?? []) as LabelRow[], [labels]);
  const filtered = useMemo(() => {
    const needle = state.query.trim().toLowerCase();
    return rows.filter((r) =>
      (state.kind === "all" || r.kind === state.kind)
      && (!needle || r.text.toLowerCase().includes(needle)
        || r.source_text.toLowerCase().includes(needle)));
  }, [rows, state.kind, state.query]);
  const onPage = useMemo(() => rowsOnPage(rows, state.page), [rows, state.page]);
  const selected = rows.find((r) => r.id === state.selectedId) ?? null;
  const version = labels?.edits_version ?? 0;

  // 편집 조작을 막아야 하는 상태 — 진행 중이면 서버가 409로 거절하므로 미리 잠근다.
  const readOnly = job.status !== "done" || Boolean(labels?.plan_missing);

  /** 409를 받아도 **사용자가 친 값을 지우지 않는다**(§4.9) — 새로 고침만 한다. */
  const run = useCallback(async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setMessage("");
    try {
      await fn();
      await reload();
    } catch (e) {
      const msg = explainPdfError(e);
      setMessage(msg.includes("먼저 저장")
        ? `${msg} — 값은 그대로 두었습니다. 다시 시도하세요.` : msg);
      await reload();
    } finally {
      setBusy(false);
    }
  }, [reload]);

  const onImageClick = (ev: React.MouseEvent<HTMLImageElement>) => {
    if (readOnly || !panels?.panels.length) return;
    const box = boxOf(imgRef.current);
    if (!box) return;
    const pt = clientPointToPt(ev.clientX, ev.clientY, box);
    const idx = hitTestPanel(pt, panels.panels.map((p) => p.rect));
    const hit = idx === null ? undefined : panels.panels[idx];
    if (idx === null || !hit) return;
    const panel = hit.rect;
    setDraft({
      panelIndex: idx,
      rel: [(pt.x - panel[0]) / Math.max(1e-6, panel[2] - panel[0]),
        (pt.y - panel[1]) / Math.max(1e-6, panel[3] - panel[1])],
      english: "", korean: "", decoded: null,
    });
  };

  const commitDrag = (row: LabelRow, dx: number, dy: number) => {
    const box = boxOf(imgRef.current);
    const size = panels?.page_size;
    if (!box || !size) return;
    const rect = dragCommitRect(row.rect, dx, dy, size);
    void run(() => patchPdfLabel(job.job_id, row.id, { rect, edits_version: version }));
  };

  const submitDraft = () => {
    if (!draft) return;
    const text = resolveSubmitText(draft.decoded, draft.korean);
    if (!text) { setMessage("해독되지 않았습니다 — 한글을 직접 입력하세요"); return; }
    void run(async () => {
      await createPdfLabel(job.job_id, {
        page: state.page, panel_index: draft.panelIndex, rel: draft.rel,
        source_text: draft.english.trim(), text, edits_version: version,
      });
      setDraft(null);
    });
  };

  const badge = (text: string, color: string) => (
    <span style={{
      fontSize: 11, padding: "2px 6px", borderRadius: 4,
      border: `1px solid ${color}`, color, marginRight: 6,
    }}>{text}</span>
  );

  return (
    <div style={{ marginTop: 8, borderTop: "1px solid #334155", paddingTop: 8 }}>
      {/* ── 상단: 페이지 이동 + 배지 ─────────────────────────────────────── */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12,
        flexWrap: "wrap", marginBottom: 8 }}>
        <strong>{job.title} — 라벨 편집</strong>
        <button type="button" onClick={() => dispatch({ type: "stepPage", delta: -1 })}
          disabled={state.page <= 0}>← 이전</button>
        <input type="number" min={1} max={pageCount} value={state.page + 1}
          onChange={(e) => dispatch({ type: "gotoPage", page: Number(e.target.value) - 1 })}
          style={{ width: 72, fontSize: 12 }} />
        <span>/ {pageCount}</span>
        <button type="button" onClick={() => dispatch({ type: "stepPage", delta: 1 })}
          disabled={state.page >= pageCount - 1}>다음 →</button>
        <label><input type="radio" checked={variant === "source"}
          onChange={() => setVariant("source")} /> 원본</label>
        <label><input type="radio" checked={variant === "translated"}
          disabled={job.status !== "done"}
          onChange={() => setVariant("translated")} /> 번역본(확인용)</label>
        <span style={{ marginLeft: "auto" }}>
          {labels?.stale ? badge("번역본이 편집보다 오래됨", "#fbbf24") : null}
          {labels?.dangling.length ? badge(`무효가 된 수정 ${labels.dangling.length}`, "#f87171") : null}
          {labels?.unresolved.length ? badge(`주소를 잃은 라벨 ${labels.unresolved.length}`, "#f87171") : null}
          {panels && !panels.is_panel_page ? badge("판넬 없는 페이지", "#64748b") : null}
        </span>
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
        <button type="button" disabled={busy || readOnly}
          onClick={() => void run(() => rebakePdfJob(job.job_id))}>
          번역본 다시 굽기
        </button>
        <button type="button" disabled={busy || job.status !== "done"}
          onClick={() => {
            const manual = rows.filter((r) => r.origin === "manual").length;
            const edited = rows.filter((r) => r.edited).length;
            if (!window.confirm(
              `자동 라벨은 다시 만들어지고, 자동 라벨에 한 수정 ${edited}건은 무효가 됩니다.\n`
              + `수동 라벨 ${manual}건은 유지됩니다. 계속할까요?`)) return;
            void run(() => retranslatePdfJob(job.job_id));
          }}>다시 번역</button>
        {labels?.dangling.length ? (
          <button type="button" disabled={busy || readOnly}
            onClick={() => void run(() => purgeDanglingLabels(job.job_id, version))}>
            무효가 된 수정 정리 ({labels.dangling.length})
          </button>
        ) : null}
        <button type="button" onClick={onClose} style={{ marginLeft: "auto" }}>닫기</button>
      </div>

      {labels?.plan_missing ? (
        <p style={{ color: "#fbbf24", fontSize: 12 }}>
          이 작업에는 편집 정보가 없습니다 — `다시 번역`을 실행하면 편집할 수 있습니다.
        </p>
      ) : null}
      {job.status !== "done" ? (
        <p style={{ color: "#94a3b8", fontSize: 12 }}>
          작업이 진행 중입니다 ({job.progress}%) — 끝나면 편집할 수 있습니다.
        </p>
      ) : null}
      {message ? <p style={{ color: "#f87171", fontSize: 12 }}>{message}</p> : null}

      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        {/* ── 좌: 목록표 ─────────────────────────────────────────────────── */}
        <div style={{ width: 320, flexShrink: 0 }}>
          <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
            <select value={state.kind} style={{ fontSize: 12 }}
              onChange={(e) => dispatch({ type: "setKind",
                kind: e.target.value as "panel_label" | "all" })}>
              <option value="panel_label">판넬 라벨</option>
              <option value="all">전체(대사·액션 포함)</option>
            </select>
            <input placeholder="텍스트 검색" value={state.query} style={{ fontSize: 12, flex: 1 }}
              onChange={(e) => dispatch({ type: "setQuery", query: e.target.value })} />
          </div>
          <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 4 }}>
            {filtered.length}건 · {state.offset + 1}–
            {Math.min(state.offset + PAGE_ROWS, filtered.length)} 표시
          </div>
          <div style={{ maxHeight: 420, overflowY: "auto",
            border: "1px solid #334155", borderRadius: 4 }}>
            {filtered.slice(state.offset, state.offset + PAGE_ROWS).map((r) => (
              <button key={r.id} type="button"
                onClick={() => dispatch({ type: "selectRow", row: r })}
                style={{
                  display: "block", width: "100%", textAlign: "left", fontSize: 12,
                  padding: "4px 6px", border: "none", cursor: "pointer",
                  background: r.id === state.selectedId ? "#1e293b" : "transparent",
                  color: r.editable ? "inherit" : "#64748b",
                }}>
                <span style={{ color: "#94a3b8" }}>p{r.page + 1}</span>{" "}
                {r.origin === "manual" ? "✎" : ""}{r.edited ? "*" : ""} {r.text}
              </button>
            ))}
          </div>
          <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
            <button type="button" disabled={state.offset <= 0}
              onClick={() => dispatch({ type: "setOffset", offset: state.offset - PAGE_ROWS })}>
              이전 {PAGE_ROWS}
            </button>
            <button type="button"
              disabled={state.offset + PAGE_ROWS >= filtered.length}
              onClick={() => dispatch({ type: "setOffset", offset: state.offset + PAGE_ROWS })}>
              다음 {PAGE_ROWS}
            </button>
          </div>
        </div>

        {/* ── 중: 페이지 + 판넬 경계 + 라벨 박스 ───────────────────────────── */}
        <div style={{ position: "relative", flex: 1, minWidth: 0 }}>
          <img ref={imgRef} src={pdfPageUrl(job.job_id, state.page, variant)}
            alt={`p${state.page + 1}`} onClick={onImageClick}
            onLoad={() => setPanels((p) => (p ? { ...p } : p))}
            style={{ maxWidth: "100%", display: "block",
              border: "1px solid #1e293b",
              cursor: readOnly || !panels?.panels.length ? "default" : "crosshair" }} />
          {(panels?.panels ?? []).map((p) => {
            const box = boxOf(imgRef.current);
            if (!box) return null;
            const s = rectToStyle(p.rect as Rect, box);
            return (
              <div key={`panel-${p.index}`} style={{
                position: "absolute", left: s.left, top: s.top,
                width: s.width, height: s.height, pointerEvents: "none",
                border: "1px dashed #38bdf8", boxSizing: "border-box",
              }}>
                <span style={{ position: "absolute", left: 2, top: 2, fontSize: 11,
                  background: "#0f172a", color: "#38bdf8", padding: "0 4px" }}>
                  {p.index + 1}
                </span>
              </div>
            );
          })}
          {onPage.map((r) => {
            const box = boxOf(imgRef.current);
            if (!box) return null;
            const s = rectToStyle(r.rect, box);
            const active = r.id === state.selectedId;
            return (
              <div key={r.id} title={r.text}
                onPointerDown={(ev) => {
                  dispatch({ type: "selectRow", row: r });
                  if (readOnly || !r.editable) return;
                  ev.currentTarget.setPointerCapture(ev.pointerId);
                  setDrag({ id: r.id, dx: 0, dy: 0 });
                }}
                onPointerMove={(ev) => {
                  if (!drag || drag.id !== r.id) return;
                  const b = boxOf(imgRef.current);
                  if (!b) return;
                  // 화면 px → pt 변환은 **순수 모듈**을 통과시킨다. 여기에
                  // 직접 쓰면 AC6의 ±0.6pt 단언이 이 코드를 잠그지 못한다.
                  setDrag({ id: r.id,
                    dx: drag.dx + pxToPt(ev.movementX, b),
                    dy: drag.dy + pxToPt(ev.movementY, b) });
                }}
                onPointerUp={() => {
                  if (drag && drag.id === r.id && (drag.dx || drag.dy)) {
                    commitDrag(r, drag.dx, drag.dy);
                  }
                  setDrag(null);
                }}
                style={{
                  position: "absolute", left: s.left, top: s.top,
                  width: s.width, height: s.height, boxSizing: "border-box",
                  border: `1px solid ${r.origin === "manual" ? "#4ade80" : "#f472b6"}`,
                  background: active ? "rgba(56,189,248,0.25)" : "transparent",
                  cursor: readOnly || !r.editable ? "default" : "move",
                }} />
            );
          })}
        </div>
      </div>

      {/* ── 하: 신규 입력 / 선택 항목 폼 ──────────────────────────────────── */}
      {draft ? (
        <div style={{ marginTop: 8, padding: 8, border: "1px solid #334155",
          borderRadius: 4, fontSize: 12 }}>
          <strong>새 라벨 — {state.page + 1}쪽 {draft.panelIndex + 1}번 판넬</strong>
          <div style={{ display: "flex", gap: 8, marginTop: 6, alignItems: "center",
            flexWrap: "wrap" }}>
            <label>원문(영문)
              <input autoFocus value={draft.english} style={{ marginLeft: 4, fontSize: 12 }}
                onChange={(e) => setDraft({ ...draft, english: e.target.value })} />
            </label>
            <span style={{ color: draft.decoded ? "#4ade80" : "#94a3b8" }}>
              → {draft.decoded ? draft.decoded.join(" / ") : "해독 안 됨"}
            </span>
            <label>한글로 덮어쓰기
              <input value={draft.korean} style={{ marginLeft: 4, fontSize: 12 }}
                placeholder={draft.decoded ? draft.decoded.join("\n") : "직접 입력"}
                onChange={(e) => setDraft({ ...draft, korean: e.target.value })} />
            </label>
            <button type="button" disabled={busy} onClick={submitDraft}>추가</button>
            <button type="button" onClick={() => setDraft(null)}>취소</button>
          </div>
        </div>
      ) : null}

      {selected ? (
        <div style={{ marginTop: 8, padding: 8, border: "1px solid #334155",
          borderRadius: 4, fontSize: 12 }}>
          <strong>
            {selected.origin === "manual" ? "수동" : "자동"} 라벨 · p{selected.page + 1}
            {selected.panel_index !== null ? ` · ${selected.panel_index + 1}번 판넬` : ""}
          </strong>
          <span style={{ color: "#64748b", marginLeft: 8 }}>
            pt [{selected.rect.map((v) => v.toFixed(1)).join(", ")}]
          </span>
          {!selected.editable ? (
            <span style={{ color: "#64748b", marginLeft: 8 }}>
              (이 종류는 읽기 전용입니다)
            </span>
          ) : null}
          <div style={{ display: "flex", gap: 8, marginTop: 6, alignItems: "center",
            flexWrap: "wrap" }}>
            <input defaultValue={selected.text} key={selected.id}
              style={{ fontSize: 12, flex: 1, minWidth: 200 }}
              disabled={readOnly || !selected.editable}
              onBlur={(e) => {
                if (e.target.value !== selected.text) {
                  void run(() => patchPdfLabel(job.job_id, selected.id,
                    { text: e.target.value, edits_version: version }));
                }
              }} />
            {selected.origin === "manual" && panels?.panels.length ? (
              <label>판넬 재지정
                <select value={selected.panel_index ?? 0} style={{ marginLeft: 4, fontSize: 12 }}
                  disabled={readOnly}
                  onChange={(e) => void run(() => repointPdfLabel(
                    job.job_id, selected.id,
                    { page: state.page, panel_index: Number(e.target.value),
                      edits_version: version }))}>
                  {panels.panels.map((p) => (
                    <option key={p.index} value={p.index}>{p.index + 1}번</option>
                  ))}
                </select>
              </label>
            ) : null}
            <button type="button" disabled={busy || readOnly || !selected.editable}
              onClick={() => void run(async () => {
                await deletePdfLabel(job.job_id, selected.id, version);
                dispatch({ type: "clearSelection" });
              })}>삭제</button>
          </div>
        </div>
      ) : null}

      {labels?.unresolved.length ? (
        <div style={{ marginTop: 8, fontSize: 12, color: "#fbbf24" }}>
          주소를 잃은 라벨 — 판넬이 되돌아오면 자동 복귀합니다. 지금 고치려면
          해당 행을 고르고 `판넬 재지정`을 쓰세요:
          <ul style={{ margin: "4px 0 0 16px" }}>
            {labels.unresolved.map((u) => (
              <li key={u.id}>p{u.page + 1} · {u.text}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
