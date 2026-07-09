// === ANCHOR: CAPTURE_PCM_START ===
// 사이드카(apps/client_sidecar)의 브라우저 등가물: 서버 wire 포맷은
// 16kHz mono pcm_s16le 고정, 청크는 20ms=320샘플=640바이트 관례.
export const TARGET_SAMPLE_RATE = 16000;
export const CHUNK_SAMPLES = 320;
export const CHUNK_BYTES = CHUNK_SAMPLES * 2;

export function floatTo16le(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    out[i] = Math.round(clamped * 32767);
  }
  return out;
}

// apps/client_sidecar/audio/rms.py의 pcm16_dbfs 포팅 (빈 입력 -120, epsilon 1e-12).
export function pcm16Dbfs(samples: Int16Array): number {
  if (samples.length === 0) return -120;
  let sumSquares = 0;
  for (let i = 0; i < samples.length; i++) {
    const v = samples[i] / 32768;
    sumSquares += v * v;
  }
  const rms = Math.sqrt(sumSquares / samples.length);
  return 20 * Math.log10(rms + 1e-12);
}

export class PcmFramer {
  private pending: number[] = [];

  push(block: Float32Array): Uint8Array[] {
    const converted = floatTo16le(block);
    for (let i = 0; i < converted.length; i++) this.pending.push(converted[i]);
    const chunks: Uint8Array[] = [];
    while (this.pending.length >= CHUNK_SAMPLES) {
      const frame = this.pending.splice(0, CHUNK_SAMPLES);
      const bytes = new Uint8Array(CHUNK_BYTES);
      const view = new DataView(bytes.buffer);
      for (let i = 0; i < CHUNK_SAMPLES; i++) view.setInt16(i * 2, frame[i], true);
      chunks.push(bytes);
    }
    return chunks;
  }
}
// === ANCHOR: CAPTURE_PCM_END ===
