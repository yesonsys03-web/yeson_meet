import { useEffect, useState } from "react";
import { consoleStyles } from "./consoleStyles";
import { hasTauriRuntime } from "./useQrFullscreenShortcut";
import { previewLabel, tokenizeSlate } from "./sceneSplitLogic";
import { SceneFilmstrip } from "./SceneFilmstrip";
import {
  exportScenes, getScenes, scanScenes, setSceneRule,
  type ScenesData, type SceneSegment,
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

  const refresh = async () => setData(await getScenes(jobId));
  useEffect(() => { void refresh(); }, [jobId]);

  // 대표 프레임 = 첫 비어있지 않은 OCR 텍스트
  const sample = data?.frames.find((f) => f.text)?.text ?? "";
  const tokens = tokenizeSlate(sample);

  const runScan = async () => {
    setBusy(true); setError(null); setNotice("프레임 스캔·OCR 중…");
    try {
      await scanScenes(jobId);
      // 스캔은 비동기 — 폴링으로 완료 대기
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 1500));
        const d = await getScenes(jobId);
        if (d.scanned) { setData(d); setNotice("스캔 완료 — 토큰을 지정하세요."); return; }
      }
      setError("스캔이 시간 내 끝나지 않았습니다.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const applyRule = async () => {
    if (!data) return;
    setBusy(true); setError(null);
    try {
      const res = await setSceneRule(jobId, {
        seq_tokens: seqIdx, scene_tokens: sceneIdx,
      });
      setData({ ...(data as ScenesData), scanned: true,
                segments_scene: res.segments_scene,
                segments_sequence: res.segments_sequence });
      setNotice("경계를 계산했습니다.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const doExport = async () => {
    setBusy(true); setError(null); setNotice(null);
    try {
      let outDir: string | undefined;
      if (hasTauriRuntime()) {
        const { open } = await import("@tauri-apps/plugin-dialog");
        const dir = await open({ directory: true, title: "저장 폴더 선택" });
        if (typeof dir !== "string") { setNotice("저장 폴더 선택이 취소되었습니다."); return; }
        outDir = dir;
      }
      const res = await exportScenes(jobId, mode, outDir);
      setNotice(`${res.count}개 클립을 내보내는 중… (${outDir ?? "서버 폴더"})`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const segments: SceneSegment[] = data
    ? (mode === "sequence" ? data.segments_sequence : data.segments_scene) : [];

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
            <button type="button" style={{ ...consoleStyles.mutedAction, marginTop: 8 }}
              disabled={busy || seqIdx.length === 0}
              onClick={() => void applyRule()}>경계 계산</button>
          </div>

          {/* 모드 토글 + 필름스트립 */}
          <div style={{ display: "flex", gap: 16, alignItems: "center", fontSize: 13 }}>
            <label><input type="radio" checked={mode === "scene"}
              onChange={() => setMode("scene")} /> 씬별</label>
            <label><input type="radio" checked={mode === "sequence"}
              onChange={() => setMode("sequence")} /> 시퀀스별</label>
            <span style={{ opacity: 0.7 }}>{segments.length}개 구간</span>
          </div>
          <SceneFilmstrip jobId={jobId} segments={segments}
            thumbCount={data.frames.length}
            intervalMs={data.interval_ms ?? 1000}
            totalMs={(data.frames.at(-1)?.t_ms ?? 0) + (data.interval_ms ?? 1000)} />
          <button type="button" style={consoleStyles.action}
            disabled={busy || segments.length === 0}
            onClick={() => void doExport()}>
            {segments.length}개 클립 익스포트
          </button>
        </>
      )}
    </div>
  );
}
