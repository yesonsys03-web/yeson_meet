import { describe, expect, it } from "vitest";
import { resolveServerWsBase, wsBaseFromDiscovery } from "./serverDiscovery";

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
