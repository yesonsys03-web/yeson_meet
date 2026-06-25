// === ANCHOR: KNOWLEDGE_API_TEST_START ===
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock setupValues so apiBase() resolves to a predictable base URL.
vi.mock("../setup/setupValues", async (importOriginal) => {
  const real = await importOriginal<typeof import("../setup/setupValues")>();
  return {
    ...real,
    loadValues: () => ({
      ...real.DEFAULT_VALUES,
      serverWsBase: "ws://localhost:8000",
    }),
  };
});

vi.mock("../diagnostics/appLog", () => ({
  appLogger: {
    latency: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

import { listSessions } from "./knowledgeApi";
import type { SessionListOut } from "./knowledgeApi";

const MOCK_ITEM = {
  external_id: "sess-abc",
  title: "Weekly sync",
  client_label: "CLIENT-A",
  status: "ended",
  started_at: "2026-06-25T09:00:00Z",
  ended_at: "2026-06-25T10:00:00Z",
  owner_user_id: 1,
  visibility: "org",
  utterance_count: 42,
  report_ready: true,
};

describe("listSessions", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("calls GET /api/v1/sessions with no q when not searching", async () => {
    const payload: SessionListOut = { items: [MOCK_ITEM], has_more: false };
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => payload,
    });

    const result = await listSessions({ operatorToken: "tok-1" });

    expect(result.items).toHaveLength(1);
    expect(result.has_more).toBe(false);
    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toMatch(/\/api\/v1\/sessions/);
    expect(url).not.toContain("q=");
  });

  it("sends Authorization: Bearer header", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [], has_more: false }),
    });

    await listSessions({ operatorToken: "my-secret-token" });

    const [, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["Authorization"]).toBe("Bearer my-secret-token");
  });

  it("includes q param when query is provided", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [], has_more: false }),
    });

    await listSessions({ q: "project review", operatorToken: "tok" });

    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toContain("q=project+review");
  });

  it("sends limit and offset params", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [], has_more: false }),
    });

    await listSessions({ limit: 10, offset: 20, operatorToken: "tok" });

    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toContain("limit=10");
    expect(url).toContain("offset=20");
  });

  it("sends scope and status params", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [], has_more: false }),
    });

    await listSessions({ scope: "mine", status: "all", operatorToken: "tok" });

    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toContain("scope=mine");
    expect(url).toContain("status=all");
  });

  it("throws on non-OK HTTP response", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({}),
    });

    await expect(listSessions({ operatorToken: "bad-tok" })).rejects.toThrow("401");
  });

  it("deserializes snippets field when present (FTS5 path — server uses [bracket] delimiters)", async () => {
    // The server's FTS5 snippet() emits square-bracket delimiters, e.g. "send the [budget] report".
    const itemWithSnippets = { ...MOCK_ITEM, snippets: ["send the [budget] report"] };
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [itemWithSnippets], has_more: false }),
    });

    const result = await listSessions({ q: "budget", operatorToken: "tok" });
    expect(result.items[0]!.snippets).toEqual(["send the [budget] report"]);
  });

  it("deserializes without snippets (LIKE fallback / no-q path) — same code path", async () => {
    // Fallback parity: a response WITHOUT snippets must deserialize through the
    // same code path as one WITH snippets. The client never branches on engine.
    const itemNoSnippets = { ...MOCK_ITEM };
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [itemNoSnippets], has_more: false }),
    });

    const result = await listSessions({ q: "text", operatorToken: "tok" });
    expect(result.items[0]!.snippets).toBeUndefined();
    // Verify the other mandatory fields are present
    expect(result.items[0]!.external_id).toBe("sess-abc");
    expect(result.items[0]!.report_ready).toBe(true);
  });

  it("has_more:true is forwarded correctly", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [MOCK_ITEM], has_more: true }),
    });

    const result = await listSessions({ operatorToken: "tok" });
    expect(result.has_more).toBe(true);
  });
});

// ---- Fallback parity contract test -----------------------------------------
// Asserts that both engine responses (FTS5 with [bracket] snippets, LIKE without)
// deserialize into SessionListItem through the identical code path.

describe("SessionListItem fallback parity", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("FTS response WITH [bracket] snippets and LIKE response WITHOUT snippets both produce valid SessionListItem", async () => {
    // FTS5 path: server wraps matched terms in square brackets
    const ftsItem = { ...MOCK_ITEM, snippets: ["send the [budget] report to [Alice]"] };
    const likeItem = { ...MOCK_ITEM }; // LIKE fallback: no snippets key

    // Simulate FTS response
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ items: [ftsItem], has_more: false }),
    });
    const ftsResult = await listSessions({ q: "budget", operatorToken: "tok" });

    // Simulate LIKE fallback response (same q, no snippets)
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ items: [likeItem], has_more: false }),
    });
    const likeResult = await listSessions({ q: "budget", operatorToken: "tok" });

    // Both must have the same mandatory fields
    const ftsFields = { ...ftsResult.items[0] };
    const likeFields = { ...likeResult.items[0] };
    delete (ftsFields as Partial<typeof ftsFields>).snippets;
    delete (likeFields as Partial<typeof likeFields>).snippets;
    expect(ftsFields).toEqual(likeFields);

    // snippets present on FTS (bracket-delimited), absent on LIKE — both OK, client never branches
    expect(ftsResult.items[0]!.snippets).toEqual(["send the [budget] report to [Alice]"]);
    expect(likeResult.items[0]!.snippets).toBeUndefined();
  });
});

