import { describe, expect, it, vi } from "vitest";
import { BUILTIN_MLX_MODELS, listLiveMlxModels } from "./translateCatalogAdmin";

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
    expect(out).toEqual([
      { id: "mlx-community/Qwen3.5-9B-4bit", label: "Qwen 9B (로컬)", bytes: 5000000000 },
    ]);
  });

  it("서버가 안 떠 있으면 빌트인으로 폴백한다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }));
    expect(await listLiveMlxModels(8000)).toEqual(BUILTIN_MLX_MODELS);
  });

  it("구버전 번들(404)이면 빌트인으로 폴백한다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 404 })));
    expect(await listLiveMlxModels(8000)).toEqual(BUILTIN_MLX_MODELS);
  });

  it("빌트인은 현행 3종을 유지한다", () => {
    expect(BUILTIN_MLX_MODELS.map((m) => m.id)).toEqual([
      "mlx-community/Qwen3.5-9B-4bit",
      "mlx-community/Qwen3.5-4B-4bit",
      "mlx-community/Qwen3.5-9B-8bit",
    ]);
  });
});
