// === ANCHOR: CAPTURE_VIEW_START ===
import { useEffect, useRef, useState, type CSSProperties } from "react";
import QRCode from "qrcode";
import { checkCaptureSupport, isChromiumLike } from "./captureSupport";
import { useCaptureSession } from "./useCaptureSession";
import { useOperatorSubtitles } from "./useOperatorSubtitles";
import { usePacedSubtitle } from "../hooks/usePacedSubtitle";
import type { UtteranceTranscribed } from "../types/events";

// 데스크탑 콘솔과 동일한 단축키: F(ㄹ)=자막 전체화면, Q(ㅂ)=QR 전체화면.
// event.code 기준이라 한글 자판에서도 같은 물리 키로 동작. Esc로 닫기.
type FullscreenMode = "subtitle" | "qr" | null;

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tagName = target.tagName.toLowerCase();
  return tagName === "input" || tagName === "textarea" || tagName === "select" || target.isContentEditable;
}

// ── 데스크탑 콘솔 F 전체화면과 동일한 렌더링 (LiveSubtitlePreview의 문장 단위 모드) ──
// 확정(final) 자막만 페이싱해 완성 문장이 통째로 뜨고, 직전 문장을 위에 흐리게 표시.
// 글자 크기는 80px 목표 고정, 화면(90vw/90vh)을 넘칠 때만 아래로 축소(잘림 방지).
const FULLSCREEN_SUBTITLE_TARGET_PX = 80;
const FULLSCREEN_SUBTITLE_MIN_PX = 24;

function useFullscreenSubtitleFit(text: string): { ref: (node: HTMLDivElement | null) => void; style: CSSProperties | null } {
  const elementRef = useRef<HTMLDivElement | null>(null);
  const [fontSize, setFontSize] = useState<number | null>(null);
  const ref = (node: HTMLDivElement | null) => {
    elementRef.current = node;
  };

  useEffect(() => {
    if (!text) {
      setFontSize(null);
      return;
    }
    const element = elementRef.current;
    if (!element) return;

    let frame = 0;
    const fitText = () => {
      const widthLimit = window.innerWidth * 0.9;
      const heightLimit = window.innerHeight * 0.9;
      let low = FULLSCREEN_SUBTITLE_MIN_PX;
      let high = FULLSCREEN_SUBTITLE_TARGET_PX;
      let best = low;
      const previousFontSize = element.style.fontSize;
      for (let index = 0; index < 9; index += 1) {
        const next = Math.floor((low + high) / 2);
        element.style.fontSize = `${next}px`;
        const fits = element.scrollWidth <= widthLimit + 1 && element.scrollHeight <= heightLimit + 1;
        if (fits) {
          best = next;
          low = next + 1;
        } else {
          high = next - 1;
        }
      }
      element.style.fontSize = previousFontSize;
      setFontSize(best);
    };
    const scheduleFit = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(fitText);
    };
    scheduleFit();
    window.addEventListener("resize", scheduleFit);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", scheduleFit);
    };
  }, [text]);

  if (fontSize === null) return { ref, style: null };
  return { ref, style: { fontSize, lineHeight: fontSize >= 72 ? 1.14 : 1.18 } };
}

function previousSubtitle(finals: UtteranceTranscribed[], latestSeq: number | null): UtteranceTranscribed | null {
  if (finals.length < 2 || latestSeq === null) return null;
  for (let index = finals.length - 2; index >= 0; index -= 1) {
    const item = finals[index];
    if (item && item.seq !== latestSeq) return item;
  }
  return null;
}

const fullscreenPageStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 50,
  boxSizing: "border-box",
  display: "flex",
  flexDirection: "column",
  justifyContent: "center",
  alignItems: "center",
  padding: "5vh 5vw",
  background:
    "radial-gradient(circle at 20% 0%, rgba(56,189,248,.2), transparent 30%), linear-gradient(135deg, #020617, #0f172a 56%, #172554)",
};

