import { MAX_CLIP_S, encodeClip } from "./audio";

/**
 * Hold-to-talk on the tablet: records with the tablet's microphone and returns one
 * base64 clip for an `audio_clip` command. Transcription happens on the PC.
 */
export class MicError extends Error {}

export class TabletMic {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private chunks: Float32Array[] = [];
  private samples = 0;
  private limitTimer: ReturnType<typeof setTimeout> | null = null;
  // Bumped by every start() and stop(): a start whose await outlives a stop (the finger
  // lifted to answer Android's permission prompt) sees a newer generation and backs out.
  private gen = 0;

  constructor(private readonly onLevel: (rms: number) => void = () => {}) {}

  get recording(): boolean {
    return this.ctx !== null;
  }

  async start(onLimit: () => void): Promise<void> {
    if (this.ctx) await this.stop(); // never reuse a stale session
    const my = ++this.gen;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      throw new MicError("The microphone needs the secure https:// address. Open Barnaby from the QR link.");
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (err) {
      if (my !== this.gen) return; // released meanwhile: nothing to report
      const name = err instanceof Error ? err.name : "";
      throw new MicError(
        name === "NotAllowedError"
          ? "Chrome blocked the microphone. Tap the icon left of the address, then Permissions, Microphone, Allow."
          : "Couldn't open the tablet's microphone.",
      );
    }
    if (my !== this.gen) {
      stream.getTracks().forEach((t) => t.stop()); // released while the prompt was open
      return;
    }
    this.stream = stream;
    const ctx = new AudioContext();
    this.ctx = ctx;
    this.chunks = [];
    this.samples = 0;
    this.source = ctx.createMediaStreamSource(this.stream);
    const collect = (frame: Float32Array) => {
      this.chunks.push(frame);
      this.samples += frame.length;
      let sum = 0;
      for (let i = 0; i < frame.length; i++) sum += frame[i]! * frame[i]!;
      this.onLevel(Math.sqrt(sum / Math.max(1, frame.length)));
    };
    try {
      await ctx.audioWorklet.addModule("/pcm-capture-worklet.js");
      if (my !== this.gen) return; // stop() already tore the session down
      const node = new AudioWorkletNode(ctx, "pcm-capture");
      node.port.onmessage = (e: MessageEvent<Float32Array>) => collect(e.data);
      this.source.connect(node);
      this.node = node;
    } catch {
      // Older WebView without AudioWorklet: the deprecated but universal fallback.
      const proc = ctx.createScriptProcessor(4096, 1, 1);
      proc.onaudioprocess = (e) => collect(new Float32Array(e.inputBuffer.getChannelData(0)));
      this.source.connect(proc);
      proc.connect(ctx.destination);
      this.node = proc;
    }
    this.limitTimer = setTimeout(onLimit, (MAX_CLIP_S - 1) * 1000);
  }

  /** Stops recording. Returns the clip, or null if nothing usable was captured. */
  async stop(): Promise<string | null> {
    this.gen++; // cancels a start() still waiting on the permission prompt
    const ctx = this.ctx;
    if (!ctx) return null;
    if (this.limitTimer) clearTimeout(this.limitTimer);
    this.limitTimer = null;
    this.source?.disconnect();
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    const rate = ctx.sampleRate;
    this.ctx = null;
    this.stream = null;
    this.node = null;
    this.source = null;
    await ctx.close().catch(() => {});
    this.onLevel(0);
    const chunks = this.chunks;
    this.chunks = [];
    return this.samples > 0 ? encodeClip(chunks, rate) : null;
  }
}
