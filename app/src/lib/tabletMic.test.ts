import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TabletMic } from "./tabletMic";

// Minimal browser audio fakes: enough to exercise start/stop ordering.
class FakeTrack {
  stopped = false;
  stop() {
    this.stopped = true;
  }
}
class FakeStream {
  tracks = [new FakeTrack()];
  getTracks() {
    return this.tracks;
  }
}
class FakeNode {
  connect() {}
  disconnect() {}
  port = { onmessage: null as unknown };
}
class FakeAudioContext {
  static instances = 0;
  sampleRate = 48_000;
  audioWorklet = { addModule: async () => {} };
  constructor() {
    FakeAudioContext.instances++;
  }
  createMediaStreamSource() {
    return new FakeNode();
  }
  close() {
    return Promise.resolve();
  }
}

describe("TabletMic (second review: release while the permission prompt is open)", () => {
  let resolveStream: (s: FakeStream) => void;
  beforeEach(() => {
    FakeAudioContext.instances = 0;
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("navigator", {
      mediaDevices: { getUserMedia: () => new Promise<FakeStream>((r) => (resolveStream = r)) },
    });
    vi.stubGlobal("AudioContext", FakeAudioContext);
    vi.stubGlobal("AudioWorkletNode", FakeNode);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("a release before getUserMedia resolves cancels the start and closes the mic", async () => {
    const mic = new TabletMic();
    const starting = mic.start(() => {});
    expect(await mic.stop()).toBeNull(); // finger lifted to tap Allow
    const stream = new FakeStream();
    resolveStream(stream);
    await starting;
    expect(stream.tracks[0]!.stopped).toBe(true);
    expect(mic.recording).toBe(false);
    expect(FakeAudioContext.instances).toBe(0); // no audio graph was built
  });

  it("the next press starts a fresh recording", async () => {
    const mic = new TabletMic();
    const first = mic.start(() => {});
    await mic.stop();
    resolveStream(new FakeStream());
    await first;
    const second = mic.start(() => {});
    const stream = new FakeStream();
    resolveStream(stream);
    await second;
    expect(mic.recording).toBe(true);
    await mic.stop();
    expect(stream.tracks[0]!.stopped).toBe(true);
    expect(mic.recording).toBe(false);
  });
});
