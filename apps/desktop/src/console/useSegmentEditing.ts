// 구간 편집 훅 — 병합/이름수정/경계 교정(nudge)/In·Out 트림/나누기(+슬레이트
// 읽기)/일괄교정(제안·접두 치환·오독 갈라짐·인접 병합)/되돌리기 스택/저장,
// 그리고 경계오류 '문제없음' 확인. data·모드·선택·프리뷰는 부모가 들고 setter를
// 주입한다(SceneSplitView 분할 — 로직은 그대로 이동).
import { useEffect, useState, type Dispatch, type MutableRefObject,
         type SetStateAction } from "react";
import {
  absorbFlankedMisreads, applyFixes, applySplitName, confidentFixes,
  effectiveFps, frameSeekMs, mergeAdjacentSameLabel, mergeSegment,
  nudgeSegments, prefixRenameFixes, previewLabel, renameSegment,
  segFrameNumber, segmentTailMs, splitSegment, trimFrames, upsertBoundaryOk,
  NTSC_FPS,
  type LabelFix, type SegPreview,
} from "./sceneSplitLogic";
import {
  overrideSceneSegments, saveBoundaryOk, testOcrRegion,
  type BoundaryOk, type OcrRegion, type ScenesData, type SceneSegment,
} from "./videoApi";

type Mode = "scene" | "sequence";

