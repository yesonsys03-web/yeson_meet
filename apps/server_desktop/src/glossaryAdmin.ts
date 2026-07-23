// === ANCHOR: GLOSSARY_ADMIN_START ===
// 용어 사전 편집 API 클라이언트 (무인증 loopback REST — reportsAdmin과 동일 사상).
const API = "/api/v1";

export type GlossaryFileName = "glossary" | "corrections";

export type GlossaryFileInfo = {
  content: string;
  terms: number;
  effective_terms: number;
};

export type GlossaryState = {
  glossary: GlossaryFileInfo;
  corrections: GlossaryFileInfo;
};

export type GlossarySaveResult = {
  saved: boolean;
  terms: number;
  effective_terms: number;
};

export type GlossaryInvalidLine = { line: number; text: string };

export class GlossaryValidationError extends Error {
  invalidLines: GlossaryInvalidLine[];

  constructor(message: string, invalidLines: GlossaryInvalidLine[]) {
    super(message);
    this.invalidLines = invalidLines;
  }
}

function base(port: number): string {
  return `http://127.0.0.1:${port}`;
}

export async function fetchGlossary(port: number): Promise<GlossaryState> {
  const r = await fetch(`${base(port)}${API}/glossary`);
  if (!r.ok) throw new Error(`용어 사전 조회 실패 (HTTP ${r.status})`);
  return (await r.json()) as GlossaryState;
}

export async function saveGlossary(
  port: number,
  name: GlossaryFileName,
  content: string,
): Promise<GlossarySaveResult> {
  const r = await fetch(`${base(port)}${API}/glossary/${name}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (r.status === 422) {
    const detail = ((await r.json()) as {
      detail: { message: string; invalid_lines: GlossaryInvalidLine[] };
    }).detail;
    throw new GlossaryValidationError(detail.message, detail.invalid_lines);
  }
  if (!r.ok) throw new Error(`용어 사전 저장 실패 (HTTP ${r.status})`);
  return (await r.json()) as GlossarySaveResult;
}
// === ANCHOR: GLOSSARY_ADMIN_END ===
