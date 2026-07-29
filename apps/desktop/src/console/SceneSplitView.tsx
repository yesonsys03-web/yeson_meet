import { useEffect, useRef, useState } from "react";
import { consoleStyles } from "./consoleStyles";
import { hasTauriRuntime } from "./useQrFullscreenShortcut";
import {
  absorbFlankedMisreads, anomalousLabels, applyFixes, applySplitName, boundaryIssueIndices, confidentFixes, effectiveFps, filterIndices, frameSeekMs, mergeAdjacentSameLabel, mergeSegment, exportedFileName, neighborIndices, nudgeSegments, probeFileName, probeToken, scanProgressKey, segPreviewFor, stepVisibleIndex, upsertBoundaryOk,
  NTSC_FPS, prefixRenameFixes, previewLabel, renameSegment, segFrameNumber, segmentTailMs, segmentThumbRange, splitSegment, tokenizeSlate, trimFrames,
  type LabelFix, type SegPreview,
} from "./sceneSplitLogic";
import { ScenePreviewPopup, type ScenePreviewPopupHandle } from "./ScenePreviewPopup";
import { SceneScanControls } from "./SceneScanControls";
import { SceneListSection } from "./SceneListSection";
import {
  cancelSceneOps, exportScenes, getBoundaryStatus, getExportStatus, getRefineStatus,
  cleanupSceneExport, sceneExportFileUrl, probeExportDir, saveBoundaryOk,
  getScenes, listSlateTemplates, overrideSceneSegments, refineScenes, scanScenes,
  setOcrRegion as setOcrRegionApi, setSceneRule, startBoundaryCheck, testOcrRegion,
  videoMediaUrl,
  type BoundaryOk, type BoundaryStatus, type ExportStatus, type OcrRegion, type RefineStatus,
  type SceneMethod, type ScenesData, type SceneSegment, type SlateTemplate,
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
  // 공백을 필드 구분자로 쓰는 슬레이트 대응(기본은 공백 비분해 — 백엔드와 동일).
  const [spaceDelim, setSpaceDelim] = useState(false);
  // 예시 슬레이트 한 줄 — 선언하면 서버가 머리글자(Seq↔Seg류 닮은꼴 오독)를
  // 다수결 대신 이 구조로 스냅한다. 빈값=기존 동작(옵트인).
  const [slateExample, setSlateExample] = useState("");
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
      setSlateExample(d.rule.example ?? "");
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
    const token = probeToken(crypto.getRandomValues(new Uint8Array(8)));
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
      // 직접 모드든 중계든 끝나면 같은 말을 한다 — 문구를 한 번만 적어 두 경로가
      // 갈라지지 않게 한다(사용자에겐 저장된 결과가 같다).
      const savedMsg = (n: number) =>
        `${n}개 클립 저장 완료 — ${labels} (${saveDir}). `
        + "경계를 공유한 이웃 씬까지 갱신했습니다.";
      const st = await pollExport((s) => direct
        ? savedMsg(s.files?.length ?? res.count)
        : `${res.count}개 클립을 구웠습니다 — ${labels}. 저장 중…`);
      if (!st) return;
      // 직접 모드는 서버가 이미 사용자 폴더에 썼다 — 받을 것도, 지울 사본도 없다.
      if (direct) return;
      if (saveDir) {
        await saveExportedFiles(st.files ?? [], saveDir);
        setNotice(savedMsg(st.files?.length ?? 0));
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
      // 직접 모드든 중계든 끝나면 같은 말을 한다 — 문구를 한 번만 적어 두 경로가
      // 갈라지지 않게 한다(사용자에겐 저장된 결과가 같다).
      const savedMsg = (n: number) => `${n}개 클립 저장 완료 (${saveDir})`;
      const st = await pollExport((s) => direct
        ? savedMsg(s.files?.length ?? res.count)
        : `${res.count}개 클립을 구웠습니다. 저장 중…`);
      if (!st) return;
      // 직접 모드는 서버가 이미 사용자 폴더에 썼다 — 받을 것도, 지울 사본도 없다.
      if (direct) return;
      if (saveDir) {
        await saveExportedFiles(st.files ?? [], saveDir);
        setNotice(savedMsg(st.files?.length ?? 0));
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
    void putBoundaryOk(upsertBoundaryOk(data?.boundary_ok ?? [],
      { label: seg.label, start_ms: seg.start_ms, end_ms: seg.end_ms }));
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
      const fps = effectiveFps(data.video_fps);
      const side = p.side ?? "head";
      const focusMs = side === "tail"
        ? frameSeekMs(segmentTailMs(restored.start_ms, restored.end_ms, fps), fps)
        : frameSeekMs(restored.start_ms, fps);
      setPreview(buildSegPreview(restored, top.survivor, focusMs, side));
      popupRef.current?.pauseAndSeek(focusMs);
    }
  };
  const renameSeg = (i: number, label: string) => {
    clearBoundaryFlags([segments[i]?.label]);  // 이름 바꾼 씬은 플래그 해제(라벨도 바뀜)
    setSegments(renameSegment(segments, i, label));
  };

  // 리스트에서 클릭한 구간 → 필름스트립 하이라이트 범위. 썸네일 클릭 → 팝업 시각.
  const [selectedSeg, setSelectedSeg] = useState<number | null>(null);
  // 슬레이트 구역 — 쇼마다 위치가 달라 사용자가 드래그로 지정한다. 스캔 전에도
  // 스캔 후에도 다시 잡을 수 있다(다시 잡으면 재스캔해야 반영된다). 분할 시
  // 슬레이트 읽기(splitAt)가 쓴다. (예전엔 팝업 단축키 effect의 의존성 배열이
  // 이 값을 읽어 선언 순서가 렌더 중 TDZ 함정이었다 — 그 effect는 이제
  // ScenePreviewPopup 몫이라 순서 제약은 없다.)
  const [ocrRegion, setOcrRegion] = useState<OcrRegion | null>(null);
  // 팝업 프리뷰 — 값 구성과 필드 설명은 sceneSplitLogic의 SegPreview/segPreviewFor.
  // 플레이어(영상·재생 상태·감시 루프·단축키)는 ScenePreviewPopup이 소유하고,
  // 여기는 '무엇을 보여줄지'(preview)와 편집 콜백만 든다. 편집 직후 영상을 편집한
  // 프레임에 멈춰 세우는 것은 popupRef.pauseAndSeek로 지시한다.
  const [preview, setPreview] = useState<SegPreview | null>(null);
  const popupRef = useRef<ScenePreviewPopupHandle>(null);
  // 구간 반복재생(기본 꺼짐) — 완료 시 꼬리 프레임에 정지해 꼬리를 확인하게 한다.
  // 켜면 [첫 프레임, 꼬리 프레임]을 반복한다. 팝업을 닫았다 열어도 유지되도록
  // 부모 상태로 둔다(nudgeFrames도 같은 이유).
  const [loopSeg, setLoopSeg] = useState(false);
  // 경계 교정 시 한 번에 옮길 프레임 수 — 디졸브/와이프는 9프레임 이상 어긋나기도
  // 해서 한 클릭에 N프레임 이동. 미세조정은 1로.
  const [nudgeFrames, setNudgeFrames] = useState(1);
  // 편집 함수들이 비동기 완료 시점에 최신 preview를 읽도록 ref로 미러링.
  const previewRef = useRef(preview);
  useEffect(() => { previewRef.current = preview; }, [preview]);

  // 프리뷰(팝업) 상태를 세그먼트로부터 구성 — 계산은 segPreviewFor(순수), 여기는
  // 현재 잡의 fps만 공급한다.
  const buildSegPreview = (s: SceneSegment, segIndex: number, seekMs: number,
                           side: "head" | "tail") =>
    segPreviewFor(s, segIndex, seekMs, side, effectiveFps(data?.video_fps));

  // 팝업에서 머리/꼬리 경계를 delta 프레임 이동 — 그 프레임을 이웃 씬으로 넘기거나
  // 이웃에서 가져온다(스캔이 못 잡는 디졸브/와이프 수동 교정). 클램프·경계 공유
  // 갱신·focusMs 계산은 nudgeSegments(순수)가 담당하고, 여기는 상태 갱신과 팝업
  // 시킹만 한다. 경계가 프레임 정렬을 유지하므로 익스포트도 프레임 정확.
  const nudgeBoundary = (side: "head" | "tail", delta: number) => {
    if (!data || preview?.segIndex == null || delta === 0) return;
    const i = preview.segIndex;
    const moved = nudgeSegments(segments, i, side, delta,
                                effectiveFps(data.video_fps));
    if (!moved) return;
    // 되돌리기용: 교정 전 세그먼트·경계플래그를 병합과 같은 스택에 쌓는다. In/Out
    // 트림은 한 클릭이라 오조작이 쉬운 만큼 되돌릴 수 있어야 한다.
    setEditUndo((prev) => [
      ...prev,
      { kind: "boundary", segs: segments, issues: data.boundary_issues, survivor: i }]);
    setSegments(moved.segs);  // dirty — "수정사항 저장" 후 익스포트에 반영
    // 교정한 씬(+맞닿은 이웃)의 경계 오류 플래그를 뺀다 — 고쳤으면 필터에서 빠져야.
    clearBoundaryFlags([moved.segs[i]!.label,
                        side === "tail" ? moved.segs[i + 1]?.label
                                        : moved.segs[i - 1]?.label]);
    setPreview(buildSegPreview(moved.segs[i]!, i, moved.focusMs, side));
    popupRef.current?.pauseAndSeek(moved.focusMs);
  };
  // 편집 프로그램식 In/Out 트림 — 지금 보고 있는 프레임을 이 씬의 첫(In)/마지막(Out)
  // 프레임으로 확정한다. 사용자가 프레임 카운터를 읽어 '프레임씩' 칸에 옮겨 적던
  // 계산을 여기서 대신 한다(오입력 제거). 경계 이동은 nudgeBoundary가 그대로 담당.
  // ms(기준 프레임 시각)는 팝업이 넘긴다 — 버튼은 카운터 값, 단축키는 영상 현재 시각.
  const trimAt = (side: "in" | "out", ms: number) => {
    const p = previewRef.current;
    if (!p || p.segIndex == null || p.startMs == null || p.endMs == null) return;
    const { k, n } = segFrameNumber(ms, p.startMs, p.endMs, p.fps);
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
  const splitAt = async (ms: number) => {
    const p = previewRef.current;
    if (!p || p.segIndex == null || p.startMs == null || p.endMs == null) return;
    const i = p.segIndex;
    const cur = segments[i];
    if (!cur) return;
    const fps = p.fps || NTSC_FPS;
    const { k } = segFrameNumber(ms, p.startMs, p.endMs, fps);
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
    popupRef.current?.pauseAndSeek(focusMs);
    setNotice("씬을 나눴습니다 — 앞 구간 이름을 읽는 중…");
    // 읽기 실패는 두 갈래로 온다(빈 결과 · 예외). 사용자에겐 같은 상황이므로 같은
    // 문구를 쓰고, 어느 줄을 고쳐야 하는지 이름을 짚어 준다.
    const unreadMsg = `앞 구간 슬레이트를 읽지 못했습니다 — '${head.label}' 줄의 `
      + "이름을 직접 입력하세요.";
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
      if (proposed && next.some((s, j) => j !== i && s.label === proposed)) {
        // 읽은 이름이 이미 목록에 있다 = 첫 나누기면 앞뒤가 같은 번호(나눌 자리가
        // 아니었을 가능성), _cut 줄을 이어 나눴으면 뒤쪽 원래 줄과의 중복이다.
        // 어느 쪽이든 얹으면 중복 이름이 되살아나므로(_cut을 붙인 이유) 자리표시자를
        // 남긴다. cur.label만 비교하면 이어 나누기가 빠져나간다(실기 2026-07-28:
        // _cut이 사라지고 같은 이름 두 줄). applySplitName이 최신 상태로 2차 방어.
        setNotice(`읽은 번호(${proposed})가 이미 목록에 있습니다 — 나눌 자리가 맞는지 `
          + `확인하세요. 맞다면 '${head.label}' 줄의 이름을 직접 고치면 됩니다.`);
      } else if (proposed && proposed !== head.label) {
        // 이름은 반드시 '지금 상태' 위에서 바꾼다. setSegments·renameSeg는 렌더 시점의
        // segments/data를 닫아두므로, OCR을 기다린 뒤 그대로 부르면 분할 전 배열이
        // 되살아나 방금 나눈 줄이 목록에서 통째로 사라진다(실기 재현 2026-07-28).
        setData((prev) => {
          if (!prev) return prev;
          const cur = mode === "sequence"
            ? prev.segments_sequence : prev.segments_scene;
          const named = applySplitName(cur, i, head.label, proposed);
          if (named === cur) return prev;   // 그 사이 다른 편집 — 건드리지 않는다
          return mode === "sequence"
            ? { ...prev, segments_sequence: named }
            : { ...prev, segments_scene: named };
        });
        // 팝업 머리글도 새 이름으로 — 화면과 목록이 어긋나면 사용자가 또 고친다.
        setPreview(buildSegPreview({ ...head, label: proposed }, i, focusMs, "head"));
        setNotice(`앞 구간 이름을 ${proposed}으로 읽었습니다 — 다르면 이름칸에서 고치세요.`);
      } else {
        setNotice(unreadMsg);
      }
    } catch {
      setNotice(unreadMsg);
    }
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
    const fps = effectiveFps(data.video_fps);
    const focusMs = side === "tail"
      ? frameSeekMs(segmentTailMs(seg.start_ms, seg.end_ms, fps), fps)
      : frameSeekMs(seg.start_ms, fps);
    setPreview(buildSegPreview(seg, next, focusMs, side));
    setSelectedSeg(next);
    popupRef.current?.pauseAndSeek(focusMs);
  };

  // ←/→로 이전·다음 씬. 선택이 바뀌면 sticky 검수 뷰의 머리·꼬리 프레임이 갱신되고
  // 목록도 그 줄로 스크롤돼(SceneFilmstrip) 스크롤 조작이 아예 필요 없다. 입력칸
  // 포커스(라벨 수정·검색)와 팝업이 열린 동안은 무시한다 — 팝업이 열려 있으면
  // ←/→는 프레임 한 칸이고 그건 팝업(ScenePreviewPopup)의 몫이다. 검수 단축키
  // (I/O/S/G/H/[/])도 팝업이 등록한다.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (preview != null) return;  // 팝업이 열려 있으면 팝업 핸들러가 처리
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
                || t.isContentEditable)) return;
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();  // 스크롤 컨테이너의 가로 스크롤 기본동작 차단
      stepSegment(e.key === "ArrowRight" ? 1 : -1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [preview, segments, visibleIndices, selectedSeg]);

  // 일괄 적용은 곧바로 바꾸지 않는다 — 무엇이 어떻게 바뀌는지 before→after로
  // 먼저 보여주고, 체크한 것만 적용한다. 적용 후에도 한 번은 되돌릴 수 있다.
  const [pendingFixes, setPendingFixes] = useState<LabelFix[] | null>(null);
  const [fixChecked, setFixChecked] = useState<Set<number>>(new Set());
  const [undoSnapshot, setUndoSnapshot] = useState<SceneSegment[] | null>(null);
  // 일괄 이름 바꾸기(접두 치환) 입력 — 자동 제안이 못 다루는 '다른 단어' 급
  // 접두(실기 EASA06 Scene12_* 26건)를 사용자가 지정해 한 번에 바꾼다.
  const [renameFrom, setRenameFrom] = useState("");
  const [renameTo, setRenameTo] = useState("");

  // 모드가 바뀌면 구간 목록 자체가 달라진다 — 이전 모드에서 만든 미리보기·되돌리기
  // 스냅샷·필터·선택은 모두 무의미해지므로 지운다(씬별 목록이 시퀀스별 화면에
  // 남아 보이던 문제). applyFixes의 from 검사가 2차 방어선이다.
  useEffect(() => {
    setPendingFixes(null);
    setFixChecked(new Set());
    setUndoSnapshot(null);
    setRenameFrom("");
    setRenameTo("");
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
    setSlateExample(t.example ?? "");
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

  // 접두 치환도 같은 미리보기·적용 경로를 탄다 — 확인 다이얼로그, 체크 선별,
  // 되돌리기 스냅샷, 인접 동일 라벨 병합(confirmFixes)이 전부 공유된다.
  const renameFixes = prefixRenameFixes(
    segments.map((s) => s.label), renameFrom, renameTo);
  const openRenamePreview = () => {
    if (renameFixes.length === 0) return;
    setPendingFixes(renameFixes);
    setFixChecked(new Set(renameFixes.map((f) => f.index)));
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

      {/* 스캔 설정·실행 툴바 — 구역/방식/간격, 진행 표시, 실행 버튼, 토큰 규칙. */}
      <SceneScanControls
        jobId={jobId} busy={busy} scanned={Boolean(data?.scanned)}
        method={data?.method} stage={stage} refineProg={refineProg}
        sample={sample} tokens={tokens} sampleMs={sampleMs}
        delimiters={delimiters}
        templates={templates} onTemplatesChange={setTemplates}
        showPicker={showPicker} onTogglePicker={() => setShowPicker((v) => !v)}
        ocrRegion={ocrRegion} onOcrRegionChange={setOcrRegion}
        scanMethod={scanMethod} onScanMethodChange={setScanMethod}
        scanIntervalS={scanIntervalS} onScanIntervalChange={setScanIntervalS}
        minSceneSec={minSceneSec} onMinSceneSecChange={setMinSceneSec}
        spaceDelim={spaceDelim}
        onSpaceDelimToggle={(checked) => {
          // 구분자가 바뀌면 토큰 경계가 달라져 인덱스 의미가 바뀐다 → 선택 초기화.
          setSpaceDelim(checked);
          setSeqIdx([]); setSceneIdx([]);
        }}
        slateExample={slateExample} onSlateExampleChange={setSlateExample}
        seqIdx={seqIdx} sceneIdx={sceneIdx}
        onToggleSeq={toggleSeq} onToggleScene={toggleScene}
        onApplyTemplate={applyTemplate}
        onCancelAll={() => void cancelAll()}
        onRunAll={(opts) => void runAll(opts)}
        onRunScan={() => void runScan()}
        onApplyRule={() => void applyRule()}
      />

      {data?.scanned ? (
        <>
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
          {/* 목록 구역 — 필터 탭·검색·일괄 도구·확인 모달·구간 줄. */}
          <SceneListSection
            segments={segments}
            anomaliesCount={anomalies.length}
            onlyAnomalies={onlyAnomalies}
            onlyBoundaryErrors={onlyBoundaryErrors}
            onFilterAll={() => {
              setOnlyAnomalies(false); setOnlyBoundaryErrors(false);
              setSelectedSeg(null);
            }}
            onFilterAnomalies={() => {
              setOnlyAnomalies(true); setOnlyBoundaryErrors(false);
              setSelectedSeg(null);
            }}
            onFilterBoundary={() => {
              setOnlyBoundaryErrors(true); setOnlyAnomalies(false);
              setSelectedSeg(null);
            }}
            showBoundaryTab={mode === "scene"}
            boundaryCount={boundaryCount}
            boundaryOkCount={boundaryOkCount}
            onClearBoundaryOk={() => void putBoundaryOk([])}
            canRecheck={mode === "scene" && (data?.scanned ?? false)}
            busy={busy}
            onRecheckBoundaries={() => void recheckBoundaries()}
            labelQuery={labelQuery}
            onLabelQueryChange={setLabelQuery}
            onSearchEnter={() => {
              const first = visibleAll[0];
              if (first != null) setSelectedSeg(first);
            }}
            visibleIndices={visibleIndices}
            confidentSuggestionCount={
              anomalies.filter((a) => a.suggestion && a.confident).length}
            onOpenFixPreview={openFixPreview}
            renameFrom={renameFrom} renameTo={renameTo}
            onRenameFromChange={setRenameFrom} onRenameToChange={setRenameTo}
            renameFixCount={renameFixes.length}
            onOpenRenamePreview={openRenamePreview}
            flankedCount={flankedCount}
            onCleanFlanked={cleanFlanked}
            adjacentDupCount={segments.length
              - mergeAdjacentSameLabel(segments).length}
            onMergeDuplicates={mergeDuplicates}
            canUndoFixes={Boolean(undoSnapshot)}
            onUndoFixes={undoFixes}
            pendingFixes={pendingFixes}
            fixChecked={fixChecked}
            onFixCheckedChange={setFixChecked}
            onConfirmFixes={confirmFixes}
            onCancelFixes={() => setPendingFixes(null)}
            filmstrip={{
              jobId, segments,
              thumbCount,
              intervalMs: thumbIntervalMs,
              totalMs: data.total_ms
                ?? ((data.frames.at(-1)?.t_ms ?? 0) + intervalMs),
              onMerge: mergeSeg, onRename: renameSeg,
              // 리스트 줄의 되돌리기는 스택 top이 '병합'일 때만 뜬다 — 경계 교정은
              // 팝업에서 물린다(같은 스택, 엄격 LIFO).
              undoIndex: editUndo.at(-1)?.kind === "merge"
                ? editUndo.at(-1)!.survivor : null,
              onUndoMerge: undoEdit,
              onExportOne: exportOne, exportingIndex: exportingOne,
              exportDisabled: busy || Boolean(exportProg?.exporting),
              // 경계오류 탭에서만 '✓ 문제없음'을 띄운다 — 다른 탭 줄에는 필요 없다.
              onBoundaryOk: onlyBoundaryErrors ? markBoundaryOk : undefined,
              selectedIndex: selectedSeg, highlight,
              visibleIndices,
              suggestions: suggestionOf,
              videoFps: data.video_fps ?? undefined,
              onSelectSegment: setSelectedSeg,
              onStepSegment: stepSegment,
              onClearSelection: () => setSelectedSeg(null),
              onThumbClick: (seekMs, seg, segIndex, side) => {
                if (!seg || segIndex == null) { setPreview({ seekMs }); return; }
                setPreview(buildSegPreview(seg, segIndex, seekMs, side ?? "head"));
              },
            }}
          />
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
      ) : null}

      {/* 썸네일 클릭 팝업 — 실제 영상을 그 시각으로 시킹해 크게 보여준다. 구간에서
          열면(머리·꼬리 클릭) 재생을 그 구간 [start,end)로 묶어, 소스 전체가 아니라
          그 컷만 재생·반복하며 분할 경계를 확인할 수 있다. 배경/닫기 클릭 시 닫힘.
          플레이어·단축키는 ScenePreviewPopup이, 편집 콜백은 여기(부모)가 담당. */}
      {preview != null ? (() => {
        const top = editUndo.at(-1);
        return (
          <ScenePreviewPopup
            ref={popupRef}
            src={videoMediaUrl(jobId)}
            preview={preview}
            segments={segments}
            visibleAll={visibleAll}
            dirty={dirty}
            // 리스트 줄과 대칭: 스택 top이 '이 씬의' 경계 교정/분할일 때만 되돌리기.
            canUndo={Boolean(top
              && (top.kind === "boundary" || top.kind === "split")
              && top.survivor === preview.segIndex)}
            loopSeg={loopSeg} onToggleLoop={() => setLoopSeg((l) => !l)}
            nudgeFrames={nudgeFrames} onNudgeFramesChange={setNudgeFrames}
            onClose={() => setPreview(null)}
            onNudge={nudgeBoundary}
            onTrim={trimAt}
            onSplit={(ms) => void splitAt(ms)}
            onStepScene={stepPreviewSegment}
            onUndo={undoEdit}
          />
        );
      })() : null}
    </div>
  );
}
