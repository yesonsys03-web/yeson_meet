import { describe, expect, it, vi } from "vitest";
import { BUILTIN_MLX_MODELS, listLiveMlxModels, listLiveMlxModelsWithRetry } from "./translateCatalogAdmin";

describe("listLiveMlxModels", () => {
  it("mlx_repo가 있는 항목만 변환한다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      models: [
        { name: "qwen", label: "Qwen 9B (로컬)", mlx_repo: "mlx-community/Qwen3.5-9B-4bit",
          mlx_bytes: 5000000000, ollama_tag: "qwen3.5:9b" },
        { name: "only_ollama", label: "Ollama 전용", mlx_repo: null,
          mlx_bytes: 0, ollama_tag: "x:1b" },
      ],
    }), { status: 200, headers: { "content-type": "application/json" } })));
    const out = await listLiveMlxModels(8000);
    expect(out).toEqual({
      models: [
        { id: "mlx-community/Qwen3.5-9B-4bit", label: "Qwen 9B (로컬)", bytes: 5000000000 },
      ],
      fromServer: true,
    });
  });

  it("서버가 안 떠 있으면 빌트인으로 폴백한다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }));
    expect(await listLiveMlxModels(8000)).toEqual({ models: BUILTIN_MLX_MODELS, fromServer: false });
  });

  it("구버전 번들(404)이면 빌트인으로 폴백한다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 404 })));
    expect(await listLiveMlxModels(8000)).toEqual({ models: BUILTIN_MLX_MODELS, fromServer: false });
  });

  it("빌트인은 현행 3종을 유지한다", () => {
    expect(BUILTIN_MLX_MODELS.map((m) => m.id)).toEqual([
      "mlx-community/Qwen3.5-9B-4bit",
      "mlx-community/Qwen3.5-4B-4bit",
      "mlx-community/Qwen3.5-9B-8bit",
    ]);
  });

  it("카탈로그가 전부 Ollama 전용(mlx_repo 없음)이어도 빌트인으로 폴백한다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      models: [
        { name: "only_ollama_a", label: "Ollama A", mlx_repo: null, mlx_bytes: 0, ollama_tag: "a:1b" },
        { name: "only_ollama_b", label: "Ollama B", mlx_repo: null, mlx_bytes: 0, ollama_tag: "b:1b" },
      ],
    }), { status: 200, headers: { "content-type": "application/json" } })));
    expect(await listLiveMlxModels(8000)).toEqual({ models: BUILTIN_MLX_MODELS, fromServer: false });
  });

  it("200 응답 본문이 JSON이 아니면(파싱 에러) 빌트인으로 폴백한다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("not json", {
      status: 200, headers: { "content-type": "application/json" },
    })));
    expect(await listLiveMlxModels(8000)).toEqual({ models: BUILTIN_MLX_MODELS, fromServer: false });
  });
});

describe("listLiveMlxModelsWithRetry", () => {
  // MlxModelPanel의 마운트 직후 재시도 루프가 실제로 회복하는지를 증명한다: 서버가
  // running=true인데도 uvicorn이 아직 HTTP를 안 받는 첫 조회는 폴백으로 끝나지만,
  // 이후 조회는 성공한다 — listLiveMlxModelsWithRetry가 폴백에서 멈추지 않고 서버
  // 응답을 받을 때까지 재시도해서 최종적으로 서버 카탈로그를 반환해야 한다.
  it("첫 조회가 폴백이어도 이후 조회가 성공하면 서버 목록으로 회복한다", async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(async () => {
        throw new TypeError("Failed to fetch");  // uvicorn이 아직 HTTP를 안 받는 창
      })
      .mockImplementationOnce(async () => new Response(JSON.stringify({
        models: [
          { name: "qwen", label: "Qwen 9B (원격)", mlx_repo: "mlx-community/Qwen3.5-9B-4bit",
            mlx_bytes: 5000000000, ollama_tag: "qwen3.5:9b" },
        ],
      }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const sleepCalls: number[] = [];
    const fakeSleep = async (ms: number) => { sleepCalls.push(ms); };

    const result = await listLiveMlxModelsWithRetry(8000, () => false, fakeSleep);

    expect(result).toEqual({
      models: [{ id: "mlx-community/Qwen3.5-9B-4bit", label: "Qwen 9B (원격)", bytes: 5000000000 }],
      fromServer: true,
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(sleepCalls).toEqual([450]);  // 재시도 사이 대기는 정확히 한 번, 450ms 주기로
  });

  it("isCancelled가 true가 되면 재시도를 멈추고 즉시 반환한다", async () => {
    let calls = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      calls += 1;
      throw new TypeError("Failed to fetch");  // 항상 폴백 — 취소가 없으면 예산까지 계속 재시도
    }));

    let cancelled = false;
    const fakeSleep = async () => { cancelled = true; };  // 첫 대기 직후 취소 상태로 전환

    const result = await listLiveMlxModelsWithRetry(8000, () => cancelled, fakeSleep);

    expect(result).toEqual({ models: BUILTIN_MLX_MODELS, fromServer: false });
    expect(calls).toBe(1);  // 취소 후에는 추가 조회를 시도하지 않는다
  });
});
