// 라이브 자막 번역용 MLX 모델 목록 — 서버 카탈로그(빌트인+원격)에서 받아온다.
// videoJobsAdmin.ts 관례를 미러: 번들 서버의 루프백 REST(127.0.0.1:<port>),
// translate-models API는 무인증(LAN 신뢰경계)이라 로그인 게이트가 없다.
//
// 목록만 서버에서 받고, 설치 상태 확인·다운로드는 Tauri 커맨드가 계속 담당한다.
// ServerConfigPanel은 서버가 아직 안 떠 있는 초기 설정 단계에서도 동작해야 하므로,
// 조회 실패 시 BUILTIN_MLX_MODELS로 조용히 폴백한다.
const API = "/api/v1";

export type LiveMlxModel = { id: string; label: string; bytes: number };

// serverConfig.ts의 MLX_MODELS에서 이관 — 서버 조회 실패 시의 폴백을 겸한다.
export const BUILTIN_MLX_MODELS: LiveMlxModel[] = [
  { id: "mlx-community/Qwen3.5-9B-4bit", label: "Qwen 9B (로컬)", bytes: 5_000_000_000 },
  { id: "mlx-community/Qwen3.5-4B-4bit", label: "Qwen 4B (로컬·빠름)", bytes: 2_300_000_000 },
  { id: "mlx-community/Qwen3.5-9B-8bit", label: "Qwen 9B (로컬·고품질 8bit)", bytes: 10_000_000_000 },
];

type TranslateModelRow = {
  label: string;
  mlx_repo: string | null;
  mlx_bytes: number;
};

function base(port: number): string {
  return `http://127.0.0.1:${port}`;
}

export async function listLiveMlxModels(port: number): Promise<LiveMlxModel[]> {
  try {
    const r = await fetch(`${base(port)}${API}/translate-models`);
    if (!r.ok) return BUILTIN_MLX_MODELS;  // 구버전 번들엔 라우트가 없다(404)
    const body = (await r.json()) as { models?: TranslateModelRow[] };
    const rows = (body.models ?? []).filter((m) => Boolean(m.mlx_repo));
    if (rows.length === 0) return BUILTIN_MLX_MODELS;
    return rows.map((m) => ({
      id: m.mlx_repo as string,
      label: m.label,
      bytes: m.mlx_bytes,
    }));
  } catch {
    return BUILTIN_MLX_MODELS;  // 서버 미기동 — 초기 설정 단계의 정상 경로다
  }
}
