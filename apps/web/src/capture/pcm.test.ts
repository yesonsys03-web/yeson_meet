// === ANCHOR: PCM_TEST_START ===
import { describe, expect, it } from "vitest";
import { CHUNK_BYTES, CHUNK_SAMPLES, PcmFramer, floatTo16le, pcm16Dbfs } from "./pcm";

describe("floatTo16le", () => {
  it("스케일과 클램프", () => {
    const out = floatTo16le(new Float32Array([0, 0.5, 1.0, -1.0, 2.0, -2.0]));
    expect(out[0]).toBe(0);
    expect(out[1]).toBe(16384); // round(0.5*32767)=16384
    expect(out[2]).toBe(32767);
    expect(out[3]).toBe(-32767);
    expect(out[4]).toBe(32767); // 클램프
    expect(out[5]).toBe(-32767);
  });
});

describe("pcm16Dbfs", () => {
  it("빈 입력은 -120 (사이드카 rms.py와 동일)", () => {
    expect(pcm16Dbfs(new Int16Array(0))).toBe(-120);
  });
  it("풀스케일 사인파는 약 -3dBFS", () => {
    const n = 320;
    const s = new Int16Array(n);
    for (let i = 0; i < n; i++) s[i] = Math.round(32767 * Math.sin((2 * Math.PI * i * 8) / n));
    const db = pcm16Dbfs(s);
    expect(db).toBeGreaterThan(-3.5);
    expect(db).toBeLessThan(-2.5);
  });
});

describe("PcmFramer", () => {
  it("320샘플 단위로 640바이트 리틀엔디언 청크를 방출한다", () => {
    const framer = new PcmFramer();
    // 100 + 250 = 350 샘플 → 청크 1개(320) + 잔여 30
    expect(framer.push(new Float32Array(100).fill(0.5))).toEqual([]);
    const chunks = framer.push(new Float32Array(250).fill(0.5));
    expect(chunks.length).toBe(1);
    expect(chunks[0].byteLength).toBe(CHUNK_BYTES);
    // 리틀엔디언 검증: 0.5 → 16384 = 0x4000 → LE 바이트 [0x00, 0x40]
    expect(chunks[0][0]).toBe(0x00);
    expect(chunks[0][1]).toBe(0x40);
  });
  it("여러 청크를 한 번에 방출한다", () => {
    const framer = new PcmFramer();
    const chunks = framer.push(new Float32Array(CHUNK_SAMPLES * 3).fill(0.1));
    expect(chunks.length).toBe(3);
  });
});
// === ANCHOR: PCM_TEST_END ===
