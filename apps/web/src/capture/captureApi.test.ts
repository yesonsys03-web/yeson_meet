// === ANCHOR: CAPTURE_API_TEST_START ===
import { describe, expect, it } from "vitest";
import { credentialStore, operatorWsUrl, sidecarWsUrl } from "./captureApi";

const loc = { protocol: "https:", host: "example.trycloudflare.com" };

describe("sidecarWsUrl", () => {
  it("https면 wss, 쿼리에 key/session", () => {
    const url = new URL(sidecarWsUrl("dev-key", "sess-1", loc));
    expect(url.protocol).toBe("wss:");
    expect(url.pathname).toBe("/ws/sidecar");
    expect(url.searchParams.get("key")).toBe("dev-key");
    expect(url.searchParams.get("session")).toBe("sess-1");
  });
  it("http(LAN dev)면 ws", () => {
    expect(sidecarWsUrl("k", "s", { protocol: "http:", host: "localhost:5173" })).toMatch(/^ws:\/\/localhost:5173/);
  });
});

describe("operatorWsUrl", () => {
  it("session/access 쿼리", () => {
    const url = new URL(operatorWsUrl("sess-1", "tok", loc));
    expect(url.pathname).toBe("/ws/operator");
    expect(url.searchParams.get("session")).toBe("sess-1");
    expect(url.searchParams.get("access")).toBe("tok");
  });
});

describe("credentialStore", () => {
  it("디바이스 키 저장/조회/삭제", () => {
    const backing = new Map<string, string>();
    const fake = {
      getItem: (k: string) => backing.get(k) ?? null,
      setItem: (k: string, v: string) => void backing.set(k, v),
      removeItem: (k: string) => void backing.delete(k),
    } as Storage;
    const store = credentialStore(fake);
    expect(store.loadDeviceKey()).toBeNull();
    store.saveDeviceKey("abc");
    expect(store.loadDeviceKey()).toBe("abc");
    store.clearDeviceKey();
    expect(store.loadDeviceKey()).toBeNull();
  });
});
// === ANCHOR: CAPTURE_API_TEST_END ===
