// === ANCHOR: ONECLICKSTART_TEST_START ===
import { describe, expect, it, vi } from "vitest";

import { runOneClickStart, type OneClickDeps } from "./oneClickStart";
import type { CreatedSession } from "./types";

const session: CreatedSession = { session_id: "sess-1", viewer_url: "https://host/v/tok" };

// === ANCHOR: ONECLICKSTART_TEST_BASEDEPS_START ===
function baseDeps(overrides: Partial<OneClickDeps> = {}): OneClickDeps {
  return {
    loadOperatorLogin: vi.fn().mockResolvedValue({ serverWsBase: "wss://host", email: "op@x", password: "pw" }),
    login: vi.fn().mockResolvedValue("token-abc"),
    createSession: vi.fn().mockResolvedValue(session),
    startSidecar: vi.fn().mockResolvedValue(undefined),
    now: () => new Date(2026, 5, 17, 9, 5),
    ...overrides,
  };
}
// === ANCHOR: ONECLICKSTART_TEST_BASEDEPS_END ===

describe("runOneClickStart", () => {
  it("logs in, creates the session with an auto title, then starts the sidecar", async () => {
    const deps = baseDeps();
    const result = await runOneClickStart(deps);

    expect(deps.login).toHaveBeenCalledWith("op@x", "pw");
    expect(deps.createSession).toHaveBeenCalledWith({ title: "2026-06-17 09:05 회의", operatorToken: "token-abc" });
    expect(deps.startSidecar).toHaveBeenCalledWith({ serverWsBase: "wss://host", sessionId: "sess-1" });
    expect(result).toEqual({ session, operatorToken: "token-abc", title: "2026-06-17 09:05 회의", sidecarStarted: true });
  });

  it("keeps the created session when the sidecar fails to start", async () => {
    const deps = baseDeps({ startSidecar: vi.fn().mockRejectedValue(new Error("boom")) });
    const result = await runOneClickStart(deps);

    expect(result.session).toEqual(session);
    expect(result.sidecarStarted).toBe(false);
    expect(result.sidecarError).toBe("boom");
  });

  it("does not create a session when login fails", async () => {
    const createSession = vi.fn();
    const deps = baseDeps({ login: vi.fn().mockRejectedValue(new Error("bad creds")), createSession });
    await expect(runOneClickStart(deps)).rejects.toThrow("bad creds");
    expect(createSession).not.toHaveBeenCalled();
  });
});
// === ANCHOR: ONECLICKSTART_TEST_END ===
