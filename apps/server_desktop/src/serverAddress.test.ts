import { describe, expect, it } from "vitest";
import { formatServerWsAddress } from "./serverAddress";

describe("formatServerWsAddress", () => {
  it("builds a ws:// address from ip and port", () => {
    expect(formatServerWsAddress("192.168.1.23", 8000)).toBe("ws://192.168.1.23:8000");
  });
});