export function useSegmentEditing(opts: {
  jobId: string;
  data: ScenesData | null;
  setData: Dispatch<SetStateAction<ScenesData | null>>;
  mode: Mode;
  segments: SceneSegment[];
  dirtyModes: Set<Mode>;
  setDirtyModes: Dispatch<SetStateAction<Set<Mode>>>;
  setBusy: (b: boolean) => void;
  setError: (e: string | null) => void;
  setNotice: (n: string | null) => void;
  setSelectedSeg: (i: number | null) => void;
  preview: SegPreview | null;
  previewRef: MutableRefObject<SegPreview | null>;
  setPreview: (p: SegPreview | null) => void;
  buildSegPreview: (s: SceneSegment, segIndex: number, seekMs: number,
                    side: "head" | "tail") => SegPreview;
  // 편집 직후 팝업 영상을 편집한 프레임에 멈춰 세운다(팝업이 닫혀 있으면 no-op).
  pauseAndSeek: (ms: number) => void;
  delimiters: string[];
  seqIdx: number[];
  sceneIdx: number[];
  ocrRegion: OcrRegion | null;
}) {
  const {
    jobId, data, setData, mode, segments, dirtyModes, setDirtyModes,
    setBusy, setError, setNotice, setSelectedSeg,
    preview, previewRef, setPreview, buildSegPreview, pauseAndSeek,
    delimiters, seqIdx, sceneIdx, ocrRegion,
  } = opts;

  // 현재 모드의 구간 목록을 편집(병합/이름수정)해 data 상태에 반영한다. dirty면
  // "수정사항 저장"으로 서버에 PATCH해야 익스포트에 반영된다.
  // dirty는 모드별로 따로 둔다 — 공유하면 씬을 저장할 때 시퀀스의 미저장 편집까지
  // '저장됨'으로 꺼져 저장 버튼이 비활성화된다(실기: 시퀀스 16개 병합했는데 저장
  // 불가). 익스포트는 서버 저장본을 쓰므로 각 모드를 반드시 따로 저장해야 한다.
  const dirty = dirtyModes.has(mode);
  const setSegments = (next: SceneSegment[]) => {
    if (!data) return;
    setData(mode === "sequence"
      ? { ...data, segments_sequence: next }
      : { ...data, segments_scene: next });
    setDirtyModes((prev) => new Set(prev).add(mode));
  };
  // 사용자가 고친 씬은 경계 오류에서 즉시 빠져야 한다 — boundary_issues는 검사
  // 시점 스냅샷이라, 편집한 씬의 플래그를 라벨로 제거한다(다음 검사 전까지 낙관적).
  const clearBoundaryFlags = (labels: Array<string | undefined>) => {
    const drop = new Set(labels.filter((l): l is string => Boolean(l)));
    if (drop.size === 0) return;
    setData((prev) => (prev && prev.boundary_issues
      ? { ...prev, boundary_issues: prev.boundary_issues.filter((b) => !drop.has(b.label)) }
      : prev));
  };
  // 확인 목록을 통째로 저장한다(전체 교체 — 서버도 같은 약속). 실패하면 화면 상태를
  // 되돌린다: 저장에 실패했는데 화면에서만 빼면 "뺐다고 봤는데 다음에 또 뜨는" 상태가
  // 된다. 낙관적 갱신 → 실패 시 롤백.
  const putBoundaryOk = async (next: BoundaryOk[]) => {
    if (!data) return;
    const before = data.boundary_ok ?? [];
    setData({ ...data, boundary_ok: next });
    try {
      await saveBoundaryOk(jobId, next);
    } catch (e) {
      setData((prev) => (prev ? { ...prev, boundary_ok: before } : prev));
      setError("확인 표시를 저장하지 못했습니다: "
        + (e instanceof Error ? e.message : String(e)));
    }
  };
  // 이 씬은 눈으로 확인했고 경계가 맞다 — 경계오류 목록에서 뺀다. 확인 당시의
  // 경계를 함께 남겨, 나중에 이 씬 경계를 고치면 다시 뜨게 한다.
  const markBoundaryOk = (i: number) => {
    const seg = segments[i];
    if (!seg) return;
    void putBoundaryOk(upsertBoundaryOk(data?.boundary_ok ?? [],
      { label: seg.label, start_ms: seg.start_ms, end_ms: seg.end_ms }));
  };
  // 편집 되돌리기 스택 — 개별 병합(mergeSeg)과 경계 교정(nudgeBoundary)마다 직전
  // 상태를 쌓아 여러 단계 물릴 수 있게 한다. 각 항목=편집 전 세그먼트·경계플래그
  // 스냅샷 + 편집 결과 구간 인덱스(병합=생존 구간, 경계 교정=교정한 구간). 두 종류를
  // 한 스택에 담아 엄격 LIFO로 물린다 — 스택을 따로 두면 병합·경계 교정을 섞었을 때
  // 되돌리는 순서가 뒤엉킨다. kind로 버튼 위치를 가른다(merge=리스트 줄,
  // boundary=팝업). 모드 전환·저장·일괄교정 시 비운다(구간 목록이 재편돼 인덱스가
  // 무의미). undoSnapshot(일괄교정 한 단계)과는 별개.
  const [editUndo, setEditUndo] = useState<
    { kind: "merge" | "boundary" | "split"; segs: SceneSegment[];
      issues: ScenesData["boundary_issues"]; survivor: number }[]
  >([]);
  const mergeSeg = (i: number, into: "prev" | "next") => {
    // 병합한 두 씬의 경계 오류 플래그를 뺀다(사라진 라벨 + 살아남은 라벨 둘 다).
    const gone = segments[i]?.label;
    const survivorLabel = into === "prev" ? segments[i - 1]?.label : segments[i + 1]?.label;
    // 병합하면 배열이 줄어 기존 선택 인덱스가 다른 구간을 가리킨다 — 살아남은 구간을
    // 선택해 필름스트립 하이라이트와 경계 썸네일이 병합 결과(넓어진 범위, 당겨진 시작
    // 시각)를 곧바로 보여주게 한다.
    const survivor = into === "prev" ? Math.max(0, i - 1) : i;
    // 되돌리기용: 병합 전 세그먼트·경계플래그와 생존 구간을 스택에 쌓는다(여러 단계).
    setEditUndo((prev) => [
      ...prev, { kind: "merge", segs: segments, issues: data?.boundary_issues, survivor }]);
    setSegments(mergeSegment(segments, i, into));
    clearBoundaryFlags([gone, survivorLabel]);
    setSelectedSeg(survivor);
  };
  // 편집 되돌리기 — 스택 top의 편집 전 상태(세그먼트·경계플래그)로 복원하고 그 구간을
  // 다시 선택한다. 여러 번 누르면 한 단계씩 거슬러 올라간다. 아직 저장 전이므로 dirty는
  // 유지(복원본도 서버 저장본과는 다르다). 경계 교정을 물릴 때 팝업이 열려 있으면
  // 프리뷰가 옛 경계(startMs/endMs·프레임 카운터)를 그대로 들고 있으므로 복원된
  // 세그먼트로 다시 만들고 그 경계 프레임으로 시킹한다 — 화면과 데이터가 어긋나면
  // 사용자가 이미 물린 편집을 다시 물린다.
  const undoEdit = () => {
    if (editUndo.length === 0 || !data) return;
    const top = editUndo[editUndo.length - 1]!;
    setData(mode === "sequence"
      ? { ...data, segments_sequence: top.segs, boundary_issues: top.issues }
      : { ...data, segments_scene: top.segs, boundary_issues: top.issues });
    setDirtyModes((prev) => new Set(prev).add(mode));
    setSelectedSeg(top.survivor);
    setEditUndo((prev) => prev.slice(0, -1));
    const p = previewRef.current;
    const restored = top.segs[top.survivor];
    if ((top.kind === "boundary" || top.kind === "split")
        && p?.segIndex === top.survivor && restored) {
      const fps = effectiveFps(data.video_fps);
      const side = p.side ?? "head";
      const focusMs = side === "tail"
        ? frameSeekMs(segmentTailMs(restored.start_ms, restored.end_ms, fps), fps)
        : frameSeekMs(restored.start_ms, fps);
      setPreview(buildSegPreview(restored, top.survivor, focusMs, side));
      pauseAndSeek(focusMs);
    }
  };
  const renameSeg = (i: number, label: string) => {
    clearBoundaryFlags([segments[i]?.label]);  // 이름 바꾼 씬은 플래그 해제(라벨도 바뀜)
    setSegments(renameSegment(segments, i, label));
  };

  // 팝업에서 머리/꼬리 경계를 delta 프레임 이동 — 그 프레임을 이웃 씬으로 넘기거나
  // 이웃에서 가져온다(스캔이 못 잡는 디졸브/와이프 수동 교정). 클램프·경계 공유
  // 갱신·focusMs 계산은 nudgeSegments(순수)가 담당하고, 여기는 상태 갱신과 팝업
  // 시킹만 한다. 경계가 프레임 정렬을 유지하므로 익스포트도 프레임 정확.
  const nudgeBoundary = (side: "head" | "tail", delta: number) => {
    if (!data || preview?.segIndex == null || delta === 0) return;
    const i = preview.segIndex;
    const moved = nudgeSegments(segments, i, side, delta,
                                effectiveFps(data.video_fps));
    if (!moved) return;
    // 되돌리기용: 교정 전 세그먼트·경계플래그를 병합과 같은 스택에 쌓는다. In/Out
    // 트림은 한 클릭이라 오조작이 쉬운 만큼 되돌릴 수 있어야 한다.
    setEditUndo((prev) => [
      ...prev,
      { kind: "boundary", segs: segments, issues: data.boundary_issues, survivor: i }]);
    setSegments(moved.segs);  // dirty — "수정사항 저장" 후 익스포트에 반영
    // 교정한 씬(+맞닿은 이웃)의 경계 오류 플래그를 뺀다 — 고쳤으면 필터에서 빠져야.
    clearBoundaryFlags([moved.segs[i]!.label,
                        side === "tail" ? moved.segs[i + 1]?.label
                                        : moved.segs[i - 1]?.label]);
    setPreview(buildSegPreview(moved.segs[i]!, i, moved.focusMs, side));
    pauseAndSeek(moved.focusMs);
  };
  // 편집 프로그램식 In/Out 트림 — 지금 보고 있는 프레임을 이 씬의 첫(In)/마지막(Out)
  // 프레임으로 확정한다. 사용자가 프레임 카운터를 읽어 '프레임씩' 칸에 옮겨 적던
  // 계산을 여기서 대신 한다(오입력 제거). 경계 이동은 nudgeBoundary가 그대로 담당.
  // ms(기준 프레임 시각)는 팝업이 넘긴다 — 버튼은 카운터 값, 단축키는 영상 현재 시각.
  const trimAt = (side: "in" | "out", ms: number) => {
    const p = previewRef.current;
    if (!p || p.segIndex == null || p.startMs == null || p.endMs == null) return;
    const { k, n } = segFrameNumber(ms, p.startMs, p.endMs, p.fps);
    const { inFrames, outFrames } = trimFrames(k, n);
    if (side === "in") {
      if (p.segIndex <= 0 || inFrames === 0) return;   // 첫 씬이거나 넘길 게 없음
      nudgeBoundary("head", inFrames);
    } else {
      if (p.segIndex >= segments.length - 1 || outFrames === 0) return;
      nudgeBoundary("tail", -outFrames);
    }
  };

  // 한 씬 안에 두 씬이 붙어 있을 때(스캔이 그 컷을 못 잡은 경우) 지금 보는 프레임에서
  // 나눈다. 지금까지 할 수 있는 편집은 병합·이름수정·트림뿐이라 나눌 수단이 재스캔밖에
  // 없었다(25분 + 수동 정렬 초기화).
  //
  // 지금 보는 프레임이 뒤 구간의 첫 프레임이 된다 — In 트림과 같은 약속이고 자르는
  // 계산도 같다. 뒤 구간이 원래 이름을 유지하고, 앞 구간 이름은 슬레이트를 읽어 채운다.
  const splitAt = async (ms: number) => {
    const p = previewRef.current;
    if (!p || p.segIndex == null || p.startMs == null || p.endMs == null) return;
    const i = p.segIndex;
    const cur = segments[i];
    if (!cur) return;
    const fps = p.fps || NTSC_FPS;
    const { k } = segFrameNumber(ms, p.startMs, p.endMs, fps);
    const next = splitSegment(segments, i, k, fps);
    if (next === segments) {
      setNotice("첫 프레임에서는 나눌 수 없습니다 — 뒤 씬이 시작되는 프레임으로 옮기세요.");
      return;
    }
    setEditUndo((prev) => [...prev,
      { kind: "split", segs: segments, issues: data?.boundary_issues, survivor: i }]);
    setSegments(next);
    // 혼입을 방금 해결했으므로 그 씬의 경계오류 표시를 뺀다(병합과 동일한 처리).
    clearBoundaryFlags([cur.label]);
    // 팝업이 옛 경계를 들고 있으면 화면과 데이터가 어긋나 사용자가 방금 한 편집을
    // 또 한다 — 앞 구간 기준으로 다시 만들고 그 머리 프레임으로 시킹한다.
    const head = next[i] as SceneSegment;
    const focusMs = frameSeekMs(head.start_ms, fps);
    setSelectedSeg(i);
    setPreview(buildSegPreview(head, i, focusMs, "head"));
    pauseAndSeek(focusMs);
    setNotice("씬을 나눴습니다 — 앞 구간 이름을 읽는 중…");
    // 읽기 실패는 두 갈래로 온다(빈 결과 · 예외). 사용자에겐 같은 상황이므로 같은
    // 문구를 쓰고, 어느 줄을 고쳐야 하는지 이름을 짚어 준다.
    const unreadMsg = `앞 구간 슬레이트를 읽지 못했습니다 — '${head.label}' 줄의 `
      + "이름을 직접 입력하세요.";
    // 앞 구간 한가운데 프레임의 슬레이트를 읽어 이름을 제안한다. 머리·꼬리는 디졸브에
    // 걸릴 확률이 높아 한가운데를 읽는다. 저장된 구역을 그대로 넘겨야 스캔과 같은
    // 상자를 읽어 같은 라벨이 나온다. 실패해도 분할은 유지한다 — 경계는 이미 맞았고
    // 남은 건 이름뿐이다.
    try {
      const midMs = frameSeekMs((head.start_ms + head.end_ms) / 2, fps);
      const res = await testOcrRegion(jobId, Math.round(midMs), ocrRegion);
      const upto = mode === "sequence"
        ? Math.max(-1, ...seqIdx)
        : Math.max(-1, ...seqIdx, ...sceneIdx);
      const proposed = previewLabel(res.tokens, upto);
      if (proposed && next.some((s, j) => j !== i && s.label === proposed)) {
        // 읽은 이름이 이미 목록에 있다 = 첫 나누기면 앞뒤가 같은 번호(나눌 자리가
        // 아니었을 가능성), _cut 줄을 이어 나눴으면 뒤쪽 원래 줄과의 중복이다.
        // 어느 쪽이든 얹으면 중복 이름이 되살아나므로(_cut을 붙인 이유) 자리표시자를
        // 남긴다. cur.label만 비교하면 이어 나누기가 빠져나간다(실기 2026-07-28:
        // _cut이 사라지고 같은 이름 두 줄). applySplitName이 최신 상태로 2차 방어.
        setNotice(`읽은 번호(${proposed})가 이미 목록에 있습니다 — 나눌 자리가 맞는지 `
          + `확인하세요. 맞다면 '${head.label}' 줄의 이름을 직접 고치면 됩니다.`);
      } else if (proposed && proposed !== head.label) {
        // 이름은 반드시 '지금 상태' 위에서 바꾼다. setSegments·renameSeg는 렌더 시점의
        // segments/data를 닫아두므로, OCR을 기다린 뒤 그대로 부르면 분할 전 배열이
        // 되살아나 방금 나눈 줄이 목록에서 통째로 사라진다(실기 재현 2026-07-28).
        setData((prev) => {
          if (!prev) return prev;
          const cur = mode === "sequence"
            ? prev.segments_sequence : prev.segments_scene;
          const named = applySplitName(cur, i, head.label, proposed);
          if (named === cur) return prev;   // 그 사이 다른 편집 — 건드리지 않는다
          return mode === "sequence"
            ? { ...prev, segments_sequence: named }
            : { ...prev, segments_scene: named };
        });
        // 팝업 머리글도 새 이름으로 — 화면과 목록이 어긋나면 사용자가 또 고친다.
        setPreview(buildSegPreview({ ...head, label: proposed }, i, focusMs, "head"));
        setNotice(`앞 구간 이름을 ${proposed}으로 읽었습니다 — 다르면 이름칸에서 고치세요.`);
      } else {
        setNotice(unreadMsg);
      }
    } catch {
      setNotice(unreadMsg);
    }
  };

  // 일괄 적용은 곧바로 바꾸지 않는다 — 무엇이 어떻게 바뀌는지 before→after로
  // 먼저 보여주고, 체크한 것만 적용한다. 적용 후에도 한 번은 되돌릴 수 있다.
  const [pendingFixes, setPendingFixes] = useState<LabelFix[] | null>(null);
  const [fixChecked, setFixChecked] = useState<Set<number>>(new Set());
  const [undoSnapshot, setUndoSnapshot] = useState<SceneSegment[] | null>(null);
  // 일괄 이름 바꾸기(접두 치환) 입력 — 자동 제안이 못 다루는 '다른 단어' 급
  // 접두(실기 EASA06 Scene12_* 26건)를 사용자가 지정해 한 번에 바꾼다.
  const [renameFrom, setRenameFrom] = useState("");
  const [renameTo, setRenameTo] = useState("");

  // 모드가 바뀌면 구간 목록 자체가 달라진다 — 이전 모드에서 만든 미리보기·되돌리기
  // 스냅샷은 모두 무의미해지므로 지운다(씬별 목록이 시퀀스별 화면에 남아 보이던
  // 문제). applyFixes의 from 검사가 2차 방어선이다. 필터·선택 초기화는 부모 몫.
  useEffect(() => {
    setPendingFixes(null);
    setFixChecked(new Set());
    setUndoSnapshot(null);
    setRenameFrom("");
    setRenameTo("");
    setEditUndo([]);  // 모드가 바뀌면 편집 되돌리기 스택의 인덱스가 무의미
  }, [mode]);

  const openFixPreview = () => {
    const fixes = confidentFixes(segments.map((s) => s.label), delimiters);
    setPendingFixes(fixes);
    setFixChecked(new Set(fixes.map((f) => f.index)));  // 기본 전체 선택
  };

  // 접두 치환도 같은 미리보기·적용 경로를 탄다 — 확인 다이얼로그, 체크 선별,
  // 되돌리기 스냅샷, 인접 동일 라벨 병합(confirmFixes)이 전부 공유된다.
  const renameFixes = prefixRenameFixes(
    segments.map((s) => s.label), renameFrom, renameTo);
  const openRenamePreview = () => {
    if (renameFixes.length === 0) return;
    setPendingFixes(renameFixes);
    setFixChecked(new Set(renameFixes.map((f) => f.index)));
  };

  const confirmFixes = () => {
    if (!pendingFixes) return;
    const applied = pendingFixes.filter((f) => fixChecked.has(f.index));
    if (applied.length === 0) { setPendingFixes(null); return; }
    setUndoSnapshot(segments);  // 되돌리기용 스냅샷
    setEditUndo([]);            // 일괄교정은 배열을 재편 — 개별 편집 스택 무효화
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
    setEditUndo([]);
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
    setEditUndo([]);
    setSegments(out);
    setNotice(`오독으로 갈라진 ${n}건을 흡수했습니다 — 저장 전입니다. 되돌리기 가능.`);
  };

  const undoFixes = () => {
    if (!undoSnapshot) return;
    setSegments(undoSnapshot);
    setUndoSnapshot(null);
    setEditUndo([]);
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
      setEditUndo([]);  // 저장하면 편집 되돌리기 히스토리 초기화(스냅샷은 저장 전 상태)
      const otherDirty = dirtyModes.has(mode === "scene" ? "sequence" : "scene");
      setNotice(otherDirty
        ? `${mode === "scene" ? "씬" : "시퀀스"} 저장 완료 — `
          + `${mode === "scene" ? "시퀀스" : "씬"} 모드에 저장 안 된 수정이 있습니다.`
        : "수정사항을 저장했습니다.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  return {
    dirty, putBoundaryOk, markBoundaryOk,
    editUndo, mergeSeg, undoEdit, renameSeg,
    nudgeBoundary, trimAt, splitAt,
    pendingFixes, setPendingFixes, fixChecked, setFixChecked,
    undoSnapshot, renameFrom, setRenameFrom, renameTo, setRenameTo,
    renameFixes, openFixPreview, openRenamePreview, confirmFixes,
    mergeDuplicates, flankedCount, cleanFlanked, undoFixes, saveEdits,
  };
}
