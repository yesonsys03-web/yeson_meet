import { describe, expect, it } from "vitest";
import { normalizeServerWsBase, resolveServerWsBase, wsBaseFromDiscovery } from "./serverDiscovery";

describe("normalizeServerWsBase", () => {
  it("bare IP → ws://IP:8000", () => {
    expect(normalizeServerWsBase("192.168.0.51")).toBe("ws://192.168.0.51:8000");
  });
  it("bare IP with custom port → ws://IP:port", () => {
    expect(normalizeServerWsBase("192.168.0.51:9000")).toBe("ws://192.168.0.51:9000");
  });
  it("existing ws:// URL → unchanged", () => {
    expect(normalizeServerWsBase("ws://x:8000")).toBe("ws://x:8000");
  });
  it("existing wss:// URL → unchanged", () => {
    expect(normalizeServerWsBase("wss://x:8000")).toBe("wss://x:8000");
  });
  it("empty string → empty string", () => {
    expect(normalizeServerWsBase("")).toBe("");
  });
  it("whitespace-only → empty string", () => {
    expect(normalizeServerWsBase("   ")).toBe("");
  });
  it("trims surrounding whitespace from bare IP", () => {
    expect(normalizeServerWsBase("  192.168.0.51  ")).toBe("ws://192.168.0.51:8000");
  });
});

describe("wsBaseFromDiscovery", () => {
  it("assembles ws:// from ip and port", () => {
    expect(wsBaseFromDiscovery({ ip: "192.168.1.23", port: 8000 })).toBe("ws://192.168.1.23:8000");
  });
});

describe("resolveServerWsBase", () => {
  it("prefers localhost when the local server responds", async () => {
    const result = await resolveServerWsBase({
      probeLocal: async () => true,
      discover: async () => ({ ip: "192.168.1.23", port: 8000 }),
    });
    expect(result).toBe("ws://127.0.0.1:8000");
  });

  it("falls back to mDNS discovery when localhost is absent", async () => {
    const result = await resolveServerWsBase({
      probeLocal: async () => false,
      discover: async () => ({ ip: "192.168.1.23", port: 8000 }),
    });
    expect(result).toBe("ws://192.168.1.23:8000");
  });

  it("returns null when nothing is found", async () => {
    const result = await resolveServerWsBase({
      probeLocal: async () => false,
      discover: async () => null,
    });
    expect(result).toBeNull();
  });
});
