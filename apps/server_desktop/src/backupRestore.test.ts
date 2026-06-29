import { describe, it, expect } from "vitest";
import { pairBackups } from "./backupRestore";

describe("pairBackups", () => {
  it("pairs snapshot with its storage zip by stamp", () => {
    const files = [
      "yeson-meet-20260629-120000.db",
      "storage-20260629-120000.zip",
      "yeson-meet-20260628-090000.db",
    ];
    const pairs = pairBackups(files);
    expect(pairs).toEqual([
      { stamp: "20260629-120000", snapshot: "yeson-meet-20260629-120000.db", storageZip: "storage-20260629-120000.zip" },
      { stamp: "20260628-090000", snapshot: "yeson-meet-20260628-090000.db", storageZip: null },
    ]);
  });

  it("sorts newest first", () => {
    const files = [
      "yeson-meet-20260628-090000.db",
      "yeson-meet-20260629-120000.db",
    ];
    const pairs = pairBackups(files);
    expect(pairs[0]!.stamp).toBe("20260629-120000");
  });

  it("ignores non-snapshot files", () => {
    const files = ["yeson-meet-20260629-120000.json", "README.md", "yeson-meet-20260629-120000.db"];
    const pairs = pairBackups(files);
    expect(pairs).toHaveLength(1);
  });

  it("returns empty for no snapshots", () => {
    expect(pairBackups(["storage-20260629-120000.zip"])).toEqual([]);
  });
});