const fullscreenContextStyle: CSSProperties = {
  width: "86vw",
  maxWidth: "86vw",
  margin: "0 auto",
  color: "#93c5fd",
  opacity: 0.72,
  fontSize: 60,
  fontWeight: 760,
  lineHeight: 1.28,
  textAlign: "center",
  wordBreak: "keep-all",
  overflowWrap: "break-word",
};

const fullscreenTextStyle: CSSProperties = {
  boxSizing: "border-box",
  width: "90vw",
  maxWidth: "90vw",
  maxHeight: "90vh",
  margin: "0 auto",
  padding: 0,
  overflow: "hidden",
  color: "#f8fafc",
  fontSize: "clamp(34px, 4.8vw, 76px)",
  fontWeight: 820,
  lineHeight: 1.24,
  letterSpacing: ".005em",
  textAlign: "center",
  textWrap: "balance",
  wordBreak: "keep-all",
  overflowWrap: "break-word",
};

function SubtitleFullscreenOverlay({ finals, onClose }: { finals: UtteranceTranscribed[]; onClose: () => void }) {
  const latestFinal = finals.length > 0 ? finals[finals.length - 1]! : null;
  const paced = usePacedSubtitle(latestFinal);
  const previous = previousSubtitle(finals, paced?.seq ?? null);
  const text = paced?.text_ko || paced?.text_en || "";
  const previousText = previous?.text_ko || previous?.text_en || "";
  const fit = useFullscreenSubtitleFit(text);
  return (
    <div style={fullscreenPageStyle} onClick={onClose}>
      {paced ? (
        <div style={{ display: "grid", gap: 10 }}>
          {previousText ? <div style={fullscreenContextStyle}>{previousText}</div> : null}
          <div ref={fit.ref} style={{ ...fullscreenTextStyle, ...(fit.style ?? {}) }}>
            {text}
          </div>
        </div>
      ) : (
        <p className="text-2xl text-slate-600">자막을 기다리는 중…</p>
      )}
      <p className="absolute bottom-6 text-sm text-slate-600">F 또는 Esc — 닫기</p>
    </div>
  );
}

function QrFullscreenOverlay({ viewerUrl, onClose }: { viewerUrl: string; onClose: () => void }) {
  const [qrSvg, setQrSvg] = useState("");
  useEffect(() => {
    let active = true;
    QRCode.toString(viewerUrl, {
      type: "svg",
      errorCorrectionLevel: "M",
      margin: 2,
      width: 480,
      color: { dark: "#020617", light: "#ffffff" },
    }).then((svg) => {
      if (active) setQrSvg(svg);
    });
    return () => {
      active = false;
    };
  }, [viewerUrl]);
  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-6 bg-slate-950 px-8" onClick={onClose}>
      <div className="rounded-2xl bg-white p-4" dangerouslySetInnerHTML={{ __html: qrSvg }} />
      <p className="max-w-3xl break-all text-center text-lg text-slate-300">{viewerUrl}</p>
      <p className="absolute bottom-6 text-sm text-slate-600">Q 또는 Esc — 닫기</p>
    </div>
  );
}

function SupportBanners() {
  const support = checkCaptureSupport();
  const chromium = isChromiumLike();
  if (support.ok && chromium) return null;
  return (
    <div className="space-y-2">
      {!support.ok && support.reason === "insecure-context" && (
        <div className="rounded-lg bg-amber-900/60 border border-amber-500 px-4 py-3 text-sm">
          이 주소에서는 탭 캡처를 쓸 수 없습니다. <b>https 주소(공유용 터널 링크)</b> 또는 localhost로 접속하세요.
        </div>
      )}
      {!support.ok && support.reason === "no-display-media" && (
        <div className="rounded-lg bg-amber-900/60 border border-amber-500 px-4 py-3 text-sm">
          이 브라우저는 탭 캡처를 지원하지 않습니다. <b>Chrome 또는 Edge</b>로 접속하세요.
        </div>
      )}
      {support.ok && !chromium && (
        <div className="rounded-lg bg-amber-900/60 border border-amber-500 px-4 py-3 text-sm">
          탭 오디오 캡처는 Chrome/Edge 계열에서만 안정적으로 동작합니다. 문제가 생기면 Chrome으로 접속하세요.
        </div>
      )}
    </div>
  );
}

