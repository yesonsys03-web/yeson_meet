import { useEffect, useMemo, useRef, useState } from "react";
import { formatMs, frameSeekMs, mergeNeighborHint, modalLabelClass, modalLabelPrefix, NTSC_FPS, segmentTailMs, segmentThumbRange, type LabelAnomaly } from "./sceneSplitLogic";
import { sceneThumbAtUrl, sceneThumbUrl, type SceneSegment } from "./videoApi";

type Props = {
  jobId: string;
  segments: SceneSegment[];
  thumbCount: number;
  intervalMs: number;
  totalMs: number;
  // 편집 콜백(선택). 주어지면 각 구간에 병합/이름수정 컨트롤을 렌더한다.
  onMerge?: (i: number, into: "prev" | "next") => void;
  onRename?: (i: number, label: string) => void;
  // 선택된 구간(리스트 클릭) — 격자 대신 그 씬의 실제 머리·꼬리 프레임만 크게
  // 보여준다(격자 2초 간격으로는 프레임 단위 혼입을 못 본다). highlight는 선택이
  // 없을 때(전체 필름스트립)만 쓰인다.
  selectedIndex?: number | null;
  highlight?: { from: number; to: number } | null;
  onSelectSegment?: (i: number) => void;
  // 머리·꼬리 뷰에서 "전체 필름스트립"으로 돌아가기(선택 해제).
  onClearSelection?: () => void;
  // 꼬리 프레임 시각 계산용 실제 fps(없으면 24 가정 — 23.976 NTSC에서도 정확).
  videoFps?: number;
  // 썸네일 클릭 → 그 시각을 팝업(실제 영상 시킹)으로 크게 보여준다. 구간(seg)과
  // 인덱스·클릭한 쪽(머리/꼬리)을 함께 넘기면 팝업이 그 구간 [start,end)로 재생을
  // 제한하고, 팝업 안에서 경계를 프레임 단위로 편집할 수 있다(분할 교정).
  onThumbClick?: (tMs: number, seg?: SceneSegment, segIndex?: number,
                  side?: "head" | "tail") => void;
  // 목록에 보여줄 구간 인덱스(오독 필터 탭). null이면 전체. 인덱스는 원본 기준을
  // 유지해야 병합/이름수정 콜백이 올바른 구간을 가리킨다.
  visibleIndices?: number[] | null;
  // 인덱스 → 오독 교정 제안(있으면 라벨 옆에 원클릭 적용 버튼).
  suggestions?: Map<number, LabelAnomaly>;
  // 병합 되돌리기 — undoIndex(방금 병합한 '생존 구간')와 같은 줄에만 되돌리기 버튼을
  // 병합 버튼 오른쪽에 렌더한다(여러 단계 undo 스택의 top). null이면 되돌릴 병합이
  // 없어 아무 줄에도 안 뜬다. 실수로 병합했을 때 그 자리에서 바로 물릴 수 있다.
  undoIndex?: number | null;
  onUndoMerge?: () => void;
  // 개별 씬 익스포트(선택) — 이 씬과 맞닿은 이웃만 지난 익스포트 폴더에 다시 굽는다.
  // 경계를 하나 고쳤을 때 수백 개를 전부 다시 굽지 않게 한다. exportingIndex가 있으면
  // 그 줄은 진행 표시, 나머지 줄은 잠근다(서버가 새 익스포트를 시작하면 진행 중인
  // 익스포트를 취소하므로 동시 실행을 막는다).
  onExportOne?: (i: number) => void;
  exportingIndex?: number | null;
  exportDisabled?: boolean;
  // 경계오류 탭에서만 넘어온다 — 이 줄을 '확인했고 문제없음'으로 표시해 목록에서
  // 뺀다. 다른 탭에는 안 넘기므로 버튼도 안 보인다(줄에 이미 병합 2개·익스포트가
  // 있어 항상 띄우면 좁다).
  onBoundaryOk?: (i: number) => void;
  // 이전/다음 씬 이동(선택) — 수백 줄을 스크롤로 훑지 않고 선택만 옮겨 검수한다.
  // 보이는 목록(필터·검색 적용) 기준으로 부모가 계산한다.
  onStepSegment?: (delta: number) => void;
};

