import { describe, expect, it } from "vitest";
import { MAX_CLIP_SAMPLES, base64FromBytes, floatToInt16LE, resampleTo16k } from "./audio";

describe("tablet audio encoding (must match the engine: 16 kHz mono int16 LE, base64)", () => {
  it("resamples 48 kHz to 16 kHz with the right length", () => {
    const src = new Float32Array(48_000).map((_, i) => Math.sin((2 * Math.PI * 440 * i) / 48_000));
    const out = resampleTo16k(src, 48_000);
    expect(out.length).toBe(16_000);
    // 440 Hz survives: count zero crossings (~880 per second).
    let crossings = 0;
    for (let i = 1; i < out.length; i++) if (out[i - 1]! < 0 !== out[i]! < 0) crossings++;
    expect(crossings).toBeGreaterThan(860);
    expect(crossings).toBeLessThan(900);
  });

  it("passes 16 kHz through and handles 44.1 kHz", () => {
    const same = new Float32Array([0.1, -0.2, 0.3]);
    expect(Array.from(resampleTo16k(same, 16_000))).toEqual(Array.from(same));
    expect(resampleTo16k(new Float32Array(44_100), 44_100).length).toBe(16_000);
    expect(resampleTo16k(new Float32Array(0), 48_000).length).toBe(0);
  });

  it("converts to little-endian int16 with clipping", () => {
    const bytes = floatToInt16LE(new Float32Array([0, 1, -1, 0.5, 2, -2]));
    const view = new DataView(bytes.buffer);
    const vals = [0, 2, 4, 6, 8, 10].map((o) => view.getInt16(o, true));
    expect(vals).toEqual([0, 32767, -32768, 16384, 32767, -32768]);
  });

  it("base64-encodes large buffers without blowing the call stack", () => {
    const bytes = new Uint8Array(MAX_CLIP_SAMPLES * 2).map((_, i) => i & 0xff);
    const b64 = base64FromBytes(bytes);
    expect(b64.length).toBe(4 * Math.ceil(bytes.length / 3));
    const back = Uint8Array.from(atob(b64.slice(0, 16)), (c) => c.charCodeAt(0));
    expect(Array.from(back)).toEqual(Array.from(bytes.slice(0, 12)));
  });

  it("caps clips at the engine's 60 s limit", () => {
    expect(MAX_CLIP_SAMPLES).toBe(60 * 16_000);
  });
});
