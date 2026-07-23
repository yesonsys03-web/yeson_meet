// === ANCHOR: GLOSSARY_LOGIC_START ===
// 용어 사전 편집기의 순수 로직 — 서버 parse_glossary_file과 동일한 줄 해석
// (구분자 우선순위 "=>", 탭, "=")을 클라에 미러링해 저장 전 즉시 검증하고,
// 파일 텍스트를 마크다운풍 미리보기 블록(제목/주석/표/오류)으로 변환한다.
// 렌더 규칙: "─"가 든 주석 줄 = 섹션 제목, 그 외 주석 = 설명 문단,
// 연속된 항목 줄 = 표 하나, 파싱 불가 줄 = 오류(줄 번호 표시).

export type GlossaryEntry = { en: string; ko: string };

export type GlossaryBlock =
  | { kind: "heading"; text: string }
  | { kind: "comment"; text: string }
  | { kind: "entries"; rows: GlossaryEntry[] }
  | { kind: "invalid"; line: number; text: string };

const SEPARATORS = ["=>", "\t", "="] as const;

export function parseEntryLine(line: string): GlossaryEntry | null {
  const trimmed = line.trim();
  if (!trimmed || trimmed.startsWith("#")) return null;
  for (const sep of SEPARATORS) {
    const idx = trimmed.indexOf(sep);
    if (idx >= 0) {
      const en = trimmed.slice(0, idx).trim();
      const ko = trimmed.slice(idx + sep.length).trim();
      return en && ko ? { en, ko } : null;
    }
  }
  return null;
}

export function invalidLines(content: string): { line: number; text: string }[] {
  const bad: { line: number; text: string }[] = [];
  content.split("\n").forEach((raw, i) => {
    const line = raw.trim();
    if (!line || line.startsWith("#")) return;
    if (!parseEntryLine(line)) bad.push({ line: i + 1, text: raw });
  });
  return bad;
}

function headingText(comment: string): string | null {
  if (!comment.includes("─")) return null;
  const text = comment.replace(/[#─═\s]+/g, " ").trim();
  return text || null;
}

export function renderBlocks(content: string): GlossaryBlock[] {
  const blocks: GlossaryBlock[] = [];
  let rows: GlossaryEntry[] = [];
  const flush = () => {
    if (rows.length) {
      blocks.push({ kind: "entries", rows });
      rows = [];
    }
  };
  content.split("\n").forEach((raw, i) => {
    const line = raw.trim();
    if (!line) {
      flush();
      return;
    }
    if (line.startsWith("#")) {
      flush();
      const heading = headingText(line);
      if (heading) blocks.push({ kind: "heading", text: heading });
      else blocks.push({ kind: "comment", text: line.replace(/^#+\s*/, "") });
      return;
    }
    const entry = parseEntryLine(line);
    if (entry) {
      rows.push(entry);
    } else {
      flush();
      blocks.push({ kind: "invalid", line: i + 1, text: raw });
    }
  });
  flush();
  return blocks;
}
// === ANCHOR: GLOSSARY_LOGIC_END ===
