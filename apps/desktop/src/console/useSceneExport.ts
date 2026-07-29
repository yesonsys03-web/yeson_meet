// 씬 익스포트 훅 — 전체/개별 익스포트, 저장 폴더 선택, 직접 저장 탐침, 진행률
// 폴링, 서버 사본 받기·정리. 화면 상태(data/notice/error/busy)는 부모가 들고
// setter를 주입한다(SceneSplitView 분할 — 로직은 그대로 이동).
import { useState } from "react";
import { hasTauriRuntime } from "./useQrFullscreenShortcut";
import { exportedFileName, neighborIndices, probeFileName, probeToken }
  from "./sceneSplitLogic";
import {
  cleanupSceneExport, exportScenes, getExportStatus, probeExportDir,
  sceneExportFileUrl,
  type ExportStatus, type SceneSegment,
} from "./videoApi";

type Mode = "scene" | "sequence";

export function useSceneExport(opts: {
  jobId: string;
  mode: Mode;
  segments: SceneSegment[];
  // 미저장 편집이 있으면 익스포트를 막는다(서버 저장본을 자르므로).
  dirty: boolean;
  busy: boolean;
  setBusy: (b: boolean) => void;
  setError: (e: string | null) => void;
  setNotice: (n: string | null) => void;
}) {
  const { jobId, mode, segments, dirty, busy, setBusy, setError, setNotice } = opts;
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

  return { exportProg, exportingOne, exportOne, doExport };
}