// ---- SnippetText render logic tests ----------------------------------------
// Tests for the bracket-parser that boldens [matched] terms in snippet strings.
// Exercises the pure parsing logic without a DOM renderer.

describe("snippet bracket parsing", () => {
  // Extract the parsing logic by testing its output shape.
  // We test it indirectly via the same algorithm used in SnippetText:
  // splitting on [bracket] segments and identifying bold vs plain parts.

  function parseSnippet(text: string): Array<{ bold: boolean; text: string }> {
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
        parts.push({ bold: false, text: rest.slice(open) });
        break;
      }
      parts.push({ bold: true, text: rest.slice(open + 1, close) });
      rest = rest.slice(close + 1);
    }
    return parts;
  }

  it("bolds a single bracketed match and leaves surrounding text plain", () => {
    const parts = parseSnippet("send the [budget] report");
    expect(parts).toEqual([
      { bold: false, text: "send the " },
      { bold: true, text: "budget" },
      { bold: false, text: " report" },
    ]);
  });

  it("bolds multiple bracketed matches", () => {
    const parts = parseSnippet("send the [budget] report to [Alice]");
    expect(parts).toEqual([
      { bold: false, text: "send the " },
      { bold: true, text: "budget" },
      { bold: false, text: " report to " },
      { bold: true, text: "Alice" },
    ]);
  });

  it("renders plain text with no brackets as a single non-bold part (LIKE fallback / no match)", () => {
    const parts = parseSnippet("no match here at all");
    expect(parts).toEqual([{ bold: false, text: "no match here at all" }]);
  });

  it("handles a bracket at the very start of the snippet", () => {
    const parts = parseSnippet("[budget] matters");
    expect(parts).toEqual([
      { bold: true, text: "budget" },
      { bold: false, text: " matters" },
    ]);
  });

  it("handles a bracket at the very end of the snippet", () => {
    const parts = parseSnippet("the item is [budget]");
    expect(parts).toEqual([
      { bold: false, text: "the item is " },
      { bold: true, text: "budget" },
    ]);
  });

  it("treats unclosed bracket as plain text (graceful fallback)", () => {
    const parts = parseSnippet("broken [snippet");
    expect(parts).toEqual([
      { bold: false, text: "broken " },
      { bold: false, text: "[snippet" },
    ]);
  });

  it("empty string produces empty parts array", () => {
    const parts = parseSnippet("");
    expect(parts).toEqual([]);
  });
});
// ---- localDateKey grouping tests -------------------------------------------
// Verifies that grouping uses the LOCAL calendar date, not the raw UTC slice.

describe("localDateKey grouping", () => {
  // Replicate the pure logic from KnowledgeRepositoryPanel for unit testing.
  function localDateKey(iso: string): string {
    const d = new Date(iso);
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  function groupByDate(items: Array<{ started_at: string }>): Array<{ date: string; items: typeof items }> {
    const groups: Map<string, typeof items> = new Map();
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

  it("two meetings on the same local date land in one group", () => {
    const items = [
      { started_at: "2026-06-25T09:00:00Z" },
      { started_at: "2026-06-25T11:30:00Z" },
    ];
    const groups = groupByDate(items);
    // Both should be in the same group regardless of local offset
    expect(groups).toHaveLength(1);
    expect(groups[0]!.items).toHaveLength(2);
  });

  it("meetings on different dates produce separate groups", () => {
    const items = [
      { started_at: "2026-06-24T10:00:00Z" },
      { started_at: "2026-06-25T10:00:00Z" },
    ];
    const groups = groupByDate(items);
    expect(groups).toHaveLength(2);
  });

  it("produces a valid YYYY-MM-DD key", () => {
    const key = localDateKey("2026-06-05T08:00:00Z");
    expect(key).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("empty items list produces empty groups", () => {
    expect(groupByDate([])).toHaveLength(0);
  });
});

// ---- collapse toggle logic tests --------------------------------------------
// Tests the Set-based toggle used by the collapsible date groups.

describe("collapse toggle logic", () => {
  // Replicate the pure toggle reducer from KnowledgeRepositoryInner.
  function toggleCollapse(prev: Set<string>, dateKey: string): Set<string> {
    const next = new Set(prev);
    if (next.has(dateKey)) {
      next.delete(dateKey);
    } else {
      next.add(dateKey);
    }
    return next;
  }

  it("toggling an expanded group collapses it (adds to set)", () => {
    const result = toggleCollapse(new Set(), "2026-06-25");
    expect(result.has("2026-06-25")).toBe(true);
  });

  it("toggling a collapsed group expands it (removes from set)", () => {
    const result = toggleCollapse(new Set(["2026-06-25"]), "2026-06-25");
    expect(result.has("2026-06-25")).toBe(false);
  });

  it("toggling one group does not affect other groups", () => {
    const initial = new Set(["2026-06-24"]);
    const result = toggleCollapse(initial, "2026-06-25");
    expect(result.has("2026-06-24")).toBe(true);
    expect(result.has("2026-06-25")).toBe(true);
  });

  it("default state (empty set) means all groups are expanded", () => {
    const collapsed = new Set<string>();
    expect(collapsed.has("2026-06-25")).toBe(false);
  });
});
// === ANCHOR: KNOWLEDGE_API_TEST_END ===
