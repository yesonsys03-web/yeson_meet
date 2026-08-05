// === ANCHOR: GLOSSARY_PANEL_START ===
// 서버 콘솔의 "용어 사전" 패널. 오버라이드 파일 4종을 앱에서 직접 편집한다 —
// 공용 용어집/사후 교정(회의 자막 라이브에 적용되고 자막 메이커에도 상속)과,
// 대사 전용 용어집/사후 교정(자막 메이커=작품 대사에만 적용, 상속 항목을
// 덮어쓸 수 있음). 파일이 단일 진실이고 저장 즉시(mtime 감지) 다음 번역부터
// 반영되므로 서버 재시작이 없다. 편집은 텍스트 그대로, 미리보기는
// 마크다운풍 렌더(# ── 섹션 ── = 제목, 주석 = 설명, 연속 항목 = 표). 저장 전
// 클라 검증 + 서버 재검증(422) 이중으로 오타 줄이 소리 없이 사전에서 빠지는
// 사고를 막는다. 기기 간 이동은 "전체 복사" → 반대쪽 편집기에 붙여넣기 → 저장.
import type { CSSProperties } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchGlossary,
  GlossaryValidationError,
  saveGlossary,
  type GlossaryFileInfo,
  type GlossaryFileName,
  type GlossaryInvalidLine,
  type GlossaryState,
} from "./glossaryAdmin";
import { invalidLines, renderBlocks, resolveGlossaryFile } from "./glossaryLogic";

type Props = { serverPort: number | null; running: boolean };

const FILE_LABELS: Record<GlossaryFileName, string> = {
  glossary: "공용 용어집 (영어 → 한국어)",
  corrections: "공용 사후 교정 (한국어 → 한국어)",
  glossary_dialogue: "대사 용어집 (영어 → 한국어)",
  corrections_dialogue: "대사 사후 교정 (한국어 → 한국어)",
};

const FILE_HINTS: Record<GlossaryFileName, string> = {
  glossary:
    "형식: 영어 => 한국어 (한 줄에 하나, # 으로 시작하면 주석). 회의 자막(라이브)에 적용되고, 자막 메이커에도 상속됩니다.",
  corrections:
    "형식: 잘못된 한국어 => 올바른 한국어. 회의 자막 파셜까지 적용되는 문자열 치환이며, 자막 메이커에도 상속됩니다. 일반 단어 치환은 넣지 마세요.",
  glossary_dialogue:
    "형식: 영어 => 한국어. 자막 메이커(작품 대사)에만 적용되며, 공용 용어집을 상속한 뒤 같은 영어 표현을 다시 쓰면 덮어씁니다. 캐릭터명·브랜드 등 작품 고유명사는 여기에 넣으세요.",
  corrections_dialogue:
    "형식: 잘못된 한국어 => 올바른 한국어. 자막 메이커(작품 대사)에만 적용되며, 공용 사후 교정을 상속한 뒤 덮어쓸 수 있습니다.",
};

// 탭을 "공용(회의 자막 + 자막 메이커 상속)" / "대사 전용(자막 메이커, 상속 항목 덮어쓰기)"
// 두 그룹으로 묶어 보여준다 — 4개 편집기가 어디에 쓰이는지 한눈에 구분되도록.
const FILE_GROUPS: { label: string; names: GlossaryFileName[] }[] = [
  { label: "공용 (회의 자막 + 자막 메이커 상속)", names: ["glossary", "corrections"] },
  { label: "대사 전용 (자막 메이커, 상속 항목 덮어쓰기)", names: ["glossary_dialogue", "corrections_dialogue"] },
];
const FILE_NAMES: GlossaryFileName[] = FILE_GROUPS.flatMap((g) => g.names);

