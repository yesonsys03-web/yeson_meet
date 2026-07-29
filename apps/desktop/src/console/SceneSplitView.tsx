// 씬별 분할 화면의 지휘부 — 화면 상태(data·모드·선택·프리뷰·알림)와 파생값을
// 들고, 실행(useSceneOps)·편집(useSegmentEditing)·익스포트(useSceneExport) 훅과
// 표시 컴포넌트(SceneScanControls·SceneListSection·ScenePreviewPopup)를 잇는다.
import { useEffect, useRef, useState } from "react";
import { consoleStyles } from "./consoleStyles";
import {
  anomalousLabels, boundaryIssueIndices, effectiveFps, filterIndices,
  frameSeekMs, mergeAdjacentSameLabel, segPreviewFor, segmentTailMs,
  segmentThumbRange, stepVisibleIndex, tokenizeSlate,
  type SegPreview,
} from "./sceneSplitLogic";
import { ScenePreviewPopup, type ScenePreviewPopupHandle } from "./ScenePreviewPopup";
import { SceneScanControls } from "./SceneScanControls";
import { SceneListSection } from "./SceneListSection";
import { useSceneOps } from "./useSceneOps";
import { useSceneExport } from "./useSceneExport";
import { useSegmentEditing } from "./useSegmentEditing";
import {
  getScenes, listSlateTemplates, setOcrRegion as setOcrRegionApi,
  videoMediaUrl,
  type OcrRegion, type SceneMethod, type ScenesData, type SceneSegment,
  type SlateTemplate,
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
  // 예시 슬레이트 한 줄 — 선언하면 서버가 머리글자(Seq↔Seg류 닮은꼴 오독)를
  // 다수결 대신 이 구조로 스냅한다. 빈값=기존 동작(옵트인).
  const [slateExample, setSlateExample] = useState("");
  // 샘플 간격(초). 짧은 씬이 많으면 촘촘하게(0.25s) — 놓치면 그 씬 클립이 없어진다.
  const [scanIntervalS, setScanIntervalS] = useState(2.0);
  // 스캔 방식 — 간격(기존)/지문(전 프레임 컷 감지, 프레임 정확·정밀화 불필요).
  // 지문에 리스크(가짜 컷 등)가 보이면 간격으로 폴백한다.
  const [scanMethod, setScanMethod] = useState<SceneMethod>("interval");
  // 최소 씬 길이(초). 빈값=자동(간격 비례). 이보다 짧은 구간은 오독 튐으로 보고
  // 흡수한다 — 진짜 짧은 씬이 삼켜지면 이 값을 낮춘다.
  const [minSceneSec, setMinSceneSec] = useState("");
  const minMs = minSceneSec.trim() === "" ? undefined
    : Math.max(0, Math.round(Number(minSceneSec) * 1000));

  const delimiters = spaceDelim ? ["_", " ", "-", "/"] : ["_", "-", "/"];

  const segments: SceneSegment[] = data
    ? (mode === "sequence" ? data.segments_sequence : data.segments_scene) : [];

  // 미저장 편집이 있는 모드 집합 — 편집 훅이 채우고, 실행 훅(경계 재검사 전 자동
  // 저장)도 지우므로 부모가 든다. dirty를 모드별로 따로 두는 이유는
  // useSegmentEditing 참조.
  const [dirtyModes, setDirtyModes] = useState<Set<Mode>>(new Set());

  // 리스트에서 클릭한 구간 → 필름스트립 하이라이트 범위. 썸네일 클릭 → 팝업 시각.
  const [selectedSeg, setSelectedSeg] = useState<number | null>(null);
  // 슬레이트 구역 — 쇼마다 위치가 달라 사용자가 드래그로 지정한다. 스캔 전에도
  // 스캔 후에도 다시 잡을 수 있다(다시 잡으면 재스캔해야 반영된다). 분할 시
  // 슬레이트 읽기(useSegmentEditing.splitAt)가 쓴다. (예전엔 팝업 단축키 effect의
  // 의존성 배열이 이 값을 읽어 선언 순서가 렌더 중 TDZ 함정이었다 — 그 effect는
  // 이제 ScenePreviewPopup 몫이라 순서 제약은 없다.)
  const [ocrRegion, setOcrRegion] = useState<OcrRegion | null>(null);
  // 팝업 프리뷰 — 값 구성과 필드 설명은 sceneSplitLogic의 SegPreview/segPreviewFor.
  // 플레이어(영상·재생 상태·감시 루프·단축키)는 ScenePreviewPopup이 소유하고,
  // 여기는 '무엇을 보여줄지'(preview)와 편집 콜백만 든다. 편집 직후 영상을 편집한
  // 프레임에 멈춰 세우는 것은 popupRef.pauseAndSeek로 지시한다.
  const [preview, setPreview] = useState<SegPreview | null>(null);
  const popupRef = useRef<ScenePreviewPopupHandle>(null);
  // 구간 반복재생(기본 꺼짐) — 완료 시 꼬리 프레임에 정지해 꼬리를 확인하게 한다.
  // 켜면 [첫 프레임, 꼬리 프레임]을 반복한다. 팝업을 닫았다 열어도 유지되도록
  // 부모 상태로 둔다(nudgeFrames도 같은 이유).
  const [loopSeg, setLoopSeg] = useState(false);
  // 경계 교정 시 한 번에 옮길 프레임 수 — 디졸브/와이프는 9프레임 이상 어긋나기도
  // 해서 한 클릭에 N프레임 이동. 미세조정은 1로.
  const [nudgeFrames, setNudgeFrames] = useState(1);
  // 편집 함수들이 비동기 완료 시점에 최신 preview를 읽도록 ref로 미러링.
  const previewRef = useRef(preview);
  useEffect(() => { previewRef.current = preview; }, [preview]);

  // 프리뷰(팝업) 상태를 세그먼트로부터 구성 — 계산은 segPreviewFor(순수), 여기는
  // 현재 잡의 fps만 공급한다.
  const buildSegPreview = (s: SceneSegment, segIndex: number, seekMs: number,
                           side: "head" | "tail") =>
    segPreviewFor(s, segIndex, seekMs, side, effectiveFps(data?.video_fps));

  // 실행(스캔·경계 계산·정밀화·경계 검사)/편집/익스포트는 훅 3개가 나눠 든다 —
  // 화면 상태는 여기 남고 setter로 주입된다.
  const ops = useSceneOps({
    jobId, data, setData, segments, setBusy, setError, setNotice,
    setSelectedSeg, delimiters, seqIdx, sceneIdx, minMs, slateExample,
    scanIntervalS, scanMethod, mode, dirtyModes, setDirtyModes,
  });
  const editing = useSegmentEditing({
    jobId, data, setData, mode, segments, dirtyModes, setDirtyModes,
    setBusy, setError, setNotice, setSelectedSeg,
    preview, previewRef, setPreview, buildSegPreview,
    pauseAndSeek: (ms) => popupRef.current?.pauseAndSeek(ms),
    delimiters, seqIdx, sceneIdx, ocrRegion,
  });
  const exportOps = useSceneExport({
    jobId, mode, segments, dirty: editing.dirty, busy,
    setBusy, setError, setNotice,
  });

  const refresh = async () => {
    const d = await getScenes(jobId);
    setData(d);
    // 서버에 저장된 규칙이 있으면 토큰 선택을 복원한다 — 화면 재진입 시 선택이
    // 초기화돼 "경계 계산"이 비활성(회색)으로 보이던 문제 수정.
    if (d.rule) {
      setSeqIdx(d.rule.seq_tokens ?? []);
      setSceneIdx(d.rule.scene_tokens ?? []);
      setSpaceDelim((d.rule.delimiters ?? []).includes(" "));
      setSlateExample(d.rule.example ?? "");
    }
    // 서버에 저장된 스캔 방식 복원 — 재진입 시 선택이 초기화되지 않게.
    if (d.method) setScanMethod(d.method);
    // 재진입 시 스캔이 이미 진행 중이면(다른 화면에서 걸어둔 스캔 등) 진행률
    // 폴링을 이어붙인다 — 시작 버튼만 덩그러니 보이던 공백 수정.
    if (d.scanning) {
      setBusy(true); setNotice("슬레이트 판독 중…");
      try {
        await ops.pollScan(false);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally { setBusy(false); }
    }
  };
  useEffect(() => { void refresh(); }, [jobId]);

  // 대표 프레임 = 첫 비어있지 않은 OCR 텍스트
  const sample = data?.frames.find((f) => f.text)?.text ?? "";
  const tokens = tokenizeSlate(sample, delimiters);

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
  const labels = segments.map((s) => s.label);
  const anomalies = anomalousLabels(labels, delimiters);
  const anomalyIdx = anomalies.map((a) => a.index);
  const suggestionOf = new Map(anomalies.map((a) => [a.index, a]));
  // 경계 오류(혼입) — 씬 모드 전용(boundary_issues는 segments_scene 기준). 다른
  // 모드에선 빈 목록이라 필터 탭이 숨겨진다.
  const [onlyBoundaryErrors, setOnlyBoundaryErrors] = useState(false);
  const boundaryIssues = mode === "scene" ? (data?.boundary_issues ?? []) : [];
  const boundaryOk = mode === "scene" ? (data?.boundary_ok ?? []) : [];
  // 라벨로 다시 찾고(인덱스가 밀려도 안전), 사용자가 '문제없음'으로 확인한 구간은
  // 뺀다 — 단 그 뒤에 경계가 바뀌었으면 확인표시를 무시한다(boundaryIssueIndices).
  const boundaryIdx = boundaryIssueIndices(boundaryIssues, segments, boundaryOk);
  const boundaryCount = boundaryIdx.length;
  // 현재 목록에 남아 실제로 무언가를 숨기고 있는 확인표시 수 — '모두 해제' 안내용.
  const boundaryOkCount = boundaryIssueIndices(boundaryIssues, segments, []).length
    - boundaryCount;
  // 라벨 검색 — 400+ 줄에서 특정 씬을 스크롤로 찾는 대신 번호 일부를 쳐서 좁힌다.
  // 탭 필터와 교차하므로 "경계 오류 중 0230"처럼 겹쳐 쓸 수 있다.
  const [labelQuery, setLabelQuery] = useState("");
  // 탭을 바꿔도 인덱스는 원본 기준을 유지한다(병합/이름수정 콜백이 인덱스를 쓴다).
  const tabIdx = onlyAnomalies ? anomalyIdx
    : onlyBoundaryErrors ? boundaryIdx : null;
  const visibleIndices = filterIndices(labels, tabIdx, labelQuery);
  // 이전/다음 씬 이동은 '보이는' 목록을 따라간다 — 필터·검색으로 3개만 남았으면
  // 그 3개 사이만 오간다(안 보이는 줄로 선택이 튀면 화면과 어긋난다).
  const visibleAll = visibleIndices ?? segments.map((_, i) => i);
  const stepSegment = (delta: number) => {
    const next = stepVisibleIndex(visibleAll, selectedSeg, delta);
    if (next != null) setSelectedSeg(next);
  };
  // 팝업 플레이어에서 씬 넘기기 — 팝업을 닫고 목록에서 다음 씬을 찾아 다시 프레임을
  // 클릭하는 왕복을 없앤다. 보던 쪽(머리/꼬리)을 유지해 "모든 컷의 꼬리를 훑는" 식의
  // 한 줄기 검수가 끊기지 않게 하고, 목록 선택도 함께 옮겨 팝업을 닫으면 그 씬에 있게
  // 한다. 이동 범위는 목록과 같은 '보이는 목록'(필터·검색 적용) 기준.
  const stepPreviewSegment = (delta: number) => {
    if (!data || preview?.segIndex == null) return;
    const next = stepVisibleIndex(visibleAll, preview.segIndex, delta);
    if (next == null) return;
    const seg = segments[next];
    if (!seg) return;
    const side = preview.side ?? "head";
    const fps = effectiveFps(data.video_fps);
    const focusMs = side === "tail"
      ? frameSeekMs(segmentTailMs(seg.start_ms, seg.end_ms, fps), fps)
      : frameSeekMs(seg.start_ms, fps);
    setPreview(buildSegPreview(seg, next, focusMs, side));
    setSelectedSeg(next);
    popupRef.current?.pauseAndSeek(focusMs);
  };

  // ←/→로 이전·다음 씬. 선택이 바뀌면 sticky 검수 뷰의 머리·꼬리 프레임이 갱신되고
  // 목록도 그 줄로 스크롤돼(SceneFilmstrip) 스크롤 조작이 아예 필요 없다. 입력칸
  // 포커스(라벨 수정·검색)와 팝업이 열린 동안은 무시한다 — 팝업이 열려 있으면
  // ←/→는 프레임 한 칸이고 그건 팝업(ScenePreviewPopup)의 몫이다. 검수 단축키
  // (I/O/S/G/H/[/])도 팝업이 등록한다.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (preview != null) return;  // 팝업이 열려 있으면 팝업 핸들러가 처리
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
                || t.isContentEditable)) return;
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();  // 스크롤 컨테이너의 가로 스크롤 기본동작 차단
      stepSegment(e.key === "ArrowRight" ? 1 : -1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [preview, segments, visibleIndices, selectedSeg]);

  // 모드가 바뀌면 구간 목록 자체가 달라진다 — 이전 모드의 필터·선택은 무의미해져
  // 지운다(씬별 목록이 시퀀스별 화면에 남아 보이던 문제). 편집 쪽 초기화(미리보기·
  // 되돌리기 스냅샷)는 useSegmentEditing의 같은 조건 효과가 담당한다.
  useEffect(() => {
    setOnlyAnomalies(false);
    setOnlyBoundaryErrors(false);
    setSelectedSeg(null);
  }, [mode]);

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
    // 지문 스캔에는 샘플 간격 개념이 없다 — 간격 UI 값을 덮어쓰지 않는다.
    if (data?.interval_ms && data.method !== "fingerprint") {
      setScanIntervalS(data.interval_ms / 1000);
    }
  }, [data?.interval_ms, data?.method]);

  // 템플릿을 고르면 구역과 토큰 규칙을 한 번에 적용한다(같은 쇼면 포맷도 같다).
  const applyTemplate = (t: SlateTemplate) => {
    setOcrRegion(t.region);
    setSeqIdx(t.seq_tokens ?? []);
    setSceneIdx(t.scene_tokens ?? []);
    setSpaceDelim((t.delimiters ?? []).includes(" "));
    setSlateExample(t.example ?? "");
    if (t.scan_interval_s) setScanIntervalS(t.scan_interval_s);
    if (t.method) setScanMethod(t.method);
    void setOcrRegionApi(jobId, t.region).catch(() => undefined);
    setNotice(`'${t.name}' 템플릿을 적용했습니다 — 구역과 토큰 규칙이 설정됐습니다.`);
  };

  // 구역 확인용 샘플 시각 = 슬레이트가 읽힌 첫 프레임(없으면 영상 중반).
  const sampleMs = data?.frames.find((f) => f.text)?.t_ms
    ?? Math.floor(((data?.frames.at(-1)?.t_ms ?? 0)) / 2);

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

      {/* 스캔 설정·실행 툴바 — 구역/방식/간격, 진행 표시, 실행 버튼, 토큰 규칙. */}
      <SceneScanControls
        jobId={jobId} busy={busy} scanned={Boolean(data?.scanned)}
        method={data?.method} stage={ops.stage} refineProg={ops.refineProg}
        sample={sample} tokens={tokens} sampleMs={sampleMs}
        delimiters={delimiters}
        templates={templates} onTemplatesChange={setTemplates}
        showPicker={showPicker} onTogglePicker={() => setShowPicker((v) => !v)}
        ocrRegion={ocrRegion} onOcrRegionChange={setOcrRegion}
        scanMethod={scanMethod} onScanMethodChange={setScanMethod}
        scanIntervalS={scanIntervalS} onScanIntervalChange={setScanIntervalS}
        minSceneSec={minSceneSec} onMinSceneSecChange={setMinSceneSec}
        spaceDelim={spaceDelim}
        onSpaceDelimToggle={(checked) => {
          // 구분자가 바뀌면 토큰 경계가 달라져 인덱스 의미가 바뀐다 → 선택 초기화.
          setSpaceDelim(checked);
          setSeqIdx([]); setSceneIdx([]);
        }}
        slateExample={slateExample} onSlateExampleChange={setSlateExample}
        seqIdx={seqIdx} sceneIdx={sceneIdx}
        onToggleSeq={toggleSeq} onToggleScene={toggleScene}
        onApplyTemplate={applyTemplate}
        onCancelAll={() => void ops.cancelAll()}
        onRunAll={(opts) => void ops.runAll(opts)}
        onRunScan={() => void ops.runScan()}
        onApplyRule={() => void ops.applyRule()}
      />

      {data?.scanned ? (
        <>
          {/* 모드 토글 + 필름스트립 */}
          <div style={{ display: "flex", gap: 16, alignItems: "center", fontSize: 13 }}>
            <label><input type="radio" checked={mode === "scene"}
              onChange={() => setMode("scene")} /> 씬별</label>
            <label><input type="radio" checked={mode === "sequence"}
              onChange={() => setMode("sequence")} /> 시퀀스별</label>
            <span style={{ opacity: 0.7 }}>{segments.length}개 구간</span>
            {/* 경계 정밀화 — 2초 샘플링의 ±1초 잔여를 프레임 단위로 좁힌다(경계마다
                이진탐색 OCR이라 수 분 소요, 현재 모드만). 지문 방식은 경계가 이미
                프레임 정확이라 버튼 자체가 없다(서버도 409로 막는다). */}
            {data.method !== "fingerprint" ? (
              <button type="button" style={consoleStyles.mutedAction}
                disabled={busy || segments.length < 2}
                onClick={() => void ops.doRefine()}>
                {ops.refineProg?.refining
                  ? `정밀화 중… ${ops.refineProg.done}/${ops.refineProg.total}`
                  : "경계 정밀화 (프레임 단위)"}
              </button>
            ) : null}
          </div>
          {ops.refineProg?.refining ? (
            <div style={{ height: 6, borderRadius: 3, background: "rgba(255,255,255,0.12)" }}>
              <div style={{ height: 6, borderRadius: 3,
                            width: `${ops.refineProg.total > 0
                              ? Math.round((ops.refineProg.done / ops.refineProg.total) * 100) : 0}%`,
                            background: "#4a9eda", transition: "width 0.3s" }} />
            </div>
          ) : null}
          {/* 목록 구역 — 필터 탭·검색·일괄 도구·확인 모달·구간 줄. */}
          <SceneListSection
            segments={segments}
            anomaliesCount={anomalies.length}
            onlyAnomalies={onlyAnomalies}
            onlyBoundaryErrors={onlyBoundaryErrors}
            onFilterAll={() => {
              setOnlyAnomalies(false); setOnlyBoundaryErrors(false);
              setSelectedSeg(null);
            }}
            onFilterAnomalies={() => {
              setOnlyAnomalies(true); setOnlyBoundaryErrors(false);
              setSelectedSeg(null);
            }}
            onFilterBoundary={() => {
              setOnlyBoundaryErrors(true); setOnlyAnomalies(false);
              setSelectedSeg(null);
            }}
            showBoundaryTab={mode === "scene"}
            boundaryCount={boundaryCount}
            boundaryOkCount={boundaryOkCount}
            onClearBoundaryOk={() => void editing.putBoundaryOk([])}
            canRecheck={mode === "scene" && (data?.scanned ?? false)}
            busy={busy}
            onRecheckBoundaries={() => void ops.recheckBoundaries()}
            labelQuery={labelQuery}
            onLabelQueryChange={setLabelQuery}
            onSearchEnter={() => {
              const first = visibleAll[0];
              if (first != null) setSelectedSeg(first);
            }}
            visibleIndices={visibleIndices}
            confidentSuggestionCount={
              anomalies.filter((a) => a.suggestion && a.confident).length}
            onOpenFixPreview={editing.openFixPreview}
            renameFrom={editing.renameFrom} renameTo={editing.renameTo}
            onRenameFromChange={editing.setRenameFrom}
            onRenameToChange={editing.setRenameTo}
            renameFixCount={editing.renameFixes.length}
            onOpenRenamePreview={editing.openRenamePreview}
            flankedCount={editing.flankedCount}
            onCleanFlanked={editing.cleanFlanked}
            adjacentDupCount={segments.length
              - mergeAdjacentSameLabel(segments).length}
            onMergeDuplicates={editing.mergeDuplicates}
            canUndoFixes={Boolean(editing.undoSnapshot)}
            onUndoFixes={editing.undoFixes}
            pendingFixes={editing.pendingFixes}
            fixChecked={editing.fixChecked}
            onFixCheckedChange={editing.setFixChecked}
            onConfirmFixes={editing.confirmFixes}
            onCancelFixes={() => editing.setPendingFixes(null)}
            filmstrip={{
              jobId, segments,
              thumbCount,
              intervalMs: thumbIntervalMs,
              totalMs: data.total_ms
                ?? ((data.frames.at(-1)?.t_ms ?? 0) + intervalMs),
              onMerge: editing.mergeSeg, onRename: editing.renameSeg,
              // 리스트 줄의 되돌리기는 스택 top이 '병합'일 때만 뜬다 — 경계 교정은
              // 팝업에서 물린다(같은 스택, 엄격 LIFO).
              undoIndex: editing.editUndo.at(-1)?.kind === "merge"
                ? editing.editUndo.at(-1)!.survivor : null,
              onUndoMerge: editing.undoEdit,
              onExportOne: exportOps.exportOne,
              exportingIndex: exportOps.exportingOne,
              exportDisabled: busy || Boolean(exportOps.exportProg?.exporting),
              // 경계오류 탭에서만 '✓ 문제없음'을 띄운다 — 다른 탭 줄에는 필요 없다.
              onBoundaryOk: onlyBoundaryErrors ? editing.markBoundaryOk : undefined,
              selectedIndex: selectedSeg, highlight,
              visibleIndices,
              suggestions: suggestionOf,
              videoFps: data.video_fps ?? undefined,
              onSelectSegment: setSelectedSeg,
              onStepSegment: stepSegment,
              onClearSelection: () => setSelectedSeg(null),
              onThumbClick: (seekMs, seg, segIndex, side) => {
                if (!seg || segIndex == null) { setPreview({ seekMs }); return; }
                setPreview(buildSegPreview(seg, segIndex, seekMs, side ?? "head"));
              },
            }}
          />
          {/* 저장·익스포트를 하단에 고정 — 400+ 리스트를 끝까지 스크롤하지 않아도
              항상 접근 가능하게. */}
          <div style={{ position: "sticky", bottom: 0, zIndex: 5,
                        background: "var(--ys-bg-app)", marginTop: 4,
                        paddingTop: 10, paddingBottom: 4,
                        borderTop: "1px solid rgba(255,255,255,0.08)" }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button type="button" style={consoleStyles.mutedAction}
              disabled={busy || !editing.dirty}
              onClick={() => void editing.saveEdits()}>
              {editing.dirty ? "수정사항 저장" : "저장됨"}
            </button>
            <button type="button" style={consoleStyles.action}
              disabled={busy || segments.length === 0}
              onClick={() => void exportOps.doExport()}>
              {exportOps.exportProg?.exporting
                ? `익스포트 중… ${exportOps.exportProg.done}/${exportOps.exportProg.total}`
                : `${segments.length}개 클립 익스포트`}
            </button>
            {editing.dirty ? (
              <span style={{ fontSize: 12, color: "#e2b340" }}>
                저장 안 된 수정이 있어요 — 익스포트 전에 저장하세요.
              </span>
            ) : null}
          </div>
          {exportOps.exportProg?.exporting ? (
            <div>
              <div style={{ height: 6, borderRadius: 3, background: "rgba(255,255,255,0.12)" }}>
                <div style={{ height: 6, borderRadius: 3,
                              width: `${exportOps.exportProg.total > 0
                                ? Math.round((exportOps.exportProg.done / exportOps.exportProg.total) * 100) : 0}%`,
                              background: "#4a9eda", transition: "width 0.3s" }} />
              </div>
              <p style={{ fontSize: 12, opacity: 0.7, margin: "4px 0 0" }}>
                클립 재인코딩 중… {exportOps.exportProg.done}/{exportOps.exportProg.total}
                {exportOps.exportProg.out_dir ? ` → ${exportOps.exportProg.out_dir}` : ""}
              </p>
            </div>
          ) : null}
          </div>
        </>
      ) : null}

      {/* 썸네일 클릭 팝업 — 실제 영상을 그 시각으로 시킹해 크게 보여준다. 구간에서
          열면(머리·꼬리 클릭) 재생을 그 구간 [start,end)로 묶어, 소스 전체가 아니라
          그 컷만 재생·반복하며 분할 경계를 확인할 수 있다. 배경/닫기 클릭 시 닫힘.
          플레이어·단축키는 ScenePreviewPopup이, 편집 콜백은 여기(부모)가 담당. */}
      {preview != null ? (() => {
        const top = editing.editUndo.at(-1);
        return (
          <ScenePreviewPopup
            ref={popupRef}
            src={videoMediaUrl(jobId)}
            preview={preview}
            segments={segments}
            visibleAll={visibleAll}
            dirty={editing.dirty}
            // 리스트 줄과 대칭: 스택 top이 '이 씬의' 경계 교정/분할일 때만 되돌리기.
            canUndo={Boolean(top
              && (top.kind === "boundary" || top.kind === "split")
              && top.survivor === preview.segIndex)}
            loopSeg={loopSeg} onToggleLoop={() => setLoopSeg((l) => !l)}
            nudgeFrames={nudgeFrames} onNudgeFramesChange={setNudgeFrames}
            onClose={() => setPreview(null)}
            onNudge={editing.nudgeBoundary}
            onTrim={editing.trimAt}
            onSplit={(ms) => void editing.splitAt(ms)}
            onStepScene={stepPreviewSegment}
            onUndo={editing.undoEdit}
          />
        );
      })() : null}
    </div>
  );
}
