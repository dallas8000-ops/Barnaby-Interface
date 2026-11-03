/**
 * Spoken languages, reply voices and recognition models: the app's copy of
 * engine/barnaby_engine/languages.py (an engine test checks the two agree).
 */

export interface Voice {
  id: string;
  label: string;
}

export interface Language {
  code: string;
  native: string;
  name: string;
  bcp47: string;
  voices: Voice[];
  /** Extra package the PC needs to speak this language (Kokoro's G2P). */
  extra?: string;
}

const v = (id: string, label: string): Voice => ({ id, label });

export const LANGUAGES: Language[] = [
  {
    code: "en",
    native: "English",
    name: "English",
    bcp47: "en-US",
    voices: [
      v("am_michael", "Michael (US)"), v("am_adam", "Adam (US)"), v("am_fenrir", "Fenrir (US)"),
      v("am_puck", "Puck (US)"), v("af_heart", "Heart (US)"), v("af_bella", "Bella (US)"),
      v("af_nicole", "Nicole (US)"), v("af_sarah", "Sarah (US)"), v("bm_george", "George (UK)"),
      v("bm_lewis", "Lewis (UK)"), v("bm_fable", "Fable (UK)"), v("bf_emma", "Emma (UK)"),
      v("bf_isabella", "Isabella (UK)"),
    ],
  },
  { code: "es", native: "Español", name: "Spanish", bcp47: "es-ES", voices: [v("ef_dora", "Dora"), v("em_alex", "Alex"), v("em_santa", "Santa")] },
  { code: "fr", native: "Français", name: "French", bcp47: "fr-FR", voices: [v("ff_siwis", "Siwis")] },
  { code: "it", native: "Italiano", name: "Italian", bcp47: "it-IT", voices: [v("if_sara", "Sara"), v("im_nicola", "Nicola")] },
  { code: "pt", native: "Português (Brasil)", name: "Portuguese", bcp47: "pt-BR", voices: [v("pf_dora", "Dora"), v("pm_alex", "Alex"), v("pm_santa", "Santa")] },
  { code: "hi", native: "हिन्दी", name: "Hindi", bcp47: "hi-IN", voices: [v("hf_alpha", "Alpha"), v("hf_beta", "Beta"), v("hm_omega", "Omega"), v("hm_psi", "Psi")] },
  {
    code: "ja",
    native: "日本語",
    name: "Japanese",
    bcp47: "ja-JP",
    extra: "misaki[ja]",
    voices: [v("jf_alpha", "Alpha"), v("jf_gongitsune", "Gongitsune"), v("jf_nezumi", "Nezumi"), v("jf_tebukuro", "Tebukuro"), v("jm_kumo", "Kumo")],
  },
  {
    code: "zh",
    native: "中文（普通话）",
    name: "Mandarin Chinese",
    bcp47: "zh-CN",
    extra: "misaki[zh]",
    voices: [
      v("zf_xiaobei", "Xiaobei"), v("zf_xiaoni", "Xiaoni"), v("zf_xiaoxiao", "Xiaoxiao"), v("zf_xiaoyi", "Xiaoyi"),
      v("zm_yunjian", "Yunjian"), v("zm_yunxi", "Yunxi"), v("zm_yunxia", "Yunxia"), v("zm_yunyang", "Yunyang"),
    ],
  },
];

export const AUTO = "auto";

export function languageOf(code: string | undefined): Language | undefined {
  return LANGUAGES.find((l) => l.code === code);
}

/** Kokoro voice ids start with their language's letter: a/b English, e Spanish, ... */
const KOKORO: Record<string, string> = { a: "en", b: "en", e: "es", f: "fr", h: "hi", i: "it", j: "ja", p: "pt", z: "zh" };

export function voiceLanguage(voice: string): string | undefined {
  return KOKORO[voice.charAt(0)];
}

/** The voice to keep when the spoken language changes: the current one if it speaks it. */
export function voiceFor(language: string, current: string): string {
  const lang = languageOf(language);
  if (!lang || voiceLanguage(current) === language) return current;
  return lang.voices[0]?.id ?? current;
}

export interface WhisperModel {
  id: string;
  label: string;
  englishOnly: boolean;
  diskMb: number;
  vramMb: number;
  /** Used instead when the spoken language isn't English. */
  multilingual: string;
}

const m = (id: string, label: string, englishOnly: boolean, diskMb: number, vramMb: number, multilingual: string): WhisperModel => ({
  id, label, englishOnly, diskMb, vramMb, multilingual,
});

export const WHISPER_MODELS: WhisperModel[] = [
  m("tiny.en", "Tiny, English only (tiny.en)", true, 75, 500, "tiny"),
  m("base.en", "Fast, English only (base.en)", true, 145, 700, "base"),
  m("small.en", "Balanced, English only (small.en)", true, 485, 1200, "small"),
  m("medium.en", "Accurate, English only (medium.en, strong CPU or GPU)", true, 1530, 2600, "medium"),
  m("distil-large-v3", "Most accurate, English only (distil-large-v3, GPU)", true, 1510, 2600, "large-v3-turbo"),
  m("tiny", "Tiny, any language (tiny)", false, 75, 500, "tiny"),
  m("base", "Fast, any language (base)", false, 145, 700, "base"),
  m("small", "Balanced, any language (small)", false, 485, 1200, "small"),
  m("medium", "Accurate, any language (medium, strong CPU or GPU)", false, 1530, 2600, "medium"),
  m("large-v3-turbo", "Most accurate fast, any language (large-v3-turbo, GPU)", false, 1620, 2800, "large-v3-turbo"),
  m("large-v3", "Most accurate, any language (large-v3, GPU with 5 GB+)", false, 3090, 4700, "large-v3"),
];

export function modelInfo(id: string): WhisperModel | undefined {
  return WHISPER_MODELS.find((w) => w.id === id);
}

/** The model that can hear `language`: English-only models swap to their multilingual sibling. */
export function modelFor(model: string, language: string): string {
  const info = modelInfo(model);
  return info && info.englishOnly && language !== "en" ? info.multilingual : model;
}

export function sizeLabel(mb: number): string {
  return mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${mb} MB`;
}

export function bcp47(code: string | undefined): string {
  return languageOf(code)?.bcp47 ?? "en-US";
}