function ViewerQr({ viewerUrl }: { viewerUrl: string }) {
  const [qrSvg, setQrSvg] = useState("");
  useEffect(() => {
    let active = true;
    QRCode.toString(viewerUrl, {
      type: "svg",
      errorCorrectionLevel: "M",
      margin: 2,
      width: 140,
      color: { dark: "#020617", light: "#f8fafc" },
    }).then((svg) => {
      if (active) setQrSvg(svg);
    });
    return () => {
      active = false;
    };
  }, [viewerUrl]);
  return (
    <div className="flex items-center gap-4">
      <div className="rounded-lg bg-slate-50 p-2" dangerouslySetInnerHTML={{ __html: qrSvg }} />
      <div className="text-sm space-y-2">
        <p className="text-slate-300">참석자 자막 링크</p>
        <p className="break-all text-slate-100">{viewerUrl}</p>
        <button
          className="rounded bg-slate-700 px-3 py-1 hover:bg-slate-600"
          onClick={() => void navigator.clipboard.writeText(viewerUrl)}
        >
          링크 복사
        </button>
      </div>
    </div>
  );
}

const QUIET_DBFS = -50;
const QUIET_WARN_AFTER_MS = 8000;

function LevelMeter({ dbfs }: { dbfs: number }) {
  const pct = Math.round(Math.min(100, Math.max(0, ((dbfs + 60) / 60) * 100)));
  // 경고는 8초 이상 "지속" 무음일 때만 표시 — 말 사이 정적으로는 절대 깜빡이지 않음.
  // 소리가 들어오면 즉시 숨김. 막대 색은 고정(색 깜빡임 제거).
  const [showWarning, setShowWarning] = useState(false);
  const warnTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (dbfs >= QUIET_DBFS) {
      if (warnTimerRef.current) {
        clearTimeout(warnTimerRef.current);
        warnTimerRef.current = null;
      }
      setShowWarning(false);
    } else if (!warnTimerRef.current) {
      warnTimerRef.current = setTimeout(() => setShowWarning(true), QUIET_WARN_AFTER_MS);
    }
  }, [dbfs]);
  useEffect(
    () => () => {
      if (warnTimerRef.current) clearTimeout(warnTimerRef.current);
    },
    [],
  );
  return (
    <div>
      <div className="h-2 w-full rounded bg-slate-700">
        <div className="h-2 rounded bg-emerald-500" style={{ width: `${pct}%` }} />
      </div>
      {/* 항상 한 줄 공간 확보(표시/숨김만 전환) — 레이아웃 불변 */}
      <p className={`mt-1 h-4 text-xs text-amber-400 ${showWarning ? "" : "invisible"}`}>
        오디오가 들어오지 않습니다 — 탭 선택 시 '탭 오디오 공유' 체크를 확인하세요.
      </p>
    </div>
  );
}

