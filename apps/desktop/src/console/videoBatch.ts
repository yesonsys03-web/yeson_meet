// 다중 파일/폴더 배치 업로드 로직. 순차 처리는 서버가 세마포어로 보장하므로
// (pipeline._JOB_SEMAPHORE), 여기서는 파일들을 순차로 업로드해 큐에 넣기만 한다.
// UI(VideoCaptionPanel)에서 분리해 단위 테스트 가능하게 둔다.

// 폴더 선택(webkitdirectory)은 폴더 안 모든 파일을 주므로 영상만 골라낸다.
const VIDEO_EXTS = new Set([
  "mp4", "mov", "mkv", "avi", "webm", "m4v", "mpg", "mpeg", "wmv", "flv", "ts", "3gp",
]);

export function filterVideoFiles(files: File[]): File[] {
  return files.filter((file) => {
    const dot = file.name.lastIndexOf(".");
    const ext = dot >= 0 ? file.name.slice(dot + 1).toLowerCase() : "";
    return VIDEO_EXTS.has(ext);
  });
}

export type BatchConfig = {
  whisperModel: string;
  translateProvider?: string;
  translateCliModel?: string;
};

export type BatchFailure = { name: string; error: string };
export type BatchResult = { ok: number; failed: BatchFailure[] };

export type UploadFn = (
  file: File,
  whisperModel: string,
  title: string,
  translateProvider?: string,
  translateCliModel?: string,
) => Promise<unknown>;

/**
 * 파일들을 공용 설정(모델·번역엔진)으로 순차 업로드한다. 한 파일이 실패해도
 * 배치를 중단하지 않고 나머지를 계속 올린 뒤, 실패 목록을 함께 반환한다.
 * onProgress(done, total, current)는 각 파일 시작 전 + 마지막 완료 시 호출된다.
 */
export async function uploadBatch(
  files: File[],
  cfg: BatchConfig,
  upload: UploadFn,
  onProgress?: (done: number, total: number, current: string) => void,
): Promise<BatchResult> {
  const failed: BatchFailure[] = [];
  let ok = 0;
  for (let i = 0; i < files.length; i++) {
    const file = files[i]!;
    onProgress?.(i, files.length, file.name);
    try {
      await upload(file, cfg.whisperModel, file.name, cfg.translateProvider, cfg.translateCliModel);
      ok += 1;
    } catch (e) {
      failed.push({ name: file.name, error: e instanceof Error ? e.message : String(e) });
    }
  }
  onProgress?.(files.length, files.length, "");
  return { ok, failed };
}
