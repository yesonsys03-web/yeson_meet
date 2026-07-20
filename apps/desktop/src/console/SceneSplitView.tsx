import { useEffect, useState } from "react";
import { consoleStyles } from "./consoleStyles";
import { hasTauriRuntime } from "./useQrFullscreenShortcut";
import {
  anomalousLabels, applyFixes, confidentFixes, formatMs, mergeSegment,
  previewLabel, renameSegment, segmentThumbRange, tokenizeSlate,
  type LabelFix,
} from "./sceneSplitLogic";
import { SceneFilmstrip } from "./SceneFilmstrip";
import {
  exportScenes, getExportStatus, getRefineStatus, getScenes,
  overrideSceneSegments, refineScenes, scanScenes,
  setSceneRule, videoMediaUrl,
  type ExportStatus, type RefineStatus, type ScenesData, type SceneSegment,
} from "./videoApi";

type Mode = "scene" | "sequence";

export function SceneSplitView({ jobId, onBack }: { jobId: string; onBack: () => void }) {
  const [data, setData] = useState<ScenesData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("scene");
  const [seqIdx, setSeqIdx] = useState<number[]>([]);
  const [sceneIdx, setSceneIdx] = useState<number[]>([]);
  // 공백을 필드 구분자로 쓰는 슬레이트 대응(기본은 공백 비분해 — 백엔드와 동일).
  const [spaceDelim, setSpaceDelim] = useState(false);

  const delimiters = spaceDelim ? ["_", " ", "-"] : ["_", "-"];

  // 스캔 진행률 폴링 — 서버의 scanning/error 신호로 종료를 판단한다.
  // 긴 영상은 OCR이 수 분 걸릴 수 있어 고정 타임아웃 대신 무진척이 오래
  // 지속될 때만 포기한다. hadScan=true(재스캔)면 옛 scanned 데이터가 프레임
  // 추출 동안 남아 보일 수 있어(구 서버) scanning을 한 번 본 뒤의 scanned만
  // 완료로 인정한다.
  const pollScan = async (hadScan: boolean) => {
    let stalled = 0;
    let lastDone = -1;
    let sawScanning = false;
    for (let i = 0; i < 1200; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      const d = await getScenes(jobId);
      if (d.error) { setError(`스캔 실패: ${d.error}`); return; }
      if (d.scanning) {
        sawScanning = true;
        const done = d.ocr_done ?? 0;
        const total = d.total_frames ?? 0;
        setNotice(total > 0
          ? `슬레이트 판독 중… ${done}/${total} 프레임`
          : "프레임 추출 중…");
        stalled = done === lastDone ? stalled + 1 : 0;
        lastDone = done;
        // 진척이 200초(133회) 넘게 멈춰 있으면 포기(서버 이상).
        if (stalled > 133) { setError("스캔이 진행되지 않습니다. 서버 상태를 확인하세요."); return; }
      } else if (d.scanned && (sawScanning || !hadScan)) {
        setData(d);
        setNotice("스캔 완료 — 토큰을 지정하세요.");
        return;
      }
    }
    setError("스캔이 시간 내 끝나지 않았습니다.");
  };

  const refresh = async () => {
    const d = await getScenes(jobId);
    setData(d);
    // 서버에 저장된 규칙이 있으면 토큰 선택을 복원한다 — 화면 재진입 시 선택이
    // 초기화돼 "경계 계산"이 비활성(회색)으로 보이던 문제 수정.
    if (d.rule) {
      setSeqIdx(d.rule.seq_tokens ?? []);
      setSceneIdx(d.rule.scene_tokens ?? []);
      setSpaceDelim((d.rule.delimiters ?? []).includes(" "));
    }
    // 재진입 시 스캔이 이미 진행 중이면(다른 화면에서 걸어둔 스캔 등) 진행률
    // 폴링을 이어붙인다 — 시작 버튼만 덩그러니 보이던 공백 수정.
    if (d.scanning) {
      setBusy(true); setNotice("슬레이트 판독 중…");
      try {
        await pollScan(false);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally { setBusy(false); }
    }
  };
  useEffect(() => { void refresh(); }, [jobId]);

  // 대표 프레임 = 첫 비어있지 않은 OCR 텍스트
  const sample = data?.frames.find((f) => f.text)?.text ?? "";
  const tokens = tokenizeSlate(sample, delimiters);

  const runScan = async () => {
    const hadScan = Boolean(data?.scanned);
    setBusy(true); setError(null); setNotice("프레임 추출 중…");
    try {
      await scanScenes(jobId);  // 스캔은 비동기 — 이후 진행률 폴링
      await pollScan(hadScan);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const applyRule = async () => {
    if (!data) return;
    setBusy(true); setError(null);
    try {
      const res = await setSceneRule(jobId, {
        delimiters, seq_tokens: seqIdx, scene_tokens: sceneIdx,
      });
      setData({ ...(data as ScenesData), scanned: true,
                segments_scene: res.segments_scene,
                segments_sequence: res.segments_sequence });
      setSelectedSeg(null);
      setNotice(`경계 계산 완료 — 시퀀스 ${res.segments_sequence.length}개 · 씬 ${res.segments_scene.length}개. 이제 익스포트하면 최신 경계로 잘립니다.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const [refineProg, setRefineProg] = useState<RefineStatus | null>(null);

  const doRefine = async () => {
    setError(null); setNotice(null); setBusy(true);
    setRefineProg({ refining: true, done: 0, total: segments.length - 1, error: null });
    try {
      await refineScenes(jobId, mode);
      // 경계마다 이진탐색 OCR이라 시간이 걸린다 — 진행률 폴링.
      for (let i = 0; i < 3600; i++) {
        await new Promise((r) => setTimeout(r, 1500));
        const st = await getRefineStatus(jobId);
        setRefineProg(st);
        if (st.error) { setError(`정밀화 실패: ${st.error}`); return; }
        if (!st.refining) {
          const d = await getScenes(jobId);  // 정밀화된 경계 다시 불러오기
          setData(d);
          setNotice("경계 정밀화 완료 — 이제 프레임 단위로 잘립니다. 재익스포트하세요.");
          return;
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setRefineProg(null);
    }
  };

  const [exportProg, setExportProg] = useState<ExportStatus | null>(null);

  const doExport = async () => {
    setError(null); setNotice(null);
    let outDir: string | undefined;
    if (hasTauriRuntime()) {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const dir = await open({ directory: true, title: "저장 폴더 선택" });
      if (typeof dir !== "string") { setNotice("저장 폴더 선택이 취소되었습니다."); return; }
      outDir = dir;
    }
    setBusy(true);
    setExportProg({ exporting: true, done: 0, total: segments.length,
                    error: null, out_dir: outDir ?? null, files: [] });
    try {
      const res = await exportScenes(jobId, mode, outDir);
      // 진행률 폴링 — 재인코딩은 클립당 수 초 걸리므로 진행바로 표시한다.
      for (let i = 0; i < 3600; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        const st = await getExportStatus(jobId);
        setExportProg(st);
        if (st.error) { setError(`익스포트 실패: ${st.error}`); return; }
        if (!st.exporting) {
          setNotice(`${st.done}/${res.count}개 클립 익스포트 완료 (${st.out_dir ?? outDir ?? "서버 폴더"})`);
          return;
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setExportProg(null);
    }
  };

  const segments: SceneSegment[] = data
    ? (mode === "sequence" ? data.segments_sequence : data.segments_scene) : [];

  // 현재 모드의 구간 목록을 편집(병합/이름수정)해 data 상태에 반영한다. dirty면
  // "수정사항 저장"으로 서버에 PATCH해야 익스포트에 반영된다.
  const [dirty, setDirty] = useState(false);
  const setSegments = (next: SceneSegment[]) => {
    if (!data) return;
    setData(mode === "sequence"
      ? { ...data, segments_sequence: next }
      : { ...data, segments_scene: next });
    setDirty(true);
  };
  const mergeSeg = (i: number, into: "prev" | "next") => {
    setSegments(mergeSegment(segments, i, into));
    // 병합하면 배열이 줄어 기존 선택 인덱스가 다른 구간을 가리킨다 — 살아남은
    // 구간을 선택해 필름스트립 하이라이트와 경계 썸네일이 병합 결과(넓어진 범위,
    // 당겨진 시작 시각)를 곧바로 보여주게 한다.
    const survivor = into === "prev" ? Math.max(0, i - 1) : i;
    setSelectedSeg(survivor);
  };
  const renameSeg = (i: number, label: string) =>
    setSegments(renameSegment(segments, i, label));

  // 리스트에서 클릭한 구간 → 필름스트립 하이라이트 범위. 썸네일 클릭 → 팝업 시각.
  const [selectedSeg, setSelectedSeg] = useState<number | null>(null);
  const [previewMs, setPreviewMs] = useState<number | null>(null);
  const intervalMs = data?.interval_ms ?? 2000;
  const thumbCount = data?.frames.length ?? 0;
  const highlight = selectedSeg != null && segments[selectedSeg]
    ? segmentThumbRange(segments[selectedSeg]!.start_ms,
                        segments[selectedSeg]!.end_ms, intervalMs, thumbCount)
    : null;

  // OCR 오독 검출 — 씬 모드는 구간이 수백 개라 눈으로 못 훑는다. 라벨 모양이
  // 다수와 어긋나는 행만 모아 보여주고, 템플릿 재분해로 만든 교정안을 일괄 적용.
  const [onlyAnomalies, setOnlyAnomalies] = useState(false);
  const anomalies = anomalousLabels(segments.map((s) => s.label), delimiters);
  const anomalyIdx = anomalies.map((a) => a.index);
  const suggestionOf = new Map(anomalies.map((a) => [a.index, a]));
  // 탭을 바꿔도 인덱스는 원본 기준을 유지한다(병합/이름수정 콜백이 인덱스를 쓴다).
  const visibleIndices = onlyAnomalies ? anomalyIdx : null;

  // 일괄 적용은 곧바로 바꾸지 않는다 — 무엇이 어떻게 바뀌는지 before→after로
  // 먼저 보여주고, 체크한 것만 적용한다. 적용 후에도 한 번은 되돌릴 수 있다.
  const [pendingFixes, setPendingFixes] = useState<LabelFix[] | null>(null);
  const [fixChecked, setFixChecked] = useState<Set<number>>(new Set());
  const [undoSnapshot, setUndoSnapshot] = useState<SceneSegment[] | null>(null);

  // 모드가 바뀌면 구간 목록 자체가 달라진다 — 이전 모드에서 만든 미리보기·되돌리기
  // 스냅샷·필터·선택은 모두 무의미해지므로 지운다(씬별 목록이 시퀀스별 화면에
  // 남아 보이던 문제). applyFixes의 from 검사가 2차 방어선이다.
  useEffect(() => {
    setPendingFixes(null);
    setFixChecked(new Set());
    setUndoSnapshot(null);
    setOnlyAnomalies(false);
    setSelectedSeg(null);
  }, [mode]);

  const openFixPreview = () => {
    const fixes = confidentFixes(segments.map((s) => s.label), delimiters);
    setPendingFixes(fixes);
    setFixChecked(new Set(fixes.map((f) => f.index)));  // 기본 전체 선택
  };

  const confirmFixes = () => {
    if (!pendingFixes) return;
    const applied = pendingFixes.filter((f) => fixChecked.has(f.index));
    if (applied.length === 0) { setPendingFixes(null); return; }
    setUndoSnapshot(segments);  // 되돌리기용 스냅샷
    setSegments(applyFixes(segments, pendingFixes, fixChecked));
    setPendingFixes(null);
    setNotice(`이름 ${applied.length}건을 바꿨습니다 — 아직 저장 전입니다. `
      + `되돌리려면 아래 "되돌리기"를 누르세요.`);
  };

  const undoFixes = () => {
    if (!undoSnapshot) return;
    setSegments(undoSnapshot);
    setUndoSnapshot(null);
    setNotice("이름 변경을 되돌렸습니다.");
  };

  const saveEdits = async () => {
    setBusy(true); setError(null);
    try {
      await overrideSceneSegments(jobId, mode, segments);
      setDirty(false);
      setNotice("수정사항을 저장했습니다.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const toggleSeq = (i: number) => {
    setSeqIdx((prev) =>
      prev.includes(i) ? prev.filter((x) => x !== i)
                       : [...prev, i].sort((a, b) => a - b));
    setSceneIdx((prev) => prev.filter((x) => x !== i)); // 같은 토큰을 씬에서 제외 (상호배타)
  };
  const toggleScene = (i: number) => {
    setSceneIdx((prev) =>
      prev.includes(i) ? prev.filter((x) => x !== i)
                       : [...prev, i].sort((a, b) => a - b));
    setSeqIdx((prev) => prev.filter((x) => x !== i)); // 같은 토큰을 시퀀스에서 제외 (상호배타)
  };

  return (
    <div style={{ ...consoleStyles.panel, display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <button type="button" style={{ ...consoleStyles.mutedAction, flexShrink: 0 }}
          onClick={onBack}>← 결과보기로</button>
        <h2 style={{ ...consoleStyles.title, margin: 0 }}>씬별 분할</h2>
      </div>
      {error ? <p style={{ color: "#e5484d", margin: 0 }}>{error}</p> : null}
      {notice ? <p style={consoleStyles.statusInfo}>{notice}</p> : null}

      {!data?.scanned ? (
        <button type="button" style={consoleStyles.action} disabled={busy}
          onClick={() => void runScan()}>
          {busy ? "스캔 중…" : "슬레이트 스캔 시작"}
        </button>
      ) : (
        <>
          {/* 규칙 지정: 토큰 칩 */}
          <div>
            <p style={{ fontSize: 13, opacity: 0.75, margin: "0 0 6px" }}>
              대표 슬레이트: <code>{sample || "(판독 실패)"}</code> — 시퀀스/씬 토큰을 고르세요.
            </p>
            <label style={{ fontSize: 12, opacity: 0.8, display: "inline-flex",
                            alignItems: "center", gap: 5, marginBottom: 8 }}>
              <input type="checkbox" checked={spaceDelim}
                onChange={(e) => {
                  // 구분자가 바뀌면 토큰 경계가 달라져 인덱스 의미가 바뀐다 → 선택 초기화.
                  setSpaceDelim(e.target.checked);
                  setSeqIdx([]); setSceneIdx([]);
                }} />
              공백도 구분자로 나누기 (기본: 공백은 필드 안에 유지)
            </label>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {tokens.map((tok, i) => (
                <span key={i} style={{
                  padding: "3px 8px", borderRadius: 6, fontFamily: "monospace",
                  border: "1px solid rgba(255,255,255,0.15)",
                  background: sceneIdx.includes(i) ? "#2b6cb0"
                    : seqIdx.includes(i) ? "#2f855a" : "transparent",
                }}>
                  {tok}
                  <button type="button" style={{ marginLeft: 6, fontSize: 11 }}
                    onClick={() => toggleSeq(i)}>SEQ</button>
                  <button type="button" style={{ marginLeft: 4, fontSize: 11 }}
                    onClick={() => toggleScene(i)}>SCENE</button>
                </span>
              ))}
            </div>
            <p style={{ fontSize: 12, opacity: 0.6, marginTop: 6 }}>
              시퀀스 라벨 미리보기: <code>{previewLabel(tokens, Math.max(-1, ...seqIdx))}</code>
              {"  ·  "}씬 라벨: <code>{previewLabel(tokens, Math.max(-1, ...seqIdx, ...sceneIdx))}</code>
            </p>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
              <button type="button" style={consoleStyles.mutedAction}
                disabled={busy || seqIdx.length === 0}
                onClick={() => void applyRule()}>
                {busy ? "계산 중…" : "경계 계산"}
              </button>
              {/* OCR 재판독 — 판독 로직 개선·오염된 스캔 데이터 복구용. 기존
                  프레임 텍스트를 버리고 처음부터 다시 스캔한다. */}
              <button type="button" style={consoleStyles.mutedAction}
                disabled={busy}
                onClick={() => void runScan()}>
                다시 스캔
              </button>
              {seqIdx.length === 0 ? (
                <span style={{ fontSize: 12, color: "#e2b340" }}>
                  먼저 위에서 SEQ 토큰을 하나 이상 고르세요 (그래야 버튼이 활성화됩니다).
                </span>
              ) : null}
            </div>
          </div>

          {/* 모드 토글 + 필름스트립 */}
          <div style={{ display: "flex", gap: 16, alignItems: "center", fontSize: 13 }}>
            <label><input type="radio" checked={mode === "scene"}
              onChange={() => setMode("scene")} /> 씬별</label>
            <label><input type="radio" checked={mode === "sequence"}
              onChange={() => setMode("sequence")} /> 시퀀스별</label>
            <span style={{ opacity: 0.7 }}>{segments.length}개 구간</span>
            {/* 경계 정밀화 — 2초 샘플링의 ±1초 잔여를 프레임 단위로 좁힌다(경계마다
                이진탐색 OCR이라 수 분 소요, 현재 모드만). */}
            <button type="button" style={consoleStyles.mutedAction}
              disabled={busy || segments.length < 2}
              onClick={() => void doRefine()}>
              {refineProg?.refining
                ? `정밀화 중… ${refineProg.done}/${refineProg.total}`
                : "경계 정밀화 (프레임 단위)"}
            </button>
          </div>
          {refineProg?.refining ? (
            <div style={{ height: 6, borderRadius: 3, background: "rgba(255,255,255,0.12)" }}>
              <div style={{ height: 6, borderRadius: 3,
                            width: `${refineProg.total > 0
                              ? Math.round((refineProg.done / refineProg.total) * 100) : 0}%`,
                            background: "#4a9eda", transition: "width 0.3s" }} />
            </div>
          ) : null}
          {/* 구간 목록 탭 — 오독 의심 행만 모아 일괄 교정할 수 있게 한다. */}
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button type="button"
              style={onlyAnomalies ? consoleStyles.mutedAction : consoleStyles.action}
              onClick={() => { setOnlyAnomalies(false); setSelectedSeg(null); }}>
              전체 ({segments.length})
            </button>
            <button type="button"
              style={onlyAnomalies ? consoleStyles.action : consoleStyles.mutedAction}
              disabled={anomalies.length === 0}
              onClick={() => { setOnlyAnomalies(true); setSelectedSeg(null); }}>
              {anomalies.length > 0
                ? `⚠ 확인 필요 (${anomalies.length})` : "확인 필요 없음"}
            </button>
            {onlyAnomalies && anomalies.some((a) => a.suggestion && a.confident) ? (
              <button type="button" style={consoleStyles.mutedAction}
                onClick={openFixPreview}>
                제안 일괄 적용 ({anomalies.filter((a) => a.suggestion && a.confident).length})…
              </button>
            ) : null}
            {undoSnapshot ? (
              <button type="button" style={consoleStyles.mutedAction}
                onClick={undoFixes}>되돌리기</button>
            ) : null}
          </div>

          {/* 일괄 적용 확인 — 무엇이 어떻게 바뀌는지 보고 체크한 것만 적용한다. */}
          {pendingFixes ? (
            <div style={{ border: "1px solid rgba(255,255,255,0.15)", borderRadius: 6,
                          padding: 10, display: "flex", flexDirection: "column", gap: 6 }}>
              <strong style={{ fontSize: 13 }}>
                이렇게 바꿉니다 — 체크한 것만 적용됩니다 ({fixChecked.size}/{pendingFixes.length})
              </strong>
              <div style={{ maxHeight: 260, overflowY: "auto", display: "flex",
                            flexDirection: "column", gap: 3 }}>
                {pendingFixes.map((f) => (
                  <label key={f.index}
                         style={{ display: "flex", gap: 8, alignItems: "center",
                                  fontSize: 12, fontFamily: "monospace",
                                  padding: "3px 4px", borderRadius: 3,
                                  background: "rgba(255,255,255,0.04)" }}>
                    <input type="checkbox" checked={fixChecked.has(f.index)}
                      onChange={(e) => {
                        const next = new Set(fixChecked);
                        if (e.target.checked) next.add(f.index); else next.delete(f.index);
                        setFixChecked(next);
                      }} />
                    <span style={{ opacity: 0.55, flexShrink: 0 }}>
                      {formatMs(segments[f.index]?.start_ms ?? 0)}
                    </span>
                    <span style={{ color: "#e2b340", overflowWrap: "anywhere" }}>{f.from}</span>
                    <span style={{ opacity: 0.6, flexShrink: 0 }}>→</span>
                    <span style={{ color: "#3f9a5f", overflowWrap: "anywhere" }}>{f.to}</span>
                  </label>
                ))}
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <button type="button" style={consoleStyles.action}
                  disabled={fixChecked.size === 0}
                  onClick={confirmFixes}>적용 ({fixChecked.size})</button>
                <button type="button" style={consoleStyles.mutedAction}
                  onClick={() => setPendingFixes(null)}>취소</button>
                <span style={{ fontSize: 11, opacity: 0.6 }}>
                  적용해도 저장 전이라 "되돌리기"로 한 번 물릴 수 있습니다.
                </span>
              </div>
            </div>
          ) : null}
          {onlyAnomalies ? (
            <p style={{ fontSize: 12, opacity: 0.7, margin: 0 }}>
              라벨 모양이 다수와 어긋나는 구간입니다(주로 OCR이 구분자를 놓친 경우).
              제안이 있으면 라벨 오른쪽에 표시되고, 숫자가 남아 애매한 제안은
              일괄 적용에서 빠집니다 — 썸네일을 눌러 실제 프레임을 확인하세요.
            </p>
          ) : null}
          <SceneFilmstrip jobId={jobId} segments={segments}
            thumbCount={data.frames.length}
            intervalMs={intervalMs}
            totalMs={(data.frames.at(-1)?.t_ms ?? 0) + intervalMs}
            onMerge={mergeSeg} onRename={renameSeg}
            selectedIndex={selectedSeg} highlight={highlight}
            visibleIndices={visibleIndices}
            suggestions={suggestionOf}
            onSelectSegment={setSelectedSeg} onThumbClick={setPreviewMs} />
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button type="button" style={consoleStyles.mutedAction}
              disabled={busy || !dirty}
              onClick={() => void saveEdits()}>
              {dirty ? "수정사항 저장" : "저장됨"}
            </button>
            <button type="button" style={consoleStyles.action}
              disabled={busy || segments.length === 0}
              onClick={() => void doExport()}>
              {exportProg?.exporting
                ? `익스포트 중… ${exportProg.done}/${exportProg.total}`
                : `${segments.length}개 클립 익스포트`}
            </button>
            {dirty ? (
              <span style={{ fontSize: 12, color: "#e2b340" }}>
                저장 안 된 수정이 있어요 — 익스포트 전에 저장하세요.
              </span>
            ) : null}
          </div>
          {exportProg?.exporting ? (
            <div>
              <div style={{ height: 6, borderRadius: 3, background: "rgba(255,255,255,0.12)" }}>
                <div style={{ height: 6, borderRadius: 3,
                              width: `${exportProg.total > 0
                                ? Math.round((exportProg.done / exportProg.total) * 100) : 0}%`,
                              background: "#4a9eda", transition: "width 0.3s" }} />
              </div>
              <p style={{ fontSize: 12, opacity: 0.7, margin: "4px 0 0" }}>
                클립 재인코딩 중… {exportProg.done}/{exportProg.total}
                {exportProg.out_dir ? ` → ${exportProg.out_dir}` : ""}
              </p>
            </div>
          ) : null}
        </>
      )}

      {/* 썸네일 클릭 팝업 — 작은 썸네일 대신 실제 영상을 그 시각으로 시킹해 크게
          보여준다(슬레이트를 읽을 수 있게). 배경/닫기 클릭 시 닫힘. */}
      {previewMs != null ? (
        <div onClick={() => setPreviewMs(null)}
          style={{ position: "fixed", inset: 0, zIndex: 1000,
                   background: "rgba(0,0,0,0.8)", display: "flex",
                   alignItems: "center", justifyContent: "center", padding: 24 }}>
          <div onClick={(e) => e.stopPropagation()}
            style={{ position: "relative", maxWidth: "90vw", maxHeight: "90vh" }}>
            <video
              src={videoMediaUrl(jobId)} controls autoPlay={false}
              onLoadedMetadata={(e) => { e.currentTarget.currentTime = previewMs / 1000; }}
              style={{ maxWidth: "90vw", maxHeight: "82vh", borderRadius: 8,
                       background: "#000" }} />
            <div style={{ display: "flex", justifyContent: "space-between",
                          alignItems: "center", marginTop: 6, color: "#fff" }}>
              <span style={{ fontSize: 13, opacity: 0.85 }}>이 지점: {formatMs(previewMs)}</span>
              <button type="button" style={consoleStyles.mutedAction}
                onClick={() => setPreviewMs(null)}>닫기</button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
