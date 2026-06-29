// === ANCHOR: SERVERADDRESSHYDRATION_TEST_START ===
// P2 tests — keychain-authoritative server WS address; localStorage = derived cache.
// Runs in the default vitest `node` environment (no jsdom): we stub the minimal
// `window` / `localStorage` surface the code touches, matching production shape.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const STORAGE_KEY = "yeson-meet-desktop-setup";

type MemStore = { store: Record<string, string> };

// === ANCHOR: SERVERADDRESSHYDRATION_TEST_STOREDSERVERWSBASE_START ===
function storedServerWsBase(mem: MemStore): string {
  const raw = mem.store[STORAGE_KEY] ?? "{}";
  return (JSON.parse(raw) as { serverWsBase?: string }).serverWsBase ?? "";
}
// === ANCHOR: SERVERADDRESSHYDRATION_TEST_STOREDSERVERWSBASE_END ===

// === ANCHOR: SERVERADDRESSHYDRATION_TEST_INSTALLDOM_START ===
function installDom(opts: { tauri: boolean }): MemStore {
  const mem: MemStore = { store: {} };
  const localStorage = {
    getItem: (k: string) => (k in mem.store ? mem.store[k] : null),
    setItem: (k: string, v: string) => {
      mem.store[k] = v;
    },
    removeItem: (k: string) => {
      delete mem.store[k];
    },
  };
  const win: Record<string, unknown> = {
    localStorage,
    dispatchEvent: () => true,
  };
  if (opts.tauri) win.__TAURI_INTERNALS__ = {};
  vi.stubGlobal("window", win);
  vi.stubGlobal("localStorage", localStorage);
  // CustomEvent is referenced by storeValues; provide a no-op constructor.
  vi.stubGlobal(
    "CustomEvent",
    class {
      constructor(public type: string) {}
    },
  );
  return mem;
}
// === ANCHOR: SERVERADDRESSHYDRATION_TEST_INSTALLDOM_END ===

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// U2 — empty-default behavior: removed hardcoded default; validators fire.
// ---------------------------------------------------------------------------
describe("U2 empty default behavior", () => {
  beforeEach(() => installDom({ tauri: false }));

  it("defaultValues().serverWsBase is empty and httpBaseFromWs('') throws", async () => {
    const { DEFAULT_VALUES, httpBaseFromWs } = await import("./setupValues");
    expect(DEFAULT_VALUES.serverWsBase).toBe("");
    expect(DEFAULT_VALUES.viewerUrl).toBe("");
    expect(() => httpBaseFromWs("")).toThrow();
  });

  it("apiBase() with empty localStorage + no VITE_API_BASE throws ('go set it')", async () => {
    // No stored value → loadValues() returns the empty default → httpBaseFromWs throws.
    const { apiBase } = await import("../console/sessionApi");
    // Only assert the throw when no VITE_API_BASE override is present.
    if (!import.meta.env.VITE_API_BASE) {
      expect(() => apiBase()).toThrow();
    }
  });
});

// ---------------------------------------------------------------------------
// U3 — hydrator logic.
// ---------------------------------------------------------------------------
describe("U3 hydrator", () => {
  it("no-ops in browser preview (!hasTauriRuntime): localStorage untouched", async () => {
    const mem = installDom({ tauri: false });
    mem.store[STORAGE_KEY] = JSON.stringify({ serverWsBase: "wss://keep" });
    const { hydrateServerAddressFromKeychain } = await import("./credentials");
    await hydrateServerAddressFromKeychain();
    expect(storedServerWsBase(mem)).toBe("wss://keep");
  });

  it("writes keychain serverWsBase into localStorage when present", async () => {
    const mem = installDom({ tauri: true });
    vi.doMock("@tauri-apps/api/core", () => ({
      invoke: vi.fn(async (cmd: string) => {
        if (cmd === "credentials_meta") {
          return { hasCredentials: true, serverWsBase: "wss://from-keychain", email: "a@b", hasDeviceKey: true };
        }
        return undefined;
      }),
    }));
    const { hydrateServerAddressFromKeychain } = await import("./credentials");
    await hydrateServerAddressFromKeychain();
    expect(storedServerWsBase(mem)).toBe("wss://from-keychain");
  });

  it("keychain empty → localStorage untouched (migration §2 step 4)", async () => {
    const mem = installDom({ tauri: true });
    mem.store[STORAGE_KEY] = JSON.stringify({ serverWsBase: "wss://existing" });
    vi.doMock("@tauri-apps/api/core", () => ({
      invoke: vi.fn(async (cmd: string) => {
        if (cmd === "credentials_meta") {
          return { hasCredentials: false, serverWsBase: "", email: "", hasDeviceKey: false };
        }
        return undefined;
      }),
    }));
    const { hydrateServerAddressFromKeychain } = await import("./credentials");
    await hydrateServerAddressFromKeychain();
    expect(storedServerWsBase(mem)).toBe("wss://existing");
  });
});