function blankDrafts(): Record<GlossaryFileName, string> {
  return Object.fromEntries(FILE_NAMES.map((name) => [name, ""])) as Record<
    GlossaryFileName,
    string
  >;
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export default function GlossaryPanel({ serverPort, running }: Props) {
  const [state, setState] = useState<GlossaryState | null>(null);
  const [active, setActive] = useState<GlossaryFileName>("glossary");
  const [drafts, setDrafts] = useState<Record<GlossaryFileName, string>>(blankDrafts);
  const [preview, setPreview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [serverInvalid, setServerInvalid] = useState<GlossaryInvalidLine[]>([]);
  // 구버전 동결 번들 서버는 새 대사 사전 키를 아직 안 줄 수 있다(응답에 키
  // 없음) — resolveGlossaryFile로 그 파일만 빈 내용 + 미지원 처리해 나머지
  // 탭(특히 공용 2종)은 정상 동작하게 한다.
  const [unsupported, setUnsupported] = useState<Set<GlossaryFileName>>(new Set());

  const load = useCallback(async () => {
    if (!serverPort) return;
    setBusy(true);
    setError(null);
    try {
      const data = await fetchGlossary(serverPort);
      const resolved = {} as Record<GlossaryFileName, GlossaryFileInfo>;
      const missing = new Set<GlossaryFileName>();
      for (const name of FILE_NAMES) {
        const looked = resolveGlossaryFile(data, name);
        resolved[name] = {
          content: looked.content,
          terms: looked.terms,
          effective_terms: looked.effective_terms,
        };
        if (!looked.supported) missing.add(name);
      }
      setState(resolved as GlossaryState);
      setUnsupported(missing);
      setDrafts(
        Object.fromEntries(FILE_NAMES.map((name) => [name, resolved[name].content])) as Record<
          GlossaryFileName,
          string
        >,
      );
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
    if (!serverPort || unsupported.has(active)) return;
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
        {FILE_GROUPS.map((group) => (
          <div key={group.label} style={styles.tabGroup}>
            <span style={styles.groupLabel}>{group.label}</span>
            {group.names.map((name) => (
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
                {unsupported.has(name)
                  ? " (미지원)"
                  : drafts[name] !== (state ? state[name].content : "")
                    ? " ●"
                    : ""}
              </button>
            ))}
          </div>
        ))}
        <span style={{ flex: 1 }} />
        <button style={styles.button} onClick={() => void load()} disabled={busy}>
          새로고침
        </button>
        <button style={styles.button} onClick={() => setPreview((p) => !p)}>
          {preview ? "편집" : "미리보기"}
        </button>
        <button style={styles.button} onClick={onCopyAll} disabled={busy}>
          전체 복사
        </button>
        <button
          style={{ ...styles.button, ...styles.primary }}
          onClick={onSave}
          disabled={busy || !dirty || clientInvalid.length > 0 || unsupported.has(active)}
        >
          저장
        </button>
      </div>

      <p style={styles.hint}>{FILE_HINTS[active]}</p>
      {unsupported.has(active) ? (
        <p style={styles.unsupported}>
          이 서버는 이 사전을 아직 지원하지 않습니다(서버 재동결 필요) — 편집기가 비어 있고 저장이
          비활성화됩니다.
        </p>
      ) : null}
      {state && !unsupported.has(active) ? (
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
          readOnly={unsupported.has(active)}
          placeholder={
            unsupported.has(active) ? "서버가 이 사전을 아직 지원하지 않습니다." : undefined
          }
          onChange={(e) =>
            setDrafts((prev) => ({ ...prev, [active]: e.target.value }))
          }
        />
      )}
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  // 부모 섹션(viewScroll: flex+overflow)의 가시 높이를 그대로 채우고, 에디터/
  // 미리보기가 남는 세로 공간을 flex로 흡수한다 — 창을 키우면 편집 영역이
  // 같이 커진다(고정 420px이던 실사용 불만 수정, 2026-07-23).
  wrap: { display: "flex", flexDirection: "column", gap: 8, minHeight: 0, height: "100%" },
  tabRow: { display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" },
  tabGroup: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" },
  groupLabel: { fontSize: 11, color: "#6b7690", whiteSpace: "nowrap" },
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
  unsupported: { margin: 0, fontSize: 12, color: "#e0b464" },
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
    flex: "1 1 auto",
    minHeight: 200,
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
    flex: "1 1 auto",
    minHeight: 200,
  },
  h4: { margin: "12px 0 4px", color: "#e6ebf5" },
  dim: { margin: "4px 0", fontSize: 12, color: "#8a94a8" },
  table: { borderCollapse: "collapse", margin: "4px 0" },
  tdEn: { padding: "2px 8px 2px 0", color: "#9ec1ff", fontFamily: "monospace", fontSize: 13 },
  tdArrow: { padding: "2px 8px", color: "#5b6b8c" },
  tdKo: { padding: "2px 0", color: "#e6ebf5", fontSize: 13 },
};
// === ANCHOR: GLOSSARY_PANEL_END ===
