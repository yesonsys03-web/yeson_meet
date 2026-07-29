// 씬 스캔 설정·실행 툴바 — 슬레이트 구역 지정, 스캔 방식/간격/최소 길이, 진행
// 단계 표시(중단), 전체 실행/스캔 버튼, 스캔 후의 토큰 규칙 지정(칩·예시 슬레이트·
// 경계 계산 버튼)까지. 상태와 실행 로직은 전부 부모(SceneSplitView)가 들고, 여기는
// 표시와 콜백 연결만 한다(JSX 분리 — 로직 이동 없음).
import { consoleStyles } from "./consoleStyles";
import { previewLabel } from "./sceneSplitLogic";
import type { OcrRegion, RefineStatus, SceneMethod, SlateTemplate } from "./videoApi";
import { SlateRegionPicker } from "./SlateRegionPicker";

type Props = {
  jobId: string;
  busy: boolean;
  // 스캔 완료 여부와 그 데이터의 방식 — 완료 전엔 실행 버튼, 후엔 규칙 지정을 그린다.
  scanned: boolean;
  method?: SceneMethod;
  stage: string | null;
  refineProg: RefineStatus | null;
  sample: string;
  tokens: string[];
  sampleMs: number;
  delimiters: string[];
  templates: SlateTemplate[];
  onTemplatesChange: (t: SlateTemplate[]) => void;
  showPicker: boolean;
  onTogglePicker: () => void;
  ocrRegion: OcrRegion | null;
  onOcrRegionChange: (r: OcrRegion) => void;
  scanMethod: SceneMethod;
  onScanMethodChange: (m: SceneMethod) => void;
  scanIntervalS: number;
  onScanIntervalChange: (s: number) => void;
  minSceneSec: string;
  onMinSceneSecChange: (v: string) => void;
  spaceDelim: boolean;
  // 구분자가 바뀌면 토큰 인덱스 의미가 바뀐다 — 선택 초기화는 부모 몫.
  onSpaceDelimToggle: (checked: boolean) => void;
  slateExample: string;
  onSlateExampleChange: (v: string) => void;
  seqIdx: number[];
  sceneIdx: number[];
  onToggleSeq: (i: number) => void;
  onToggleScene: (i: number) => void;
  onApplyTemplate: (t: SlateTemplate) => void;
  onCancelAll: () => void;
  onRunAll: (opts: { rescan: boolean }) => void;
  onRunScan: () => void;
  onApplyRule: () => void;
};

