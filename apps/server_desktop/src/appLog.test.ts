// === ANCHOR: APPLOG_TEST_START ===
import { describe, it, expect } from "vitest";
import {
  filterLogEntries,
  formatAppLogEntry,
  formatAppLogSnapshot,
  type AppLogEntry,
} from "./appLog";

// === ANCHOR: APPLOG_TEST_ENTRY_START ===
const entry = (over: Partial<AppLogEntry>): AppLogEntry => ({
  id: 1,
  ts: "2026-06-23T08:00:01.000Z",
  level: "info",
  source: "server",
  message: "hello world",
  ...over,
});
// === ANCHOR: APPLOG_TEST_ENTRY_END ===

describe("filterLogEntries", () => {
  const entries = [
    entry({ id: 1, level: "info", source: "server", message: "started ok" }),
    entry({ id: 2, level: "warn", source: "gemini", message: "slow turn" }),
    entry({ id: 3, level: "error", source: "server", message: "boom failure" }),
  ];

  it("returns all when level=all and query empty", () => {
    expect(filterLogEntries(entries, "all", "")).toHaveLength(3);
  });

  it("filters by level", () => {
    const out = filterLogEntries(entries, "warn", "");
    expect(out).toHaveLength(1);
    expect(out[0]!.id).toBe(2);
  });

  it("filters by case-insensitive substring over source+message", () => {
    expect(filterLogEntries(entries, "all", "GEMINI")).toHaveLength(1);
    expect(filterLogEntries(entries, "all", "boom")).toHaveLength(1);
    expect(filterLogEntries(entries, "all", "nope")).toHaveLength(0);
  });

  it("combines level and query (AND)", () => {
    expect(filterLogEntries(entries, "error", "server")).toHaveLength(1);
    expect(filterLogEntries(entries, "error", "gemini")).toHaveLength(0);
  });
});

describe("formatAppLogEntry / formatAppLogSnapshot", () => {
  it("formats one entry as a single line in local time with offset", () => {
    const line = formatAppLogEntry(entry({ message: "ready" }));
    // 로컬 시간 + 오프셋 표기 — 실행 환경 타임존과 무관하게 모양과 시각 동치성만 잠근다.
    const m = line.match(
      /^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2})\] INFO source=server message=ready$/,
    );
    expect(m).not.toBeNull();
    // 표기된 로컬 시각은 원본 UTC 순간과 같은 시점이어야 한다(라운드트립).
    const ts = m?.[1] ?? "";
    expect(new Date(ts.replace(" ", "T")).getTime())
      .toBe(Date.parse("2026-06-23T08:00:01.000Z"));
  });

  it("wraps a snapshot with a header and entry count", () => {
    const text = formatAppLogSnapshot([entry({}), entry({ id: 2 })]);
    expect(text).toContain("yeson server console log");
    expect(text).toContain("entries=2");
    expect(text.split("\n").filter((l) => l.startsWith("[")).length).toBe(2);
  });
});
// === ANCHOR: APPLOG_TEST_END ===
