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

// 체크박스로 고를 수 있는(=일괄 동작 대상이 될 수 있는) 작업 id. 진행 중/오류는 제외.
export function actionableJobIds(jobs: VideoJobSummary[]): string[] {
  return jobs
    .filter((j) => j.status === BURNABLE_STATUS || j.status === DOWNLOADABLE_STATUS)
    .map((j) => j.job_id);
}

// 선택된 id 집합을 굽기/다운로드 대상으로 분할한다. 선택됐어도 상태가 맞지 않으면 어느 쪽에도 안 들어간다.
export function partitionSelection(
  jobs: VideoJobSummary[],
  selected: Set<string>,
): { burnable: VideoJobSummary[]; downloadable: VideoJobSummary[] } {
  const burnable: VideoJobSummary[] = [];
  const downloadable: VideoJobSummary[] = [];
  for (const j of jobs) {
    if (!selected.has(j.job_id)) continue;
    if (j.status === BURNABLE_STATUS) burnable.push(j);
    else if (j.status === DOWNLOADABLE_STATUS) downloadable.push(j);
  }
  return { burnable, downloadable };
}
