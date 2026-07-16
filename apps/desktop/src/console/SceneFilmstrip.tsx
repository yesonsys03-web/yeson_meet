import { formatMs } from "./sceneSplitLogic";
import { sceneThumbUrl, type SceneSegment } from "./videoApi";

type Props = {
  jobId: string;
  segments: SceneSegment[];
  thumbCount: number;
  intervalMs: number;
  totalMs: number;
};

// 다빈치 리졸브식 필름스트립: 썸네일을 시간축에 깔고 세그먼트 경계를 세로선으로
// 얹는다. MVP는 읽기 전용(확인용) — 드래그 조정은 후속(Task 밖, 스펙 8절).
export function SceneFilmstrip({ jobId, segments, thumbCount, intervalMs, totalMs }: Props) {
  const thumbs = Array.from({ length: thumbCount }, (_, i) => i);
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
                                justifyContent: "space-between",
                                padding: "3px 8px", borderRadius: 4,
                                background: "rgba(255,255,255,0.05)" }}>
            <span style={{ fontFamily: "monospace", overflowWrap: "anywhere" }}>{s.label}</span>
            <span style={{ opacity: 0.7, flexShrink: 0 }}>
              {formatMs(s.start_ms)}–{formatMs(s.end_ms)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
