import { describe, expect, it } from "vitest";

import { EMPTY_META, loadCredentialsMeta } from "./credentials";

describe("loadCredentialsMeta", () => {
  it("returns EMPTY_META when there is no Tauri runtime", async () => {
    // jsdom has no __TAURI_INTERNALS__, so invoke must not be reached.
    await expect(loadCredentialsMeta()).resolves.toEqual(EMPTY_META);
  });

  it("EMPTY_META carries no credentials", () => {
    expect(EMPTY_META.hasCredentials).toBe(false);
    expect(EMPTY_META.hasDeviceKey).toBe(false);
  });
});
