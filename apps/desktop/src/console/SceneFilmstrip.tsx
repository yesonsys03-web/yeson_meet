import { formatMs } from "./sceneSplitLogic";
import { sceneThumbUrl, type SceneSegment } from "./videoApi";

type Props = {
  jobId: string;
  segments: SceneSegment[];
  thumbCount: number;
  intervalMs: number;
  totalMs: number;
  // 편집 콜백(선택). 주어지면 각 구간에 병합/이름수정 컨트롤을 렌더한다.
  onMerge?: (i: number, into: "prev" | "next") => void;
  onRename?: (i: number, label: string) => void;
};

// 다빈치 리졸브식 필름스트립: 썸네일을 시간축에 깔고 아래에 구간 목록을 얹는다.
// 편집 콜백이 주어지면 잘못 인식된 구간(예: OCR 노이즈 'VAL')을 이웃에 병합하거나
// 이름을 고칠 수 있다.
export function SceneFilmstrip(
  { jobId, segments, thumbCount, onMerge, onRename }: Props,
) {
  const thumbs = Array.from({ length: thumbCount }, (_, i) => i);
  const editable = Boolean(onMerge || onRename);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", overflowX: "auto", gap: 1,
                    background: "#000", borderRadius: 6, padding: 2 }}>
        {thumbs.map((i) => (
          <img key={i} src={sceneThumbUrl(jobId, i)} alt=""
               style={{ height: 64, flexShrink: 0 }} />
        ))}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        {segments.map((s, i) => (
          <div key={i} style={{ display: "flex", gap: 8, fontSize: 13,
                                alignItems: "center",
                                padding: "3px 8px", borderRadius: 4,
                                background: "rgba(255,255,255,0.05)" }}>
            {onRename ? (
              <input value={s.label}
                onChange={(e) => onRename(i, e.target.value)}
                style={{ fontFamily: "monospace", fontSize: 13, flex: 1, minWidth: 0,
                         background: "transparent", color: "inherit",
                         border: "1px solid rgba(255,255,255,0.12)", borderRadius: 3,
                         padding: "1px 4px" }} />
            ) : (
              <span style={{ fontFamily: "monospace", overflowWrap: "anywhere",
                             flex: 1, minWidth: 0 }}>{s.label}</span>
            )}
            <span style={{ opacity: 0.7, flexShrink: 0 }}>
              {formatMs(s.start_ms)}–{formatMs(s.end_ms)}
            </span>
            {onMerge ? (
              <span style={{ flexShrink: 0, display: "flex", gap: 3 }}>
                {/* 이 구간을 이웃에 흡수(잘못 인식된 짧은 구간 제거용) */}
                <button type="button" title="이전 구간에 병합"
                  disabled={i === 0}
                  style={miniBtn} onClick={() => onMerge(i, "prev")}>◀병합</button>
                <button type="button" title="다음 구간에 병합"
                  disabled={i === segments.length - 1}
                  style={miniBtn} onClick={() => onMerge(i, "next")}>병합▶</button>
              </span>
            ) : null}
          </div>
        ))}
      </div>
      {editable && segments.length > 0 ? (
        <p style={{ fontSize: 11, opacity: 0.55, margin: 0 }}>
          잘못 인식된 구간은 ◀/▶ 병합으로 이웃에 흡수하고, 이름은 직접 고칠 수 있어요.
          수정 후 아래 "수정사항 저장"을 눌러야 익스포트에 반영됩니다.
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
