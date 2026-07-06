// === ANCHOR: KNOWLEDGE_REPOSITORY_PANEL_START ===
import { useEffect, useState } from "react";
import { loginOperator } from "./sessionApi";
import { fetchSessionReportHtml, fetchSessionSummaryHtml } from "./sessionApi";
import { exportReports } from "./reportExport";
import { useKnowledgeRepository } from "./useKnowledgeRepository";
import { consoleStyles } from "./consoleStyles";
import type { SessionListItem } from "./knowledgeApi";

type KnowledgeRepositoryPanelProps = {
  // Operator JWT lifted to shared app state (C5). null = not yet logged in.
  operatorToken: string | null;
  onTokenAcquired: (token: string) => void;
};

// ---- Auth gate (C5) --------------------------------------------------------

type LoginGateProps = {
  onTokenAcquired: (token: string) => void;
};

export function LoginGate({ onTokenAcquired }: LoginGateProps) {
  const [email, setEmail] = useState("admin@yeson.local");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const pair = await loginOperator(email.trim(), password);
      onTokenAcquired(pair.access_token);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      setPassword("");
    }
  }

  return (
    <div style={{ ...consoleStyles.panel, maxWidth: 480, margin: "60px auto" }}>
      <h2 style={{ ...consoleStyles.title, fontSize: 26, marginBottom: 8 }}>회의 기록 열람</h2>
      <p style={consoleStyles.subtitle}>
        열람하려면 Operator 로그인이 필요합니다.
      </p>
      <form onSubmit={(e) => { void handleLogin(e); }} style={{ marginTop: 24 }}>
        <label style={consoleStyles.label}>
          이메일
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            style={{ ...consoleStyles.input, marginTop: 6 }}
            autoComplete="username"
          />
        </label>
        <label style={{ ...consoleStyles.label, marginTop: 14, display: "block" }}>
          비밀번호
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            style={{ ...consoleStyles.input, marginTop: 6 }}
            autoComplete="current-password"
          />
        </label>
        {error && <p style={consoleStyles.statusError}>{error}</p>}
        <button
          type="submit"
          disabled={busy}
          style={{
            ...consoleStyles.action,
            ...(busy ? consoleStyles.actionDisabled : null),
            marginTop: 18,
            width: "100%",
          }}
        >
          {busy ? "로그인 중..." : "로그인"}
        </button>
      </form>
    </div>
  );
}

// ---- Helpers ---------------------------------------------------------------