export function CaptureView() {
  const s = useCaptureSession();
  const subtitles = useOperatorSubtitles(s.phase === "capturing" ? s.sessionId : null, s.operatorToken);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullscreen, setFullscreen] = useState<FullscreenMode>(null);
  const finals = subtitles.utterances.filter((u) => u.is_final);
  const recent = finals.slice(-2);

  const canSubtitleFullscreen = s.phase === "capturing";
  const canQrFullscreen = !!s.viewerUrl;

  // 데스크탑 앱의 전체화면 창처럼 모니터 전체를 덮도록 브라우저 Fullscreen API 사용.
  // 키다운(사용자 제스처) 안에서 직접 호출해야 허용된다. 실패 시(권한 등) 창 내 오버레이로 폴백.
  function openFullscreen(mode: Exclude<FullscreenMode, null>) {
    setFullscreen(mode);
    if (!document.fullscreenElement) void document.documentElement.requestFullscreen().catch(() => {});
  }
  function closeFullscreen() {
    setFullscreen(null);
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
  }

  useEffect(() => {
    if (!canSubtitleFullscreen && !canQrFullscreen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        closeFullscreen();
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.repeat || isEditableTarget(event.target)) return;
      if (event.code === "KeyF" && canSubtitleFullscreen) {
        event.preventDefault();
        if (fullscreen === "subtitle") closeFullscreen();
        else openFullscreen("subtitle");
      } else if (event.code === "KeyQ" && canQrFullscreen) {
        event.preventDefault();
        if (fullscreen === "qr") closeFullscreen();
        else openFullscreen("qr");
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  // 브라우저가 자체적으로 전체화면을 빠져나간 경우(Esc는 브라우저가 먼저 처리) 오버레이도 닫는다.
  useEffect(() => {
    function onFsChange() {
      if (!document.fullscreenElement) setFullscreen(null);
    }
    document.addEventListener("fullscreenchange", onFsChange);
    return () => document.removeEventListener("fullscreenchange", onFsChange);
  }, []);

  return (
    <main className="min-h-screen bg-slate-900 text-slate-100 flex justify-center p-6">
      <div className={`w-full space-y-6 ${s.phase === "capturing" ? "max-w-5xl" : "max-w-2xl"}`}>
        <header>
          <h1 className="text-2xl font-bold">YESON-MEET WEB-실시간 자막</h1>
          <p className="text-sm text-slate-400">앱 설치 없이 웹에서 라이브 미팅 번역</p>
        </header>
        <SupportBanners />
        {s.error && (
          <div className="rounded-lg bg-rose-900/60 border border-rose-500 px-4 py-3 text-sm flex justify-between gap-4">
            <span>{s.error}</span>
            <button className="shrink-0 underline" onClick={s.resetError}>닫기</button>
          </div>
        )}

        {s.phase === "login" && (
          <form
            className="space-y-3 rounded-xl bg-slate-800 p-5"
            onSubmit={(e) => {
              e.preventDefault();
              void s.login(email, password);
            }}
          >
            <h2 className="font-semibold">운영자 로그인</h2>
            <input className="w-full rounded bg-slate-700 px-3 py-2" type="email" placeholder="이메일" value={email} onChange={(e) => setEmail(e.target.value)} required />
            <input className="w-full rounded bg-slate-700 px-3 py-2" type="password" placeholder="비밀번호" value={password} onChange={(e) => setPassword(e.target.value)} required />
            <button className="w-full rounded bg-emerald-600 py-2 font-semibold hover:bg-emerald-500 disabled:opacity-50" disabled={s.busy}>
              {s.busy ? "로그인 중…" : "로그인"}
            </button>
          </form>
        )}

        {s.phase === "ready" && (
          <div className="space-y-3 rounded-xl bg-slate-800 p-5">
            <h2 className="font-semibold">회의 시작</h2>
            <input className="w-full rounded bg-slate-700 px-3 py-2" placeholder="회의 제목" value={s.title} onChange={(e) => s.setTitle(e.target.value)} />
            {!s.sessionId ? (
              <button className="w-full rounded bg-emerald-600 py-2 font-semibold hover:bg-emerald-500 disabled:opacity-50" disabled={s.busy || !s.title.trim()} onClick={() => void s.startMeeting()}>
                {s.busy ? "생성 중…" : "회의 만들기"}
              </button>
            ) : (
              <>
                {s.viewerUrl && <ViewerQr viewerUrl={s.viewerUrl} />}
                <div className="rounded-lg bg-slate-900/60 px-4 py-3 text-sm text-slate-300">
                  다음 화면에서 <b>구글밋이 열린 탭</b>을 선택하고 왼쪽 아래 <b>'탭 오디오 공유'를 반드시 체크</b>하세요.
                </div>
                <button className="w-full rounded bg-emerald-600 py-2 font-semibold hover:bg-emerald-500 disabled:opacity-50" disabled={s.busy} onClick={() => void s.startCapture()}>
                  {s.busy ? "준비 중…" : "탭 선택하고 캡처 시작"}
                </button>
              </>
            )}
          </div>
        )}

        {s.phase === "capturing" && (
          <div className="space-y-4 rounded-xl bg-slate-800 p-5">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold">캡처 중 — {s.title}</h2>
              <span className={`text-xs rounded-full px-2 py-1 ${s.wsStatus === "streaming" ? "bg-emerald-700" : "bg-amber-700"}`}>
                {s.wsStatus === "streaming" ? "전송 중" : s.wsStatus === "reconnecting" ? "재접속 중" : s.wsStatus}
              </span>
            </div>
            {s.captureLost && (
              <div className="rounded-lg bg-amber-900/60 border border-amber-500 px-4 py-3 text-sm">
                캡처가 끊겼습니다(공유 중지/탭 닫힘). 회의는 유지 중 —
                <button className="ml-2 underline" onClick={() => void s.startCapture()}>다시 탭 선택</button>
              </div>
            )}
            <LevelMeter dbfs={s.levelDbfs} />
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={s.micOn} onChange={() => void s.toggleMic()} />
              내 목소리 포함(마이크)
            </label>
            {/* 고정 높이 + 아래 정렬: 최신 자막이 항상 바닥에, 넘치는 옛 자막은 위로 잘림 — 레이아웃 출렁임 방지 */}
            <div className="rounded-lg bg-slate-900/60 p-4 h-44 overflow-hidden flex flex-col justify-end gap-3">
              {recent.length === 0 && <p className="text-sm text-slate-500">자막이 오면 여기 표시됩니다…</p>}
              {recent.map((u, i) => (
                <div key={u.seq} className={i === recent.length - 1 ? "" : "opacity-50"}>
                  <p className="text-xs text-slate-500 truncate">{u.text_en}</p>
                  <p className="text-lg leading-snug text-slate-50">{u.text_ko}</p>
                </div>
              ))}
            </div>
            {s.viewerUrl && <ViewerQr viewerUrl={s.viewerUrl} />}
            <p className="text-xs text-slate-500">
              단축키: <b>F</b>(ㄹ) 자막 전체화면 · <b>Q</b>(ㅂ) QR 전체화면 · Esc 닫기
            </p>
            <button className="w-full rounded bg-rose-700 py-2 font-semibold hover:bg-rose-600 disabled:opacity-50" disabled={s.busy} onClick={() => void s.stopCaptureAndEnd()}>
              캡처 중지 + 회의 종료
            </button>
          </div>
        )}

        {s.phase === "ended" && (
          <div className="rounded-xl bg-slate-800 p-5 space-y-3">
            <h2 className="font-semibold">회의가 종료됐습니다</h2>
            <p className="text-sm text-slate-400">보고서·요약은 서버 콘솔(또는 데스크탑 앱)에서 확인하세요.</p>
            <button className="rounded bg-slate-700 px-4 py-2 hover:bg-slate-600" onClick={() => window.location.reload()}>
              새 회의 시작
            </button>
          </div>
        )}
      </div>
      {fullscreen === "subtitle" && canSubtitleFullscreen && (
        <SubtitleFullscreenOverlay finals={finals} onClose={closeFullscreen} />
      )}
      {fullscreen === "qr" && s.viewerUrl && <QrFullscreenOverlay viewerUrl={s.viewerUrl} onClose={closeFullscreen} />}
    </main>
  );
}
// === ANCHOR: CAPTURE_VIEW_END ===
