// === ANCHOR: GLOSSARY_PANEL_START ===
// 서버 콘솔의 "용어 사전" 패널. 라이브 자막 번역이 쓰는 오버라이드 파일
// (STORAGE_ROOT/glossary.txt = 영→한 용어집, glossary_ko.txt = 한국어 사후
// 교정)을 앱에서 직접 편집한다 — 파일이 단일 진실이고 저장 즉시(mtime 감지)
// 다음 번역부터 반영되므로 서버 재시작이 없다. 편집은 텍스트 그대로, 미리보기는
// 마크다운풍 렌더(# ── 섹션 ── = 제목, 주석 = 설명, 연속 항목 = 표). 저장 전
// 클라 검증 + 서버 재검증(422) 이중으로 오타 줄이 소리 없이 사전에서 빠지는
// 사고를 막는다. 기기 간 이동은 "전체 복사" → 반대쪽 편집기에 붙여넣기 → 저장.
import type { CSSProperties } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchGlossary,
  GlossaryValidationError,
  saveGlossary,
  type GlossaryFileName,
  type GlossaryInvalidLine,
  type GlossaryState,
} from "./glossaryAdmin";
import { invalidLines, renderBlocks } from "./glossaryLogic";

type Props = { serverPort: number | null; running: boolean };

const FILE_LABELS: Record<GlossaryFileName, string> = {
  glossary: "용어집 (영어 → 한국어)",
  corrections: "사후 교정 (한국어 → 한국어)",
};

