/**
 * Speaks the engine's `say` events with the tablet's own voice (Android TTS via
 * the Web Speech API). The engine sends only sentences it would have spoken on
 * the PC: already shortened and with secrets redacted, never raw agent output.
 */
export const SPEECH_KEY = "barnaby.tabletSpeech";

type Synth = Pick<SpeechSynthesis, "speak" | "cancel">;
type UtteranceCtor = new (text: string) => SpeechSynthesisUtterance;

export class TabletSpeech {
  enabled = true;
  private pending = 0;

  constructor(
    private readonly synth: Synth | undefined = typeof window !== "undefined" ? window.speechSynthesis : undefined,
    private readonly Utterance: UtteranceCtor | undefined = typeof window !== "undefined"
      ? window.SpeechSynthesisUtterance
      : undefined,
    private readonly onSpeakingChange: (speaking: boolean) => void = () => {},
  ) {}

  private settle(delta: number): void {
    const was = this.pending > 0;
    this.pending = Math.max(0, this.pending + delta);
    if (was !== this.pending > 0) this.onSpeakingChange(this.pending > 0);
  }

  get available(): boolean {
    return Boolean(this.synth && this.Utterance);
  }

  /** `lang` is the BCP-47 tag the engine sends with each sentence, so Android picks a voice for it. */
  say(text: string, lang = "en-US"): void {
    if (!this.enabled || !this.synth || !this.Utterance || !text.trim()) return;
    const u = new this.Utterance(text);
    u.lang = lang;
    u.onend = () => this.settle(-1);
    u.onerror = () => this.settle(-1);
    this.settle(+1);
    this.synth.speak(u);
  }

  stop(): void {
    this.synth?.cancel();
    this.settle(-this.pending);
  }
}

let shared: TabletSpeech | null = null;

/** The tablet's one voice, shared by the event handler, Quiet, and hold-to-talk barge-in. */
export function getTabletSpeech(onSpeakingChange?: (speaking: boolean) => void): TabletSpeech {
  if (!shared) {
    shared = new TabletSpeech(undefined, undefined, onSpeakingChange);
    try {
      shared.enabled = localStorage.getItem(SPEECH_KEY) !== "off";
    } catch {
      /* storage blocked: default on */
    }
  }
  return shared;
}
