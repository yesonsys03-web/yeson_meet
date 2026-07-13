// 자막 메이커 일괄 작업(선택 굽기/선택 다운로드)의 순수 선택 로직.
// UI/Tauri I/O 없이 "어떤 작업이 각 일괄 동작의 대상인가"만 계산한다 — 테스트 가능.
import type { VideoJobSummary } from "./videoApi";
import { sanitizeFilename } from "./videoReviewLogic";

// 저장 파일명 = {원본명(확장자 제거)}_KO.mp4. 제목에 이미 .mp4가 붙어 오므로
// 그대로 "-captioned.mp4"를 덧붙이면 이중 확장자가 된다 → 확장자를 떼고 _KO를 붙인다.
const VIDEO_EXT_RE = /\.(mp4|mov|mkv|webm|avi|m4v|mpg|mpeg)$/i;
export function captionedFileName(title: string): string {
  const base = sanitizeFilename(title.replace(VIDEO_EXT_RE, ""));
  return `${base}_KO.mp4`;
}

// 굽기 대상 = 검수 대기(review). 다운로드 대상 = 완료(done).
export const BURNABLE_STATUS = "review";
export const DOWNLOADABLE_STATUS = "done";

// 재생성(rebuild) 가능한 터미널 상태 — cancelled(취소됨)는 실패가 아니라
// 초기화된 상태이지만, 복구 동작(재생성) 관점에서는 error와 동일 취급한다.
const REBUILDABLE_STATUSES = new Set(["review", "done", "error", "cancelled"]);

export function canRebuild(status: string): boolean {
  return REBUILDABLE_STATUSES.has(status);
}

// 체크박스 선택 가능 상태 = 터미널 상태 전부. 진행 중만 제외 — 오류/취소됨도
// '선택 재생성'의 대상이므로 선택 가능해야 한다(취소된 배치 일괄 복구 흐름).
export function isSelectableStatus(status: string): boolean {
  return REBUILDABLE_STATUSES.has(status);
}

// 서버는 단계별 원시 진행률(전사/번역/굽기 각 0→100)을 보내고, 고정 단계는
// 베이스라인(ingesting=5, extracting=15)만 갖는다. 그대로 그리면 단계 전환 때
// 바가 뒤로 간다(전사 100% → 번역 0%). 각 단계를 전체 구간의 밴드로 환산해
// 표시용 전체 진행률(단조 증가)을 만든다: 전사 15~60, 번역 60~80, 굽기 80~100.
const STAGE_BANDS: Record<string, { base: number; span: number }> = {
  queued: { base: 0, span: 0 },
  ingesting: { base: 5, span: 0 },
  extracting: { base: 15, span: 0 },
  transcribing: { base: 15, span: 45 },
  translating: { base: 60, span: 20 },
  burning: { base: 80, span: 20 },
  review: { base: 100, span: 0 },
  done: { base: 100, span: 0 },
};

export function overallProgress(status: string, progress: number): number {
  const band = STAGE_BANDS[status];
  if (!band) return 0; // error/cancelled/미지 상태 — 바는 어차피 숨김
  const p = Math.min(100, Math.max(0, progress));
  return Math.min(100, Math.max(0, Math.round(band.base + (band.span * p) / 100)));
}

// 체크박스로 고를 수 있는(=일괄 동작 대상이 될 수 있는) 작업 id. 진행 중만 제외.
export function actionableJobIds(jobs: VideoJobSummary[]): string[] {
  return jobs.filter((j) => isSelectableStatus(j.status)).map((j) => j.job_id);
}

// 선택된 id 집합을 굽기/다운로드/재생성 대상으로 분할한다. 선택됐어도 상태가 맞지 않으면 어느 쪽에도 안 들어간다.
export function partitionSelection(
  jobs: VideoJobSummary[],
  selected: Set<string>,
): {
  burnable: VideoJobSummary[];
  downloadable: VideoJobSummary[];
  rebuildable: VideoJobSummary[];
} {
  const burnable: VideoJobSummary[] = [];
  const downloadable: VideoJobSummary[] = [];
  // 일괄 재생성 대상은 오류/취소됨만 — 검수 대기/완료 작업을 실수로 처음부터
  // 다시 돌리는 사고를 막는다(개별 행의 재생성 버튼은 여전히 터미널 전부 허용).
  const rebuildable: VideoJobSummary[] = [];
  for (const j of jobs) {
    if (!selected.has(j.job_id)) continue;
    if (j.status === BURNABLE_STATUS) burnable.push(j);
    else if (j.status === DOWNLOADABLE_STATUS) downloadable.push(j);
    else if (j.status === "error" || j.status === "cancelled") rebuildable.push(j);
  }
  return { burnable, downloadable, rebuildable };
}
