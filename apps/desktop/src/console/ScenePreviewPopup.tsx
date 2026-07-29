// 씬 검수 팝업 — 썸네일 클릭으로 열리는 프레임 정확 플레이어와 그 위의 편집
// 컨트롤(In/Out 트림·나누기·경계 교정·씬 이동). 재생 위치·구간 감시·단축키 등
// '영상을 다루는' 상태는 전부 여기가 소유하고, 세그먼트를 '편집하는' 일은 부모
// (SceneSplitView)의 콜백에 맡긴다 — 편집 결과는 preview prop으로 다시 내려온다.
//
// 부모가 편집 직후 팝업 영상을 편집한 경계 프레임에 멈춰 세울 수 있게
// pauseAndSeek 핸들을 노출한다(ref). 화면과 데이터가 어긋나면 사용자가 이미 한
// 편집을 또 한다.
import { forwardRef, useEffect, useImperativeHandle, useRef, useState,
         type CSSProperties } from "react";
import { consoleStyles } from "./consoleStyles";
import {
  formatMs, frameNumberAt, NTSC_FPS, scenePopupAction, segFrameNumber,
  stepVisibleIndex, trimFrames, type SegPreview,
} from "./sceneSplitLogic";
import type { SceneSegment } from "./videoApi";

export type ScenePreviewPopupHandle = {
  // 영상을 멈추고 ms로 시킹한다 — 부모가 경계 교정·분할·되돌리기·씬 이동 직후
  // 편집한 그 프레임을 곧바로 보여줄 때 쓴다.
  pauseAndSeek: (ms: number) => void;
};

type Props = {
  src: string;
  preview: SegPreview;
  segments: SceneSegment[];
  // 목록과 같은 '보이는 목록'(필터·검색 적용) — 씬 이동·카운터가 이 기준을 따라야
  // 화면과 어긋나지 않는다.
  visibleAll: number[];
  dirty: boolean;
  // 스택 top이 이 씬의 경계 교정/분할일 때만 되돌리기 버튼을 띄운다(부모가 판정).
  canUndo: boolean;
  // 구간 반복재생·경계 교정 프레임 수는 부모 상태 — 팝업을 닫았다 열어도 유지된다.
  loopSeg: boolean;
  onToggleLoop: () => void;
  nudgeFrames: number;
  onNudgeFramesChange: (n: number) => void;
  onClose: () => void;
  onNudge: (side: "head" | "tail", delta: number) => void;
  onTrim: (side: "in" | "out", ms: number) => void;
  onSplit: (ms: number) => void;
  onStepScene: (delta: number) => void;
  onUndo: () => void;
};

const editBtn: CSSProperties = {
  fontSize: 12, padding: "4px 9px", borderRadius: 5, whiteSpace: "nowrap",
  border: "1px solid rgba(255,255,255,0.25)", background: "rgba(255,255,255,0.10)",
  color: "#fff", cursor: "pointer",
};
// 팝업 좌우 씬 이동 버튼 — 영상 바깥 여백에 세로 중앙으로 띄운다(검수할 프레임을
// 가리지 않게). 라이트박스의 좌우 화살표와 같은 자리라 설명 없이 눌러진다.
const sideNavBtn: CSSProperties = {
  position: "absolute", top: "50%", transform: "translateY(-50%)", zIndex: 2,
  display: "flex", flexDirection: "column", alignItems: "center", gap: 4,
  width: 56, padding: "18px 0", borderRadius: 10, fontSize: 11,
  border: "1px solid rgba(255,255,255,0.18)", background: "rgba(18,18,22,0.72)",
  color: "#fff", whiteSpace: "nowrap",
};

