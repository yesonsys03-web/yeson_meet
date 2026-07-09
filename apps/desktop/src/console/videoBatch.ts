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
export type BatchResult = { ok: number; failed: BatchFailure[]; skipped: number };

// 배치 도중 "전체 취소"를 눌렀는지 매 파일 시작 전에 확인하기 위한 훅.
export type BatchAbortOpts = { isCancelled?: () => boolean };

export type UploadFn = (
  file: File,
  whisperModel: string,
  title: string,
  translateProvider?: string,
  translateCliModel?: string,
) => Promise<unknown>;

// Tauri 네이티브 폴더 선택 경로(list_video_files 커맨드)의 항목.
export type NativeVideoFile = { path: string; name: string };

async function runSequentialBatch<T extends { name: string }>(
  items: T[],
  doUpload: (item: T) => Promise<unknown>,
  onProgress?: (done: number, total: number, current: string) => void,
  opts?: BatchAbortOpts,
): Promise<BatchResult> {
  const failed: BatchFailure[] = [];
  let ok = 0;
  let skipped = 0;
  for (let i = 0; i < items.length; i++) {
    // 각 파일 업로드 시작 "전"에 확인 — "전체 취소"가 눌렸으면 남은 파일은
    // 아예 시작하지 않고 스킵한다(이미 시작한 업로드는 끝까지 둔다).
    if (opts?.isCancelled?.()) {
      skipped = items.length - i;
      break;
    }
    const item = items[i]!;
    onProgress?.(i, items.length, item.name);
    try {
      await doUpload(item);
      ok += 1;
    } catch (e) {
      failed.push({ name: item.name, error: e instanceof Error ? e.message : String(e) });
    }
  }
  onProgress?.(items.length - skipped, items.length, "");
  return { ok, failed, skipped };
}

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
  opts?: BatchAbortOpts,
): Promise<BatchResult> {
  return runSequentialBatch(
    files,
    (file) => upload(file, cfg.whisperModel, file.name,
                     cfg.translateProvider, cfg.translateCliModel),
    onProgress,
    opts,
  );
}

/**
 * 네이티브 폴더 선택 경로용 배치 — 파일 내용을 웹뷰가 못 읽으므로 업로드는
 * Rust 커맨드(upload_video_file)가 경로에서 직접 스트리밍한다. 실패 무중단
 * 순차 처리 semantics는 uploadBatch와 동일.
 */
export async function uploadBatchNative(
  entries: NativeVideoFile[],
  cfg: BatchConfig,
  uploadPath: (entry: NativeVideoFile, cfg: BatchConfig) => Promise<unknown>,
  onProgress?: (done: number, total: number, current: string) => void,
  opts?: BatchAbortOpts,
): Promise<BatchResult> {
  return runSequentialBatch(entries, (entry) => uploadPath(entry, cfg), onProgress, opts);
}
