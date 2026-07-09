// === ANCHOR: TAB_CAPTURE_START ===
// getDisplayMedia(탭+오디오) → AudioContext(16kHz, 브라우저 내장 리샘플) →
// AudioWorklet 탭에서 Float32 블록을 메인 스레드로 전달. 마이크는 동일 워크릿
// 입력에 추가 연결(Web Audio는 다중 연결을 자동 합산)으로 믹싱한다.
// destination에 연결하지 않으므로 로컬 재생에는 영향 없음.
import { TARGET_SAMPLE_RATE } from "./pcm";

export class NoTabAudioError extends Error {
  constructor() {
    super("selected surface has no audio track");
    this.name = "NoTabAudioError";
  }
}

export type TabCaptureHandle = {
  attachMic(): Promise<void>;
  detachMic(): void;
  micAttached(): boolean;
  stop(): void;
};

const WORKLET_SOURCE = `
class PcmTapProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel && channel.length > 0) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor("pcm-tap", PcmTapProcessor);
`;

export async function startTabCapture(opts: {
  onPcmBlock: (block: Float32Array) => void;
  onEnded: () => void;
}): Promise<TabCaptureHandle> {
  const display = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
  const audioTracks = display.getAudioTracks();
  if (audioTracks.length === 0) {
    display.getTracks().forEach((t) => t.stop());
    throw new NoTabAudioError();
  }

  const ctx = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
  const workletUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "text/javascript" }));
  try {
    await ctx.audioWorklet.addModule(workletUrl);
  } finally {
    URL.revokeObjectURL(workletUrl);
  }
  const tap = new AudioWorkletNode(ctx, "pcm-tap", {
    numberOfInputs: 1,
    numberOfOutputs: 0,
    channelCount: 1,
    channelCountMode: "explicit",
    channelInterpretation: "speakers",
  });
  tap.port.onmessage = (event: MessageEvent<Float32Array>) => opts.onPcmBlock(event.data);

  const tabSource = ctx.createMediaStreamSource(new MediaStream(audioTracks));
  tabSource.connect(tap);

  let stopped = false;
  let micStream: MediaStream | null = null;
  let micSource: MediaStreamAudioSourceNode | null = null;

  function handleEnded() {
    if (stopped) return;
    stop();
    opts.onEnded();
  }
  display.getTracks().forEach((track) => track.addEventListener("ended", handleEnded));

  function detachMic() {
    micSource?.disconnect();
    micSource = null;
    micStream?.getTracks().forEach((t) => t.stop());
    micStream = null;
  }

  function stop() {
    if (stopped) return;
    stopped = true;
    detachMic();
    tabSource.disconnect();
    tap.port.onmessage = null;
    display.getTracks().forEach((t) => t.stop());
    void ctx.close();
  }

  return {
    async attachMic() {
      if (stopped || micSource) return;
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      micSource = ctx.createMediaStreamSource(micStream);
      micSource.connect(tap);
    },
    detachMic,
    micAttached: () => micSource !== null,
    stop,
  };
}
// === ANCHOR: TAB_CAPTURE_END ===