// 다빈치 리졸브식 필름스트립: 썸네일을 시간축에 깔고 아래에 구간 목록을 얹는다.
// 리스트 구간을 클릭하면 필름스트립에서 그 범위를 하이라이트하고 중앙으로 스크롤한다.
// 썸네일을 클릭하면 실제 프레임을 팝업으로 크게 확인할 수 있다. 편집 콜백이 주어지면
// 잘못 인식된 구간(예 'VAL')을 이웃에 병합하거나 이름을 고칠 수 있다.
export function SceneFilmstrip(
  { jobId, segments, thumbCount, intervalMs, onMerge, onRename,
    selectedIndex, highlight, onSelectSegment, onClearSelection, videoFps,
    onThumbClick, visibleIndices, suggestions, undoIndex, onUndoMerge,
    onExportOne, exportingIndex, exportDisabled, onBoundaryOk,
    onStepSegment }: Props,
) {
  const thumbs = Array.from({ length: thumbCount }, (_, i) => i);
  // 이 쇼의 '정상 라벨 모양' — 병합 추천이 깨진 이웃 쪽을 가리키지 않게 하는
  // 자격 심사에 쓴다(mergeNeighborHint 참조). 목록이 길어 한 번만 계산한다.
  const { validClass, validPrefix } = useMemo(() => {
    const labels = segments.map((s) => s.label);
    const cls = modalLabelClass(labels);
    return { validClass: cls, validPrefix: modalLabelPrefix(labels, cls) };
  }, [segments]);
  const editable = Boolean(onMerge || onRename);
  const stripRef = useRef<HTMLDivElement>(null);
  // 경계 썸네일을 못 가져오면(구버전 서버 등) 그 칸을 숨긴다 — 깨진 이미지
  // 아이콘을 늘어놓는 대신 격자 썸네일만 있는 이전 동작으로 조용히 물러난다.
  const [failedBoundaries, setFailedBoundaries] = useState<Set<number>>(new Set());

  // 리스트에서 씬을 고르면 격자 대신 그 씬의 머리·꼬리 프레임만 본다.
  const selectedSeg = selectedIndex != null ? segments[selectedIndex] ?? null : null;
  // 머리·꼬리 프레임을 못 가져온 시각(구버전 서버 등) — 깨진 아이콘 대신 안내.
  const [failedFrames, setFailedFrames] = useState<Set<number>>(new Set());

  // 구간 시작이 2초 격자 위가 아니면(정밀화된 경계) 그 시각의 실제 첫 프레임을
  // 격자 썸네일 앞에 끼워 넣는다 — 격자 썸네일만 있으면 구간의 첫 칸이 시작보다
  // 최대 2초 뒤라 "첫 프레임"으로 오해된다(실기: 샷 프레임번호가 1이 아닌 24로 보임).
  const boundaryBefore = new Map<number, { seg: SceneSegment; idx: number }[]>();
  segments.forEach((seg, idx) => {
    const { from } = segmentThumbRange(seg.start_ms, seg.end_ms, intervalMs, thumbCount);
    if (seg.start_ms === from * intervalMs) return;  // 격자와 일치하면 불필요
    const list = boundaryBefore.get(from) ?? [];
    list.push({ seg, idx });
    boundaryBefore.set(from, list);
  });

  // 보이는 목록(필터·검색 적용)에서 선택 씬의 위치 — 이전/다음 버튼의 카운터·비활성
  // 판정에 쓴다. 0은 "선택이 보이는 목록에 없음"(필터가 바뀐 직후).
  const visibleRows = visibleIndices ?? segments.map((_, i) => i);
  const selPos = selectedIndex != null ? visibleRows.indexOf(selectedIndex) + 1 : 0;

  // 선택이 바뀌면 그 줄을 보이게 스크롤 — 이전/다음 버튼이나 ←/→로 이동할 때 수백 줄
  // 목록에서 그 줄을 눈으로 찾지 않아도 되게 한다. block:"nearest"라 이미 보이는 줄을
  // 클릭했을 때는 화면이 움직이지 않는다.
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (selectedIndex == null) return;
    listRef.current?.querySelector<HTMLElement>(`[data-row="${selectedIndex}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [selectedIndex]);

  // 선택 구간이 바뀌면 하이라이트 범위의 중앙 썸네일을 필름스트립 중앙으로 스크롤.
  useEffect(() => {
    if (!highlight || !stripRef.current) return;
    const center = Math.round((highlight.from + highlight.to) / 2);
    const el = stripRef.current.querySelector<HTMLElement>(`[data-thumb="${center}"]`);
    el?.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
  }, [highlight?.from, highlight?.to]);

  // 소스 fps — 팝업 시킹의 프레임 인덱스가 여기에 민감하다(경계에서 24 vs 23.976이
  // 프레임을 1 어긋냄). 서버가 측정값을 보내면 그걸, 없으면 NTSC를 쓴다.
  const fps = videoFps && videoFps > 0 ? videoFps : NTSC_FPS;

  // 선택된 씬의 머리·꼬리 검수 카드 한 장(실제 첫/끝 프레임 + 이웃 라벨 안내).
  const renderHeadTail = (seg: SceneSegment, idx: number) => {
    const prevLabel = idx > 0 ? segments[idx - 1]!.label : "(없음 · 영상 시작)";
    const nextLabel = idx < segments.length - 1
      ? segments[idx + 1]!.label : "(없음 · 영상 끝)";
    const tailMs = segmentTailMs(seg.start_ms, seg.end_ms, fps);
    // 썸네일 이미지는 ms(경계) 그대로 — 서버 -ss snap-up이 이 씬 첫/끝 프레임을
    // 집는다. 팝업(HTML5)만 그 프레임 중앙으로 시킹해야 같은 프레임이 뜬다.
    const frame = (ms: number, kind: "head" | "tail") => {
      const accent = kind === "head" ? "#4a9eda" : "#e2b340";
      const seekMs = frameSeekMs(ms, fps);
      const failed = failedFrames.has(ms);
      return (
        <figure style={{ margin: 0, flex: "1 1 320px", minWidth: 240,
                         display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: accent }}>
            {kind === "head" ? "머리 · 첫 프레임" : "꼬리 · 마지막 프레임"}
            <span style={{ opacity: 0.6, fontWeight: 400, marginLeft: 6 }}>
              {formatMs(kind === "head" ? seg.start_ms : seg.end_ms)}
            </span>
          </div>
          {failed ? (
            <div style={{ height: 150, display: "flex", alignItems: "center",
                          justifyContent: "center", background: "#111",
                          borderRadius: 4, fontSize: 12, opacity: 0.6,
                          outline: `2px solid ${accent}`, outlineOffset: "-2px" }}>
              프레임을 가져올 수 없습니다
            </div>
          ) : (
            <img src={sceneThumbAtUrl(jobId, ms, 240)} alt=""
                 loading="lazy" decoding="async"
                 onError={() => setFailedFrames((p) =>
                   p.has(ms) ? p : new Set(p).add(ms))}
                 title={`${formatMs(ms)} — 클릭하면 크게 보기`}
                 onClick={() => onThumbClick?.(seekMs, seg, idx, kind)}
                 style={{ width: "100%", height: 150, objectFit: "contain",
                          background: "#000", borderRadius: 4,
                          cursor: onThumbClick ? "zoom-in" : "default",
                          outline: `2px solid ${accent}`, outlineOffset: "-2px" }} />
          )}
          {/* 두 줄 높이를 미리 잡아둔다 — 이웃 라벨 길이에 따라 한 줄/두 줄이 오가면
              카드 높이가 바뀌어, 이전/다음 씬으로 넘길 때마다 위 썸네일이 위아래로
              움직인다(검수 중 눈이 따라가야 하는 최악의 UX). */}
          <small style={{ fontSize: 11, opacity: 0.7, display: "block",
                          minHeight: 28 }}>
            {kind === "head"
              ? `이전 씬(${prevLabel}) 슬레이트가 보이면 머리 혼입`
              : `다음 씬(${nextLabel}) 슬레이트가 보이면 꼬리 혼입`}
          </small>
        </figure>
      );
    };
    return (
      <div style={{ background: "#000", borderRadius: 6, padding: 10,
                    display: "flex", flexDirection: "column", gap: 8 }}>
        {/* 한 줄로 고정한다(nowrap) — 줄바꿈이 생기면 카드가 커져 아래 썸네일이
            내려간다. 라벨은 길면 말줄임, 카운터는 폭을 못박아 자릿수가 바뀌어도
            (9/421 → 15/421) 다른 요소가 밀리지 않게 한다. */}
        <div style={{ display: "flex", alignItems: "center", gap: 10,
                      flexWrap: "nowrap", overflow: "hidden" }}>
          <strong style={{ fontFamily: "monospace", fontSize: 14, minWidth: 0,
                           whiteSpace: "nowrap", overflow: "hidden",
                           textOverflow: "ellipsis" }}>{seg.label}</strong>
          <span style={{ fontSize: 12, opacity: 0.7, whiteSpace: "nowrap",
                         flexShrink: 0 }}>
            {formatMs(seg.start_ms)}–{formatMs(seg.end_ms)}
          </span>
          {/* 이전/다음 씬 — 목록을 스크롤하지 않고 검수 대상을 옮긴다. 카운터는 지금
              보이는 목록(필터·검색 적용) 기준이라, 좁혀 놓으면 그 안에서만 오간다. */}
          {onStepSegment ? (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 4,
                           flexShrink: 0 }}>
              <button type="button" style={miniBtn} disabled={selPos === 1}
                title="이전 씬 (키보드 ←)"
                onClick={() => onStepSegment(-1)}>◀ 이전 씬</button>
              <span style={{ fontSize: 12, opacity: 0.7, whiteSpace: "nowrap",
                             fontVariantNumeric: "tabular-nums",
                             minWidth: 78, textAlign: "center" }}>
                {selPos > 0 ? selPos : "–"} / {visibleRows.length}
              </span>
              <button type="button" style={miniBtn}
                disabled={selPos === visibleRows.length}
                title="다음 씬 (키보드 →)"
                onClick={() => onStepSegment(1)}>다음 씬 ▶</button>
            </span>
          ) : null}
          {onClearSelection ? (
            <button type="button" style={{ ...miniBtn, marginLeft: "auto",
                                           flexShrink: 0 }}
              onClick={onClearSelection}>◀ 전체 필름스트립</button>
          ) : null}
        </div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          {frame(seg.start_ms, "head")}
          {frame(tailMs, "tail")}
        </div>
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {/* 검수 뷰(격자/머리·꼬리)를 상단에 고정 — 400+ 리스트에서 아래로 스크롤해
          씬을 클릭해도 미리보기가 화면 밖으로 사라지지 않게(위로 다시 안 가도 됨). */}
      <div style={{ position: "sticky", top: 0, zIndex: 5,
                    background: "var(--ys-bg-app)", paddingBottom: 6 }}>
      {selectedSeg ? renderHeadTail(selectedSeg, selectedIndex!) : (
      <div ref={stripRef} className="filmstrip-scroll"
           style={{ display: "flex", overflowX: "auto", gap: 1,
                    background: "#000", borderRadius: 6, padding: 2 }}>
        {thumbs.flatMap((i) => {
          const on = highlight ? i >= highlight.from && i <= highlight.to : false;
          const cells = (boundaryBefore.get(i) ?? [])
            .filter(({ seg }) => !failedBoundaries.has(seg.start_ms))
            .map(({ seg, idx }) => {
            const bOn = selectedIndex === idx;
            return (
              <img key={`b${idx}`} data-boundary={idx}
                   src={sceneThumbAtUrl(jobId, seg.start_ms)} alt=""
                   // 씬 모드는 구간이 수백 개다. 경계 썸네일은 서버가 요청 시
                   // ffmpeg로 뽑으므로, 화면에 보이는 것만 지연 로드해 한꺼번에
                   // 수백 번 추출하는 일이 없게 한다.
                   loading="lazy" decoding="async"
                   onError={() => setFailedBoundaries((prev) =>
                     prev.has(seg.start_ms) ? prev : new Set(prev).add(seg.start_ms))}
                   title={`${seg.label} 시작 ${formatMs(seg.start_ms)} (첫 프레임) — 클릭하면 크게 보기`}
                   onClick={() => onThumbClick?.(frameSeekMs(seg.start_ms, fps), seg, idx, "head")}
                   style={{ height: 72, flexShrink: 0,
                            cursor: onThumbClick ? "zoom-in" : "default",
                            opacity: highlight && !bOn ? 0.4 : 1,
                            // 격자 썸네일과 구분되게 경계 프레임은 호박색 테두리.
                            outline: `2px solid ${bOn ? "#4a9eda" : "#e2b340"}`,
                            outlineOffset: "-2px",
                            transition: "opacity 0.15s" }} />
            );
          });
          return [
            ...cells,
            <img key={i} data-thumb={i} src={sceneThumbUrl(jobId, i)} alt=""
                 loading="lazy" decoding="async"
                 title={`${formatMs(i * intervalMs)} — 클릭하면 크게 보기`}
                 onClick={() => onThumbClick?.(i * intervalMs)}
                 style={{ height: 72, flexShrink: 0, cursor: onThumbClick ? "zoom-in" : "default",
                          // 선택 구간에 속한 썸네일만 밝게, 나머지는 살짝 어둡게.
                          opacity: highlight && !on ? 0.4 : 1,
                          outline: on ? "2px solid #4a9eda" : "none",
                          outlineOffset: on ? "-2px" : undefined,
                          transition: "opacity 0.15s" }} />,
          ];
        })}
      </div>
      )}
      </div>
      {/* 목록에 자체 스크롤 영역을 준다 — 목록이 바깥 패널과 같은 스크롤을 쓰면,
          이전/다음 씬으로 넘길 때 선택 줄을 보이게 하려고 바깥이 스크롤되면서 위쪽
          검수 카드(sticky가 아직 안 붙은 구간)까지 함께 움직인다. 목록만 스크롤하면
          카드는 제자리에 고정되고 줄만 흐른다. */}
      <div ref={listRef}
           style={{ display: "flex", flexDirection: "column", gap: 3,
                    maxHeight: "45vh", overflowY: "auto" }}>
        {segments.map((s, i) => ({ s, i }))
          .filter(({ i }) => !visibleIndices || visibleIndices.includes(i))
          .map(({ s, i }) => (
          <div key={i} data-row={i}
               onClick={() => onSelectSegment?.(i)}
               style={{ display: "flex", gap: 8, fontSize: 13, alignItems: "center",
                        padding: "8px 8px", borderRadius: 4,
                        cursor: onSelectSegment ? "pointer" : "default",
                        outline: selectedIndex === i ? "1px solid #4a9eda" : "none",
                        background: selectedIndex === i
                          ? "rgba(74,158,218,0.18)" : "rgba(255,255,255,0.05)" }}>
            {onRename ? (
              // 클릭이 행 선택으로 버블링되게 stopPropagation을 걸지 않는다 —
              // 라벨을 눌러도 필름스트립 하이라이트가 동작해야 한다(행 전체가 타깃).
              // 이름 편집은 그대로 되고, 편집 중 텍스트 드래그도 정상.
              <input value={s.label}
                onChange={(e) => onRename(i, e.target.value)}
                style={{ fontFamily: "monospace", fontSize: 13, flex: 1, minWidth: 0,
                         background: "transparent", color: "inherit",
                         border: "1px solid rgba(255,255,255,0.12)", borderRadius: 3,
                         padding: "3px 4px" }} />
            ) : (
              <span style={{ fontFamily: "monospace", overflowWrap: "anywhere",
                             flex: 1, minWidth: 0 }}>{s.label}</span>
            )}
            {/* 오독 교정 제안 — 누르면 그 행 라벨만 바꾼다(일괄 적용과 별개). */}
            {(() => {
              const a = suggestions?.get(i);
              if (!a || !a.suggestion || a.suggestion === s.label) return null;
              return (
                <button type="button" style={{ ...miniBtn, flexShrink: 0,
                          borderColor: a.confident ? "#3f9a5f" : "#e2b340",
                          fontFamily: "monospace" }}
                  title={a.confident
                    ? "제안 적용" : "숫자가 남아 애매한 제안 — 프레임을 확인하세요"}
                  onClick={(e) => { e.stopPropagation(); onRename?.(i, a.suggestion!); }}>
                  {a.confident ? "→ " : "→? "}{a.suggestion}
                </button>
              );
            })()}
            <span style={{ opacity: 0.7, flexShrink: 0 }}>
              {formatMs(s.start_ms)}–{formatMs(s.end_ms)}
            </span>
            {onMerge ? (() => {
              // 병합 버튼에 '진짜 이웃'의 이름을 적는다 — 필터를 걸면 그 이웃이
              // 목록에서 사라져, 이름 없이는 어느 쪽을 눌러야 할지 알 수 없다
              // (실기: 경계 오류 탭). 라벨이 더 가까운 쪽엔 힌트 테두리만 준다
              // (자동 적용 없음 — 판단은 사용자 몫).
              const prevLabel = i > 0 ? segments[i - 1]?.label ?? null : null;
              const nextLabel = i < segments.length - 1
                ? segments[i + 1]?.label ?? null : null;
              const hint = mergeNeighborHint({
                label: s.label, prev: prevLabel, next: nextLabel,
                suggestion: suggestions?.get(i)?.suggestion,
                validClass, validPrefix });
              const same = hint === "both";
              const tip = (side: "prev" | "next", label: string | null) =>
                label == null ? (side === "prev" ? "이전 구간이 없습니다"
                                                 : "다음 구간이 없습니다")
                  : same ? `양쪽 이웃이 같은 씬(${label})입니다 — 어느 쪽이든 결과가 같습니다`
                  : hint === side ? `${label}에 병합 (이 구간 이름과 거의 같습니다)`
                  : `${label}에 병합`;
              const btn = (side: "prev" | "next") => ({
                ...miniBtn, fontFamily: "monospace",
                ...(hint === side || same
                  ? { borderColor: "#3f9a5f", color: "#8fe0a8" } : null),
              });
              // 긴 라벨이 행을 밀지 않게 자른다(끝자리가 판단 근거라 앞을 자른다).
              const short = (l: string) => (l.length > 14 ? `…${l.slice(-13)}` : l);
              return (
              <span style={{ flexShrink: 0, display: "flex", gap: 3 }}>
                {/* 이 구간을 이웃에 흡수(잘못 인식된 짧은 구간 제거용) */}
                <button type="button" title={tip("prev", prevLabel)}
                  disabled={i === 0}
                  style={btn("prev")}
                  onClick={(e) => { e.stopPropagation(); onMerge(i, "prev"); }}>
                  ◀{prevLabel ? ` ${short(prevLabel)}` : "병합"}</button>
                <button type="button" title={tip("next", nextLabel)}
                  disabled={i === segments.length - 1}
                  style={btn("next")}
                  onClick={(e) => { e.stopPropagation(); onMerge(i, "next"); }}>
                  {nextLabel ? `${short(nextLabel)} ` : "병합"}▶</button>
                {/* 되돌리기 — 방금 병합한 이 구간에만 뜬다. 여러 번 누르면 한
                    단계씩 이전 병합까지 거슬러 올라간다(실수 복구용). */}
                {onUndoMerge && undoIndex === i ? (
                  <button type="button" title="방금 병합을 되돌립니다"
                    style={{ ...miniBtn, color: "#6db6ff",
                             borderColor: "rgba(109,182,255,0.5)" }}
                    onClick={(e) => { e.stopPropagation(); onUndoMerge(); }}>↩되돌리기</button>
                ) : null}
              </span>
              );
            })() : null}
            {/* 이 씬만(+맞닿은 이웃) 다시 익스포트 — 경계를 하나 고친 뒤 전체를 다시
                굽지 않아도 되게 한다. 이웃까지 굽는 이유는 경계가 공유돼 이웃의
                프레임 수도 함께 바뀌기 때문(이 씬만 내보내면 이웃이 옛 경계로 남는다). */}
            {onExportOne ? (
              <button type="button" style={{ ...miniBtn, flexShrink: 0 }}
                disabled={exportDisabled || exportingIndex != null}
                title="이 씬과 맞닿은 이웃 씬을 지난 익스포트 폴더에 다시 내보냅니다"
                onClick={(e) => { e.stopPropagation(); onExportOne(i); }}>
                {/* 화살표는 ↩되돌리기와 같은 화살표 블록(U+2193)을 쓴다 — ⬇(U+2B07)는
                    Windows에서 Segoe UI Emoji로 잡혀 컬러 이모지처럼 튄다. */}
                {exportingIndex === i ? "내보내는 중…" : "↓익스포트"}</button>
            ) : null}
            {/* 확인했고 문제없다 — 경계오류 목록에서 뺀다. 검사가 디졸브를 혼입으로
                잡는 거짓 양성이 있어, 눈으로 확인한 줄을 지울 수단이 필요하다. */}
            {onBoundaryOk ? (
              <button type="button" style={{ ...miniBtn, flexShrink: 0 }}
                title="이 씬의 경계를 확인했고 문제가 없습니다 — 경계 오류 목록에서 뺍니다(저장됨). 나중에 이 씬 경계를 고치면 다시 나타납니다."
                onClick={(e) => { e.stopPropagation(); onBoundaryOk(i); }}>
                ✓ 문제없음</button>
            ) : null}
          </div>
        ))}
      </div>
      {editable && segments.length > 0 ? (
        <p style={{ fontSize: 11, opacity: 0.55, margin: 0 }}>
          구간을 클릭하면 그 씬의 머리·꼬리 프레임만 크게 보여줍니다 — 머리에 이전 씬,
          꼬리에 다음 씬 슬레이트가 보이면 경계 혼입입니다. 프레임을 클릭하면 더 크게 볼 수 있어요.
          잘못 인식된 구간은 ◀/▶ 병합으로 이웃에 흡수하고 이름은 직접 고친 뒤 "수정사항 저장"을 누르세요.
          {onExportOne ? " 저장 후 ↓익스포트를 누르면 그 씬과 맞닿은 이웃만 지난 폴더에 다시 내보냅니다(전체를 다시 굽지 않아도 됩니다)." : ""}
        </p>
      ) : null}
    </div>
  );
}

const miniBtn = {
  fontSize: 11, padding: "1px 5px", borderRadius: 3, whiteSpace: "nowrap" as const,
  border: "1px solid rgba(255,255,255,0.15)", background: "transparent",
  color: "inherit", cursor: "pointer",
};