// ---------------------------------------------------------------------------
// U1 / U4 — hydrated value drives apiBase(); migration is non-destructive.
// ---------------------------------------------------------------------------
describe("U1/U4 hydrated value drives apiBase, migration non-destructive", () => {
  it("after hydrate, apiBase() returns https host from the keychain value", async () => {
    const mem = installDom({ tauri: true });
    vi.doMock("@tauri-apps/api/core", () => ({
      invoke: vi.fn(async (cmd: string) => {
        if (cmd === "credentials_meta") {
          return { hasCredentials: true, serverWsBase: "wss://host-a", email: "a@b", hasDeviceKey: true };
        }
        return undefined;
      }),
    }));
    const { hydrateServerAddressFromKeychain } = await import("./credentials");
    await hydrateServerAddressFromKeychain();
    expect(storedServerWsBase(mem)).toBe("wss://host-a");

    const { apiBase } = await import("../console/sessionApi");
    if (!import.meta.env.VITE_API_BASE) {
      expect(apiBase()).toBe("https://host-a");
    }
  });

  it("existing localStorage value is preserved when keychain is empty", async () => {
    const mem = installDom({ tauri: true });
    mem.store[STORAGE_KEY] = JSON.stringify({ serverWsBase: "wss://192.168.0.38" });
    vi.doMock("@tauri-apps/api/core", () => ({
      invoke: vi.fn(async () => ({ hasCredentials: false, serverWsBase: "", email: "", hasDeviceKey: false })),
    }));
    const { hydrateServerAddressFromKeychain } = await import("./credentials");
    await hydrateServerAddressFromKeychain();
    expect(storedServerWsBase(mem)).toBe("wss://192.168.0.38");
  });
});

// ---------------------------------------------------------------------------
// P2 regression — a post-device-key manual address edit calls update_server_ws_base
// (partial-merge keychain write) and SURVIVES a hydrate. This is the lost-write the
// verifier flagged: previously the write-through was gated on !hasDeviceKey, so an
// edit made AFTER a device key existed never reached the keychain and the next
// hydrate clobbered it back to the stale stored address.
// ---------------------------------------------------------------------------
describe("P2 post-device-key address edit survives hydrate", () => {
  it("updateServerWsBase writes only the address (key preserved) and hydrate keeps it", async () => {
    const mem = installDom({ tauri: true });
    // Keychain starts with a device key already present and a stale address.
    let storedAddress = "wss://stale";
    const invoke = vi.fn(async (cmd: string, args?: { serverWsBase?: string }) => {
      if (cmd === "update_server_ws_base") {
        // Partial-merge: ONLY the address changes; the device key is untouched server-side.
        storedAddress = args?.serverWsBase ?? storedAddress;
        return undefined;
      }
      if (cmd === "credentials_meta") {
        return { hasCredentials: true, serverWsBase: storedAddress, email: "a@b", hasDeviceKey: true };
      }
      return undefined;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { updateServerWsBase, hydrateServerAddressFromKeychain } = await import("./credentials");

    // The operator edits the address after the device key exists.
    await updateServerWsBase("wss://edited");
    expect(invoke).toHaveBeenCalledWith("update_server_ws_base", { serverWsBase: "wss://edited" });

    // A subsequent hydrate (mock returns the NEW keychain value) keeps the edit.
    await hydrateServerAddressFromKeychain();
    expect(storedServerWsBase(mem)).toBe("wss://edited");
  });
});
// === ANCHOR: SERVERADDRESSHYDRATION_TEST_END ===
