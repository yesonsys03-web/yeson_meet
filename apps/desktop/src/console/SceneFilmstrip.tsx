import { useEffect, useRef } from "react";
import { formatMs, segmentThumbRange, type LabelAnomaly } from "./sceneSplitLogic";
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
  // 선택된 구간(리스트 클릭) — 해당 썸네일 범위를 하이라이트·중앙정렬한다.
  selectedIndex?: number | null;
  highlight?: { from: number; to: number } | null;
  onSelectSegment?: (i: number) => void;
  // 썸네일 클릭 → 그 시각을 팝업(실제 영상 시킹)으로 크게 보여준다.
  onThumbClick?: (tMs: number) => void;
  // 목록에 보여줄 구간 인덱스(오독 필터 탭). null이면 전체. 인덱스는 원본 기준을
  // 유지해야 병합/이름수정 콜백이 올바른 구간을 가리킨다.
  visibleIndices?: number[] | null;
  // 인덱스 → 오독 교정 제안(있으면 라벨 옆에 원클릭 적용 버튼).
  suggestions?: Map<number, LabelAnomaly>;
};

// 다빈치 리졸브식 필름스트립: 썸네일을 시간축에 깔고 아래에 구간 목록을 얹는다.
// 리스트 구간을 클릭하면 필름스트립에서 그 범위를 하이라이트하고 중앙으로 스크롤한다.
// 썸네일을 클릭하면 실제 프레임을 팝업으로 크게 확인할 수 있다. 편집 콜백이 주어지면
// 잘못 인식된 구간(예 'VAL')을 이웃에 병합하거나 이름을 고칠 수 있다.
export function SceneFilmstrip(
  { jobId, segments, thumbCount, intervalMs, onMerge, onRename,
    selectedIndex, highlight, onSelectSegment, onThumbClick,
    visibleIndices, suggestions }: Props,
) {
  const thumbs = Array.from({ length: thumbCount }, (_, i) => i);
  const editable = Boolean(onMerge || onRename);
  const stripRef = useRef<HTMLDivElement>(null);

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

  // 선택 구간이 바뀌면 하이라이트 범위의 중앙 썸네일을 필름스트립 중앙으로 스크롤.
  useEffect(() => {
    if (!highlight || !stripRef.current) return;
    const center = Math.round((highlight.from + highlight.to) / 2);
    const el = stripRef.current.querySelector<HTMLElement>(`[data-thumb="${center}"]`);
    el?.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
  }, [highlight?.from, highlight?.to]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div ref={stripRef}
           style={{ display: "flex", overflowX: "auto", gap: 1,
                    background: "#000", borderRadius: 6, padding: 2 }}>
        {thumbs.flatMap((i) => {
          const on = highlight ? i >= highlight.from && i <= highlight.to : false;
          const cells = (boundaryBefore.get(i) ?? []).map(({ seg, idx }) => {
            const bOn = selectedIndex === idx;
            return (
              <img key={`b${idx}`} data-boundary={idx}
                   src={sceneThumbAtUrl(jobId, seg.start_ms)} alt=""
                   title={`${seg.label} 시작 ${formatMs(seg.start_ms)} (첫 프레임) — 클릭하면 크게 보기`}
                   onClick={() => onThumbClick?.(seg.start_ms)}
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
      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        {segments.map((s, i) => ({ s, i }))
          .filter(({ i }) => !visibleIndices || visibleIndices.includes(i))
          .map(({ s, i }) => (
          <div key={i}
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
            {onMerge ? (
              <span style={{ flexShrink: 0, display: "flex", gap: 3 }}>
                {/* 이 구간을 이웃에 흡수(잘못 인식된 짧은 구간 제거용) */}
                <button type="button" title="이전 구간에 병합"
                  disabled={i === 0}
                  style={miniBtn}
                  onClick={(e) => { e.stopPropagation(); onMerge(i, "prev"); }}>◀병합</button>
                <button type="button" title="다음 구간에 병합"
                  disabled={i === segments.length - 1}
                  style={miniBtn}
                  onClick={(e) => { e.stopPropagation(); onMerge(i, "next"); }}>병합▶</button>
              </span>
            ) : null}
          </div>
        ))}
      </div>
      {editable && segments.length > 0 ? (
        <p style={{ fontSize: 11, opacity: 0.55, margin: 0 }}>
          구간을 클릭하면 필름스트립에서 위치가 하이라이트됩니다. 썸네일을 클릭하면 크게 볼 수 있어요.
          잘못 인식된 구간은 ◀/▶ 병합으로 이웃에 흡수하고 이름은 직접 고친 뒤 "수정사항 저장"을 누르세요.
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
