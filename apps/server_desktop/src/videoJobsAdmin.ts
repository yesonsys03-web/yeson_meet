// === ANCHOR: VIDEO_JOBS_ADMIN_START ===
// 자막 메이커 데이터 관리(조회/삭제)를 서버 콘솔에서 수행하기 위한 클라이언트.
// 파일이 물리적으로 쌓이는 곳이 서버이므로, 관리는 서버 control plane의 몫이다
// (deviceAdmin과 동일 사상). 번들 서버의 루프백 REST(127.0.0.1:<port>)에 붙는다.
// video-jobs API는 무인증(LAN 신뢰경계 + capability URL)이라 로그인 게이트가 없다.
const API = "/api/v1";

export type VideoJobRow = {
  job_id: string;
  title: string;
  source_type: "youtube" | "upload";
  status: string;
  created_at: string | null;
  size_bytes?: number;
};

export type StorageInfo = {
  total_bytes: number;
  job_count: number;
  keep: number;
};

function base(port: number): string {
  return `http://127.0.0.1:${port}`;
}

export async function listVideoJobs(port: number): Promise<VideoJobRow[]> {
  // with_sizes=true → 작업별 폴더 용량 포함(어느 게 용량을 먹는지 보여주기 위해).
  const r = await fetch(`${base(port)}${API}/video-jobs?with_sizes=true`);
  if (!r.ok) throw new Error(`영상 작업 목록 조회 실패 (HTTP ${r.status})`);
  return ((await r.json()) as { items: VideoJobRow[] }).items;
}

export async function getStorage(port: number): Promise<StorageInfo> {
  const r = await fetch(`${base(port)}${API}/video-jobs/storage`);
  if (!r.ok) throw new Error(`스토리지 정보 조회 실패 (HTTP ${r.status})`);
  return (await r.json()) as StorageInfo;
}

export async function deleteVideoJob(port: number, jobId: string): Promise<void> {
  const r = await fetch(`${base(port)}${API}/video-jobs/${jobId}`, { method: "DELETE" });
  if (!r.ok && r.status !== 204) throw new Error(`작업 삭제 실패 (HTTP ${r.status})`);
}
// === ANCHOR: VIDEO_JOBS_ADMIN_END ===
