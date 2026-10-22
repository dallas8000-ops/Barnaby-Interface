import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const sent: unknown[] = [];
vi.mock("../lib/client", () => ({
  send: (cmd: unknown) => {
    sent.push(cmd);
    return true;
  },
  client: { onEvent: () => () => {}, onStatus: () => () => {}, start: () => {}, stop: () => {} },
}));

import { bcp47, modelFor, voiceFor, voiceLanguage } from "../lib/languages";
import { TabletSpeech } from "../lib/tabletSpeech";
import { initialState, reduceEvent, useApp } from "../store/app";
import { SettingsDialog } from "./SettingsDialog";

const base = {
  workspace: "/proj", input_device: null, whisper_model: "small.en", continuous_mode_timeout: 1.2,
  claude_model: "", cursor_model: "", tts_enabled: true, tts_speed: 1.05, tts_voice: "am_michael",
  max_spoken_chars: 400, review_transcripts: false, save_voice_samples: false,
};

beforeEach(() => {
  sent.length = 0;
  useApp.setState({ ...initialState, devices: [] } as never);
});

describe("language helpers", () => {
  it("keeps a voice that speaks the language, else picks that language's default", () => {
    expect(voiceFor("es", "am_michael")).toBe("ef_dora");
    expect(voiceFor("es", "em_alex")).toBe("em_alex");
    expect(voiceFor("en", "bf_emma")).toBe("bf_emma");
    expect(voiceLanguage("zf_xiaobei")).toBe("zh");
  });

  it("swaps English-only models outside English", () => {
    expect(modelFor("small.en", "ja")).toBe("small");
    expect(modelFor("small.en", "auto")).toBe("small");
    expect(modelFor("distil-large-v3", "fr")).toBe("large-v3-turbo");
    expect(modelFor("small.en", "en")).toBe("small.en");
    expect(modelFor("medium", "hi")).toBe("medium");
  });

  it("maps languages to BCP-47 for the tablet voice", () => {
    expect(bcp47("pt")).toBe("pt-BR");
    expect(bcp47(undefined)).toBe("en-US");
  });
});

describe("Settings → Language", () => {
  it("defaults to English when an older engine sends no spoken_language", () => {
    useApp.setState({ config: base as never });
    render(<SettingsDialog onClose={() => {}} />);
    expect((screen.getByLabelText("Spoken language") as HTMLSelectElement).value).toBe("en");
    const voices = Array.from((screen.getByLabelText("Reply voice") as HTMLSelectElement).options).map((o) => o.value);
    expect(voices).toContain("am_michael");
    expect(voices).not.toContain("ef_dora");
  });

  it("choosing a language moves the voice and an English-only model with it", () => {
    useApp.setState({ config: base as never });
    render(<SettingsDialog onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("Spoken language"), { target: { value: "es" } });
    expect(sent).toContainEqual({
      type: "set_config",
      values: { spoken_language: "es", tts_voice: "ef_dora", whisper_model: "small" },
    });
  });

  it("filters reply voices to the language, and explains extra packages", () => {
    useApp.setState({ config: { ...base, spoken_language: "ja", tts_voice: "jf_alpha", whisper_model: "small" } as never });
    render(<SettingsDialog onClose={() => {}} />);
    const voice = screen.getByLabelText("Reply voice") as HTMLSelectElement;
    expect(Array.from(voice.options).map((o) => o.value)).toEqual(["jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo"]);
    expect(screen.getByText(/pip install "misaki\[ja\]"/)).toBeTruthy();
    const model = screen.getByLabelText("Recognition model") as HTMLSelectElement;
    const english = Array.from(model.options).find((o) => o.value === "small.en")!;
    expect(english.disabled).toBe(true);
  });

  it("Auto lists every language's voices and keeps English-only models selectable only for English", () => {
    useApp.setState({ config: { ...base, spoken_language: "auto", whisper_model: "small" } as never });
    render(<SettingsDialog onClose={() => {}} />);
    const voice = screen.getByLabelText("Reply voice") as HTMLSelectElement;
    expect(voice.querySelectorAll("optgroup").length).toBe(8);
    fireEvent.change(screen.getByLabelText("Spoken language"), { target: { value: "en" } });
    expect(sent).toContainEqual({ type: "set_config", values: { spoken_language: "en" } });
  });

  it("says when the model isn't downloaded yet, with its size", () => {
    useApp.setState({ config: { ...base, spoken_language: "it", whisper_model: "medium" } as never });
    useApp.setState(reduceEvent(useApp.getState(), { type: "stt_models", installed: { medium: false, small: true } }));
    render(<SettingsDialog onClose={() => {}} />);
    expect(screen.getByText(/Not downloaded yet/)).toBeTruthy();
    expect(screen.getByText(/medium: 1.5 GB download/)).toBeTruthy();
  });
});

describe("tablet speech language", () => {
  it("uses the engine's language tag, English when missing", () => {
    const langs: string[] = [];
    const synth = { speak: (u: { lang: string }) => langs.push(u.lang), cancel: () => {} };
    class U {
      lang = "";
      onend: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(public text: string) {}
    }
    const s = new TabletSpeech(synth as never, U as never);
    s.say("完成了。", "zh-CN");
    s.say("Done.");
    expect(langs).toEqual(["zh-CN", "en-US"]);
  });
});
