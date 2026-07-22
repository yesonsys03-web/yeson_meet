import { useEffect, useRef, useState } from "react";
import { consoleStyles } from "./consoleStyles";
import { hasTauriRuntime } from "./useQrFullscreenShortcut";
import {
  absorbFlankedMisreads, anomalousLabels, applyFixes, confidentFixes, formatMs, mergeAdjacentSameLabel, mergeSegment,
  previewLabel, renameSegment, segmentThumbRange, tokenizeSlate,
  type LabelFix,
} from "./sceneSplitLogic";
import { SceneFilmstrip } from "./SceneFilmstrip";
import {
  cancelSceneOps, exportScenes, getExportStatus, getRefineStatus, getScenes,
  listSlateTemplates, overrideSceneSegments, refineScenes, scanScenes,
  setOcrRegion as setOcrRegionApi, setSceneRule, videoMediaUrl,
  type ExportStatus, type OcrRegion, type RefineStatus, type ScenesData,
  type SceneSegment, type SlateTemplate,
} from "./videoApi";
import { SlateRegionPicker } from "./SlateRegionPicker";

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
  // 샘플 간격(초). 짧은 씬이 많으면 촘촘하게(0.25s) — 놓치면 그 씬 클립이 없어진다.
  const [scanIntervalS, setScanIntervalS] = useState(2.0);
  // 최소 씬 길이(초). 빈값=자동(간격 비례). 이보다 짧은 구간은 오독 튐으로 보고
  // 흡수한다 — 진짜 짧은 씬이 삼켜지면 이 값을 낮춘다.
  const [minSceneSec, setMinSceneSec] = useState("");
  const minMs = minSceneSec.trim() === "" ? undefined
    : Math.max(0, Math.round(Number(minSceneSec) * 1000));

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
    let pollFails = 0;
    for (let i = 0; i < 1200; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      let d: ScenesData;
      try {
        d = await getScenes(jobId);
      } catch {
        // 스캔도 CPU를 강하게 써 폴링이 순간 실패할 수 있다 — 연속 실패만 포기.
        pollFails += 1;
        if (pollFails >= 20) { setError("서버 응답이 없습니다. 상태를 확인하세요."); return; }
        continue;
      }
      pollFails = 0;
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
      await scanScenes(jobId, scanIntervalS);  // 스캔은 비동기 — 이후 진행률 폴링
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
        delimiters, seq_tokens: seqIdx, scene_tokens: sceneIdx, min_ms: minMs,
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
  // 연속 실행(스캔→경계→정밀화) 중 단계 표시 + 취소 신호. 취소는 서버 작업을
  // 멈추는 것과 별개로 이 로컬 루프도 빠져나와야 한다.
  const [stage, setStage] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const cancelAll = async () => {
    cancelledRef.current = true;
    try {
      await cancelSceneOps(jobId);
    } catch { /* 이미 끝났을 수 있다 */ }
    setStage(null); setRefineProg(null); setBusy(false);
    setNotice("중단했습니다.");
  };

  // 정밀화 한 모드 실행 + 완료까지 폴링. 연속 실행에서 모드별로 재사용한다.
  const refineOnce = async (m: Mode, total: number) => {
    setRefineProg({ refining: true, done: 0, total, error: null });
    await refineScenes(jobId, m);
    // 정밀화는 ffmpeg를 병렬로 띄워 CPU를 강하게 써서, 상태 폴링 요청이 순간
    // 실패("Load failed")할 수 있다 — 작업은 서버에서 계속되므로 한 번 실패에
    // 포기하지 않고 연속 실패가 쌓일 때만(서버 다운 등) 중단한다.
    let pollFails = 0;
    for (let i = 0; i < 3600; i++) {
      if (cancelledRef.current) return;
      await new Promise((r) => setTimeout(r, 1500));
      let st: RefineStatus;
      try {
        st = await getRefineStatus(jobId);
      } catch {
        pollFails += 1;
        if (pollFails >= 20) throw new Error("서버 응답이 없습니다. 상태를 확인하세요.");
        continue;
      }
      pollFails = 0;
      setRefineProg(st);
      if (st.error) throw new Error(`정밀화 실패: ${st.error}`);
      if (!st.refining) return;
    }
    throw new Error("정밀화가 시간 내 끝나지 않았습니다.");
  };

  const doRefine = async (m: Mode = mode) => {
    setError(null); setNotice(null); setBusy(true);
    cancelledRef.current = false;
    try {
      await refineOnce(m, Math.max(1, segments.length - 1));
      if (cancelledRef.current) return;
      setData(await getScenes(jobId));  // 정밀화된 경계 다시 불러오기
      setNotice("경계 정밀화 완료 — 이제 프레임 단위로 잘립니다. 재익스포트하세요.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setRefineProg(null);
    }
  };

  // 스캔 → 경계 계산 → 정밀화(시퀀스·씬 둘 다)를 한 번에. 세 단계는 어차피 항상
  // 함께 필요하고, 정밀화를 빠뜨린 채 익스포트해 경계가 어긋나는 사고가 잦았다.
  // 토큰 규칙이 있어야 경계 계산이 가능하므로(스캔 결과를 보고 고르는 값), 규칙이
  // 없으면 스캔까지만 하고 토큰 선택을 기다린다. 익스포트 양식이 쇼마다 달라
  // (씬별로 내보내는 쇼도 있다) 두 모드를 모두 정밀화한다.
  const runAll = async (opts: { rescan: boolean }) => {
    setError(null); cancelledRef.current = false; setBusy(true);
    try {
      if (opts.rescan) {
        setStage("1/4 슬레이트 스캔");
        await scanScenes(jobId, scanIntervalS);
        await pollScan(Boolean(data?.scanned));
        if (cancelledRef.current) return;
      }
      if (seqIdx.length === 0) {
        setStage(null);
        setNotice("스캔 완료 — 토큰을 고른 뒤 '경계 계산 + 정밀화'를 누르세요.");
        return;
      }
      setStage("2/4 경계 계산");
      const res = await setSceneRule(jobId, {
        delimiters, seq_tokens: seqIdx, scene_tokens: sceneIdx, min_ms: minMs,
      });
      if (cancelledRef.current) return;
      setStage(`3/4 시퀀스 정밀화 (${res.segments_sequence.length}구간)`);
      await refineOnce("sequence", Math.max(1, res.segments_sequence.length - 1));
      if (cancelledRef.current) return;
      setStage(`4/4 씬 정밀화 (${res.segments_scene.length}구간)`);
      await refineOnce("scene", Math.max(1, res.segments_scene.length - 1));
      if (cancelledRef.current) return;
      setData(await getScenes(jobId));
      setSelectedSeg(null);
      setNotice(`전체 완료 — 시퀀스 ${res.segments_sequence.length}개 · 씬 `
        + `${res.segments_scene.length}개, 양쪽 다 프레임 단위 경계입니다.`);
    } catch (e) {
      if (!cancelledRef.current) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false); setStage(null); setRefineProg(null);
    }
  };

  const [exportProg, setExportProg] = useState<ExportStatus | null>(null);

  const doExport = async () => {
    setError(null); setNotice(null);
    // 익스포트는 서버 저장본을 자른다 — 현재 모드에 미저장 편집이 있으면 화면과
    // 다른 옛 경계로 잘린다. 먼저 저장하도록 막는다(실기: 시퀀스 16개 병합했는데
    // 저장 안 해 79개로 익스포트될 뻔한 사고 방지).
    if (dirty) {
      setError('저장 안 된 수정이 있습니다 — 먼저 "수정사항 저장"을 누르세요.');
      return;
    }
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
  // dirty는 모드별로 따로 둔다 — 공유하면 씬을 저장할 때 시퀀스의 미저장 편집까지
  // '저장됨'으로 꺼져 저장 버튼이 비활성화된다(실기: 시퀀스 16개 병합했는데 저장
  // 불가). 익스포트는 서버 저장본을 쓰므로 각 모드를 반드시 따로 저장해야 한다.
  const [dirtyModes, setDirtyModes] = useState<Set<Mode>>(new Set());
  const dirty = dirtyModes.has(mode);
  const setSegments = (next: SceneSegment[]) => {
    if (!data) return;
    setData(mode === "sequence"
      ? { ...data, segments_sequence: next }
      : { ...data, segments_scene: next });
    setDirtyModes((prev) => new Set(prev).add(mode));
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
  // 필름스트립은 썸네일 격자(성긴 간격)로 그린다 — 스캔 간격과 다르다.
  const thumbIntervalMs = data?.thumb_interval_ms ?? intervalMs;
  const thumbCount = data?.thumb_count ?? data?.frames.length ?? 0;
  const highlight = selectedSeg != null && segments[selectedSeg]
    ? segmentThumbRange(segments[selectedSeg]!.start_ms,
                        segments[selectedSeg]!.end_ms, thumbIntervalMs, thumbCount)
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

  // 슬레이트 구역 — 쇼마다 위치가 달라 사용자가 드래그로 지정한다. 스캔 전에도
  // 스캔 후에도 다시 잡을 수 있다(다시 잡으면 재스캔해야 반영된다).
  const [ocrRegion, setOcrRegion] = useState<OcrRegion | null>(null);
  const [templates, setTemplates] = useState<SlateTemplate[]>([]);
  const [showPicker, setShowPicker] = useState(false);
  useEffect(() => {
    void (async () => {
      try {
        setTemplates((await listSlateTemplates()).templates);
      } catch { /* 템플릿은 편의 기능 — 실패해도 화면은 살아 있어야 한다 */ }
    })();
  }, []);
  useEffect(() => { setOcrRegion(data?.ocr_region ?? null); }, [data?.ocr_region]);
  useEffect(() => {
    if (data?.interval_ms) setScanIntervalS(data.interval_ms / 1000);
  }, [data?.interval_ms]);

  // 템플릿을 고르면 구역과 토큰 규칙을 한 번에 적용한다(같은 쇼면 포맷도 같다).
  const applyTemplate = (t: SlateTemplate) => {
    setOcrRegion(t.region);
    setSeqIdx(t.seq_tokens ?? []);
    setSceneIdx(t.scene_tokens ?? []);
    setSpaceDelim((t.delimiters ?? []).includes(" "));
    if (t.scan_interval_s) setScanIntervalS(t.scan_interval_s);
    void setOcrRegionApi(jobId, t.region).catch(() => undefined);
    setNotice(`'${t.name}' 템플릿을 적용했습니다 — 구역과 토큰 규칙이 설정됐습니다.`);
  };

  // 구역 확인용 샘플 시각 = 슬레이트가 읽힌 첫 프레임(없으면 영상 중반).
  const sampleMs = data?.frames.find((f) => f.text)?.t_ms
    ?? Math.floor(((data?.frames.at(-1)?.t_ms ?? 0)) / 2);

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
    // 교정으로 같아진 인접 라벨을 바로 병합한다 — 안 그러면 한 씬이 여러 조각으로
    // 남는다(오독이 씬 한가운데를 쪼갠 케이스).
    const fixed = applyFixes(segments, pendingFixes, fixChecked);
    const mergedSegs = mergeAdjacentSameLabel(fixed);
    setSegments(mergedSegs);
    setPendingFixes(null);
    const mergedCount = fixed.length - mergedSegs.length;
    setNotice(`이름 ${applied.length}건 교정`
      + (mergedCount > 0 ? ` + 인접 중복 ${mergedCount}건 병합` : "")
      + ` — 아직 저장 전입니다. 되돌리려면 "되돌리기"를 누르세요.`);
  };

  // 교정 없이 인접 중복만 정리 — 라벨은 맞는데 갈라진 경우(예: 사용자가 오독을
  // 수동 교정했지만 병합은 안 한 경우) 한 번에 합친다.
  const mergeDuplicates = () => {
    const mergedSegs = mergeAdjacentSameLabel(segments);
    const n = segments.length - mergedSegs.length;
    if (n === 0) { setNotice("인접 중복이 없습니다."); return; }
    setUndoSnapshot(segments);
    setSegments(mergedSegs);
    setNotice(`인접 중복 ${n}건을 병합했습니다 — 저장 전입니다.`);
  };

  // 오독 갈라짐 정리 — 앞뒤 같은 라벨로 둘러싸인 짧은 구간(확정 오독)을 흡수한다.
  // 라벨 교정이 안 되는 접두 유실 오독도 처리한다(시퀀스에서 특히 유효 — 실기
  // 시퀀스 79개 중 28곳이 이 형태였다). 5초 이하만 흡수해 진짜 비단조는 보존.
  const FLANK_MAX_MS = 5000;
  const flankedCount = segments.length
    - absorbFlankedMisreads(segments, FLANK_MAX_MS).length;
  const cleanFlanked = () => {
    const out = absorbFlankedMisreads(segments, FLANK_MAX_MS);
    const n = segments.length - out.length;
    if (n === 0) { setNotice("정리할 오독 갈라짐이 없습니다."); return; }
    setUndoSnapshot(segments);
    setSegments(out);
    setNotice(`오독으로 갈라진 ${n}건을 흡수했습니다 — 저장 전입니다. 되돌리기 가능.`);
  };

  const undoFixes = () => {
    if (!undoSnapshot) return;
    setSegments(undoSnapshot);
    setUndoSnapshot(null);
    setNotice("되돌렸습니다.");
  };

  const saveEdits = async () => {
    setBusy(true); setError(null);
    try {
      await overrideSceneSegments(jobId, mode, segments);
      // 저장한 모드만 dirty 해제 — 다른 모드의 미저장 편집은 유지한다.
      setDirtyModes((prev) => {
        const next = new Set(prev); next.delete(mode); return next;
      });
      const otherDirty = dirtyModes.has(mode === "scene" ? "sequence" : "scene");
      setNotice(otherDirty
        ? `${mode === "scene" ? "씬" : "시퀀스"} 저장 완료 — `
          + `${mode === "scene" ? "시퀀스" : "씬"} 모드에 저장 안 된 수정이 있습니다.`
        : "수정사항을 저장했습니다.");
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

      {/* 슬레이트 구역 — 쇼마다 위치가 다르므로 스캔 전에 잡아두면 판독이 빠르고
          정확하다. 스캔 후에도 다시 잡을 수 있다(다시 스캔해야 반영). */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" style={consoleStyles.mutedAction}
          onClick={() => setShowPicker((v) => !v)}>
          {showPicker ? "구역 지정 닫기" : "슬레이트 구역 지정"}
        </button>
        <span style={{ fontSize: 12, opacity: 0.7 }}>
          {ocrRegion
            ? `지정됨 — 가로 ${(ocrRegion.w * 100).toFixed(0)}% · 세로 ${(ocrRegion.h * 100).toFixed(0)}%`
            : "미지정 — 전체 프레임에서 상단을 훑습니다(느리고 쇼에 따라 실패)"}
        </span>
        {/* 샘플 간격 — 짧은 씬(2초 미만)이 많으면 촘촘하게. 놓치면 그 씬 클립이
            아예 생기지 않는다(2초 샘플이 사이의 짧은 컷을 건너뛴다). */}
        <label style={{ fontSize: 12, opacity: 0.8, display: "inline-flex",
                        alignItems: "center", gap: 5, marginLeft: "auto" }}>
          샘플 간격
          <select value={scanIntervalS}
            onChange={(e) => setScanIntervalS(Number(e.target.value))}
            style={{ fontSize: 12, padding: "3px 6px", borderRadius: 4,
                     background: "transparent", color: "inherit",
                     border: "1px solid rgba(255,255,255,0.15)" }}>
            <option value={2.0}>2초 (빠름·긴 컷)</option>
            <option value={1.0}>1초</option>
            <option value={0.5}>0.5초</option>
            <option value={0.25}>0.25초 (짧은 컷·느림)</option>
          </select>
        </label>
        {/* 최소 씬 길이 — 이보다 짧은 구간은 오독 튐으로 보고 흡수. 빈값=자동
            (간격 비례). 진짜 짧은 씬이 삼켜지면 낮춘다. */}
        <label style={{ fontSize: 12, opacity: 0.8, display: "inline-flex",
                        alignItems: "center", gap: 5 }}>
          최소 씬 길이
          <input value={minSceneSec} onChange={(e) => setMinSceneSec(e.target.value)}
            placeholder="자동" inputMode="decimal"
            style={{ width: 56, fontSize: 12, padding: "3px 6px", borderRadius: 4,
                     background: "transparent", color: "inherit",
                     border: "1px solid rgba(255,255,255,0.15)" }} />
          초
        </label>
      </div>
      {showPicker ? (
        <SlateRegionPicker jobId={jobId} sampleMs={sampleMs} region={ocrRegion}
          onChange={setOcrRegion} templates={templates}
          onTemplatesChange={setTemplates}
          rule={{ delimiters, seq_tokens: seqIdx, scene_tokens: sceneIdx,
                  scan_interval_s: scanIntervalS }}
          onApplyTemplate={applyTemplate} />
      ) : null}

      {/* 진행 단계 + 중단. 긴 작업이라 무엇이 도는지 보이고 멈출 수 있어야 한다. */}
      {stage ? (
        <div style={{ display: "flex", gap: 10, alignItems: "center",
                      padding: "6px 10px", borderRadius: 6,
                      background: "rgba(74,158,218,0.12)" }}>
          <strong style={{ fontSize: 13 }}>{stage}</strong>
          {refineProg?.refining ? (
            <span style={{ fontSize: 12, opacity: 0.8 }}>
              {refineProg.done}/{refineProg.total} 경계
            </span>
          ) : null}
          <button type="button" style={consoleStyles.mutedAction}
            onClick={() => void cancelAll()}>중단</button>
        </div>
      ) : null}

      {!data?.scanned ? (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button type="button" style={consoleStyles.action} disabled={busy}
            onClick={() => void runAll({ rescan: true })}>
            {busy ? "실행 중…" : "전체 실행 (스캔 → 경계 → 정밀화)"}
          </button>
          <button type="button" style={consoleStyles.mutedAction} disabled={busy}
            onClick={() => void runScan()}>스캔만</button>
          <span style={{ fontSize: 12, opacity: 0.65 }}>
            토큰 규칙이 없으면 스캔까지만 하고 멈춥니다(스캔 결과를 보고 고르는 값이라).
          </span>
        </div>
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
              {/* 토큰을 고른 뒤의 주 동작 — 경계 계산과 정밀화는 항상 함께 필요하다. */}
              <button type="button" style={consoleStyles.action}
                disabled={busy || seqIdx.length === 0}
                onClick={() => void runAll({ rescan: false })}>
                {busy ? "실행 중…" : "경계 계산 + 정밀화 (시퀀스·씬)"}
              </button>
              <button type="button" style={consoleStyles.mutedAction}
                disabled={busy || seqIdx.length === 0}
                onClick={() => void applyRule()}>
                경계 계산만
              </button>
              {/* OCR 재판독 — 구역·판독 로직을 바꿨거나 오염된 스캔 복구용. */}
              <button type="button" style={consoleStyles.mutedAction}
                disabled={busy}
                onClick={() => void runAll({ rescan: true })}>
                다시 스캔(전체)
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
            {/* 오독 갈라짐 정리 — 앞뒤 동일 라벨 사이 낀 오독을 흡수(시퀀스 특효). */}
            {flankedCount > 0 ? (
              <button type="button" style={consoleStyles.action}
                onClick={cleanFlanked}>
                오독 갈라짐 정리 ({flankedCount})
              </button>
            ) : null}
            {/* 인접 중복 병합 — 교정으로 같아졌거나 수동 교정 후 갈라진 씬을 합친다. */}
            {mergeAdjacentSameLabel(segments).length < segments.length ? (
              <button type="button" style={consoleStyles.mutedAction}
                onClick={mergeDuplicates}>
                인접 중복 병합 ({segments.length - mergeAdjacentSameLabel(segments).length})
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
            thumbCount={thumbCount}
            intervalMs={thumbIntervalMs}
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
