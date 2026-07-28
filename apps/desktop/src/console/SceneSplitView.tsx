import { useEffect, useRef, useState, type CSSProperties } from "react";
import { consoleStyles } from "./consoleStyles";
import { hasTauriRuntime } from "./useQrFullscreenShortcut";
import {
  absorbFlankedMisreads, anomalousLabels, applyFixes, boundaryIssueIndices, confidentFixes, filterIndices, formatMs, frameNumberAt, frameSeekMs, mergeAdjacentSameLabel, mergeSegment, exportedFileName, neighborIndices, probeFileName, scanProgressKey, scenePopupAction, stepVisibleIndex,
  NTSC_FPS, previewLabel, renameSegment, segFrameNumber, segmentTailMs, segmentThumbRange, shiftBoundaryMs, splitSegment, tokenizeSlate, trimFrames,
  type LabelFix,
} from "./sceneSplitLogic";
import { SceneFilmstrip } from "./SceneFilmstrip";
import {
  cancelSceneOps, exportScenes, getBoundaryStatus, getExportStatus, getRefineStatus,
  cleanupSceneExport, sceneExportFileUrl, probeExportDir, saveBoundaryOk,
  getScenes, listSlateTemplates, overrideSceneSegments, refineScenes, scanScenes,
  setOcrRegion as setOcrRegionApi, setSceneRule, startBoundaryCheck, testOcrRegion,
  videoMediaUrl,
  type BoundaryOk, type BoundaryStatus, type ExportStatus, type OcrRegion, type RefineStatus,
  type SceneMethod, type ScenesData, type SceneSegment, type SlateTemplate,
} from "./videoApi";
import { SlateRegionPicker } from "./SlateRegionPicker";

type Mode = "scene" | "sequence";