export const ScenePreviewPopup = forwardRef<ScenePreviewPopupHandle, Props>(
  function ScenePreviewPopup({
    src, preview, segments, visibleAll, dirty, canUndo,
    loopSeg, onToggleLoop, nudgeFrames, onNudgeFramesChange,
    onClose, onNudge, onTrim, onSplit, onStepScene, onUndo,
  }, ref) {
  const videoRef = useRef<HTMLVideoElement>(null);
  // 팝업 플레이어 컨트롤 — 현재 재생 위치(ms)·재생 여부·영상 길이(ms). 프레임
  // 카운터(1부터)·스크러버·재생/정지 버튼이 쓴다. 프로그램적 시킹은 <video onSeeked>,
  // 재생 중 갱신은 onTimeUpdate(~4Hz)가 previewMs를 맞춘다.
  const [previewMs, setPreviewMs] = useState(preview.seekMs);
  const [previewPlaying, setPreviewPlaying] = useState(false);
  const [previewDur, setPreviewDur] = useState(0);
  // 재생 감시가 최신 preview/loop을 읽도록 ref로 미러링(재생 중 갱신 반영).
  const previewRef = useRef(preview);
  const loopRef = useRef(loopSeg);
  const rafRef = useRef<number | null>(null);
  const rvfcRef = useRef<number | null>(null);
  useEffect(() => { previewRef.current = preview; }, [preview]);
  useEffect(() => { loopRef.current = loopSeg; }, [loopSeg]);
  // 경계 편집·씬 이동으로 preview가 바뀌면 프레임 카운터/스크러버를 그 시각으로
  // 초기화한다(직후 onSeeked가 실제 currentTime으로 정밀 보정).
  useEffect(() => { setPreviewMs(preview.seekMs); }, [preview]);
  const stopGuards = () => {
    const v = videoRef.current;
    if (rafRef.current != null) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
    if (rvfcRef.current != null && v && typeof v.cancelVideoFrameCallback === "function") {
      v.cancelVideoFrameCallback(rvfcRef.current); rvfcRef.current = null;
    }
  };
  // 팝업이 닫히면(언마운트) 감시 루프를 확실히 멈춘다.
  useEffect(() => stopGuards, []);

  useImperativeHandle(ref, () => ({
    pauseAndSeek: (ms: number) => {
      const v = videoRef.current;
      if (v) { v.pause(); v.currentTime = ms / 1000; }
    },
  }), []);

  // 재생 시작 시 '꼬리 프레임'에 도달하면 멈춘다(반복이면 첫 프레임으로 되감음).
  // 프레임 정확한 requestVideoFrameCallback.mediaTime으로 현재 프레임 인덱스를 재
  // 프레임 단위로 비교한다 — 정확히 잡으면 시킹 없이 그 자리에서 정지해 깜빡임이
  // 없다. video.currentTime은 브라우저가 드물게(~4Hz) 갱신해 폴링해도 뒤처지므로
  // (실제 화면은 이미 다음 씬) rVFC가 없을 때만 폴백으로 쓴다.
  const startSegmentGuard = () => {
    const v = videoRef.current;
    if (!v) return;
    stopGuards();
    const lastIdxOf = (p: { lastFrameMs?: number; fps?: number }) =>
      Math.round((p.lastFrameMs ?? 0) / (1000 / (p.fps || NTSC_FPS)) - 0.5);
    if (typeof v.requestVideoFrameCallback === "function") {
      const step = (_now: number, meta: { mediaTime: number }) => {
        const vv = videoRef.current;
        const p = previewRef.current;
        if (!vv || vv.paused || !p || p.lastFrameMs == null || !p.fps) { rvfcRef.current = null; return; }
        const frameMs = 1000 / p.fps;
        const lastIdx = lastIdxOf(p);
        const curIdx = Math.round(meta.mediaTime * 1000 / frameMs);
        // 한 프레임 일찍(마지막-1) 멈춘다 — rVFC가 프레임을 ~1프레임 늦게 잡아서,
        // 마지막 프레임에서 멈추게 하면 실제로는 다음 씬을 한 프레임 보여준 뒤
        // 되돌아가 깜빡였다(실기). 마지막-1에서 잡으면 재생 중엔 다음 씬에 절대
        // 닿지 않고, 정지 후 꼬리 프레임으로 스냅해 최종만 정확히 맞춘다.
        if (curIdx >= lastIdx - 1) {
          if (loopRef.current && p.playStartMs != null) {
            vv.currentTime = p.playStartMs / 1000;
            rvfcRef.current = vv.requestVideoFrameCallback(step);
          } else {
            vv.pause();
            vv.currentTime = p.lastFrameMs / 1000;  // 꼬리 프레임에 정확히 스냅
            rvfcRef.current = null;
          }
        } else {
          rvfcRef.current = vv.requestVideoFrameCallback(step);
        }
      };
      rvfcRef.current = v.requestVideoFrameCallback(step);
      return;
    }
    // 폴백(rVFC 미지원): rAF로 currentTime 폴링(값이 뒤처져 약간의 오버슈트 감수).
    const tick = () => {
      const vv = videoRef.current;
      const p = previewRef.current;
      if (!vv || vv.paused || !p || p.lastFrameMs == null) { rafRef.current = null; return; }
      const halfFrameSec = p.fps && p.fps > 0 ? 0.5 / p.fps : 0.02;
      if (vv.currentTime >= p.lastFrameMs / 1000 - halfFrameSec) {
        if (loopRef.current && p.playStartMs != null) {
          vv.currentTime = p.playStartMs / 1000;
        } else {
          vv.pause();
          vv.currentTime = p.lastFrameMs / 1000;
          rafRef.current = null;
          return;
        }
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  };

  // 팝업 영상을 특정 프레임 시각으로 이동(머리/꼬리 확인용).
  const seekPreview = (ms?: number) => {
    const v = videoRef.current;
    if (v && ms != null) { v.pause(); v.currentTime = ms / 1000; }
  };
  // 한 프레임씩 이동 — 정지 후 그 프레임 중앙으로 시킹한다(오류 프레임을 눈으로
  // 한 칸씩 찾는다). 소스 fps로 프레임 인덱스를 계산해 프레임 정확. 0 미만은 클램프.
  const stepPreviewFrame = (delta: number) => {
    const v = videoRef.current;
    if (!v) return;
    const p = previewRef.current;
    const fps = p?.fps || NTSC_FPS;
    const frameMs = 1000 / fps;
    const cur = Math.floor((v.currentTime * 1000) / frameMs + 1e-6);
    let target = Math.max(0, cur + delta);
    // 구간 프리뷰면 이 씬의 첫/마지막 프레임을 벗어나지 못하게 클램프 — 스텝이든
    // 스크러버든 해당 씬 밖(이전/다음 씬)으로 넘어가면 안 된다(익스포트 컷과 동일
    // 프레임 수식: f0=ceil(start/frameMs), N=round((end-start)·fps/1000)).
    if (p?.startMs != null && p.endMs != null) {
      const f0 = Math.ceil(p.startMs / frameMs - 1e-6);
      const n = Math.max(1, Math.round((p.endMs - p.startMs) / frameMs));
      target = Math.min(f0 + n - 1, Math.max(f0, target));
    }
    v.pause();
    v.currentTime = ((target + 0.5) * frameMs) / 1000;  // 그 프레임 표시구간 중앙
  };
  const togglePreviewPlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (!v.paused) { v.pause(); return; }
    // 재생 시작: 구간 재생이 꼬리 프레임에서 멈춘 상태(=한 번 완료)면 다시 누를 때
    // 머리부터 재생한다 — 안 그러면 꼬리에서 play()하자마자 구간 감시가 "이미 꼬리
    // 도달"로 즉시 멈춰 재생이 안 되는 것처럼 보인다. 중간 정지면 그 자리서 이어재생.
    const p = previewRef.current;
    if (p?.playStartMs != null && p.lastFrameMs != null) {
      const halfFrame = p.fps ? 0.5 / p.fps : 0.02;
      if (v.currentTime >= p.lastFrameMs / 1000 - halfFrame) {
        v.currentTime = p.playStartMs / 1000;
      }
    }
    void v.play();
  };

  // 팝업이 열려 있을 때의 검수 단축키(매핑·한글 IME 처리는 scenePopupAction).
  // I/O=In/Out 트림(편집 프로그램 관례), G/H=이전/다음 씬, [/]=머리로/꼬리로 —
  // 화면의 해당 버튼에 같은 키를 적어 뒀다. 입력칸에 포커스가 있으면 무시한다
  // — '프레임씩' 수나 라벨을 타이핑하다 경계가 바뀌면 안 된다. 편집 콜백은 부모가
  // 매 렌더 새로 만들어 내려보내므로 deps에 넣어 다시 등록해야 핸들러가 최신
  // 세그먼트·토큰 규칙·저장된 구역을 본다(빠뜨리면 단축키 경로만 옛 값으로 동작).
  useEffect(() => {
    if (preview.segIndex == null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
                || t.isContentEditable)) return;
      const action = scenePopupAction(e);
      if (action == null) return;
      e.preventDefault();
      // 트림·나누기의 기준 시각은 영상의 현재 시각 — 버튼 경로(previewMs)와 같은
      // 프레임을 가리킨다(둘 다 onSeeked/onTimeUpdate로 맞춰진 값).
      const v = videoRef.current;
      const at = v ? v.currentTime * 1000 : preview.seekMs;
      if (action === "trimIn" || action === "trimOut") {
        onTrim(action === "trimIn" ? "in" : "out", at);
      } else if (action === "split") {
        onSplit(at);
      } else if (action === "prevScene" || action === "nextScene") {
        onStepScene(action === "prevScene" ? -1 : 1);
      } else {
        seekPreview(action === "toHead"
          ? preview.playStartMs : preview.lastFrameMs);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [preview, onTrim, onSplit, onStepScene]);

  // ←/→=프레임 한 칸(화면의 ◀이전/다음▶ 버튼과 같은 동작). 팝업이 닫혀 있을 때의
  // ←/→(씬 이동)는 부모가 처리한다 — 이 핸들러는 ref만 읽어 재등록이 필요 없다.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
                || t.isContentEditable)) return;
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();  // 스크롤 컨테이너의 가로 스크롤 기본동작 차단
      stepPreviewFrame(e.key === "ArrowRight" ? 1 : -1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div onClick={onClose}
      style={{ position: "fixed", inset: 0, zIndex: 1000, display: "flex",
               alignItems: "center", justifyContent: "center", padding: 24 }}>
      {/* 어두운 배경 틴트는 영상의 '조상'이 아니라 '형제'로 둔다 — 조상 div에
          반투명 배경을 주면 WebKit이 하드웨어 합성한 <video>를 그 틴트 아래로
          합성해 영상이 어둡게 보인다(실기: 네이티브 플레이어보다 어두움). */}
      <div style={{ position: "absolute", inset: 0,
                    background: "rgba(0,0,0,0.8)" }} />
      {/* 양 사이드 씬 이동 — 팝업을 닫고 목록에서 다음 씬을 찾아 프레임을 다시
          클릭하는 왕복을 없앤다. 보던 쪽(머리/꼬리)을 유지해 이어서 확인한다.
          영상 위가 아니라 좌우 여백에 두어 검수할 프레임을 가리지 않는다. */}
      {preview.segIndex != null ? (() => {
        const nav = (delta: -1 | 1, idx: number | null) => {
          const label = delta < 0 ? "이전 씬" : "다음 씬";
          const hotkey = delta < 0 ? "G" : "H";
          const target = idx != null ? segments[idx]?.label : null;
          return (
            <button type="button" disabled={idx == null}
              title={target
                ? `${label} · ${target} — 보던 쪽 프레임으로 (단축키 ${hotkey})`
                : `${label}이 없습니다`}
              onClick={(e) => { e.stopPropagation(); onStepScene(delta); }}
              style={{ ...sideNavBtn, ...(delta < 0 ? { left: 6 } : { right: 6 }),
                       opacity: idx == null ? 0.3 : 1,
                       cursor: idx == null ? "default" : "pointer" }}>
              <span style={{ fontSize: 20, lineHeight: 1 }}>
                {delta < 0 ? "◀" : "▶"}
              </span>
              <span>{label}</span>
              {/* 키는 한 줄 아래 작게 — 버튼 폭(56)에 라벨과 나란히 두면 넘친다. */}
              <span style={{ fontSize: 10, opacity: 0.55 }}>{hotkey}</span>
            </button>
          );
        };
        return (
          <>
            {nav(-1, stepVisibleIndex(visibleAll, preview.segIndex, -1))}
            {nav(1, stepVisibleIndex(visibleAll, preview.segIndex, 1))}
          </>
        );
      })() : null}
      <div onClick={(e) => e.stopPropagation()}
        style={{ position: "relative", zIndex: 1,
                 maxWidth: "90vw", maxHeight: "90vh" }}>
        {/* 네이티브 controls는 마우스 오버 시 영상 위에 어두운 스크림을 덧씌워
            검수용 밝기 비교를 방해한다 — 끄고 영상 클릭으로 재생/일시정지한다. */}
        <video ref={videoRef}
          src={src} autoPlay={false}
          onLoadedMetadata={(e) => {
            setPreviewDur(e.currentTarget.duration * 1000);
            e.currentTarget.currentTime = preview.seekMs / 1000;
          }}
          onPlay={() => { setPreviewPlaying(true); startSegmentGuard(); }}
          onPause={() => setPreviewPlaying(false)}
          onTimeUpdate={(e) => setPreviewMs(e.currentTarget.currentTime * 1000)}
          onSeeked={(e) => setPreviewMs(e.currentTarget.currentTime * 1000)}
          onClick={togglePreviewPlay}
          style={{ maxWidth: "90vw", maxHeight: "78vh", borderRadius: 8,
                   cursor: "pointer" }} />
        <div style={{ display: "flex", flexDirection: "column", gap: 6,
                      marginTop: 6, color: "#fff" }}>
          {/* 재생 컨트롤러 — 네이티브 controls는 영상 위에 어두운 스크림을 씌워
              검수용 밝기 비교를 방해하므로 끄고 직접 만든다. 프레임 스텝(◀/▶)으로
              오류 프레임을 한 칸씩 찾고, 프레임 카운터(1부터)로 몇 번째 프레임인지
              읽어 경계 교정에 그대로 입력한다. 스크러버로 임의 위치로 이동. */}
          <div style={{ display: "flex", alignItems: "center", gap: 8,
                        flexWrap: "wrap" }}>
            <button type="button" style={consoleStyles.mutedAction}
              title="한 프레임 뒤로 (키보드 ←)"
              onClick={() => stepPreviewFrame(-1)}>◀ 이전</button>
            <button type="button" style={consoleStyles.action}
              onClick={togglePreviewPlay}>{previewPlaying ? "⏸ 정지" : "▶ 재생"}</button>
            <button type="button" style={consoleStyles.mutedAction}
              title="한 프레임 앞으로 (키보드 →)"
              onClick={() => stepPreviewFrame(1)}>다음 ▶</button>
            {(() => {
              const seg = preview.startMs != null && preview.endMs != null;
              // 구간이면 이 씬의 '첫 프레임 중앙~마지막 프레임 중앙'으로 범위를
              // 잡는다 — 원본 start_ms/end_ms는 경계 시각이라 그리로 시킹하면
              // <video>가 이웃 씬 프레임(이전 씬 마지막/다음 씬 첫)을 보여준다.
              // playStartMs/lastFrameMs(=frameSeekMs)는 이 씬 안 프레임에 떨어져,
              // 왼쪽 끝까지 끌어도 씬을 벗어나지 않는다("머리로/꼬리로"와 동일 값).
              const min = seg ? (preview.playStartMs ?? preview.startMs!) : 0;
              const max = seg ? (preview.lastFrameMs ?? preview.endMs!)
                              : (previewDur || previewMs + 1);
              return (
                <input type="range" min={min} max={max} step={1}
                  value={Math.min(max, Math.max(min, previewMs))}
                  onChange={(e) => seekPreview(Number(e.target.value))}
                  style={{ flex: 1, minWidth: 140, accentColor: "#6db6ff" }} />
              );
            })()}
            <span style={{ fontSize: 12, opacity: 0.9, minWidth: 92,
                           textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
              {preview.startMs != null && preview.endMs != null
                ? (() => {
                    const { k, n } = segFrameNumber(previewMs, preview.startMs,
                      preview.endMs, preview.fps);
                    return `프레임 ${k} / ${n}`;
                  })()
                : `프레임 ${frameNumberAt(previewMs, preview.fps)}`}
            </span>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between",
                        alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <span style={{ fontSize: 13, opacity: 0.85 }}>
              {preview.label ? `${preview.label} · ` : ""}
              {preview.startMs != null && preview.endMs != null
                ? `${formatMs(preview.startMs)}–${formatMs(preview.endMs)}`
                : `이 지점: ${formatMs(preview.seekMs)}`}
              {preview.startMs != null && preview.endMs != null ? (
                <span style={{ opacity: 0.7, marginLeft: 6 }}>
                  · {Math.max(1, Math.round((preview.endMs - preview.startMs)
                      / (1000 / (preview.fps || NTSC_FPS))))}프레임
                </span>
              ) : null}
              {/* 좌우 버튼으로 씬을 넘길 때 지금 몇 번째인지 — 목록 카운터와 같은
                  '보이는 목록' 기준이라 검색으로 좁혀 놓으면 그 안에서 센다. */}
              {preview.segIndex != null ? (() => {
                const pos = visibleAll.indexOf(preview.segIndex) + 1;
                return (
                  <span style={{ opacity: 0.7, marginLeft: 6,
                                 fontVariantNumeric: "tabular-nums" }}>
                    · {pos > 0 ? pos : "–"} / {visibleAll.length}
                  </span>
                );
              })() : null}
              <span style={{ opacity: 0.55, marginLeft: 8 }}>· 영상 클릭: 재생/일시정지</span>
            </span>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              {preview.playStartMs != null ? (
                <button type="button" style={consoleStyles.action}
                  onClick={() => {
                    const v = videoRef.current;
                    if (v && preview.playStartMs != null) {
                      v.currentTime = preview.playStartMs / 1000;
                      void v.play();
                    }
                  }}>▶ 구간 재생</button>
              ) : null}
              {preview.playStartMs != null ? (
                <button type="button" style={consoleStyles.mutedAction}
                  title="이 씬의 첫 프레임으로 (단축키 [)"
                  onClick={() => seekPreview(preview.playStartMs)}>
                  머리로 [</button>
              ) : null}
              {preview.lastFrameMs != null ? (
                <button type="button" style={consoleStyles.mutedAction}
                  title="이 씬의 마지막 프레임으로 (단축키 ])"
                  onClick={() => seekPreview(preview.lastFrameMs)}>
                  꼬리로 ]</button>
              ) : null}
              {preview.startMs != null ? (
                <button type="button" style={consoleStyles.mutedAction}
                  onClick={onToggleLoop}>
                  {loopSeg ? "🔁 반복 켜짐" : "반복 꺼짐"}
                </button>
              ) : null}
              <button type="button" style={consoleStyles.mutedAction}
                onClick={onClose}>닫기</button>
            </div>
          </div>
          {/* In/Out 트림 — 편집 프로그램의 인점·아웃점처럼, 찾은 프레임을 이 씬의
              첫/마지막 프레임으로 확정하면 그 밖의 프레임이 이웃 씬으로 넘어간다.
              버튼 라벨의 프레임 수는 재생 위치에 따라 실시간으로 바뀌므로 카운터를
              읽어 옮겨 적을 필요가 없다(아래 '경계 교정'의 수동 입력도 그대로 유지). */}
          {preview.segIndex != null && preview.startMs != null
            && preview.endMs != null ? (() => {
            const i = preview.segIndex;
            // 라벨의 프레임 수와 실제 동작이 반드시 같아야 하므로 둘 다 previewMs
            // (카운터가 쓰는 값)로 계산한다 — 영상 currentTime을 따로 읽으면
            // 표시와 한 프레임 어긋날 수 있다.
            const { k, n } = segFrameNumber(previewMs, preview.startMs,
              preview.endMs, preview.fps);
            const { inFrames, outFrames } = trimFrames(k, n);
            return (
              <div style={{ display: "flex", gap: 6, alignItems: "center",
                            flexWrap: "wrap", fontSize: 12 }}>
                <span style={{ opacity: 0.7 }}>현재 프레임 기준</span>
                <button type="button" style={editBtn}
                  disabled={i === 0 || inFrames === 0}
                  title="지금 보는 프레임을 이 씬의 첫 프레임으로 — 앞 프레임은 이전 씬으로 넘어갑니다 (단축키 I)"
                  onClick={() => onTrim("in", previewMs)}>
                  ◀ 여기부터(I) · 앞 {inFrames}f → 이전 씬</button>
                <button type="button" style={editBtn}
                  disabled={i >= segments.length - 1 || outFrames === 0}
                  title="지금 보는 프레임을 이 씬의 마지막 프레임으로 — 뒤 프레임은 다음 씬으로 넘어갑니다 (단축키 O)"
                  onClick={() => onTrim("out", previewMs)}>
                  여기까지(O) · 뒤 {outFrames}f → 다음 씬 ▶</button>
                {/* 한 줄에 두 씬이 붙어 있을 때 여기서 가른다 — 지금 보는 프레임이
                    뒤 씬의 첫 프레임이 된다(In 트림과 같은 약속). 첫 프레임에서는
                    앞 구간이 0프레임이라 잠근다. */}
                <button type="button" style={editBtn}
                  disabled={k <= 1}
                  title="지금 보는 프레임부터 새 씬으로 나눕니다 — 앞쪽이 새 씬이 되고 이름은 슬레이트를 읽어 채웁니다 (단축키 S)"
                  onClick={() => onSplit(previewMs)}>
                  ✂ 여기서 나누기(S) · 앞 {k - 1}f | 뒤 {n - k + 1}f</button>
                {canUndo ? (
                  <button type="button"
                    style={{ ...editBtn, color: "#6db6ff",
                             borderColor: "rgba(109,182,255,0.5)" }}
                    title="방금 경계 교정을 되돌립니다"
                    onClick={onUndo}>↩되돌리기</button>
                ) : null}
              </div>
            );
          })() : null}
          {/* 경계 프레임 편집 — 머리/꼬리에 붙은 프레임을 이웃 씬으로 넘기거나
              이웃에서 가져온다(스캔이 못 잡는 디졸브/와이프 수동 교정). 누를 때마다
              영상이 그 경계 프레임으로 이동하니 눈으로 확인하며 맞춘다. 편집 후
              "닫기"→"수정사항 저장" 해야 익스포트에 반영된다. */}
          {preview.segIndex != null ? (
            <div style={{ display: "flex", gap: 6, alignItems: "center",
                          flexWrap: "wrap", fontSize: 12 }}>
              <span style={{ opacity: 0.7 }}>경계 교정</span>
              <label style={{ display: "inline-flex", alignItems: "center", gap: 4,
                              opacity: 0.85 }}>
                <input type="number" min={1} max={999} value={nudgeFrames}
                  onChange={(e) => onNudgeFramesChange(
                    Math.max(1, Math.floor(Number(e.target.value) || 1)))}
                  style={{ width: 46, fontSize: 12, padding: "3px 5px", borderRadius: 4,
                           textAlign: "right", background: "rgba(255,255,255,0.10)",
                           color: "#fff", border: "1px solid rgba(255,255,255,0.25)" }} />
                프레임씩:
              </label>
              <button type="button" style={editBtn} disabled={preview.segIndex === 0}
                onClick={() => onNudge("head", nudgeFrames)}>
                머리 {nudgeFrames}f → 이전 씬</button>
              <button type="button" style={editBtn} disabled={preview.segIndex === 0}
                onClick={() => onNudge("head", -nudgeFrames)}>
                이전 씬 → 머리 {nudgeFrames}f</button>
              <span style={{ opacity: 0.3, padding: "0 2px" }}>|</span>
              <button type="button" style={editBtn}
                disabled={preview.segIndex >= segments.length - 1}
                onClick={() => onNudge("tail", -nudgeFrames)}>
                꼬리 {nudgeFrames}f → 다음 씬</button>
              <button type="button" style={editBtn}
                disabled={preview.segIndex >= segments.length - 1}
                onClick={() => onNudge("tail", nudgeFrames)}>
                다음 씬 → 꼬리 {nudgeFrames}f</button>
              {dirty ? (
                <span style={{ color: "#e2b340", marginLeft: 4 }}>· 저장 필요</span>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
});
