import { useRef, useState } from "react";
import { consoleStyles } from "./consoleStyles";
import { regionFromDrag } from "./sceneSplitLogic";
import {
  saveSlateTemplate, setOcrRegion, testOcrRegion, videoMediaUrl,
  type OcrRegion, type SlateTemplate,
} from "./videoApi";

type Props = {
  jobId: string;
  // 구역을 확인할 프레임 시각(슬레이트가 떠 있는 지점).
  sampleMs: number;
  region: OcrRegion | null;
  onChange: (r: OcrRegion) => void;
  templates: SlateTemplate[];
  onTemplatesChange: (t: SlateTemplate[]) => void;
  // 템플릿 저장 시 함께 묶을 현재 토큰 규칙(같은 쇼면 포맷도 같다).
  rule: { delimiters: string[]; seq_tokens: number[]; scene_tokens: number[] };
  onApplyTemplate: (t: SlateTemplate) => void;
};

// 슬레이트 구역 지정 — 쇼마다 슬레이트 위치가 달라 코드로 가정할 수 없다.
// 프레임 위에서 드래그해 구역을 잡고, 저장 전에 그 구역으로 실제로 읽어본다.
// 확정한 구역+토큰 규칙은 쇼 이름으로 저장해 다음 작품에서 그대로 불러 쓴다.
export function SlateRegionPicker(
  { jobId, sampleMs, region, onChange, templates, onTemplatesChange,
    rule, onApplyTemplate }: Props,
) {
  const frameRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<{ x: number; y: number } | null>(null);
  const [hover, setHover] = useState<OcrRegion | null>(null);
  const [busy, setBusy] = useState(false);
  const [tested, setTested] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");

  const shown = hover ?? region;

  const boxOf = () => {
    const el = frameRef.current;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { left: r.left, top: r.top, width: r.width, height: r.height };
  };

  const onDown = (e: React.MouseEvent) => {
    setDrag({ x: e.clientX, y: e.clientY });
    setTested(null);
  };
  const onMove = (e: React.MouseEvent) => {
    const box = boxOf();
    if (!drag || !box) return;
    setHover(regionFromDrag(drag, { x: e.clientX, y: e.clientY }, box));
  };
  const onUp = (e: React.MouseEvent) => {
    const box = boxOf();
    if (drag && box) {
      const r = regionFromDrag(drag, { x: e.clientX, y: e.clientY }, box);
      if (r) onChange(r);
    }
    setDrag(null); setHover(null);
  };

  const runTest = async () => {
    if (!region) return;
    setBusy(true); setError(null); setTested(null);
    try {
      const res = await testOcrRegion(jobId, sampleMs, region);
      setTested(res.text || "(판독 실패 — 구역을 다시 잡아보세요)");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const persist = async () => {
    if (!region) return;
    setBusy(true); setError(null);
    try {
      await setOcrRegion(jobId, region);
      setTested("구역을 저장했습니다 — 이제 스캔하면 이 구역만 읽습니다.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const saveTemplate = async () => {
    if (!region || !name.trim()) return;
    setBusy(true); setError(null);
    try {
      const res = await saveSlateTemplate({ name: name.trim(), region, ...rule });
      onTemplatesChange(res.templates);
      setName("");
      setTested(`템플릿 '${name.trim()}'으로 저장했습니다.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const pct = (v: number) => `${(v * 100).toFixed(2)}%`;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <p style={{ fontSize: 13, opacity: 0.8, margin: 0 }}>
        슬레이트가 있는 구역을 프레임 위에서 드래그하세요. 쇼마다 위치가 다르므로
        이 지정이 곧 그 작품의 설정입니다 — 구역을 좁힐수록 판독이 빠르고 정확합니다.
      </p>
      {templates.length > 0 ? (
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: 12, opacity: 0.7 }}>쇼 템플릿:</span>
          {templates.map((t) => (
            <button key={t.name} type="button" style={consoleStyles.mutedAction}
              onClick={() => onApplyTemplate(t)}>{t.name}</button>
          ))}
        </div>
      ) : null}
      <div ref={frameRef}
           onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp}
           onMouseLeave={() => { setDrag(null); setHover(null); }}
           style={{ position: "relative", userSelect: "none", cursor: "crosshair",
                    borderRadius: 6, overflow: "hidden", background: "#000",
                    maxWidth: 960 }}>
        <video src={`${videoMediaUrl(jobId)}#t=${(sampleMs / 1000).toFixed(3)}`}
               preload="metadata" muted playsInline
               onLoadedMetadata={(e) => { e.currentTarget.currentTime = sampleMs / 1000; }}
               style={{ width: "100%", display: "block", pointerEvents: "none" }} />
        {shown ? (
          <div style={{ position: "absolute", pointerEvents: "none",
                        left: pct(shown.x), top: pct(shown.y),
                        width: pct(shown.w), height: pct(shown.h),
                        border: "2px solid #4a9eda",
                        boxShadow: "0 0 0 9999px rgba(0,0,0,0.45)" }} />
        ) : null}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" style={consoleStyles.mutedAction}
          disabled={busy || !region} onClick={() => void runTest()}>
          이 구역으로 읽어보기
        </button>
        <button type="button" style={consoleStyles.action}
          disabled={busy || !region} onClick={() => void persist()}>
          구역 저장
        </button>
        <input value={name} onChange={(e) => setName(e.target.value)}
          placeholder="쇼 이름(템플릿)"
          style={{ fontSize: 13, padding: "4px 6px", borderRadius: 4,
                   background: "transparent", color: "inherit",
                   border: "1px solid rgba(255,255,255,0.15)", width: 150 }} />
        <button type="button" style={consoleStyles.mutedAction}
          disabled={busy || !region || !name.trim()}
          onClick={() => void saveTemplate()}>
          템플릿으로 저장
        </button>
      </div>
      {tested ? (
        <p style={{ fontSize: 13, margin: 0, fontFamily: "monospace",
                    color: tested.startsWith("(") ? "#e2b340" : "#3f9a5f" }}>
          {tested}
        </p>
      ) : null}
      {error ? <p style={{ color: "#e5484d", margin: 0 }}>{error}</p> : null}
    </div>
  );
}
