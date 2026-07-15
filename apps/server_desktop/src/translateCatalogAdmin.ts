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

// fromServer: 서버 카탈로그를 실제로 받아왔는지 여부. 호출부(MlxModelPanel)가
// "폴백이라 재시도해야 한다"를 판단할 수 있어야 하므로, 실패 경로와 성공 경로를
// 구분해서 돌려준다 — 이 함수 자체는 어떤 실패 경로에서도 절대 throw하지 않는다.
export type LiveMlxModelsResult = { models: LiveMlxModel[]; fromServer: boolean };

export async function listLiveMlxModels(port: number): Promise<LiveMlxModelsResult> {
  try {
    const r = await fetch(`${base(port)}${API}/translate-models`);
    if (!r.ok) return { models: BUILTIN_MLX_MODELS, fromServer: false };  // 구버전 번들엔 라우트가 없다(404)
    const body = (await r.json()) as { models?: TranslateModelRow[] };
    const rows = (body.models ?? []).filter((m) => Boolean(m.mlx_repo));
    if (rows.length === 0) return { models: BUILTIN_MLX_MODELS, fromServer: false };
    return {
      models: rows.map((m) => ({
        id: m.mlx_repo as string,
        label: m.label,
        bytes: m.mlx_bytes,
      })),
      fromServer: true,
    };
  } catch {
    return { models: BUILTIN_MLX_MODELS, fromServer: false };  // 서버 미기동 — 초기 설정 단계의 정상 경로다
  }
}

// MlxModelPanel의 재시도 게이트 — ServerConsole.tsx의 READINESS_POLL_INTERVAL_MS/
// READINESS_TIMEOUT_MS와 같은 근거(값도 동일)를 미러링한다: 서버 프로세스는 포트를
// 바인딩(status.running=true)한 뒤 ~1초가 지나서야 uvicorn이 실제로 HTTP를 받기
// 시작한다. 그 창에서 뜬 폴백(fromServer:false)을 세션 내내 최종값으로 굳히지 않도록,
// 서버가 실제로 응답하거나(fromServer:true) 취소되거나 예산이 끝날 때까지 재시도한다.
export const CATALOG_RETRY_POLL_INTERVAL_MS = 450;
export const CATALOG_RETRY_TIMEOUT_MS = 12_000;

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function listLiveMlxModelsWithRetry(
  port: number,
  isCancelled: () => boolean,
  sleep: (ms: number) => Promise<void> = defaultSleep,
): Promise<LiveMlxModelsResult> {
  const deadline = Date.now() + CATALOG_RETRY_TIMEOUT_MS;
  for (;;) {
    const result = await listLiveMlxModels(port);
    if (result.fromServer || isCancelled() || Date.now() >= deadline) return result;
    await sleep(CATALOG_RETRY_POLL_INTERVAL_MS);
    if (isCancelled()) return result;
  }
}