export function SceneScanControls({
  jobId, busy, scanned, method, stage, refineProg, sample, tokens, sampleMs,
  delimiters, templates, onTemplatesChange, showPicker, onTogglePicker,
  ocrRegion, onOcrRegionChange, scanMethod, onScanMethodChange,
  scanIntervalS, onScanIntervalChange, minSceneSec, onMinSceneSecChange,
  spaceDelim, onSpaceDelimToggle, slateExample, onSlateExampleChange,
  seqIdx, sceneIdx, onToggleSeq, onToggleScene, onApplyTemplate,
  onCancelAll, onRunAll, onRunScan, onApplyRule,
}: Props) {
  return (
    <>
      {/* 슬레이트 구역 — 쇼마다 위치가 다르므로 스캔 전에 잡아두면 판독이 빠르고
          정확하다. 스캔 후에도 다시 잡을 수 있다(다시 스캔해야 반영). */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" style={consoleStyles.mutedAction}
          onClick={onTogglePicker}>
          {showPicker ? "구역 지정 닫기" : "슬레이트 구역 지정"}
        </button>
        <span style={{ fontSize: 12, opacity: 0.7 }}>
          {ocrRegion
            ? `지정됨 — 가로 ${(ocrRegion.w * 100).toFixed(0)}% · 세로 ${(ocrRegion.h * 100).toFixed(0)}%`
            : "미지정 — 전체 프레임에서 상단을 훑습니다(느리고 쇼에 따라 실패)"}
        </span>
        {/* 스캔 방식 — 지문은 전 프레임 컷 감지라 경계가 프레임 정확하고 정밀화가
            없다. 가짜 컷 등 리스크가 보이면 간격 방식으로 폴백한다. */}
        <label style={{ fontSize: 12, opacity: 0.8, display: "inline-flex",
                        alignItems: "center", gap: 5, marginLeft: "auto" }}>
          방식
          <select value={scanMethod}
            onChange={(e) => onScanMethodChange(e.target.value as SceneMethod)}
            style={{ fontSize: 12, padding: "3px 6px", borderRadius: 4,
                     background: "transparent", color: "inherit",
                     border: "1px solid rgba(255,255,255,0.15)" }}>
            <option value="interval">간격 스캔 (샘플링+정밀화)</option>
            <option value="fingerprint">지문 컷 감지 (프레임 정확)</option>
          </select>
        </label>
        {scanMethod !== "fingerprint" ? (
          <>
            {/* 샘플 간격 — 짧은 씬(2초 미만)이 많으면 촘촘하게. 놓치면 그 씬 클립이
                아예 생기지 않는다(2초 샘플이 사이의 짧은 컷을 건너뛴다). */}
            <label style={{ fontSize: 12, opacity: 0.8, display: "inline-flex",
                            alignItems: "center", gap: 5 }}>
              샘플 간격
              <select value={scanIntervalS}
                onChange={(e) => onScanIntervalChange(Number(e.target.value))}
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
                (간격 비례). 진짜 짧은 씬이 삼켜지면 낮춘다. 지문 방식은 컷이
                프레임 정확이라 이 흡수 자체가 없다. */}
            <label style={{ fontSize: 12, opacity: 0.8, display: "inline-flex",
                            alignItems: "center", gap: 5 }}>
              최소 씬 길이
              <input value={minSceneSec}
                onChange={(e) => onMinSceneSecChange(e.target.value)}
                placeholder="자동" inputMode="decimal"
                style={{ width: 56, fontSize: 12, padding: "3px 6px", borderRadius: 4,
                         background: "transparent", color: "inherit",
                         border: "1px solid rgba(255,255,255,0.15)" }} />
              초
            </label>
          </>
        ) : null}
      </div>
      {showPicker ? (
        <SlateRegionPicker jobId={jobId} sampleMs={sampleMs} region={ocrRegion}
          onChange={onOcrRegionChange} templates={templates}
          onTemplatesChange={onTemplatesChange}
          rule={{ delimiters, seq_tokens: seqIdx, scene_tokens: sceneIdx,
                  scan_interval_s: scanIntervalS, method: scanMethod,
                  example: slateExample.trim() || undefined }}
          onApplyTemplate={onApplyTemplate} />
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
            onClick={onCancelAll}>중단</button>
        </div>
      ) : null}

      {!scanned ? (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button type="button" style={consoleStyles.action} disabled={busy}
            onClick={() => onRunAll({ rescan: true })}>
            {busy ? "실행 중…"
              : scanMethod === "fingerprint" ? "전체 실행 (컷 감지 → 경계)"
              : "전체 실행 (스캔 → 경계 → 정밀화)"}
          </button>
          <button type="button" style={consoleStyles.mutedAction} disabled={busy}
            onClick={onRunScan}>스캔만</button>
          <span style={{ fontSize: 12, opacity: 0.65 }}>
            토큰 규칙이 없으면 스캔까지만 하고 멈춥니다(스캔 결과를 보고 고르는 값이라).
          </span>
        </div>
      ) : (
        /* 규칙 지정: 토큰 칩 */
        <div>
          <p style={{ fontSize: 13, opacity: 0.75, margin: "0 0 6px" }}>
            대표 슬레이트: <code>{sample || "(판독 실패)"}</code> — 시퀀스/씬 토큰을 고르세요.
          </p>
          <label style={{ fontSize: 12, opacity: 0.8, display: "inline-flex",
                          alignItems: "center", gap: 5, marginBottom: 8 }}>
            <input type="checkbox" checked={spaceDelim}
              onChange={(e) => onSpaceDelimToggle(e.target.checked)} />
            공백도 구분자로 나누기 (기본: 공백은 필드 안에 유지)
          </label>
          {/* 예시 슬레이트 — 선언하면 Seq↔Seg류 머리글자 오독을 다수결 추측이
              아니라 이 구조로 교정한다(경계 계산 시 적용, 빈값=기존 동작). */}
          <label style={{ fontSize: 12, opacity: 0.8, display: "flex",
                          alignItems: "center", gap: 5, marginBottom: 8 }}>
            예시 슬레이트
            <input value={slateExample}
              onChange={(e) => onSlateExampleChange(e.target.value)}
              placeholder="예: Seq 01A_S01 - Panel 1" maxLength={200}
              style={{ flex: "0 1 260px", fontSize: 12, padding: "3px 6px",
                       borderRadius: 4, background: "transparent",
                       color: "inherit",
                       border: "1px solid rgba(255,255,255,0.15)" }} />
            <span style={{ opacity: 0.6 }}>
              실제 슬레이트 한 줄을 그대로 적으면 오독 교정이 정확해집니다
            </span>
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
                  onClick={() => onToggleSeq(i)}>SEQ</button>
                <button type="button" style={{ marginLeft: 4, fontSize: 11 }}
                  onClick={() => onToggleScene(i)}>SCENE</button>
              </span>
            ))}
          </div>
          <p style={{ fontSize: 12, opacity: 0.6, marginTop: 6 }}>
            시퀀스 라벨 미리보기: <code>{previewLabel(tokens, Math.max(-1, ...seqIdx))}</code>
            {"  ·  "}씬 라벨: <code>{previewLabel(tokens, Math.max(-1, ...seqIdx, ...sceneIdx))}</code>
          </p>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
            {/* 토큰을 고른 뒤의 주 동작 — 간격 방식은 경계 계산과 정밀화가 항상
                함께 필요하고, 지문 방식은 경계 계산으로 끝난다(이미 프레임 정확). */}
            <button type="button" style={consoleStyles.action}
              disabled={busy || seqIdx.length === 0}
              onClick={() => onRunAll({ rescan: false })}>
              {busy ? "실행 중…"
                : method === "fingerprint" ? "경계 계산 (시퀀스·씬)"
                : "경계 계산 + 정밀화 (시퀀스·씬)"}
            </button>
            <button type="button" style={consoleStyles.mutedAction}
              disabled={busy || seqIdx.length === 0}
              onClick={onApplyRule}>
              경계 계산만
            </button>
            {/* OCR 재판독 — 구역·판독 로직을 바꿨거나 오염된 스캔 복구용. */}
            <button type="button" style={consoleStyles.mutedAction}
              disabled={busy}
              onClick={() => onRunAll({ rescan: true })}>
              다시 스캔(전체)
            </button>
            {seqIdx.length === 0 ? (
              <span style={{ fontSize: 12, color: "#e2b340" }}>
                먼저 위에서 SEQ 토큰을 하나 이상 고르세요 (그래야 버튼이 활성화됩니다).
              </span>
            ) : null}
          </div>
        </div>
      )}
    </>
  );
}
