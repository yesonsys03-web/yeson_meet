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
  fetchAllPdfLabels, getPdfPanels, patchPdfLabel, pdfPageUrl, purgeDanglingLabels,
  rebakePdfJob, repointPdfLabel, retranslatePdfJob,
  type PdfJobSummary, type PdfLabelItem, type PdfLabelsResponse,
  type PdfPanelsResponse,
} from "./pdfApi";
import {
  dragCommitRect, editorReducer, initialEditorState, nextPageWithout,
  pagesWithoutRows, resolveSubmitText, rowsOnPage,
  type EditorState, type LabelRow,
} from "./pdfLabelEditorState";
import {
  clientPointToPt, hitTestPanel, ptToPx, pxToPt, rectToStyle, relToPoint,
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

/**
 * 확인 대화 — 이 리포의 관례대로 `plugin-dialog`를 쓴다.
 *
 * `window.confirm`은 웹뷰가 막으면 **조용히 false**가 되어 "눌렀는데 아무 일도
 * 안 일어남"이 된다. `PdfTranslatePanel.tsx`가 이미 두 번(저장·취소) 고친 결함
 * 클래스라 같은 함정을 새로 만들지 않는다. 브라우저 dev에서는 폴백한다.
 */
async function confirmAction(message: string, title: string): Promise<boolean> {
  type TauriGlobal = typeof globalThis & { __TAURI_INTERNALS__?: unknown };
  try {
    if ((globalThis as TauriGlobal).__TAURI_INTERNALS__) {
      const { ask } = await import("@tauri-apps/plugin-dialog");
      return await ask(message, { title, kind: "warning" });
    }
  } catch {
    /* 플러그인을 못 부르면 아래 폴백 */
  }
  return window.confirm(message);
}

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
  // 시작점(clientX/Y)을 들고 간다 — `movementX`는 엔진에 따라 **속성은 있는데
  // 늘 0**인 경우가 있어(WKWebView 계열) 누적하면 delta가 영영 0이 된다.
  // 시작점 대비 절대 차이로 재면 그 함정을 아예 밟지 않고, 이벤트를 몇 개
  // 놓쳐도 위치가 어긋나지 않는다.
  const [drag, setDrag] = useState<
    { id: string; startX: number; startY: number; dx: number; dy: number } | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const draftRef = useRef<HTMLDivElement | null>(null);

  const reload = useCallback(async () => {
    try {
      // 전량을 받아 목록표·오버레이가 같은 데이터를 본다(1037p 문서에서
      // 항목 1321개 = 수백 KB 수준이라 페이지네이션은 표시에만 쓴다).
      // 서버 응답 상한이 500이라 **여러 번 나눠 받아야** 전량이 된다 —
      // 한 번만 부르면 뒷페이지가 목록에서도 화면에서도 사라진다.
      setLabels(await fetchAllPdfLabels(job.job_id, { kind: "all" }));
    } catch (e) {
      setMessage(explainPdfError(e));
    }
  }, [job.job_id]);

  useEffect(() => { void reload(); }, [reload]);

  // 창 크기·배율이 바뀌면 이미지의 표시 배율이 달라진다. 오버레이 좌표는
  // **렌더 중에** `getBoundingClientRect()`로 파생하므로, 다시 그리게 하지
  // 않으면 박스가 옛 자리에 남는다 — 지금까지는 원본/번역본을 눌러 `img`가
  // 새로 load될 때만 맞춰졌다(실사용 보고). 이미지 자체를 관찰하면 창 크기,
  // 목록 칸 높이 변화, 확대/축소가 한 경로로 모인다.
  const [, redrawOverlays] = useState(0);
  useEffect(() => {
    const el = imgRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => redrawOverlays((n) => n + 1));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

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
  // 종류 필터는 목록과 **화면을 함께** 지배한다. 한쪽에만 걸면 "화면엔 보이는데
  // 목록에서 못 찾는 박스"가 생겨(예: 판넬 라벨 필터인데 action 주석 박스가 그려짐)
  // 사람이 그게 무엇인지 알 수 없다. 전체를 보려면 `전체`로 바꾼다.
  const byKind = useMemo(
    () => rows.filter((r) => state.kind === "all" || r.kind === state.kind),
    [rows, state.kind]);
  // 텍스트 검색은 **목록에만** 건다 — 화면에서까지 숨기면 검색 중에 주변 맥락
  // (옆 라벨과의 간격)이 사라져 위치를 못 잡는다.
  const filtered = useMemo(() => {
    const needle = state.query.trim().toLowerCase();
    if (!needle) return byKind;
    return byKind.filter((r) => r.text.toLowerCase().includes(needle)
      || r.source_text.toLowerCase().includes(needle));
  }, [byKind, state.query]);
  const onPage = useMemo(() => rowsOnPage(byKind, state.page), [byKind, state.page]);
  // 번역 누락은 목록 **안**이 아니라 목록에 **없는 페이지**로 나타난다
  // (pagesWithoutRows 주석 참고) — 종류 필터를 통과한 행을 기준으로 센다.
  const blankPages = useMemo(
    () => pagesWithoutRows(byKind, pageCount), [byKind, pageCount]);
  const pageIsBlank = useMemo(
    () => blankPages.includes(state.page), [blankPages, state.page]);
  const prevBlank = nextPageWithout(blankPages, state.page, -1);
  const nextBlank = nextPageWithout(blankPages, state.page, 1);
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

  const commitDrag = (row: LabelRow, dx: number, dy: number): Promise<void> => {
    const box = boxOf(imgRef.current);
    const size = panels?.page_size;
    if (!box || !size) return Promise.resolve();
    const rect = dragCommitRect(row.rect, dx, dy, size);
    return run(() => patchPdfLabel(job.job_id, row.id,
      { rect, edits_version: version }));
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

  // 새 라벨 폼은 이미지 **아래**에 그려진다 — 세로로 긴 페이지에서는 화면 밖에
  // 생겨서, 판넬을 눌러도 "아무 일도 안 일어난다"로 보인다(실사용 보고).
  // 새로 누를 때만 끌어온다 — 글자를 칠 때마다 스크롤하면 성가시다.
  const draftKey = draft ? `${draft.panelIndex}:${draft.rel[0]}:${draft.rel[1]}` : "";
  useEffect(() => {
    if (draftKey) draftRef.current?.scrollIntoView({ block: "nearest" });
  }, [draftKey]);

  // 어디를 눌렀는지 그림 위에 남긴다 — 폼이 멀리 있어 잊기 쉽다.
  const draftMark = (() => {
    const panel = draft ? panels?.panels[draft.panelIndex]?.rect : undefined;
    const box = boxOf(imgRef.current);
    if (!draft || !panel || !box) return null;
    const pt = relToPoint(panel as Rect, draft.rel);
    return { left: ptToPx(pt.x, box), top: ptToPx(pt.y, box) };
  })();

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

      {/* ── 누락 순회 ─────────────────────────────────────────────────────
          번역이 빠진 자리는 목록에 행으로 남지 않는다(미번역 라벨은 주석이
          아예 안 만들어지고, OCR이 못 읽은 손글씨는 블록조차 없다). 그래서
          "주석이 하나도 없는 페이지"만 건너뛰며 보는 것이 사람이 누락을 찾는
          가장 짧은 길이다 — 실측 113장 중 69장으로 좁혀진다. */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12,
        flexWrap: "wrap", marginBottom: 8 }}>
        <span style={{ color: "#94a3b8" }}>
          주석 없는 페이지 {blankPages.length} / {pageCount}
        </span>
        <button type="button" disabled={prevBlank === null}
          onClick={() => { if (prevBlank !== null) dispatch({ type: "gotoPage", page: prevBlank }); }}>
          ← 이전 누락 후보
        </button>
        <button type="button" disabled={nextBlank === null}
          onClick={() => { if (nextBlank !== null) dispatch({ type: "gotoPage", page: nextBlank }); }}>
          다음 누락 후보 →
        </button>
        {pageIsBlank ? badge(
          state.kind === "all" ? "이 페이지 주석 0개" : "이 페이지 판넬 라벨 0개",
          "#fbbf24") : null}
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
        <button type="button" disabled={busy || readOnly}
          onClick={() => void run(() => rebakePdfJob(job.job_id))}>
          번역본 다시 굽기
        </button>
        <button type="button" disabled={busy || job.status !== "done"}
          onClick={() => void (async () => {
            const manual = rows.filter((r) => r.origin === "manual").length;
            const edited = rows.filter((r) => r.edited).length;
            const ok = await confirmAction(
              `자동 라벨은 다시 만들어지고, 자동 라벨에 한 수정 ${edited}건은 무효가 됩니다.\n`
              + `수동 라벨 ${manual}건은 유지됩니다. 계속할까요?`, "다시 번역");
            if (!ok) return;
            await run(() => retranslatePdfJob(job.job_id));
          })()}>다시 번역</button>
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

      {/* `alignItems`를 기본값(stretch)으로 둔다 — flex-start면 목록 칸이 제 내용
          높이에서 멈춰, 세로로 긴 페이지 옆에 커다란 빈 공간이 남는다. */}
      <div style={{ display: "flex", gap: 12 }}>
        {/* ── 좌: 목록표 ─────────────────────────────────────────────────── */}
        <div style={{ width: 320, flexShrink: 0,
          display: "flex", flexDirection: "column" }}>
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
          {/* 목록 높이는 **옆 이미지가 정한다.**
              `flex:1`만으로는 안 된다 — 부모 높이가 내용으로 정해지는 상황에서는
              목록이 제 길이만큼 늘어나 행 높이를 지배하고, 창을 줄여도 따라오지
              않는다(실사용 보고). 그래서 스크롤 상자를 `absolute`로 띄워
              **높이 계산에서 빼고**, 남는 자리를 채우게 한다. 그러면 행 높이는
              이미지가 정하고 목록은 그 안에서 접힌다. */}
          <div style={{ flex: 1, position: "relative", minHeight: 160 }}>
            <div style={{ position: "absolute", top: 0, right: 0, bottom: 0, left: 0,
              overflowY: "auto", border: "1px solid #334155", borderRadius: 4 }}>
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
        <div style={{ flex: 1, minWidth: 0 }}>
          {!readOnly && panels?.panels.length ? (
            <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 4 }}>
              판넬(파란 점선) 안을 클릭하면 그 자리에 라벨을 추가합니다 —
              OCR이 못 읽은 손글씨(IN → 들어온다)를 넣는 방법입니다.
              라벨 상자는 끌어서 옮길 수 있고, 놓는 순간 저장됩니다.
            </div>
          ) : null}
          {/* 절대배치 오버레이의 원점은 **이미지 좌상단**이어야 한다 — 안내문
              같은 형제를 같은 relative 상자에 두면 박스가 통째로 밀린다. */}
          <div style={{ position: "relative" }}>
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
            const live = drag && drag.id === r.id ? drag : null;
            const size = panels?.page_size;
            // 끄는 동안 **보이는 자리 = 저장될 자리**다 — 같은 함수를 통과시켜
            // 놓는 순간 박스가 튀지 않게 한다(페이지 밖 클램프까지 동일).
            const shown = live && size
              ? dragCommitRect(r.rect, live.dx, live.dy, size) : r.rect;
            const s = rectToStyle(shown, box);
            const active = r.id === state.selectedId;
            return (
              <div key={r.id} title={r.text}
                onPointerDown={(ev) => {
                  dispatch({ type: "selectRow", row: r });
                  if (readOnly || !r.editable) return;
                  ev.currentTarget.setPointerCapture(ev.pointerId);
                  setDrag({ id: r.id, startX: ev.clientX, startY: ev.clientY,
                    dx: 0, dy: 0 });
                }}
                onPointerMove={(ev) => {
                  if (!drag || drag.id !== r.id) return;
                  const b = boxOf(imgRef.current);
                  if (!b) return;
                  // 화면 px → pt 변환은 **순수 모듈**을 통과시킨다. 여기에
                  // 직접 쓰면 AC6의 ±0.6pt 단언이 이 코드를 잠그지 못한다.
                  setDrag({ ...drag,
                    dx: pxToPt(ev.clientX - drag.startX, b),
                    dy: pxToPt(ev.clientY - drag.startY, b) });
                }}
                onPointerUp={() => {
                  // 저장이 끝난 뒤에 미리보기를 놓는다 — 먼저 놓으면 서버
                  // 왕복 동안 옛 자리로 튀었다가 새 자리로 가는 깜박임이 보인다.
                  if (drag && drag.id === r.id && (drag.dx || drag.dy)) {
                    void commitDrag(r, drag.dx, drag.dy).finally(() => setDrag(null));
                  } else {
                    setDrag(null);
                  }
                }}
                onPointerCancel={() => setDrag(null)}
                style={{
                  position: "absolute", left: s.left, top: s.top,
                  width: s.width, height: s.height, boxSizing: "border-box",
                  border: `1px solid ${r.origin === "manual" ? "#4ade80" : "#f472b6"}`,
                  background: live ? "rgba(74,222,128,0.35)"
                    : active ? "rgba(56,189,248,0.25)" : "transparent",
                  cursor: readOnly || !r.editable ? "default" : "move",
                  // 끄는 도중 브라우저가 스크롤·선택으로 가로채지 못하게 한다.
                  touchAction: "none", userSelect: "none",
                }} />
            );
          })}
          {draftMark ? (
            <div style={{
              position: "absolute", left: draftMark.left - 6, top: draftMark.top - 6,
              width: 12, height: 12, borderRadius: "50%", pointerEvents: "none",
              border: "2px solid #4ade80", background: "rgba(74,222,128,0.25)",
            }} />
          ) : null}
          </div>
        </div>
      </div>

      {/* ── 하: 신규 입력 / 선택 항목 폼 ──────────────────────────────────── */}
      {draft ? (
        <div ref={draftRef} style={{ marginTop: 8, padding: 8,
          border: "1px solid #4ade80", borderRadius: 4, fontSize: 12 }}>
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
