// === ANCHOR: CAPTURE_VIEW_START ===
import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { checkCaptureSupport, isChromiumLike } from "./captureSupport";
import { useCaptureSession } from "./useCaptureSession";
import { useOperatorSubtitles } from "./useOperatorSubtitles";

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

function LevelMeter({ dbfs }: { dbfs: number }) {
  const pct = Math.round(Math.min(100, Math.max(0, ((dbfs + 60) / 60) * 100)));
  const quiet = dbfs < -50;
  return (
    <div>
      <div className="h-2 w-full rounded bg-slate-700">
        <div className={`h-2 rounded ${quiet ? "bg-amber-500" : "bg-emerald-500"}`} style={{ width: `${pct}%` }} />
      </div>
      {quiet && <p className="mt-1 text-xs text-amber-400">오디오가 거의 들어오지 않습니다 — 탭 선택 시 '탭 오디오 공유' 체크를 확인하세요.</p>}
    </div>
  );
}

export function CaptureView() {
  const s = useCaptureSession();
  const subtitles = useOperatorSubtitles(s.phase === "capturing" ? s.sessionId : null, s.operatorToken);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const recent = subtitles.utterances.filter((u) => u.is_final).slice(-2);

  return (
    <main className="min-h-screen bg-slate-900 text-slate-100 flex justify-center p-6">
      <div className="w-full max-w-2xl space-y-6">
        <header>
          <h1 className="text-2xl font-bold">웹 캡처 — 실시간 자막</h1>
          <p className="text-sm text-slate-400">구글밋 탭의 소리를 잡아 자막을 만듭니다. 앱 설치가 필요 없습니다.</p>
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
    </main>
  );
}
// === ANCHOR: CAPTURE_VIEW_END ===
