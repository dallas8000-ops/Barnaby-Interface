import { describe, expect, it } from "vitest";
import { TabletSpeech } from "./tabletSpeech";

class FakeSynth {
  spoken: string[] = [];
  cancels = 0;
  last: FakeUtterance | null = null;
  speak(u: FakeUtterance) {
    this.spoken.push(u.text);
    this.last = u;
  }
  cancel() {
    this.cancels++;
  }
}
class FakeUtterance {
  lang = "";
  rate = 1;
  onend: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public text: string) {}
}

describe("TabletSpeech", () => {
  it("speaks say events in order and stops on request", () => {
    const synth = new FakeSynth();
    const s = new TabletSpeech(synth as never, FakeUtterance as never);
    s.say("First.");
    s.say("Second.");
    expect(synth.spoken).toEqual(["First.", "Second."]);
    s.stop();
    expect(synth.cancels).toBe(1);
  });

  it("stays silent when turned off, and ignores empty text", () => {
    const synth = new FakeSynth();
    const s = new TabletSpeech(synth as never, FakeUtterance as never);
    s.enabled = false;
    s.say("Hidden.");
    s.enabled = true;
    s.say("   ");
    expect(synth.spoken).toEqual([]);
  });

  it("does nothing where speech synthesis is unavailable", () => {
    const s = new TabletSpeech(undefined, undefined);
    expect(() => s.say("x")).not.toThrow();
    expect(s.available).toBe(false);
  });
});

describe("TabletSpeech speaking state (second review, AC5.2)", () => {
  it("reports speaking while utterances are queued, and stop() ends it", () => {
    const synth = new FakeSynth();
    const states: boolean[] = [];
    const s = new TabletSpeech(synth as never, FakeUtterance as never, (v) => states.push(v));
    s.say("One.");
    const first = synth.last!;
    s.say("Two.");
    const second = synth.last!;
    first.onend!();
    expect(states).toEqual([true]); // still one to go
    second.onend!();
    expect(states).toEqual([true, false]);
    s.say("Three.");
    s.stop();
    expect(states).toEqual([true, false, true, false]);
  });
});