// Render a single FTS5 snippet string, where the server wraps matched terms in
// square brackets: e.g. "send the [budget] report to [Alice]".
// Bracketed segments are rendered bold; surrounding text is plain.
// No HTML is involved — this is purely React text nodes, XSS-safe.
function SnippetText({ text }: { text: string }) {
  const parts: Array<{ bold: boolean; text: string }> = [];
  let rest = text;
  while (rest.length > 0) {
    const open = rest.indexOf("[");
    if (open === -1) {
      parts.push({ bold: false, text: rest });
      break;
    }
    if (open > 0) {
      parts.push({ bold: false, text: rest.slice(0, open) });
    }
    const close = rest.indexOf("]", open + 1);
    if (close === -1) {
      // Unclosed bracket — treat remainder as plain text.
      parts.push({ bold: false, text: rest.slice(open) });
      break;
    }
    parts.push({ bold: true, text: rest.slice(open + 1, close) });
    rest = rest.slice(close + 1);
  }
  return (
    <>
      {parts.map((part, i) =>
        part.bold ? (
          <b key={i} style={{ color: "var(--ys-text-strong)" }}>{part.text}</b>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </>
  );
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("ko-KR", {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("ko-KR", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

// Group items by LOCAL calendar date derived from started_at.
// Using local date (not raw UTC slice) ensures a meeting's group matches its
// displayed local time. When the server emits UTC-aware ISO (Z suffix),
// new Date() converts to local automatically.
function localDateKey(iso: string): string {
  const d = new Date(iso);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function groupByDate(items: SessionListItem[]): Array<{ date: string; items: SessionListItem[] }> {
  const groups: Map<string, SessionListItem[]> = new Map();
  for (const item of items) {
    const dateKey = localDateKey(item.started_at);
    const group = groups.get(dateKey);
    if (group) {
      group.push(item);
    } else {
      groups.set(dateKey, [item]);
    }
  }
  return Array.from(groups.entries()).map(([date, dateItems]) => ({ date, items: dateItems }));
}

// ---- Detail pane -----------------------------------------------------------

type DetailPaneProps = {
  item: SessionListItem;
  operatorToken: string;
  reportHtmlCache: Map<string, string>;
  onCacheHtml: (id: string, html: string) => void;
};

function DetailPane({ item, operatorToken, reportHtmlCache, onCacheHtml }: DetailPaneProps) {
  const [reportHtml, setReportHtml] = useState<string | null>(reportHtmlCache.get(item.external_id) ?? null);
  const [summaryHtml, setSummaryHtml] = useState<string | null>(null);
  const [loadingHtml, setLoadingHtml] = useState(item.report_ready && !reportHtml);
  const [loadingSummary, setLoadingSummary] = useState(true);
  const [htmlError, setHtmlError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState<string | null>(null);
  const [tab, setTab] = useState<"summary" | "report">("summary");

  useEffect(() => {
    let cancelled = false;
    // Skip fetch entirely when the report is not yet ready (non-ended sessions).
    // Avoids a guaranteed 409 and shows a friendly placeholder instead.
    if (!item.report_ready) {
      setLoadingHtml(false);
      setReportHtml(null);
      setHtmlError(null);
      return;
    }
    // Fetch report HTML (use cache first)
    const cached = reportHtmlCache.get(item.external_id);
    if (cached) {
      setReportHtml(cached);
      setLoadingHtml(false);
    } else {
      setLoadingHtml(true);
      setHtmlError(null);
      fetchSessionReportHtml(item.external_id, operatorToken)
        .then((html) => {
          if (cancelled) return;
          setReportHtml(html);
          onCacheHtml(item.external_id, html);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setHtmlError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          if (!cancelled) setLoadingHtml(false);
        });
    }

    // Fetch summary HTML (styled like the report; best-effort)
    setLoadingSummary(true);
    setSummaryHtml(null);
    fetchSessionSummaryHtml(item.external_id, operatorToken)
      .then((result) => {
        if (cancelled) return;
        if (result.ok && result.html) setSummaryHtml(result.html);
      })
      .finally(() => {
        if (!cancelled) setLoadingSummary(false);
      });

    return () => {
      cancelled = true;
    };
  }, [item.external_id, operatorToken]);

  async function handleExport() {
    setExporting(true);
    setExportMsg(null);
    try {
      const result = await exportReports(item.external_id, operatorToken);
      const parts: string[] = [];
      if (result.saved.length > 0) parts.push(`저장됨: ${result.saved.join(", ")}`);
      if (result.skipped.length > 0) {
        parts.push(`실패: ${result.skipped.map((s) => `${s.fmt}(${s.reason})`).join(", ")}`);
      }
      setExportMsg(parts.join(" / ") || "저장된 파일이 없습니다.");
    } catch (err) {
      setExportMsg(`내보내기 실패: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setExporting(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0, overflow: "hidden" }}>
      {/* Header */}
      <div style={{ padding: "18px 22px 12px", borderBottom: "1px solid var(--ys-border-subtle)", flexShrink: 0 }}>
        <h3 style={{ margin: "0 0 4px", fontSize: 18, fontWeight: 900, color: "var(--ys-text-strong)" }}>
          {item.title}
        </h3>
        <p style={{ margin: 0, fontSize: 12, color: "var(--ys-text-muted)" }}>
          {item.client_label && <span style={{ marginRight: 10 }}>{item.client_label}</span>}
          {formatDate(item.started_at)} {formatTime(item.started_at)}
          {item.ended_at && ` ~ ${formatTime(item.ended_at)}`}
          <span style={{ marginLeft: 10 }}>({item.utterance_count}발화)</span>
        </p>
        <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            onClick={() => { void handleExport(); }}
            disabled={exporting}
            style={{
              ...consoleStyles.action,
              ...(exporting ? consoleStyles.actionDisabled : null),
              fontSize: 12,
              padding: "8px 12px",
            }}
          >
            {exporting ? "내보내는 중..." : "보고서 내보내기"}
          </button>
        </div>
        {exportMsg && <p style={consoleStyles.statusInfo}>{exportMsg}</p>}
      </div>

      {/* Tabs: 요약 / 보고서 — each gets the full pane below */}
      <div style={{ display: "flex", gap: 4, padding: "10px 22px 0", borderBottom: "1px solid var(--ys-border-subtle)", flexShrink: 0 }}>
        {([["summary", "요약"], ["report", "보고서"]] as const).map(([key, label]) => {
          const active = tab === key;
          return (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              aria-selected={active}
              style={{
                appearance: "none",
                background: "none",
                border: "none",
                cursor: "pointer",
                padding: "8px 14px",
                marginBottom: -1,
                fontSize: 14,
                fontWeight: 800,
                color: active ? "var(--ys-text-strong)" : "var(--ys-text-muted)",
                borderBottom: active ? "2px solid var(--ys-accent)" : "2px solid transparent",
              }}
            >
              {label}
            </button>
          );
        })}
      </div>

      {/* Active tab content */}
      <div style={{ flex: 1, minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        {tab === "summary" && (
          <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
            {loadingSummary && (
              <p style={{ padding: 22, color: "var(--ys-text-muted)", fontSize: 13 }}>요약을 불러오는 중...</p>
            )}
            {!loadingSummary && summaryHtml && (
              <iframe
                sandbox=""
                srcDoc={summaryHtml}
                style={{ width: "100%", height: "100%", border: "none", display: "block" }}
                title="Summary preview"
              />
            )}
            {!loadingSummary && !summaryHtml && (
              <p style={{ padding: 22, color: "var(--ys-text-muted)", fontSize: 13 }}>요약이 없습니다.</p>
            )}
          </div>
        )}
        {tab === "report" && (
          <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
            {!item.report_ready && (
              <p style={{ padding: 22, color: "var(--ys-text-muted)", fontSize: 13 }}>
                보고서는 회의 종료 후 생성됩니다.
              </p>
            )}
            {item.report_ready && loadingHtml && (
              <p style={{ padding: 22, color: "var(--ys-text-muted)", fontSize: 13 }}>보고서를 불러오는 중...</p>
            )}
            {item.report_ready && htmlError && (
              <p style={{ ...consoleStyles.statusError, margin: 22 }}>{htmlError}</p>
            )}
            {item.report_ready && !loadingHtml && !htmlError && reportHtml && (
              <iframe
                sandbox=""
                srcDoc={reportHtml}
                style={{ width: "100%", height: "100%", border: "none", display: "block" }}
                title="Report preview"
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---- Main panel ------------------------------------------------------------

export function KnowledgeRepositoryPanel({ operatorToken, onTokenAcquired }: KnowledgeRepositoryPanelProps) {
  if (!operatorToken) {
    return <LoginGate onTokenAcquired={onTokenAcquired} />;
  }

  return <KnowledgeRepositoryInner operatorToken={operatorToken} />;
}

function KnowledgeRepositoryInner({ operatorToken }: { operatorToken: string }) {
  const {
    items,
    hasMore,
    loading,
    loadingMore,
    error,
    query,
    selectedId,
    selectedItem,
    reportHtmlCache,
    setQuery,
    loadMore,
    selectSession,
    cacheReportHtml,
    reload,
  } = useKnowledgeRepository(operatorToken);

  // Set of dateKeys whose groups are collapsed. Default: all expanded (empty set).
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  function toggleCollapse(dateKey: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(dateKey)) {
        next.delete(dateKey);
      } else {
        next.add(dateKey);
      }
      return next;
    });
  }

  const isSearching = query.trim() !== "";
  const grouped = isSearching ? null : groupByDate(items);

  return (
    <div style={{ display: "flex", height: "100%", minHeight: 0, overflow: "hidden" }}>
      {/* Left: list */}
      <div
        style={{
          width: 320,
          flexShrink: 0,
          borderRight: "1px solid var(--ys-border-subtle)",
          display: "flex",
          flexDirection: "column",
          height: "100%",
          minHeight: 0,
          overflow: "hidden",
        }}
      >
        {/* Search */}
        <div style={{ padding: "14px 16px 10px", borderBottom: "1px solid var(--ys-border-subtle)", flexShrink: 0 }}>
          <h2 style={{ margin: "0 0 12px", fontSize: 18, fontWeight: 900, color: "var(--ys-text-strong)" }}>
            회의 기록
          </h2>
          <input
            type="search"
            placeholder="검색..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ ...consoleStyles.input, fontSize: 13 }}
            aria-label="회의 기록 검색"
          />
        </div>

        {/* List body */}
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "8px 0" }}>
          {loading && (
            <p style={{ padding: "16px", color: "var(--ys-text-muted)", fontSize: 13 }}>불러오는 중...</p>
          )}
          {error && !loading && (
            <div style={{ padding: "12px 16px" }}>
              <p style={consoleStyles.statusError}>{error}</p>
              <button
                type="button"
                onClick={reload}
                style={{ ...consoleStyles.mutedAction, marginTop: 8, fontSize: 12 }}
              >
                다시 시도
              </button>
            </div>
          )}
          {!loading && !error && items.length === 0 && (
            <p style={{ padding: "16px", color: "var(--ys-text-muted)", fontSize: 13 }}>
              {isSearching ? "검색 결과가 없습니다." : "종료된 회의가 없습니다."}
            </p>
          )}

          {/* Search results (flat, with snippets) */}
          {!loading && isSearching && items.map((item) => (
            <SessionRow
              key={item.external_id}
              item={item}
              isSelected={selectedId === item.external_id}
              onSelect={selectSession}
              showSnippets
            />
          ))}

          {/* Grouped browse list */}
          {!loading && !isSearching && grouped && grouped.map(({ date, items: dateItems }) => {
            const isCollapsed = collapsed.has(date);
            return (
              <div key={date}>
                <button
                  type="button"
                  onClick={() => toggleCollapse(date)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    width: "100%",
                    padding: "10px 16px 6px",
                    border: "none",
                    borderBottom: "1px solid var(--ys-border-subtle)",
                    background: "transparent",
                    color: "var(--ys-text-muted)",
                    fontSize: 11,
                    fontWeight: 900,
                    letterSpacing: ".06em",
                    textTransform: "uppercase",
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                  aria-expanded={!isCollapsed}
                >
                  <span style={{ fontSize: 10 }}>{isCollapsed ? "▸" : "▾"}</span>
                  <span>{formatDate(`${date}T00:00:00`)}</span>
                  <span style={{ fontWeight: 700, opacity: 0.7 }}>({dateItems.length})</span>
                </button>
                {!isCollapsed && dateItems.map((item) => (
                  <SessionRow
                    key={item.external_id}
                    item={item}
                    isSelected={selectedId === item.external_id}
                    onSelect={selectSession}
                    showSnippets={false}
                  />
                ))}
              </div>
            );
          })}

          {/* Load more */}
          {hasMore && !loading && (
            <div style={{ padding: "10px 16px" }}>
              <button
                type="button"
                onClick={loadMore}
                disabled={loadingMore}
                style={{
                  ...consoleStyles.mutedAction,
                  width: "100%",
                  fontSize: 12,
                  ...(loadingMore ? consoleStyles.actionDisabled : null),
                }}
              >
                {loadingMore ? "불러오는 중..." : "더 보기"}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Right: detail */}
      <div style={{ flex: 1, minWidth: 0, height: "100%", minHeight: 0, overflow: "hidden" }}>
        {selectedItem ? (
          <DetailPane
            item={selectedItem}
            operatorToken={operatorToken}
            reportHtmlCache={reportHtmlCache}
            onCacheHtml={cacheReportHtml}
          />
        ) : (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%" }}>
            <p style={{ color: "var(--ys-text-muted)", fontSize: 13 }}>
              왼쪽에서 회의를 선택하세요.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

// ---- Session row -----------------------------------------------------------

type SessionRowProps = {
  item: SessionListItem;
  isSelected: boolean;
  onSelect: (id: string) => void;
  showSnippets: boolean;
};

function SessionRow({ item, isSelected, onSelect, showSnippets }: SessionRowProps) {
  return (
    <button
      type="button"
      onClick={() => onSelect(item.external_id)}
      style={{
        display: "block",
        width: "100%",
        textAlign: "left",
        padding: "10px 16px",
        border: "none",
        borderBottom: "1px solid var(--ys-border-subtle)",
        background: isSelected ? "var(--ys-accent-soft)" : "transparent",
        color: "var(--ys-text-body)",
        cursor: "pointer",
      }}
    >
      <p style={{ margin: "0 0 2px", fontWeight: 800, fontSize: 13, color: isSelected ? "var(--ys-on-accent)" : "var(--ys-text-strong)" }}>
        {item.title}
      </p>
      <p style={{ margin: 0, fontSize: 11, color: "var(--ys-text-muted)" }}>
        {item.client_label && <span style={{ marginRight: 6 }}>{item.client_label}</span>}
        {formatTime(item.started_at)}
      </p>
      {showSnippets && item.snippets && item.snippets.length > 0 && (
        <p style={{ margin: "4px 0 0", fontSize: 11, color: "var(--ys-text-muted)", lineHeight: 1.5 }}>
          {item.snippets.map((s, i) => (
            <span key={i}>
              {i > 0 && " … "}
              <SnippetText text={s} />
            </span>
          ))}
        </p>
      )}
    </button>
  );
}
// === ANCHOR: KNOWLEDGE_REPOSITORY_PANEL_END ===
