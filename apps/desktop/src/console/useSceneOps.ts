// 씬 스캔·경계 계산·정밀화·경계 오류 검사의 실행 오케스트레이션 훅 — 진행률
// 폴링, 연속 실행(스캔→경계→정밀화), 취소, 백그라운드 경계 검사. 화면 상태
// (data/notice/error/busy/선택)는 부모가 들고 setter를 주입한다(SceneSplitView
// 분할 — 로직은 그대로 이동).
import { useRef, useState, type Dispatch, type SetStateAction } from "react";
import {
  cancelSceneOps, getBoundaryStatus, getRefineStatus, getScenes,
  overrideSceneSegments, refineScenes, scanScenes, setSceneRule,
  startBoundaryCheck,
  type BoundaryStatus, type RefineStatus, type SceneMethod, type ScenesData,
  type SceneSegment,
} from "./videoApi";
import { scanProgressKey } from "./sceneSplitLogic";

type Mode = "scene" | "sequence";

export function useSceneOps(opts: {
  jobId: string;
  data: ScenesData | null;
  setData: Dispatch<SetStateAction<ScenesData | null>>;
  segments: SceneSegment[];
  setBusy: (b: boolean) => void;
  setError: (e: string | null) => void;
  setNotice: (n: string | null) => void;
  setSelectedSeg: (i: number | null) => void;
  delimiters: string[];
  seqIdx: number[];
  sceneIdx: number[];
  minMs: number | undefined;
  slateExample: string;
  scanIntervalS: number;
  scanMethod: SceneMethod;
  mode: Mode;
  dirtyModes: Set<Mode>;
  setDirtyModes: Dispatch<SetStateAction<Set<Mode>>>;
}) {
  const {
    jobId, data, setData, segments, setBusy, setError, setNotice,
    setSelectedSeg, delimiters, seqIdx, sceneIdx, minMs, slateExample,
    scanIntervalS, scanMethod, mode, dirtyModes, setDirtyModes,
  } = opts;

  // 스캔 진행률 폴링 — 서버의 scanning/error 신호로 종료를 판단한다.
  // 긴 영상은 OCR이 수 분 걸릴 수 있어 고정 타임아웃 대신 무진척이 오래
  // 지속될 때만 포기한다. hadScan=true(재스캔)면 옛 scanned 데이터가 프레임
  // 추출 동안 남아 보일 수 있어(구 서버) scanning을 한 번 본 뒤의 scanned만
  // 완료로 인정한다. 반환=스캔 성공 여부 — 실패했는데 runAll이 경계 계산으로
  // 진행하면 빈 스캔 데이터에 409가 떠 원인이 가려진다(실기).
  const pollScan = async (hadScan: boolean): Promise<boolean> => {
    let stalled = 0;
    let lastKey = "";
    let sawScanning = false;
    let pollFails = 0;
    for (let i = 0; i < 1200; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      let d: ScenesData;
      try {
        d = await getScenes(jobId);
      } catch {
        // 스캔도 CPU를 강하게 써 폴링이 순간 실패할 수 있다 — 연속 실패만 포기.
        pollFails += 1;
        if (pollFails >= 20) { setError("서버 응답이 없습니다. 상태를 확인하세요."); return false; }
        continue;
      }
      pollFails = 0;
      if (d.error) { setError(`스캔 실패: ${d.error}`); return false; }
      if (d.scanning) {
        sawScanning = true;
        const done = d.ocr_done ?? 0;
        const total = d.total_frames ?? 0;
        setNotice(total > 0
          ? `슬레이트 판독 중… ${done}/${total} 프레임`
          : `${d.stage ?? "프레임 추출"} 중…`);
        // 판독 수만 보면 카운터가 없는 앞 구간과 판독 뒤 재시도 단계를 정체로
        // 오인한다(scanProgressKey 주석 참조).
        const key = scanProgressKey(d);
        stalled = key === lastKey ? stalled + 1 : 0;
        lastKey = key;
        // 진척이 200초(133회) 넘게 멈춰 있으면 포기(서버 이상).
        if (stalled > 133) { setError("스캔이 진행되지 않습니다. 서버 상태를 확인하세요."); return false; }
      } else if (d.scanned && (sawScanning || !hadScan)) {
        setData(d);
        setNotice("스캔 완료 — 토큰을 지정하세요.");
        return true;
      }
    }
    setError("스캔이 시간 내 끝나지 않았습니다.");
    return false;
  };

  const runScan = async () => {
    const hadScan = Boolean(data?.scanned);
    setBusy(true); setError(null); setNotice("프레임 추출 중…");
    try {
      await scanScenes(jobId, scanIntervalS, scanMethod);  // 비동기 — 이후 진행률 폴링
      await pollScan(hadScan);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const applyRule = async () => {
    if (!data) return;
    setBusy(true); setError(null);
    try {
      const res = await setSceneRule(jobId, {
        delimiters, seq_tokens: seqIdx, scene_tokens: sceneIdx, min_ms: minMs,
        example: slateExample.trim() || null,
      });
      setData({ ...(data as ScenesData), scanned: true,
                segments_scene: res.segments_scene,
                segments_sequence: res.segments_sequence,
                // 서버도 재계산 시 옛 경계오류 플래그를 버린다 — 화면만 들고
                // 있으면 리페치 전까지 유령 플래그가 보인다(실기 07-29).
                boundary_issues: [] });
      setSelectedSeg(null);
      setNotice(`경계 계산 완료 — 시퀀스 ${res.segments_sequence.length}개 · 씬 ${res.segments_scene.length}개. 이제 익스포트하면 최신 경계로 잘립니다.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const [refineProg, setRefineProg] = useState<RefineStatus | null>(null);
  // 연속 실행(스캔→경계→정밀화) 중 단계 표시 + 취소 신호. 취소는 서버 작업을
  // 멈추는 것과 별개로 이 로컬 루프도 빠져나와야 한다.
  const [stage, setStage] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const cancelAll = async () => {
    cancelledRef.current = true;
    try {
      await cancelSceneOps(jobId);
    } catch { /* 이미 끝났을 수 있다 */ }
    setStage(null); setRefineProg(null); setBusy(false);
    setNotice("중단했습니다.");
  };

  // 정밀화 한 모드 실행 + 완료까지 폴링. 연속 실행에서 모드별로 재사용한다.
  const refineOnce = async (m: Mode, total: number) => {
    setRefineProg({ refining: true, done: 0, total, error: null });
    await refineScenes(jobId, m);
    // 정밀화는 ffmpeg를 병렬로 띄워 CPU를 강하게 써서, 상태 폴링 요청이 순간
    // 실패("Load failed")할 수 있다 — 작업은 서버에서 계속되므로 한 번 실패에
    // 포기하지 않고 연속 실패가 쌓일 때만(서버 다운 등) 중단한다.
    let pollFails = 0;
    for (let i = 0; i < 3600; i++) {
      if (cancelledRef.current) return;
      await new Promise((r) => setTimeout(r, 1500));
      let st: RefineStatus;
      try {
        st = await getRefineStatus(jobId);
      } catch {
        pollFails += 1;
        if (pollFails >= 20) throw new Error("서버 응답이 없습니다. 상태를 확인하세요.");
        continue;
      }
      pollFails = 0;
      setRefineProg(st);
      if (st.error) throw new Error(`정밀화 실패: ${st.error}`);
      if (!st.refining) return;
    }
    throw new Error("정밀화가 시간 내 끝나지 않았습니다.");
  };

  // 경계 오류(혼입) 검사 — 비차단 백그라운드. "전체 실행"이 경계계산까지 끝나면
  // 곧바로 완료되고, 이 검사는 뒤에서 돌다가 끝나면 ⚠ 필터 숫자만 채운다(체감 대기
  // 0). 새 실행이 시작되면 boundaryRunRef로 옛 폴링을 버린다. 실패는 조용히 무시.
  const boundaryRunRef = useRef(0);
  const runBoundaryCheckInBackground = async () => {
    const myRun = ++boundaryRunRef.current;
    try {
      await startBoundaryCheck(jobId);
    } catch {
      return;  // 시작 실패(구형 서버 등)면 조용히 건너뛴다.
    }
    let pollFails = 0;
    for (let i = 0; i < 3600; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      if (boundaryRunRef.current !== myRun) return;  // 새 실행/취소 — 옛 폴링 중단
      let st: BoundaryStatus;
      try {
        st = await getBoundaryStatus(jobId);
      } catch {
        pollFails += 1;
        if (pollFails >= 20) return;
        continue;
      }
      pollFails = 0;
      if (st.error) return;
      if (!st.checking) break;
    }
    if (boundaryRunRef.current !== myRun) return;
    try {
      const fresh = await getScenes(jobId);
      if (boundaryRunRef.current !== myRun) return;
      // boundary_issues만 병합 — 검사 중 사용자가 편집한 세그먼트를 덮지 않는다.
      setData((prev) => (prev
        ? { ...prev, boundary_issues: fresh.boundary_issues } : prev));
      const n = fresh.boundary_issues?.length ?? 0;
      setNotice(n > 0
        ? `⚠ 경계 오류 ${n}건 발견 — "⚠ 경계 오류" 탭에서 확인하세요.`
        : "경계 오류 검사 완료 — 발견된 혼입 없음.");
    } catch { /* 조용히 — 부가 기능이라 흐름을 막지 않는다 */ }
  };

  // 경계 오류만 다시 검사 — 현재(편집된) 세그먼트 그대로 OCR 재검증한다. "경계 계산"
  // 은 세그먼트를 런에서 재계산해 수동 편집을 날리므로, 고친 뒤 재검증엔 이걸 쓴다.
  // 미저장 편집은 먼저 저장(서버가 저장본을 검사하므로).
  const recheckBoundaries = async () => {
    if (dirtyModes.has("scene")) {
      try {
        await overrideSceneSegments(jobId, "scene",
          data?.segments_scene ?? []);
        setDirtyModes((prev) => { const nx = new Set(prev); nx.delete("scene"); return nx; });
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return;
      }
    }
    setNotice("경계 오류 다시 검사 중… (뒤에서 진행)");
    void runBoundaryCheckInBackground();
  };

  const doRefine = async (m: Mode = mode) => {
    setError(null); setNotice(null); setBusy(true);
    cancelledRef.current = false;
    try {
      await refineOnce(m, Math.max(1, segments.length - 1));
      if (cancelledRef.current) return;
      setData(await getScenes(jobId));  // 정밀화된 경계 다시 불러오기
      setNotice("경계 정밀화 완료 — 이제 프레임 단위로 잘립니다. 재익스포트하세요.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setRefineProg(null);
    }
  };

  // 스캔 → 경계 계산 → 정밀화(시퀀스·씬 둘 다)를 한 번에. 세 단계는 어차피 항상
  // 함께 필요하고, 정밀화를 빠뜨린 채 익스포트해 경계가 어긋나는 사고가 잦았다.
  // 토큰 규칙이 있어야 경계 계산이 가능하므로(스캔 결과를 보고 고르는 값), 규칙이
  // 없으면 스캔까지만 하고 토큰 선택을 기다린다. 익스포트 양식이 쇼마다 달라
  // (씬별로 내보내는 쇼도 있다) 두 모드를 모두 정밀화한다.
  const runAll = async (runOpts: { rescan: boolean }) => {
    // 재스캔이면 사용자가 고른 방식, 기존 데이터 재계산이면 그 데이터의 방식.
    const fp = (runOpts.rescan ? scanMethod : (data?.method ?? scanMethod))
      === "fingerprint";
    setError(null); cancelledRef.current = false; setBusy(true);
    try {
      if (runOpts.rescan) {
        setStage(fp ? "1/2 지문 컷 감지" : "1/4 슬레이트 스캔");
        await scanScenes(jobId, scanIntervalS, scanMethod);
        const ok = await pollScan(Boolean(data?.scanned));
        // 스캔 실패면 여기서 멈춘다 — 빈 데이터로 경계 계산을 치면 409가 떠
        // 진짜 원인(스캔 실패)이 가려진다.
        if (!ok || cancelledRef.current) return;
      }
      if (seqIdx.length === 0) {
        setStage(null);
        setNotice(fp
          ? "컷 감지 완료 — 토큰을 고른 뒤 '경계 계산'을 누르세요."
          : "스캔 완료 — 토큰을 고른 뒤 '경계 계산 + 정밀화'를 누르세요.");
        return;
      }
      setStage(fp ? "2/2 경계 계산" : "2/4 경계 계산");
      const res = await setSceneRule(jobId, {
        delimiters, seq_tokens: seqIdx, scene_tokens: sceneIdx, min_ms: minMs,
        example: slateExample.trim() || null,
      });
      if (cancelledRef.current) return;
      if (!fp) {
        // 간격 스캔만 정밀화가 필요하다 — 지문 경계는 이미 프레임 정확한 컷.
        setStage(`3/4 시퀀스 정밀화 (${res.segments_sequence.length}구간)`);
        await refineOnce("sequence", Math.max(1, res.segments_sequence.length - 1));
        if (cancelledRef.current) return;
        setStage(`4/4 씬 정밀화 (${res.segments_scene.length}구간)`);
        await refineOnce("scene", Math.max(1, res.segments_scene.length - 1));
        if (cancelledRef.current) return;
      }
      setData(await getScenes(jobId));
      setSelectedSeg(null);
      setNotice(`전체 완료 — 시퀀스 ${res.segments_sequence.length}개 · 씬 `
        + `${res.segments_scene.length}개, 양쪽 다 프레임 단위 경계입니다. `
        + `경계 오류 검사를 백그라운드에서 진행합니다(끝나면 ⚠ 탭에 표시).`);
      // 경계 오류 검사는 비차단으로 뒤에서 돌린다 — 전체 실행은 여기서 완료되어
      // 바로 검수를 시작할 수 있고, 검사가 끝나면 ⚠ 필터 숫자만 채워진다(체감 대기 0).
      void runBoundaryCheckInBackground();
    } catch (e) {
      if (!cancelledRef.current) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false); setStage(null); setRefineProg(null);
    }
  };

  return {
    stage, refineProg, pollScan, runScan, applyRule, cancelAll,
    recheckBoundaries, doRefine, runAll,
  };
}