export function SceneSplitView({ jobId, onBack }: { jobId: string; onBack: () => void }) {
  const [data, setData] = useState<ScenesData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("scene");
  const [seqIdx, setSeqIdx] = useState<number[]>([]);
  const [sceneIdx, setSceneIdx] = useState<number[]>([]);
  // 공백을 필드 구분자로 쓰는 슬레이트 대응(기본은 공백 비분해 — 백엔드와 동일).
  const [spaceDelim, setSpaceDelim] = useState(false);
  // 샘플 간격(초). 짧은 씬이 많으면 촘촘하게(0.25s) — 놓치면 그 씬 클립이 없어진다.
  const [scanIntervalS, setScanIntervalS] = useState(2.0);
  // 스캔 방식 — 간격(기존)/지문(전 프레임 컷 감지, 프레임 정확·정밀화 불필요).
  // 지문에 리스크(가짜 컷 등)가 보이면 간격으로 폴백한다.
  const [scanMethod, setScanMethod] = useState<SceneMethod>("interval");
  // 최소 씬 길이(초). 빈값=자동(간격 비례). 이보다 짧은 구간은 오독 튐으로 보고
  // 흡수한다 — 진짜 짧은 씬이 삼켜지면 이 값을 낮춘다.
  const [minSceneSec, setMinSceneSec] = useState("");
  const minMs = minSceneSec.trim() === "" ? undefined
    : Math.max(0, Math.round(Number(minSceneSec) * 1000));

  const delimiters = spaceDelim ? ["_", " ", "-", "/"] : ["_", "-", "/"];

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

  const refresh = async () => {
    const d = await getScenes(jobId);
    setData(d);
    // 서버에 저장된 규칙이 있으면 토큰 선택을 복원한다 — 화면 재진입 시 선택이
    // 초기화돼 "경계 계산"이 비활성(회색)으로 보이던 문제 수정.
    if (d.rule) {
      setSeqIdx(d.rule.seq_tokens ?? []);
      setSceneIdx(d.rule.scene_tokens ?? []);
      setSpaceDelim((d.rule.delimiters ?? []).includes(" "));
    }
    // 서버에 저장된 스캔 방식 복원 — 재진입 시 선택이 초기화되지 않게.
    if (d.method) setScanMethod(d.method);
    // 재진입 시 스캔이 이미 진행 중이면(다른 화면에서 걸어둔 스캔 등) 진행률
    // 폴링을 이어붙인다 — 시작 버튼만 덩그러니 보이던 공백 수정.
    if (d.scanning) {
      setBusy(true); setNotice("슬레이트 판독 중…");
      try {
        await pollScan(false);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally { setBusy(false); }
    }
  };
  useEffect(() => { void refresh(); }, [jobId]);

  // 대표 프레임 = 첫 비어있지 않은 OCR 텍스트
  const sample = data?.frames.find((f) => f.text)?.text ?? "";
  const tokens = tokenizeSlate(sample, delimiters);

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
      });
      setData({ ...(data as ScenesData), scanned: true,
                segments_scene: res.segments_scene,
                segments_sequence: res.segments_sequence });
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
  const runAll = async (opts: { rescan: boolean }) => {
    // 재스캔이면 사용자가 고른 방식, 기존 데이터 재계산이면 그 데이터의 방식.
    const fp = (opts.rescan ? scanMethod : (data?.method ?? scanMethod))
      === "fingerprint";
    setError(null); cancelledRef.current = false; setBusy(true);
    try {
      if (opts.rescan) {
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

  const [exportProg, setExportProg] = useState<ExportStatus | null>(null);
  // 개별 씬 익스포트 진행 중인 구간(그 줄만 진행 표시, 다른 줄은 잠금).
  const [exportingOne, setExportingOne] = useState<number | null>(null);

  // 저장 폴더는 '클라 PC'의 폴더다 — 서버가 아니라 여기에 파일이 놓인다. 잡마다
  // 기억해 두 번째 익스포트부터는 다시 묻지 않는다(예전엔 서버 out_dir을 재사용
  // 했지만, 이제 서버는 자기 폴더에 굽고 클라가 받아 쓰므로 서버 값은 답이 아니다).
  const saveDirKey = `yeson.sceneExportDir.${jobId}`;
  const pickSaveDir = async (reuse: boolean): Promise<string | null> => {
    if (!hasTauriRuntime()) return null;   // 브라우저: 서버 폴더에 남는다
    const last = reuse ? localStorage.getItem(saveDirKey) : null;
    if (last) return last;
    const { open } = await import("@tauri-apps/plugin-dialog");
    const dir = await open({ directory: true, title: "저장 폴더 선택(이 PC)" });
    if (typeof dir !== "string") return null;
    localStorage.setItem(saveDirKey, dir);
    return dir;
  };

  // 서버가 구운 클립을 사용자가 고른 로컬 폴더로 받아 쓴다.
  //
  // 자르기는 서버가 해야 한다(원본 burned.mp4와 ffmpeg가 서버에 있다). 예전엔
  // 클라에서 고른 경로를 서버에 넘겨 서버가 '자기 디스크'의 그 경로에 썼는데,
  // 두 PC가 다르면 서버에 폴더만 새로 생기고 사용자가 보는 폴더는 끝까지 비어
  // 있었다(실기 윈도우 — 에러도 안 났다). 받기·쓰기는 Rust에 맡긴다(다른 드라이브
  // 허용 + 대용량 IPC 회피, 배치 다운로드와 같은 경로).
  const saveExportedFiles = async (files: string[], dir: string) => {
    const { join } = await import("@tauri-apps/api/path");
    const { invoke } = await import("@tauri-apps/api/core");
    for (let i = 0; i < files.length; i += 1) {
      const name = exportedFileName(files[i] as string);
      setNotice(`저장 중 ${i + 1}/${files.length} — ${name}`);
      await invoke("download_to_file",
                   { url: sceneExportFileUrl(jobId, name),
                     path: await join(dir, name) });
    }
    // 전부 받은 뒤에만 서버 사본을 지운다 — 위에서 하나라도 실패하면 예외가 나
    // 여기 도달하지 않으므로, 원본이 남아 다시 받을 수 있다(재인코딩 불필요).
    await cleanupSceneExport(jobId);
  };

  // 서버가 사용자가 고른 그 폴더에 직접 구워도 되는지 한 번 확인한다(수십 ms).
  //
  // 같은 PC면 위의 중계가 통째로 낭비다: 같은 바이트를 디스크에 두 번 쓰고, 굽기가
  // 전부 끝난 뒤에야 복사가 시작된다. 그렇다고 '같은 PC냐'를 호스트명으로 추측하면
  // 위(saveExportedFiles)에 적힌 그 실패가 되살아난다 — 사용자 폴더는 빈 채 서버에만
  // 파일이 생기는데 에러도 안 나던. 그래서 추측하지 않고 증명한다: 여기서 쓴 토큰
  // 파일을 서버가 같은 경로에서 읽고, 서버도 거기 쓸 수 있을 때만 직접 모드.
  //
  // 어떤 실패든(구버전 서버의 404 포함) false를 돌려 기존 중계 경로로 간다 — 이 확인이
  // 익스포트를 막는 일은 없어야 한다.
  const probeDirect = async (dir: string): Promise<boolean> => {
    if (!hasTauriRuntime()) return false;
    const bytes = crypto.getRandomValues(new Uint8Array(8));
    const token = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    const { join } = await import("@tauri-apps/api/path");
    const { invoke } = await import("@tauri-apps/api/core");
    const path = await join(dir, probeFileName(token));
    try {
      await invoke("probe_file_write", { path, token });
      const res = await probeExportDir(jobId, dir, token);
      return res.direct === true;
    } catch {
      return false;
    } finally {
      // 실패 경로에서도 우리가 만든 파일은 치운다(없으면 Rust가 성공으로 처리).
      try { await invoke("probe_file_remove", { path }); } catch { /* 잔여물뿐 */ }
    }
  };

  // 익스포트 진행률 폴링 — 전체/개별 익스포트가 같은 상태 파일(export_status)을 쓰므로
  // 폴링도 공유한다. 완료 문구만 호출자가 정한다. 완료 상태를 돌려줘 호출자가
  // 그 파일 목록을 로컬로 받아 쓸 수 있게 한다(실패·중단이면 null).
  const pollExport = async (doneMsg: (st: ExportStatus) => string) => {
    // 재인코딩은 클립당 수 초 걸리므로 1초 폴링으로 진행바를 갱신한다.
    for (let i = 0; i < 3600; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      const st = await getExportStatus(jobId);
      setExportProg(st);
      if (st.error) {
        // Windows는 다른 프로그램이 열고 있는 mp4를 덮어쓸 수 없다(mac/Linux는 가능).
        // 개별 익스포트는 "그 클립을 보다가 문제를 발견 → 다시 굽기" 흐름이라 플레이어에
        // 열어둔 파일을 덮어쓰려는 경우가 특히 잦다 — 원인을 짐작할 수 있게 붙여준다.
        setError(`익스포트 실패: ${st.error} — 저장 폴더의 그 mp4를 플레이어에서 `
          + "열어두면 덮어쓸 수 없습니다(Windows). 폴더 경로·쓰기 권한도 확인하세요.");
        return null;
      }
      if (!st.exporting) { setNotice(doneMsg(st)); return st; }
    }
    return null;
  };

  // 개별 씬 익스포트 — 고른 씬과 '맞닿은 이웃'까지 다시 굽는다. 경계를 옮기면 이웃의
  // 프레임 수도 함께 바뀌므로 고른 씬만 내보내면 이웃 mp4가 옛 경계로 남는다.
  // 저장 폴더는 지난 익스포트 폴더를 재사용한다 — 서버 export_status에 남아 있어 앱을
  // 다시 켜도 복구되고, "아까 그 폴더의 그 파일만 갱신"이 이 기능의 목적이다.
  const exportOne = async (i: number) => {
    setError(null); setNotice(null);
    if (dirty) {
      setError('저장 안 된 수정이 있습니다 — 먼저 "수정사항 저장"을 누르세요.');
      return;
    }
    // 서버는 새 익스포트를 시작할 때 generation을 올려 진행 중인 익스포트를
    // 취소시킨다 — 동시 실행을 막아 전체 익스포트가 중간에 끊기는 일이 없게 한다.
    if (busy || exportProg?.exporting || exportingOne != null) {
      setNotice("다른 작업이 진행 중입니다 — 끝난 뒤에 다시 시도하세요.");
      return;
    }
    const indices = neighborIndices(i, segments.length);
    if (indices.length === 0) return;
    // 지난 폴더를 그대로 쓴다 — "아까 그 폴더의 그 파일만 갱신"이 이 기능의 목적.
    const saveDir = await pickSaveDir(true);
    if (hasTauriRuntime() && !saveDir) {
      setNotice("저장 폴더 선택이 취소되었습니다."); return;
    }
    const direct = saveDir ? await probeDirect(saveDir) : false;
    setBusy(true); setExportingOne(i);
    setExportProg({ exporting: true, done: 0, total: indices.length,
                    error: null, out_dir: saveDir, files: [] });
    try {
      // 직접 모드면 out_dir을 넘겨 서버가 그 폴더에 바로 굽는다. 아니면 넘기지
      // 않는다 — 서버는 자기 폴더에 굽고, 받아 쓰는 건 아래에서.
      const res = await exportScenes(jobId, mode,
                                     direct && saveDir ? saveDir : undefined,
                                     indices);
      const labels = indices.map((k) => segments[k]?.label ?? "?").join(", ");
      const st = await pollExport((s) => direct
        ? `${s.files?.length ?? res.count}개 클립 저장 완료 — ${labels} (${saveDir}). `
          + "경계를 공유한 이웃 씬까지 갱신했습니다."
        : `${res.count}개 클립을 구웠습니다 — ${labels}. 저장 중…`);
      if (!st) return;
      // 직접 모드는 서버가 이미 사용자 폴더에 썼다 — 받을 것도, 지울 사본도 없다.
      if (direct) return;
      if (saveDir) {
        await saveExportedFiles(st.files ?? [], saveDir);
        setNotice(`${st.files?.length ?? 0}개 클립 저장 완료 — ${labels} (${saveDir}). `
          + "경계를 공유한 이웃 씬까지 갱신했습니다.");
      } else {
        setNotice(`${res.count}개 클립 익스포트 완료 — ${labels} `
          + `(서버 폴더 ${st.out_dir ?? ""}).`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false); setExportingOne(null); setExportProg(null);
    }
  };

  const doExport = async () => {
    setError(null); setNotice(null);
    // 익스포트는 서버 저장본을 자른다 — 현재 모드에 미저장 편집이 있으면 화면과
    // 다른 옛 경계로 잘린다. 먼저 저장하도록 막는다(실기: 시퀀스 16개 병합했는데
    // 저장 안 해 79개로 익스포트될 뻔한 사고 방지).
    if (dirty) {
      setError('저장 안 된 수정이 있습니다 — 먼저 "수정사항 저장"을 누르세요.');
      return;
    }
    // 전체 익스포트는 매번 폴더를 묻는다(다른 곳에 내보낼 수 있다).
    const saveDir = await pickSaveDir(false);
    if (hasTauriRuntime() && !saveDir) {
      setNotice("저장 폴더 선택이 취소되었습니다."); return;
    }
    const direct = saveDir ? await probeDirect(saveDir) : false;
    setBusy(true);
    setExportProg({ exporting: true, done: 0, total: segments.length,
                    error: null, out_dir: saveDir, files: [] });
    try {
      // 직접 모드면 out_dir을 넘겨 서버가 그 폴더에 바로 굽는다. 아니면 넘기지
      // 않는다 — 서버는 자기 폴더에 굽고, 받아 쓰는 건 아래에서.
      const res = await exportScenes(jobId, mode,
                                     direct && saveDir ? saveDir : undefined);
      const st = await pollExport((s) => direct
        ? `${s.files?.length ?? res.count}개 클립 저장 완료 (${saveDir})`
        : `${res.count}개 클립을 구웠습니다. 저장 중…`);
      if (!st) return;
      // 직접 모드는 서버가 이미 사용자 폴더에 썼다 — 받을 것도, 지울 사본도 없다.
      if (direct) return;
      if (saveDir) {
        await saveExportedFiles(st.files ?? [], saveDir);
        setNotice(`${st.files?.length ?? 0}개 클립 저장 완료 (${saveDir})`);
      } else {
        setNotice(`${res.count}개 클립 익스포트 완료 (서버 폴더 ${st.out_dir ?? ""})`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setExportProg(null);
    }
  };

  const segments: SceneSegment[] = data
    ? (mode === "sequence" ? data.segments_sequence : data.segments_scene) : [];

  // 현재 모드의 구간 목록을 편집(병합/이름수정)해 data 상태에 반영한다. dirty면
  // "수정사항 저장"으로 서버에 PATCH해야 익스포트에 반영된다.
  // dirty는 모드별로 따로 둔다 — 공유하면 씬을 저장할 때 시퀀스의 미저장 편집까지
  // '저장됨'으로 꺼져 저장 버튼이 비활성화된다(실기: 시퀀스 16개 병합했는데 저장
  // 불가). 익스포트는 서버 저장본을 쓰므로 각 모드를 반드시 따로 저장해야 한다.
  const [dirtyModes, setDirtyModes] = useState<Set<Mode>>(new Set());
  const dirty = dirtyModes.has(mode);
  const setSegments = (next: SceneSegment[]) => {
    if (!data) return;
    setData(mode === "sequence"
      ? { ...data, segments_sequence: next }
      : { ...data, segments_scene: next });
    setDirtyModes((prev) => new Set(prev).add(mode));
  };
  // 사용자가 고친 씬은 경계 오류에서 즉시 빠져야 한다 — boundary_issues는 검사
  // 시점 스냅샷이라, 편집한 씬의 플래그를 라벨로 제거한다(다음 검사 전까지 낙관적).
  const clearBoundaryFlags = (labels: Array<string | undefined>) => {
    const drop = new Set(labels.filter((l): l is string => Boolean(l)));
    if (drop.size === 0) return;
    setData((prev) => (prev && prev.boundary_issues
      ? { ...prev, boundary_issues: prev.boundary_issues.filter((b) => !drop.has(b.label)) }
      : prev));
  };
  // 확인 목록을 통째로 저장한다(전체 교체 — 서버도 같은 약속). 실패하면 화면 상태를
  // 되돌린다: 저장에 실패했는데 화면에서만 빼면 "뺐다고 봤는데 다음에 또 뜨는" 상태가
  // 된다. 낙관적 갱신 → 실패 시 롤백.
  const putBoundaryOk = async (next: BoundaryOk[]) => {
    if (!data) return;
    const before = data.boundary_ok ?? [];
    setData({ ...data, boundary_ok: next });
    try {
      await saveBoundaryOk(jobId, next);
    } catch (e) {
      setData((prev) => (prev ? { ...prev, boundary_ok: before } : prev));
      setError("확인 표시를 저장하지 못했습니다: "
        + (e instanceof Error ? e.message : String(e)));
    }
  };
  // 이 씬은 눈으로 확인했고 경계가 맞다 — 경계오류 목록에서 뺀다. 확인 당시의
  // 경계를 함께 남겨, 나중에 이 씬 경계를 고치면 다시 뜨게 한다.
  const markBoundaryOk = (i: number) => {
    const seg = segments[i];
    if (!seg) return;
    const rest = (data?.boundary_ok ?? []).filter((o) => o.label !== seg.label);
    void putBoundaryOk([...rest,
      { label: seg.label, start_ms: seg.start_ms, end_ms: seg.end_ms }]);
  };
  // 편집 되돌리기 스택 — 개별 병합(mergeSeg)과 경계 교정(nudgeBoundary)마다 직전
  // 상태를 쌓아 여러 단계 물릴 수 있게 한다. 각 항목=편집 전 세그먼트·경계플래그
  // 스냅샷 + 편집 결과 구간 인덱스(병합=생존 구간, 경계 교정=교정한 구간). 두 종류를
  // 한 스택에 담아 엄격 LIFO로 물린다 — 스택을 따로 두면 병합·경계 교정을 섞었을 때
  // 되돌리는 순서가 뒤엉킨다. kind로 버튼 위치를 가른다(merge=리스트 줄,
  // boundary=팝업). 모드 전환·저장·일괄교정 시 비운다(구간 목록이 재편돼 인덱스가
  // 무의미). undoSnapshot(일괄교정 한 단계)과는 별개.
  const [editUndo, setEditUndo] = useState<
    { kind: "merge" | "boundary" | "split"; segs: SceneSegment[];
      issues: ScenesData["boundary_issues"]; survivor: number }[]
  >([]);
  const mergeSeg = (i: number, into: "prev" | "next") => {
    // 병합한 두 씬의 경계 오류 플래그를 뺀다(사라진 라벨 + 살아남은 라벨 둘 다).
    const gone = segments[i]?.label;
    const survivorLabel = into === "prev" ? segments[i - 1]?.label : segments[i + 1]?.label;
    // 병합하면 배열이 줄어 기존 선택 인덱스가 다른 구간을 가리킨다 — 살아남은 구간을
    // 선택해 필름스트립 하이라이트와 경계 썸네일이 병합 결과(넓어진 범위, 당겨진 시작
    // 시각)를 곧바로 보여주게 한다.
    const survivor = into === "prev" ? Math.max(0, i - 1) : i;
    // 되돌리기용: 병합 전 세그먼트·경계플래그와 생존 구간을 스택에 쌓는다(여러 단계).
    setEditUndo((prev) => [
      ...prev, { kind: "merge", segs: segments, issues: data?.boundary_issues, survivor }]);
    setSegments(mergeSegment(segments, i, into));
    clearBoundaryFlags([gone, survivorLabel]);
    setSelectedSeg(survivor);
  };
  // 편집 되돌리기 — 스택 top의 편집 전 상태(세그먼트·경계플래그)로 복원하고 그 구간을
  // 다시 선택한다. 여러 번 누르면 한 단계씩 거슬러 올라간다. 아직 저장 전이므로 dirty는
  // 유지(복원본도 서버 저장본과는 다르다). 경계 교정을 물릴 때 팝업이 열려 있으면
  // 프리뷰가 옛 경계(startMs/endMs·프레임 카운터)를 그대로 들고 있으므로 복원된
  // 세그먼트로 다시 만들고 그 경계 프레임으로 시킹한다 — 화면과 데이터가 어긋나면
  // 사용자가 이미 물린 편집을 다시 물린다.
  const undoEdit = () => {
    if (editUndo.length === 0 || !data) return;
    const top = editUndo[editUndo.length - 1]!;
    setData(mode === "sequence"
      ? { ...data, segments_sequence: top.segs, boundary_issues: top.issues }
      : { ...data, segments_scene: top.segs, boundary_issues: top.issues });
    setDirtyModes((prev) => new Set(prev).add(mode));
    setSelectedSeg(top.survivor);
    setEditUndo((prev) => prev.slice(0, -1));
    const p = previewRef.current;
    const restored = top.segs[top.survivor];
    if ((top.kind === "boundary" || top.kind === "split")
        && p?.segIndex === top.survivor && restored) {
      const fps = data.video_fps && data.video_fps > 0 ? data.video_fps : NTSC_FPS;
      const side = p.side ?? "head";
      const focusMs = side === "tail"
        ? frameSeekMs(segmentTailMs(restored.start_ms, restored.end_ms, fps), fps)
        : frameSeekMs(restored.start_ms, fps);
      setPreview(buildSegPreview(restored, top.survivor, focusMs, side));
      const v = previewVideoRef.current;
      if (v) { v.pause(); v.currentTime = focusMs / 1000; }
    }
  };
  const renameSeg = (i: number, label: string) => {
    clearBoundaryFlags([segments[i]?.label]);  // 이름 바꾼 씬은 플래그 해제(라벨도 바뀜)
    setSegments(renameSegment(segments, i, label));
  };

  // 리스트에서 클릭한 구간 → 필름스트립 하이라이트 범위. 썸네일 클릭 → 팝업 시각.
  const [selectedSeg, setSelectedSeg] = useState<number | null>(null);
  // 슬레이트 구역 — 쇼마다 위치가 달라 사용자가 드래그로 지정한다. 스캔 전에도
  // 스캔 후에도 다시 잡을 수 있다(다시 잡으면 재스캔해야 반영된다).
  // 팝업 단축키 effect의 의존성이 이 값을 읽으므로 그 위에 선언해 둔다(아래에
  // 두면 의존성 배열이 렌더 중 TDZ에 걸린다 — 분할 시 슬레이트 읽기가 쓴다).
  const [ocrRegion, setOcrRegion] = useState<OcrRegion | null>(null);
  // 팝업 프리뷰 — seekMs(첫 표시 프레임)와, 구간이면 그 [start,end)·라벨·프레임정확
  // 재생/정지 시각. playStartMs=첫 프레임 중앙, lastFrameMs=마지막(꼬리) 프레임 중앙
  // (둘 다 소스 fps로 계산). 구간이면 재생을 그 범위로 묶어 분할을 확인하게 한다.
  const [preview, setPreview] = useState<
    { seekMs: number; startMs?: number; endMs?: number; label?: string;
      playStartMs?: number; lastFrameMs?: number; fps?: number;
      segIndex?: number; side?: "head" | "tail" } | null>(null);
  const previewVideoRef = useRef<HTMLVideoElement>(null);
  // 구간 반복재생(기본 꺼짐) — 완료 시 꼬리 프레임에 정지해 꼬리를 확인하게 한다.
  // 켜면 [첫 프레임, 꼬리 프레임]을 반복한다.
  const [loopSeg, setLoopSeg] = useState(false);
  // 경계 교정 시 한 번에 옮길 프레임 수 — 디졸브/와이프는 9프레임 이상 어긋나기도
  // 해서 한 클릭에 N프레임 이동. 미세조정은 1로.
  const [nudgeFrames, setNudgeFrames] = useState(1);
  // 팝업 플레이어 컨트롤 — 현재 재생 위치(ms)·재생 여부·영상 길이(ms). 프레임
  // 카운터(1부터)·스크러버·재생/정지 버튼이 쓴다. 프로그램적 시킹은 <video onSeeked>,
  // 재생 중 갱신은 onTimeUpdate(~4Hz)가 previewMs를 맞춘다.
  const [previewMs, setPreviewMs] = useState(0);
  const [previewPlaying, setPreviewPlaying] = useState(false);
  const [previewDur, setPreviewDur] = useState(0);
  // 재생 감시가 최신 preview/loop을 읽도록 ref로 미러링(재생 중 갱신 반영).
  const previewRef = useRef(preview);
  const loopRef = useRef(loopSeg);
  const rafRef = useRef<number | null>(null);
  const rvfcRef = useRef<number | null>(null);
  useEffect(() => { previewRef.current = preview; }, [preview]);
  useEffect(() => { loopRef.current = loopSeg; }, [loopSeg]);
  // 팝업이 열리거나 경계 편집으로 preview가 바뀌면 프레임 카운터/스크러버를 그 시각으로
  // 초기화한다(직후 onSeeked가 실제 currentTime으로 정밀 보정).
  useEffect(() => { if (preview) setPreviewMs(preview.seekMs); }, [preview]);
  const stopGuards = () => {
    const v = previewVideoRef.current;
    if (rafRef.current != null) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
    if (rvfcRef.current != null && v && typeof v.cancelVideoFrameCallback === "function") {
      v.cancelVideoFrameCallback(rvfcRef.current); rvfcRef.current = null;
    }
  };
  // 팝업이 닫히면 감시 루프를 확실히 멈춘다.
  useEffect(() => { if (preview == null) stopGuards(); }, [preview]);

  // 재생 시작 시 '꼬리 프레임'에 도달하면 멈춘다(반복이면 첫 프레임으로 되감음).
  // 프레임 정확한 requestVideoFrameCallback.mediaTime으로 현재 프레임 인덱스를 재
  // 프레임 단위로 비교한다 — 정확히 잡으면 시킹 없이 그 자리에서 정지해 깜빡임이
  // 없다. video.currentTime은 브라우저가 드물게(~4Hz) 갱신해 폴링해도 뒤처지므로
  // (실제 화면은 이미 다음 씬) rVFC가 없을 때만 폴백으로 쓴다.
  const startSegmentGuard = () => {
    const v = previewVideoRef.current;
    if (!v) return;
    stopGuards();
    const lastIdxOf = (p: { lastFrameMs?: number; fps?: number }) =>
      Math.round((p.lastFrameMs ?? 0) / (1000 / (p.fps || NTSC_FPS)) - 0.5);
    if (typeof v.requestVideoFrameCallback === "function") {
      const step = (_now: number, meta: { mediaTime: number }) => {
        const vv = previewVideoRef.current;
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
      const vv = previewVideoRef.current;
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

  // 프리뷰(팝업) 상태를 세그먼트로부터 구성 — 프레임 정확 재생/편집 값 포함.
  const buildSegPreview = (s: SceneSegment, segIndex: number, seekMs: number,
                           side: "head" | "tail") => {
    const fps = data?.video_fps && data.video_fps > 0 ? data.video_fps : NTSC_FPS;
    return {
      seekMs, segIndex, side, label: s.label,
      startMs: s.start_ms, endMs: s.end_ms, fps,
      playStartMs: frameSeekMs(s.start_ms, fps),
      lastFrameMs: frameSeekMs(segmentTailMs(s.start_ms, s.end_ms, fps), fps),
    };
  };

  // 팝업에서 머리/꼬리 경계를 delta 프레임 이동 — 그 프레임을 이웃 씬으로 넘기거나
  // 이웃에서 가져온다(스캔이 못 잡는 디졸브/와이프 수동 교정). 인접 두 세그먼트를
  // 같은 새 경계로 갱신(dirty)하고, 팝업 영상을 편집한 경계 프레임으로 시킹해 즉시
  // 확인시킨다. 경계가 프레임 정렬을 유지하므로 익스포트도 프레임 정확.
  const nudgeBoundary = (side: "head" | "tail", delta: number) => {
    if (!data || preview?.segIndex == null || delta === 0) return;
    const i = preview.segIndex;
    const fps = data.video_fps && data.video_fps > 0 ? data.video_fps : NTSC_FPS;
    const frameMs = 1000 / fps;
    const frames = (s: SceneSegment) =>
      Math.max(1, Math.round((s.end_ms - s.start_ms) / frameMs));
    const segs = segments.slice();
    let focusMs: number;
    if (side === "tail") {
      if (i >= segs.length - 1) return;
      // 빈 씬 방지 클램프: delta<0(이 씬이 넘김)은 이 씬이 1프레임 남게, delta>0(다음
      // 씬에서 가져옴)은 다음 씬이 1프레임 남게. 요청 N이 넘치면 가능한 만큼만 이동.
      const d = Math.max(-(frames(segs[i]!) - 1),
                         Math.min(frames(segs[i + 1]!) - 1, delta));
      if (d === 0) return;
      const nb = shiftBoundaryMs(segs[i]!.end_ms, fps, d);
      segs[i] = { ...segs[i]!, end_ms: nb };
      segs[i + 1] = { ...segs[i + 1]!, start_ms: nb };
      focusMs = frameSeekMs(segmentTailMs(segs[i]!.start_ms, segs[i]!.end_ms, fps), fps);
    } else {
      if (i <= 0) return;
      const d = Math.max(-(frames(segs[i - 1]!) - 1),
                         Math.min(frames(segs[i]!) - 1, delta));
      if (d === 0) return;
      const nh = shiftBoundaryMs(segs[i]!.start_ms, fps, d);
      segs[i - 1] = { ...segs[i - 1]!, end_ms: nh };
      segs[i] = { ...segs[i]!, start_ms: nh };
      focusMs = frameSeekMs(segs[i]!.start_ms, fps);
    }
    // 되돌리기용: 교정 전 세그먼트·경계플래그를 병합과 같은 스택에 쌓는다. In/Out
    // 트림은 한 클릭이라 오조작이 쉬운 만큼 되돌릴 수 있어야 한다.
    setEditUndo((prev) => [
      ...prev,
      { kind: "boundary", segs: segments, issues: data.boundary_issues, survivor: i }]);
    setSegments(segs);  // dirty — "수정사항 저장" 후 익스포트에 반영
    // 교정한 씬(+맞닿은 이웃)의 경계 오류 플래그를 뺀다 — 고쳤으면 필터에서 빠져야.
    clearBoundaryFlags([segs[i]!.label,
                        side === "tail" ? segs[i + 1]?.label : segs[i - 1]?.label]);
    setPreview(buildSegPreview(segs[i]!, i, focusMs, side));
    const v = previewVideoRef.current;
    if (v) { v.pause(); v.currentTime = focusMs / 1000; }
  };
  // 편집 프로그램식 In/Out 트림 — 지금 보고 있는 프레임을 이 씬의 첫(In)/마지막(Out)
  // 프레임으로 확정한다. 사용자가 프레임 카운터를 읽어 '프레임씩' 칸에 옮겨 적던
  // 계산을 여기서 대신 한다(오입력 제거). 경계 이동은 nudgeBoundary가 그대로 담당.
  // ms를 안 주면 영상의 현재 시각을 쓴다(키보드 단축키 경로).
  const trimAt = (side: "in" | "out", ms?: number) => {
    const p = previewRef.current;
    if (!p || p.segIndex == null || p.startMs == null || p.endMs == null) return;
    const at = ms ?? (previewVideoRef.current
      ? previewVideoRef.current.currentTime * 1000 : p.seekMs);
    const { k, n } = segFrameNumber(at, p.startMs, p.endMs, p.fps);
    const { inFrames, outFrames } = trimFrames(k, n);
    if (side === "in") {
      if (p.segIndex <= 0 || inFrames === 0) return;   // 첫 씬이거나 넘길 게 없음
      nudgeBoundary("head", inFrames);
    } else {
      if (p.segIndex >= segments.length - 1 || outFrames === 0) return;
      nudgeBoundary("tail", -outFrames);
    }
  };

  // 한 씬 안에 두 씬이 붙어 있을 때(스캔이 그 컷을 못 잡은 경우) 지금 보는 프레임에서
  // 나눈다. 지금까지 할 수 있는 편집은 병합·이름수정·트림뿐이라 나눌 수단이 재스캔밖에
  // 없었다(25분 + 수동 정렬 초기화).
  //
  // 지금 보는 프레임이 뒤 구간의 첫 프레임이 된다 — In 트림과 같은 약속이고 자르는
  // 계산도 같다. 뒤 구간이 원래 이름을 유지하고, 앞 구간 이름은 슬레이트를 읽어 채운다.
  const splitAt = async (ms?: number) => {
    const p = previewRef.current;
    if (!p || p.segIndex == null || p.startMs == null || p.endMs == null) return;
    const i = p.segIndex;
    const cur = segments[i];
    if (!cur) return;
    const fps = p.fps || NTSC_FPS;
    const at = ms ?? (previewVideoRef.current
      ? previewVideoRef.current.currentTime * 1000 : p.seekMs);
    const { k } = segFrameNumber(at, p.startMs, p.endMs, fps);
    const next = splitSegment(segments, i, k, fps);
    if (next === segments) {
      setNotice("첫 프레임에서는 나눌 수 없습니다 — 뒤 씬이 시작되는 프레임으로 옮기세요.");
      return;
    }
    setEditUndo((prev) => [...prev,
      { kind: "split", segs: segments, issues: data?.boundary_issues, survivor: i }]);
    setSegments(next);
    // 혼입을 방금 해결했으므로 그 씬의 경계오류 표시를 뺀다(병합과 동일한 처리).
    clearBoundaryFlags([cur.label]);
    // 팝업이 옛 경계를 들고 있으면 화면과 데이터가 어긋나 사용자가 방금 한 편집을
    // 또 한다 — 앞 구간 기준으로 다시 만들고 그 머리 프레임으로 시킹한다.
    const head = next[i] as SceneSegment;
    const focusMs = frameSeekMs(head.start_ms, fps);
    setSelectedSeg(i);
    setPreview(buildSegPreview(head, i, focusMs, "head"));
    const v = previewVideoRef.current;
    if (v) { v.pause(); v.currentTime = focusMs / 1000; }
    setNotice("씬을 나눴습니다 — 앞 구간 이름을 읽는 중…");
    // 앞 구간 한가운데 프레임의 슬레이트를 읽어 이름을 제안한다. 머리·꼬리는 디졸브에
    // 걸릴 확률이 높아 한가운데를 읽는다. 저장된 구역을 그대로 넘겨야 스캔과 같은
    // 상자를 읽어 같은 라벨이 나온다. 실패해도 분할은 유지한다 — 경계는 이미 맞았고
    // 남은 건 이름뿐이다.
    try {
      const midMs = frameSeekMs((head.start_ms + head.end_ms) / 2, fps);
      const res = await testOcrRegion(jobId, Math.round(midMs), ocrRegion);
      const upto = mode === "sequence"
        ? Math.max(-1, ...seqIdx)
        : Math.max(-1, ...seqIdx, ...sceneIdx);
      const proposed = previewLabel(res.tokens, upto);
      if (proposed && proposed !== head.label) {
        renameSeg(i, proposed);
        setNotice(`앞 구간 이름을 ${proposed}으로 읽었습니다 — 다르면 이름칸에서 고치세요.`);
      } else {
        setNotice(`앞 구간 슬레이트를 읽지 못했습니다 — '${head.label}' 줄의 이름을 `
          + "직접 입력하세요.");
      }
    } catch {
      setNotice("앞 구간 슬레이트를 읽지 못했습니다 — 이름을 직접 입력하세요.");
    }
  };

  // 팝업 영상을 특정 프레임 시각으로 이동(머리/꼬리 확인용).
  const seekPreview = (ms?: number) => {
    const v = previewVideoRef.current;
    if (v && ms != null) { v.pause(); v.currentTime = ms / 1000; }
  };
  // 한 프레임씩 이동 — 정지 후 그 프레임 중앙으로 시킹한다(오류 프레임을 눈으로
  // 한 칸씩 찾는다). 소스 fps로 프레임 인덱스를 계산해 프레임 정확. 0 미만은 클램프.
  const stepPreviewFrame = (delta: number) => {
    const v = previewVideoRef.current;
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
    const v = previewVideoRef.current;
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

  const intervalMs = data?.interval_ms ?? 2000;
  // 필름스트립은 썸네일 격자(성긴 간격)로 그린다 — 스캔 간격과 다르다.
  const thumbIntervalMs = data?.thumb_interval_ms ?? intervalMs;
  const thumbCount = data?.thumb_count ?? data?.frames.length ?? 0;
  const highlight = selectedSeg != null && segments[selectedSeg]
    ? segmentThumbRange(segments[selectedSeg]!.start_ms,
                        segments[selectedSeg]!.end_ms, thumbIntervalMs, thumbCount)
    : null;

  // OCR 오독 검출 — 씬 모드는 구간이 수백 개라 눈으로 못 훑는다. 라벨 모양이
  // 다수와 어긋나는 행만 모아 보여주고, 템플릿 재분해로 만든 교정안을 일괄 적용.
  const [onlyAnomalies, setOnlyAnomalies] = useState(false);
  const labels = segments.map((s) => s.label);
  const anomalies = anomalousLabels(labels, delimiters);
  const anomalyIdx = anomalies.map((a) => a.index);
  const suggestionOf = new Map(anomalies.map((a) => [a.index, a]));
  // 경계 오류(혼입) — 씬 모드 전용(boundary_issues는 segments_scene 기준). 다른
  // 모드에선 빈 목록이라 필터 탭이 숨겨진다.
  const [onlyBoundaryErrors, setOnlyBoundaryErrors] = useState(false);
  const boundaryIssues = mode === "scene" ? (data?.boundary_issues ?? []) : [];
  const boundaryOk = mode === "scene" ? (data?.boundary_ok ?? []) : [];
  // 라벨로 다시 찾고(인덱스가 밀려도 안전), 사용자가 '문제없음'으로 확인한 구간은
  // 뺀다 — 단 그 뒤에 경계가 바뀌었으면 확인표시를 무시한다(boundaryIssueIndices).
  const boundaryIdx = boundaryIssueIndices(boundaryIssues, segments, boundaryOk);
  const boundaryCount = boundaryIdx.length;
  // 현재 목록에 남아 실제로 무언가를 숨기고 있는 확인표시 수 — '모두 해제' 안내용.
  const boundaryOkCount = boundaryIssueIndices(boundaryIssues, segments, []).length
    - boundaryCount;
  // 라벨 검색 — 400+ 줄에서 특정 씬을 스크롤로 찾는 대신 번호 일부를 쳐서 좁힌다.
  // 탭 필터와 교차하므로 "경계 오류 중 0230"처럼 겹쳐 쓸 수 있다.
  const [labelQuery, setLabelQuery] = useState("");
  // 탭을 바꿔도 인덱스는 원본 기준을 유지한다(병합/이름수정 콜백이 인덱스를 쓴다).
  const tabIdx = onlyAnomalies ? anomalyIdx
    : onlyBoundaryErrors ? boundaryIdx : null;
  const visibleIndices = filterIndices(labels, tabIdx, labelQuery);
  // 이전/다음 씬 이동은 '보이는' 목록을 따라간다 — 필터·검색으로 3개만 남았으면
  // 그 3개 사이만 오간다(안 보이는 줄로 선택이 튀면 화면과 어긋난다).
  const visibleAll = visibleIndices ?? segments.map((_, i) => i);
  const stepSegment = (delta: number) => {
    const next = stepVisibleIndex(visibleAll, selectedSeg, delta);
    if (next != null) setSelectedSeg(next);
  };
  // 팝업 플레이어에서 씬 넘기기 — 팝업을 닫고 목록에서 다음 씬을 찾아 다시 프레임을
  // 클릭하는 왕복을 없앤다. 보던 쪽(머리/꼬리)을 유지해 "모든 컷의 꼬리를 훑는" 식의
  // 한 줄기 검수가 끊기지 않게 하고, 목록 선택도 함께 옮겨 팝업을 닫으면 그 씬에 있게
  // 한다. 이동 범위는 목록과 같은 '보이는 목록'(필터·검색 적용) 기준.
  const stepPreviewSegment = (delta: number) => {
    if (!data || preview?.segIndex == null) return;
    const next = stepVisibleIndex(visibleAll, preview.segIndex, delta);
    if (next == null) return;
    const seg = segments[next];
    if (!seg) return;
    const side = preview.side ?? "head";
    const fps = data.video_fps && data.video_fps > 0 ? data.video_fps : NTSC_FPS;
    const focusMs = side === "tail"
      ? frameSeekMs(segmentTailMs(seg.start_ms, seg.end_ms, fps), fps)
      : frameSeekMs(seg.start_ms, fps);
    setPreview(buildSegPreview(seg, next, focusMs, side));
    setSelectedSeg(next);
    const v = previewVideoRef.current;
    if (v) { v.pause(); v.currentTime = focusMs / 1000; }
  };

  // 팝업이 열려 있을 때의 검수 단축키(매핑·한글 IME 처리는 scenePopupAction).
  // I/O=In/Out 트림(편집 프로그램 관례), G/H=이전/다음 씬, [/]=머리로/꼬리로 —
  // 화면의 해당 버튼에 같은 키를 적어 뒀다. 입력칸에 포커스가 있으면 무시한다
  // — '프레임씩' 수나 라벨을 타이핑하다 경계가 바뀌면 안 된다. preview·목록이
  // 바뀔 때마다 다시 등록해 핸들러가 최신 세그먼트·보이는 목록을 본다(검색으로
  // 목록이 줄면 씬 이동 범위도 함께 줄어야 화면과 어긋나지 않는다).
  useEffect(() => {
    if (preview?.segIndex == null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
                || t.isContentEditable)) return;
      const action = scenePopupAction(e);
      if (action == null) return;
      e.preventDefault();
      if (action === "trimIn" || action === "trimOut") {
        trimAt(action === "trimIn" ? "in" : "out");
      } else if (action === "split") {
        void splitAt();
      } else if (action === "prevScene" || action === "nextScene") {
        stepPreviewSegment(action === "prevScene" ? -1 : 1);
      } else {
        seekPreview(action === "toHead"
          ? preview.playStartMs : preview.lastFrameMs);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // splitAt은 이름 제안에 mode·토큰 규칙·저장된 구역을 쓴다 — 여기 빠뜨리면
    // 단축키 경로만 옛 값으로 OCR을 부른다(버튼은 매 렌더 새로 만들어져 멀쩡하다).
  }, [preview, segments, visibleIndices, data, mode, seqIdx, sceneIdx, ocrRegion]);

  // ←/→로 이전·다음 씬. 선택이 바뀌면 sticky 검수 뷰의 머리·꼬리 프레임이 갱신되고
  // 목록도 그 줄로 스크롤돼(SceneFilmstrip) 스크롤 조작이 아예 필요 없다. 입력칸
  // 포커스(라벨 수정·검색)와 팝업이 열린 동안은 무시한다 — 팝업에서는 프레임 이동이
  // 주인이고, 씬이 바뀌면 검수 흐름이 끊긴다.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
                || t.isContentEditable)) return;
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();  // 스크롤 컨테이너의 가로 스크롤 기본동작 차단
      const delta = e.key === "ArrowRight" ? 1 : -1;
      // 팝업이 열려 있으면 프레임 한 칸(그 화면의 ◀이전/다음▶과 같은 동작), 닫혀
      // 있으면 목록의 씬 이동. 두 화면 모두 그 동작을 하는 버튼이 눈에 보이므로
      // 같은 키가 다른 일을 해도 헷갈리지 않는다. 팝업의 씬 이동은 좌우 사이드 버튼.
      if (preview != null) stepPreviewFrame(delta);
      else stepSegment(delta);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [preview, segments, visibleIndices, selectedSeg]);

  // 일괄 적용은 곧바로 바꾸지 않는다 — 무엇이 어떻게 바뀌는지 before→after로
  // 먼저 보여주고, 체크한 것만 적용한다. 적용 후에도 한 번은 되돌릴 수 있다.
  const [pendingFixes, setPendingFixes] = useState<LabelFix[] | null>(null);
  const [fixChecked, setFixChecked] = useState<Set<number>>(new Set());
  const [undoSnapshot, setUndoSnapshot] = useState<SceneSegment[] | null>(null);

  // 모드가 바뀌면 구간 목록 자체가 달라진다 — 이전 모드에서 만든 미리보기·되돌리기
  // 스냅샷·필터·선택은 모두 무의미해지므로 지운다(씬별 목록이 시퀀스별 화면에
  // 남아 보이던 문제). applyFixes의 from 검사가 2차 방어선이다.
  useEffect(() => {
    setPendingFixes(null);
    setFixChecked(new Set());
    setUndoSnapshot(null);
    setEditUndo([]);  // 모드가 바뀌면 편집 되돌리기 스택의 인덱스가 무의미
    setOnlyAnomalies(false);
    setOnlyBoundaryErrors(false);
    setSelectedSeg(null);
  }, [mode]);

  const [templates, setTemplates] = useState<SlateTemplate[]>([]);
  const [showPicker, setShowPicker] = useState(false);
  useEffect(() => {
    void (async () => {
      try {
        setTemplates((await listSlateTemplates()).templates);
      } catch { /* 템플릿은 편의 기능 — 실패해도 화면은 살아 있어야 한다 */ }
    })();
  }, []);
  useEffect(() => { setOcrRegion(data?.ocr_region ?? null); }, [data?.ocr_region]);
  useEffect(() => {
    // 지문 스캔에는 샘플 간격 개념이 없다 — 간격 UI 값을 덮어쓰지 않는다.
    if (data?.interval_ms && data.method !== "fingerprint") {
      setScanIntervalS(data.interval_ms / 1000);
    }
  }, [data?.interval_ms, data?.method]);

  // 템플릿을 고르면 구역과 토큰 규칙을 한 번에 적용한다(같은 쇼면 포맷도 같다).
  const applyTemplate = (t: SlateTemplate) => {
    setOcrRegion(t.region);
    setSeqIdx(t.seq_tokens ?? []);
    setSceneIdx(t.scene_tokens ?? []);
    setSpaceDelim((t.delimiters ?? []).includes(" "));
    if (t.scan_interval_s) setScanIntervalS(t.scan_interval_s);
    if (t.method) setScanMethod(t.method);
    void setOcrRegionApi(jobId, t.region).catch(() => undefined);
    setNotice(`'${t.name}' 템플릿을 적용했습니다 — 구역과 토큰 규칙이 설정됐습니다.`);
  };

  // 구역 확인용 샘플 시각 = 슬레이트가 읽힌 첫 프레임(없으면 영상 중반).
  const sampleMs = data?.frames.find((f) => f.text)?.t_ms
    ?? Math.floor(((data?.frames.at(-1)?.t_ms ?? 0)) / 2);

  const openFixPreview = () => {
    const fixes = confidentFixes(segments.map((s) => s.label), delimiters);
    setPendingFixes(fixes);
    setFixChecked(new Set(fixes.map((f) => f.index)));  // 기본 전체 선택
  };

  const confirmFixes = () => {
    if (!pendingFixes) return;
    const applied = pendingFixes.filter((f) => fixChecked.has(f.index));
    if (applied.length === 0) { setPendingFixes(null); return; }
    setUndoSnapshot(segments);  // 되돌리기용 스냅샷
    setEditUndo([]);            // 일괄교정은 배열을 재편 — 개별 편집 스택 무효화
    // 교정으로 같아진 인접 라벨을 바로 병합한다 — 안 그러면 한 씬이 여러 조각으로
    // 남는다(오독이 씬 한가운데를 쪼갠 케이스).
    const fixed = applyFixes(segments, pendingFixes, fixChecked);
    const mergedSegs = mergeAdjacentSameLabel(fixed);
    setSegments(mergedSegs);
    setPendingFixes(null);
    const mergedCount = fixed.length - mergedSegs.length;
    setNotice(`이름 ${applied.length}건 교정`
      + (mergedCount > 0 ? ` + 인접 중복 ${mergedCount}건 병합` : "")
      + ` — 아직 저장 전입니다. 되돌리려면 "되돌리기"를 누르세요.`);
  };

  // 교정 없이 인접 중복만 정리 — 라벨은 맞는데 갈라진 경우(예: 사용자가 오독을
  // 수동 교정했지만 병합은 안 한 경우) 한 번에 합친다.
  const mergeDuplicates = () => {
    const mergedSegs = mergeAdjacentSameLabel(segments);
    const n = segments.length - mergedSegs.length;
    if (n === 0) { setNotice("인접 중복이 없습니다."); return; }
    setUndoSnapshot(segments);
    setEditUndo([]);
    setSegments(mergedSegs);
    setNotice(`인접 중복 ${n}건을 병합했습니다 — 저장 전입니다.`);
  };

  // 오독 갈라짐 정리 — 앞뒤 같은 라벨로 둘러싸인 짧은 구간(확정 오독)을 흡수한다.
  // 라벨 교정이 안 되는 접두 유실 오독도 처리한다(시퀀스에서 특히 유효 — 실기
  // 시퀀스 79개 중 28곳이 이 형태였다). 5초 이하만 흡수해 진짜 비단조는 보존.
  const FLANK_MAX_MS = 5000;
  const flankedCount = segments.length
    - absorbFlankedMisreads(segments, FLANK_MAX_MS).length;
  const cleanFlanked = () => {
    const out = absorbFlankedMisreads(segments, FLANK_MAX_MS);
    const n = segments.length - out.length;
    if (n === 0) { setNotice("정리할 오독 갈라짐이 없습니다."); return; }
    setUndoSnapshot(segments);
    setEditUndo([]);
    setSegments(out);
    setNotice(`오독으로 갈라진 ${n}건을 흡수했습니다 — 저장 전입니다. 되돌리기 가능.`);
  };

  const undoFixes = () => {
    if (!undoSnapshot) return;
    setSegments(undoSnapshot);
    setUndoSnapshot(null);
    setEditUndo([]);
    setNotice("되돌렸습니다.");
  };

  const saveEdits = async () => {
    setBusy(true); setError(null);
    try {
      await overrideSceneSegments(jobId, mode, segments);
      // 저장한 모드만 dirty 해제 — 다른 모드의 미저장 편집은 유지한다.
      setDirtyModes((prev) => {
        const next = new Set(prev); next.delete(mode); return next;
      });
      setEditUndo([]);  // 저장하면 편집 되돌리기 히스토리 초기화(스냅샷은 저장 전 상태)
      const otherDirty = dirtyModes.has(mode === "scene" ? "sequence" : "scene");
      setNotice(otherDirty
        ? `${mode === "scene" ? "씬" : "시퀀스"} 저장 완료 — `
          + `${mode === "scene" ? "시퀀스" : "씬"} 모드에 저장 안 된 수정이 있습니다.`
        : "수정사항을 저장했습니다.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

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

      {/* 슬레이트 구역 — 쇼마다 위치가 다르므로 스캔 전에 잡아두면 판독이 빠르고
          정확하다. 스캔 후에도 다시 잡을 수 있다(다시 스캔해야 반영). */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" style={consoleStyles.mutedAction}
          onClick={() => setShowPicker((v) => !v)}>
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
            onChange={(e) => setScanMethod(e.target.value as SceneMethod)}
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
                onChange={(e) => setScanIntervalS(Number(e.target.value))}
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
              <input value={minSceneSec} onChange={(e) => setMinSceneSec(e.target.value)}
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
          onChange={setOcrRegion} templates={templates}
          onTemplatesChange={setTemplates}
          rule={{ delimiters, seq_tokens: seqIdx, scene_tokens: sceneIdx,
                  scan_interval_s: scanIntervalS, method: scanMethod }}
          onApplyTemplate={applyTemplate} />
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
            onClick={() => void cancelAll()}>중단</button>
        </div>
      ) : null}

      {!data?.scanned ? (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button type="button" style={consoleStyles.action} disabled={busy}
            onClick={() => void runAll({ rescan: true })}>
            {busy ? "실행 중…"
              : scanMethod === "fingerprint" ? "전체 실행 (컷 감지 → 경계)"
              : "전체 실행 (스캔 → 경계 → 정밀화)"}
          </button>
          <button type="button" style={consoleStyles.mutedAction} disabled={busy}
            onClick={() => void runScan()}>스캔만</button>
          <span style={{ fontSize: 12, opacity: 0.65 }}>
            토큰 규칙이 없으면 스캔까지만 하고 멈춥니다(스캔 결과를 보고 고르는 값이라).
          </span>
        </div>
      ) : (
        <>
          {/* 규칙 지정: 토큰 칩 */}
          <div>
            <p style={{ fontSize: 13, opacity: 0.75, margin: "0 0 6px" }}>
              대표 슬레이트: <code>{sample || "(판독 실패)"}</code> — 시퀀스/씬 토큰을 고르세요.
            </p>
            <label style={{ fontSize: 12, opacity: 0.8, display: "inline-flex",
                            alignItems: "center", gap: 5, marginBottom: 8 }}>
              <input type="checkbox" checked={spaceDelim}
                onChange={(e) => {
                  // 구분자가 바뀌면 토큰 경계가 달라져 인덱스 의미가 바뀐다 → 선택 초기화.
                  setSpaceDelim(e.target.checked);
                  setSeqIdx([]); setSceneIdx([]);
                }} />
              공백도 구분자로 나누기 (기본: 공백은 필드 안에 유지)
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
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
              {/* 토큰을 고른 뒤의 주 동작 — 간격 방식은 경계 계산과 정밀화가 항상
                  함께 필요하고, 지문 방식은 경계 계산으로 끝난다(이미 프레임 정확). */}
              <button type="button" style={consoleStyles.action}
                disabled={busy || seqIdx.length === 0}
                onClick={() => void runAll({ rescan: false })}>
                {busy ? "실행 중…"
                  : data.method === "fingerprint" ? "경계 계산 (시퀀스·씬)"
                  : "경계 계산 + 정밀화 (시퀀스·씬)"}
              </button>
              <button type="button" style={consoleStyles.mutedAction}
                disabled={busy || seqIdx.length === 0}
                onClick={() => void applyRule()}>
                경계 계산만
              </button>
              {/* OCR 재판독 — 구역·판독 로직을 바꿨거나 오염된 스캔 복구용. */}
              <button type="button" style={consoleStyles.mutedAction}
                disabled={busy}
                onClick={() => void runAll({ rescan: true })}>
                다시 스캔(전체)
              </button>
              {seqIdx.length === 0 ? (
                <span style={{ fontSize: 12, color: "#e2b340" }}>
                  먼저 위에서 SEQ 토큰을 하나 이상 고르세요 (그래야 버튼이 활성화됩니다).
                </span>
              ) : null}
            </div>
          </div>

          {/* 모드 토글 + 필름스트립 */}
          <div style={{ display: "flex", gap: 16, alignItems: "center", fontSize: 13 }}>
            <label><input type="radio" checked={mode === "scene"}
              onChange={() => setMode("scene")} /> 씬별</label>
            <label><input type="radio" checked={mode === "sequence"}
              onChange={() => setMode("sequence")} /> 시퀀스별</label>
            <span style={{ opacity: 0.7 }}>{segments.length}개 구간</span>
            {/* 경계 정밀화 — 2초 샘플링의 ±1초 잔여를 프레임 단위로 좁힌다(경계마다
                이진탐색 OCR이라 수 분 소요, 현재 모드만). 지문 방식은 경계가 이미
                프레임 정확이라 버튼 자체가 없다(서버도 409로 막는다). */}
            {data.method !== "fingerprint" ? (
              <button type="button" style={consoleStyles.mutedAction}
                disabled={busy || segments.length < 2}
                onClick={() => void doRefine()}>
                {refineProg?.refining
                  ? `정밀화 중… ${refineProg.done}/${refineProg.total}`
                  : "경계 정밀화 (프레임 단위)"}
              </button>
            ) : null}
          </div>
          {refineProg?.refining ? (
            <div style={{ height: 6, borderRadius: 3, background: "rgba(255,255,255,0.12)" }}>
              <div style={{ height: 6, borderRadius: 3,
                            width: `${refineProg.total > 0
                              ? Math.round((refineProg.done / refineProg.total) * 100) : 0}%`,
                            background: "#4a9eda", transition: "width 0.3s" }} />
            </div>
          ) : null}
          {/* 구간 목록 탭 — 오독 의심 행만 모아 일괄 교정할 수 있게 한다. */}
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button type="button"
              style={(onlyAnomalies || onlyBoundaryErrors)
                ? consoleStyles.mutedAction : consoleStyles.action}
              onClick={() => {
                setOnlyAnomalies(false); setOnlyBoundaryErrors(false);
                setSelectedSeg(null);
              }}>
              전체 ({segments.length})
            </button>
            <button type="button"
              style={onlyAnomalies ? consoleStyles.action : consoleStyles.mutedAction}
              disabled={anomalies.length === 0}
              onClick={() => {
                setOnlyAnomalies(true); setOnlyBoundaryErrors(false);
                setSelectedSeg(null);
              }}>
              {anomalies.length > 0
                ? `⚠ 확인 필요 (${anomalies.length})` : "확인 필요 없음"}
            </button>
            {/* 경계 오류(혼입) — 씬 모드 전용. 머리/꼬리 프레임에 이웃 슬레이트가
                잡힌 구간만 모아 본다(runAll 마지막 단계가 채운다). */}
            {mode === "scene" ? (
              <button type="button"
                style={onlyBoundaryErrors ? consoleStyles.action : consoleStyles.mutedAction}
                disabled={boundaryCount === 0}
                onClick={() => {
                  setOnlyBoundaryErrors(true); setOnlyAnomalies(false);
                  setSelectedSeg(null);
                }}>
                {boundaryCount > 0
                  ? `⚠ 경계 오류 (${boundaryCount})` : "경계 오류 없음"}
              </button>
            ) : null}
            {/* 고친 뒤 재검증 — 현재 세그먼트 그대로 경계만 다시 OCR 검사(세그먼트
                재계산 없음). 편집한 씬은 즉시 필터에서 빠지고, 이 버튼으로 전체를
                다시 확인할 수 있다(미저장 편집은 자동 저장 후 검사). */}
            {mode === "scene" && (data?.scanned ?? false) ? (
              <button type="button" style={consoleStyles.mutedAction}
                disabled={busy}
                onClick={() => void recheckBoundaries()}>🔄 경계 다시 검사</button>
            ) : null}
            {/* 라벨 검색 — 슬레이트 번호 일부만 쳐도 좁혀진다(대소문자·구분자 무시).
                400+ 줄을 스크롤로 훑는 대신 쓰는 주 경로. */}
            <label style={{ display: "inline-flex", alignItems: "center", gap: 4,
                            fontSize: 12, opacity: 0.85 }}>
              검색
              <input value={labelQuery}
                onChange={(e) => setLabelQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key !== "Enter") return;
                  // Enter로 첫 결과를 고르고 포커스를 뺀다 — 입력칸에 포커스가 있으면
                  // 방향키가 캐럿 이동이라 ←/→ 훑기가 안 먹는다(검색 직후가 바로
                  // 그 상황이다). 검색 → Enter → ←/→로 이어지게 한다.
                  e.preventDefault();
                  const first = visibleAll[0];
                  if (first != null) setSelectedSeg(first);
                  e.currentTarget.blur();
                }}
                placeholder="씬 번호 일부"
                style={{ width: 130, fontSize: 12, padding: "4px 6px", borderRadius: 4,
                         fontFamily: "monospace", background: "rgba(255,255,255,0.08)",
                         color: "inherit", border: "1px solid rgba(255,255,255,0.2)" }} />
            </label>
            {labelQuery ? (
              <>
                <button type="button" style={consoleStyles.mutedAction}
                  title="검색 지우기" onClick={() => setLabelQuery("")}>×</button>
                <span style={{ fontSize: 12, opacity: 0.7 }}>
                  {visibleIndices?.length ?? 0}개 표시
                  {(visibleIndices?.length ?? 0) === 0 ? " — 일치하는 씬이 없어요" : ""}
                </span>
              </>
            ) : null}
            {onlyAnomalies && anomalies.some((a) => a.suggestion && a.confident) ? (
              <button type="button" style={consoleStyles.mutedAction}
                onClick={openFixPreview}>
                제안 일괄 적용 ({anomalies.filter((a) => a.suggestion && a.confident).length})…
              </button>
            ) : null}
            {/* 오독 갈라짐 정리 — 앞뒤 동일 라벨 사이 낀 오독을 흡수(시퀀스 특효). */}
            {flankedCount > 0 ? (
              <button type="button" style={consoleStyles.action}
                onClick={cleanFlanked}>
                오독 갈라짐 정리 ({flankedCount})
              </button>
            ) : null}
            {/* 인접 중복 병합 — 교정으로 같아졌거나 수동 교정 후 갈라진 씬을 합친다. */}
            {mergeAdjacentSameLabel(segments).length < segments.length ? (
              <button type="button" style={consoleStyles.mutedAction}
                onClick={mergeDuplicates}>
                인접 중복 병합 ({segments.length - mergeAdjacentSameLabel(segments).length})
              </button>
            ) : null}
            {undoSnapshot ? (
              <button type="button" style={consoleStyles.mutedAction}
                onClick={undoFixes}>되돌리기</button>
            ) : null}
          </div>

          {/* 일괄 적용 확인 — 무엇이 어떻게 바뀌는지 보고 체크한 것만 적용한다. */}
          {pendingFixes ? (
            <div style={{ border: "1px solid rgba(255,255,255,0.15)", borderRadius: 6,
                          padding: 10, display: "flex", flexDirection: "column", gap: 6 }}>
              <strong style={{ fontSize: 13 }}>
                이렇게 바꿉니다 — 체크한 것만 적용됩니다 ({fixChecked.size}/{pendingFixes.length})
              </strong>
              <div style={{ maxHeight: 260, overflowY: "auto", display: "flex",
                            flexDirection: "column", gap: 3 }}>
                {pendingFixes.map((f) => (
                  <label key={f.index}
                         style={{ display: "flex", gap: 8, alignItems: "center",
                                  fontSize: 12, fontFamily: "monospace",
                                  padding: "3px 4px", borderRadius: 3,
                                  background: "rgba(255,255,255,0.04)" }}>
                    <input type="checkbox" checked={fixChecked.has(f.index)}
                      onChange={(e) => {
                        const next = new Set(fixChecked);
                        if (e.target.checked) next.add(f.index); else next.delete(f.index);
                        setFixChecked(next);
                      }} />
                    <span style={{ opacity: 0.55, flexShrink: 0 }}>
                      {formatMs(segments[f.index]?.start_ms ?? 0)}
                    </span>
                    <span style={{ color: "#e2b340", overflowWrap: "anywhere" }}>{f.from}</span>
                    <span style={{ opacity: 0.6, flexShrink: 0 }}>→</span>
                    <span style={{ color: "#3f9a5f", overflowWrap: "anywhere" }}>{f.to}</span>
                  </label>
                ))}
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <button type="button" style={consoleStyles.action}
                  disabled={fixChecked.size === 0}
                  onClick={confirmFixes}>적용 ({fixChecked.size})</button>
                <button type="button" style={consoleStyles.mutedAction}
                  onClick={() => setPendingFixes(null)}>취소</button>
                <span style={{ fontSize: 11, opacity: 0.6 }}>
                  적용해도 저장 전이라 "되돌리기"로 한 번 물릴 수 있습니다.
                </span>
              </div>
            </div>
          ) : null}
          {onlyAnomalies ? (
            <p style={{ fontSize: 12, opacity: 0.7, margin: 0 }}>
              라벨 모양이 다수와 어긋나는 구간입니다(주로 OCR이 구분자를 놓친 경우).
              제안이 있으면 라벨 오른쪽에 표시되고, 숫자가 남아 애매한 제안은
              일괄 적용에서 빠집니다 — 썸네일을 눌러 실제 프레임을 확인하세요.
            </p>
          ) : null}
          {onlyBoundaryErrors ? (
            <p style={{ fontSize: 12, opacity: 0.7, margin: 0 }}>
              경계(머리·꼬리) 프레임에 이웃 씬의 슬레이트가 잡힌 구간입니다 —
              익스포트 시 앞뒤 씬이 한두 프레임 섞일 수 있습니다. 썸네일을 눌러 실제
              경계 프레임을 확인하고, 필요하면 병합하거나 경계를 조정하세요.
              확인했는데 문제가 없으면 <b>✓ 문제없음</b>으로 목록에서 뺄 수 있습니다.
              {boundaryOkCount > 0 ? (
                <>
                  {"  "}확인함 {boundaryOkCount}건 ·{" "}
                  <button type="button" style={consoleStyles.mutedAction}
                    title="확인 표시를 전부 지우고 처음부터 다시 봅니다"
                    onClick={() => void putBoundaryOk([])}>모두 해제</button>
                </>
              ) : null}
            </p>
          ) : null}
          <SceneFilmstrip jobId={jobId} segments={segments}
            thumbCount={thumbCount}
            intervalMs={thumbIntervalMs}
            totalMs={data.total_ms
              ?? ((data.frames.at(-1)?.t_ms ?? 0) + intervalMs)}
            onMerge={mergeSeg} onRename={renameSeg}
            // 리스트 줄의 되돌리기는 스택 top이 '병합'일 때만 뜬다 — 경계 교정은
            // 팝업에서 물린다(같은 스택, 엄격 LIFO).
            undoIndex={editUndo.at(-1)?.kind === "merge"
              ? editUndo.at(-1)!.survivor : null}
            onUndoMerge={undoEdit}
            onExportOne={exportOne} exportingIndex={exportingOne}
            exportDisabled={busy || Boolean(exportProg?.exporting)}
            // 경계오류 탭에서만 '✓ 문제없음'을 띄운다 — 다른 탭 줄에는 필요 없다.
            onBoundaryOk={onlyBoundaryErrors ? markBoundaryOk : undefined}
            selectedIndex={selectedSeg} highlight={highlight}
            visibleIndices={visibleIndices}
            suggestions={suggestionOf}
            videoFps={data.video_fps ?? undefined}
            onSelectSegment={setSelectedSeg}
            onStepSegment={stepSegment}
            onClearSelection={() => setSelectedSeg(null)}
            onThumbClick={(seekMs, seg, segIndex, side) => {
              if (!seg || segIndex == null) { setPreview({ seekMs }); return; }
              setPreview(buildSegPreview(seg, segIndex, seekMs, side ?? "head"));
            }} />
          {/* 저장·익스포트를 하단에 고정 — 400+ 리스트를 끝까지 스크롤하지 않아도
              항상 접근 가능하게. */}
          <div style={{ position: "sticky", bottom: 0, zIndex: 5,
                        background: "var(--ys-bg-app)", marginTop: 4,
                        paddingTop: 10, paddingBottom: 4,
                        borderTop: "1px solid rgba(255,255,255,0.08)" }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button type="button" style={consoleStyles.mutedAction}
              disabled={busy || !dirty}
              onClick={() => void saveEdits()}>
              {dirty ? "수정사항 저장" : "저장됨"}
            </button>
            <button type="button" style={consoleStyles.action}
              disabled={busy || segments.length === 0}
              onClick={() => void doExport()}>
              {exportProg?.exporting
                ? `익스포트 중… ${exportProg.done}/${exportProg.total}`
                : `${segments.length}개 클립 익스포트`}
            </button>
            {dirty ? (
              <span style={{ fontSize: 12, color: "#e2b340" }}>
                저장 안 된 수정이 있어요 — 익스포트 전에 저장하세요.
              </span>
            ) : null}
          </div>
          {exportProg?.exporting ? (
            <div>
              <div style={{ height: 6, borderRadius: 3, background: "rgba(255,255,255,0.12)" }}>
                <div style={{ height: 6, borderRadius: 3,
                              width: `${exportProg.total > 0
                                ? Math.round((exportProg.done / exportProg.total) * 100) : 0}%`,
                              background: "#4a9eda", transition: "width 0.3s" }} />
              </div>
              <p style={{ fontSize: 12, opacity: 0.7, margin: "4px 0 0" }}>
                클립 재인코딩 중… {exportProg.done}/{exportProg.total}
                {exportProg.out_dir ? ` → ${exportProg.out_dir}` : ""}
              </p>
            </div>
          ) : null}
          </div>
        </>
      )}

      {/* 썸네일 클릭 팝업 — 실제 영상을 그 시각으로 시킹해 크게 보여준다. 구간에서
          열면(머리·꼬리 클릭) 재생을 그 구간 [start,end)로 묶어, 소스 전체가 아니라
          그 컷만 재생·반복하며 분할 경계를 확인할 수 있다. 배경/닫기 클릭 시 닫힘. */}
      {preview != null ? (
        <div onClick={() => setPreview(null)}
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
                  onClick={(e) => { e.stopPropagation(); stepPreviewSegment(delta); }}
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
            <video ref={previewVideoRef}
              src={videoMediaUrl(jobId)} autoPlay={false}
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
                        const v = previewVideoRef.current;
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
                      onClick={() => setLoopSeg((l) => !l)}>
                      {loopSeg ? "🔁 반복 켜짐" : "반복 꺼짐"}
                    </button>
                  ) : null}
                  <button type="button" style={consoleStyles.mutedAction}
                    onClick={() => setPreview(null)}>닫기</button>
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
                const top = editUndo.at(-1);
                const canUndo = (top?.kind === "boundary" || top?.kind === "split")
                  && top.survivor === i;
                return (
                  <div style={{ display: "flex", gap: 6, alignItems: "center",
                                flexWrap: "wrap", fontSize: 12 }}>
                    <span style={{ opacity: 0.7 }}>현재 프레임 기준</span>
                    <button type="button" style={editBtn}
                      disabled={i === 0 || inFrames === 0}
                      title="지금 보는 프레임을 이 씬의 첫 프레임으로 — 앞 프레임은 이전 씬으로 넘어갑니다 (단축키 I)"
                      onClick={() => trimAt("in", previewMs)}>
                      ◀ 여기부터(I) · 앞 {inFrames}f → 이전 씬</button>
                    <button type="button" style={editBtn}
                      disabled={i >= segments.length - 1 || outFrames === 0}
                      title="지금 보는 프레임을 이 씬의 마지막 프레임으로 — 뒤 프레임은 다음 씬으로 넘어갑니다 (단축키 O)"
                      onClick={() => trimAt("out", previewMs)}>
                      여기까지(O) · 뒤 {outFrames}f → 다음 씬 ▶</button>
                    {/* 한 줄에 두 씬이 붙어 있을 때 여기서 가른다 — 지금 보는 프레임이
                        뒤 씬의 첫 프레임이 된다(In 트림과 같은 약속). 첫 프레임에서는
                        앞 구간이 0프레임이라 잠근다. */}
                    <button type="button" style={editBtn}
                      disabled={k <= 1}
                      title="지금 보는 프레임부터 새 씬으로 나눕니다 — 앞쪽이 새 씬이 되고 이름은 슬레이트를 읽어 채웁니다 (단축키 S)"
                      onClick={() => void splitAt(previewMs)}>
                      ✂ 여기서 나누기(S) · 앞 {k - 1}f | 뒤 {n - k + 1}f</button>
                    {canUndo ? (
                      <button type="button"
                        style={{ ...editBtn, color: "#6db6ff",
                                 borderColor: "rgba(109,182,255,0.5)" }}
                        title="방금 경계 교정을 되돌립니다"
                        onClick={undoEdit}>↩되돌리기</button>
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
                      onChange={(e) => setNudgeFrames(
                        Math.max(1, Math.floor(Number(e.target.value) || 1)))}
                      style={{ width: 46, fontSize: 12, padding: "3px 5px", borderRadius: 4,
                               textAlign: "right", background: "rgba(255,255,255,0.10)",
                               color: "#fff", border: "1px solid rgba(255,255,255,0.25)" }} />
                    프레임씩:
                  </label>
                  <button type="button" style={editBtn} disabled={preview.segIndex === 0}
                    onClick={() => nudgeBoundary("head", nudgeFrames)}>
                    머리 {nudgeFrames}f → 이전 씬</button>
                  <button type="button" style={editBtn} disabled={preview.segIndex === 0}
                    onClick={() => nudgeBoundary("head", -nudgeFrames)}>
                    이전 씬 → 머리 {nudgeFrames}f</button>
                  <span style={{ opacity: 0.3, padding: "0 2px" }}>|</span>
                  <button type="button" style={editBtn}
                    disabled={preview.segIndex >= segments.length - 1}
                    onClick={() => nudgeBoundary("tail", -nudgeFrames)}>
                    꼬리 {nudgeFrames}f → 다음 씬</button>
                  <button type="button" style={editBtn}
                    disabled={preview.segIndex >= segments.length - 1}
                    onClick={() => nudgeBoundary("tail", nudgeFrames)}>
                    다음 씬 → 꼬리 {nudgeFrames}f</button>
                  {dirty ? (
                    <span style={{ color: "#e2b340", marginLeft: 4 }}>· 저장 필요</span>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