const FILE_HINTS: Record<GlossaryFileName, string> = {
  glossary:
    "형식: 영어 => 한국어 (한 줄에 하나, # 으로 시작하면 주석). 파이널 번역의 용어를 고정합니다.",
  corrections:
    "형식: 잘못된 한국어 => 올바른 한국어. 파셜 자막까지 적용되는 문자열 치환 — 일반 단어 치환은 넣지 마세요.",
};

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export default function GlossaryPanel({ serverPort, running }: Props) {
  const [state, setState] = useState<GlossaryState | null>(null);
  const [active, setActive] = useState<GlossaryFileName>("glossary");
  const [drafts, setDrafts] = useState<Record<GlossaryFileName, string>>({
    glossary: "",
    corrections: "",
  });
  const [preview, setPreview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [serverInvalid, setServerInvalid] = useState<GlossaryInvalidLine[]>([]);

  const load = useCallback(async () => {
    if (!serverPort) return;
    setBusy(true);
    setError(null);
    try {
      const data = await fetchGlossary(serverPort);
      setState(data);
      setDrafts({
        glossary: data.glossary.content,
        corrections: data.corrections.content,
      });
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }, [serverPort]);

  useEffect(() => {
    if (running && serverPort) void load();
  }, [running, serverPort, load]);

  const draft = drafts[active];
  const saved = state ? state[active].content : "";
  const dirty = draft !== saved;
  const clientInvalid = useMemo(() => invalidLines(draft), [draft]);
  const blocks = useMemo(() => renderBlocks(draft), [draft]);
  const shownInvalid = clientInvalid.length ? clientInvalid : serverInvalid;

  const onSave = async () => {
    if (!serverPort) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    setServerInvalid([]);
    try {
      const result = await saveGlossary(serverPort, active, draft);
      setState((prev) =>
        prev
          ? {
              ...prev,
              [active]: {
                content: draft,
                terms: result.terms,
                effective_terms: result.effective_terms,
              },
            }
          : prev,
      );
      setNotice(
        `저장됨 — 이 파일 ${result.terms}항목, 실제 적용 ${result.effective_terms}항목. 다음 번역부터 즉시 반영됩니다.`,
      );
    } catch (e) {
      if (e instanceof GlossaryValidationError) {
        setServerInvalid(e.invalidLines);
        setError(e.message);
      } else {
        setError(errText(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const onCopyAll = async () => {
    try {
      await navigator.clipboard.writeText(draft);
      setNotice("전체 내용을 복사했습니다 — 다른 기기 편집기에 붙여넣고 저장하세요.");
    } catch (e) {
      setError(errText(e));
    }
  };

  if (!running) {
    return <p style={styles.dim}>서버가 실행 중일 때 용어 사전을 편집할 수 있습니다.</p>;
  }

  return (
    <div style={styles.wrap}>
      <div style={styles.tabRow}>
        {(Object.keys(FILE_LABELS) as GlossaryFileName[]).map((name) => (
          <button
            key={name}
            style={{ ...styles.tab, ...(active === name ? styles.tabActive : {}) }}
            onClick={() => {
              setActive(name);
              setNotice(null);
              setError(null);
              setServerInvalid([]);
            }}
          >
            {FILE_LABELS[name]}
            {drafts[name] !== (state ? state[name].content : "") ? " ●" : ""}
          </button>
        ))}
        <span style={{ flex: 1 }} />
        <button style={styles.button} onClick={() => setPreview((p) => !p)}>
          {preview ? "편집" : "미리보기"}
        </button>
        <button style={styles.button} onClick={onCopyAll} disabled={busy}>
          전체 복사
        </button>
        <button
          style={{ ...styles.button, ...styles.primary }}
          onClick={onSave}
          disabled={busy || !dirty || clientInvalid.length > 0}
        >
          저장
        </button>
      </div>

      <p style={styles.hint}>{FILE_HINTS[active]}</p>
      {state ? (
        <p style={styles.counts}>
          이 파일 {state[active].terms}항목 · 실제 적용 {state[active].effective_terms}항목(내장 기본 포함)
          {dirty ? " · 저장되지 않은 변경 있음" : ""}
        </p>
      ) : null}
      {error ? <p style={styles.error}>{error}</p> : null}
      {notice ? <p style={styles.notice}>{notice}</p> : null}
      {shownInvalid.length ? (
        <div style={styles.invalidBox}>
          형식 오류 — 아래 줄을 고쳐야 저장할 수 있습니다:
          {shownInvalid.slice(0, 8).map((b) => (
            <div key={b.line} style={styles.invalidLine}>
              {b.line}행: {b.text}
            </div>
          ))}
        </div>
      ) : null}

      {preview ? (
        <div style={styles.previewBox}>
          {blocks.map((block, i) => {
            if (block.kind === "heading") {
              return (
                <h4 key={i} style={styles.h4}>
                  {block.text}
                </h4>
              );
            }
            if (block.kind === "comment") {
              return (
                <p key={i} style={styles.dim}>
                  {block.text}
                </p>
              );
            }
            if (block.kind === "invalid") {
              return (
                <p key={i} style={styles.invalidLine}>
                  ⚠ {block.line}행 형식 오류: {block.text}
                </p>
              );
            }
            return (
              <table key={i} style={styles.table}>
                <tbody>
                  {block.rows.map((row, j) => (
                    <tr key={j}>
                      <td style={styles.tdEn}>{row.en}</td>
                      <td style={styles.tdArrow}>→</td>
                      <td style={styles.tdKo}>{row.ko}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            );
          })}
        </div>
      ) : (
        <textarea
          style={styles.editor}
          value={draft}
          spellCheck={false}
          onChange={(e) =>
            setDrafts((prev) => ({ ...prev, [active]: e.target.value }))
          }
        />
      )}
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  wrap: { display: "flex", flexDirection: "column", gap: 8, minHeight: 0 },
  tabRow: { display: "flex", gap: 8, alignItems: "center" },
  tab: {
    padding: "6px 12px",
    borderRadius: 6,
    border: "1px solid #3a4356",
    background: "transparent",
    color: "#c8d0e0",
    cursor: "pointer",
  },
  tabActive: { background: "#2b3446", color: "#fff", borderColor: "#5b6b8c" },
  button: {
    padding: "6px 12px",
    borderRadius: 6,
    border: "1px solid #3a4356",
    background: "#222a3a",
    color: "#c8d0e0",
    cursor: "pointer",
  },
  primary: { background: "#2f6fed", borderColor: "#2f6fed", color: "#fff" },
  hint: { margin: 0, fontSize: 12, color: "#8a94a8" },
  counts: { margin: 0, fontSize: 12, color: "#a8b2c6" },
  error: { margin: 0, fontSize: 12, color: "#ff7b7b" },
  notice: { margin: 0, fontSize: 12, color: "#7bd88f" },
  invalidBox: {
    border: "1px solid #7a3b3b",
    background: "#3a2020",
    color: "#ffb3b3",
    borderRadius: 6,
    padding: 8,
    fontSize: 12,
  },
  invalidLine: { fontFamily: "monospace", fontSize: 12, color: "#ffb3b3" },
  editor: {
    width: "100%",
    minHeight: 420,
    resize: "vertical",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    fontSize: 13,
    lineHeight: 1.5,
    background: "#161b26",
    color: "#e6ebf5",
    border: "1px solid #3a4356",
    borderRadius: 6,
    padding: 10,
    boxSizing: "border-box",
  },
  previewBox: {
    border: "1px solid #3a4356",
    borderRadius: 6,
    padding: 12,
    background: "#161b26",
    overflowY: "auto",
    minHeight: 420,
  },
  h4: { margin: "12px 0 4px", color: "#e6ebf5" },
  dim: { margin: "4px 0", fontSize: 12, color: "#8a94a8" },
  table: { borderCollapse: "collapse", margin: "4px 0" },
  tdEn: { padding: "2px 8px 2px 0", color: "#9ec1ff", fontFamily: "monospace", fontSize: 13 },
  tdArrow: { padding: "2px 8px", color: "#5b6b8c" },
  tdKo: { padding: "2px 0", color: "#e6ebf5", fontSize: 13 },
};
// === ANCHOR: GLOSSARY_PANEL_END ===
