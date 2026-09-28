/**
 * Tablet microphone audio, encoded exactly as the engine expects for an
 * `audio_clip`: 16 kHz mono, int16 little-endian, base64 (protocol.MAX_CLIP_B64).
 */
export const STT_RATE = 16_000;
export const MAX_CLIP_S = 60;
export const MAX_CLIP_SAMPLES = MAX_CLIP_S * STT_RATE;

/** Box-filter anti-aliasing then linear interpolation (the engine's `voice.resample`). */
export function resampleTo16k(input: Float32Array, rate: number): Float32Array {
  if (rate === STT_RATE || input.length === 0) return input;
  let src = input;
  const factor = rate / STT_RATE;
  const k = Math.round(factor);
  if (k > 1) {
    src = new Float32Array(input.length);
    const half = Math.floor(k / 2);
    for (let i = 0; i < input.length; i++) {
      let sum = 0;
      let n = 0;
      for (let j = i - half; j < i - half + k; j++) {
        if (j >= 0 && j < input.length) {
          sum += input[j]!;
          n++;
        }
      }
      src[i] = sum / n;
    }
  }
  const outLen = Math.round((src.length * STT_RATE) / rate);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const pos = (i * src.length) / outLen;
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, src.length - 1);
    const frac = pos - i0;
    out[i] = src[i0]! * (1 - frac) + src[i1]! * frac;
  }
  return out;
}

export function floatToInt16LE(samples: Float32Array): Uint8Array {
  const buf = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buf);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]!));
    view.setInt16(i * 2, s < 0 ? Math.round(s * 32768) : Math.round(s * 32767), true);
  }
  return new Uint8Array(buf);
}

/** btoa over chunks: String.fromCharCode(...bigArray) overflows the stack on long clips. */
export function base64FromBytes(bytes: Uint8Array): string {
  let binary = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + CHUNK)));
  }
  return btoa(binary);
}

export function encodeClip(chunks: Float32Array[], rate: number): string {
  let total = 0;
  for (const c of chunks) total += c.length;
  const joined = new Float32Array(total);
  let offset = 0;
  for (const c of chunks) {
    joined.set(c, offset);
    offset += c.length;
  }
  const pcm = resampleTo16k(joined, rate).subarray(0, MAX_CLIP_SAMPLES);
  return base64FromBytes(floatToInt16LE(pcm));
}
