import type { Config } from "../lib/protocol";
import {
  AUTO,
  LANGUAGES,
  WHISPER_MODELS,
  languageOf,
  modelFor,
  modelInfo,
  sizeLabel,
  voiceFor,
  voiceLanguage,
} from "../lib/languages";
import { useApp } from "../store/app";
import { Field, inputCls } from "./SettingsDialog";

type Set = (values: Partial<Config>) => void;

/** Settings → Language: what Barnaby listens for, and the voice it answers with. */
export function LanguageSection({ config, set }: { config: Config; set: Set }) {
  const spoken = config.spoken_language ?? "en";
  const lang = languageOf(spoken);

  const chooseLanguage = (code: string) => {
    const values: Partial<Config> = { spoken_language: code };
    // Keep the voice if it speaks the new language; otherwise that language's default.
    if (code !== AUTO) {
      const voice = voiceFor(code, config.tts_voice);
      if (voice !== config.tts_voice) values.tts_voice = voice;
    }
    // English-only recognition can't hear other languages: move to its multilingual sibling.
    const model = modelFor(config.whisper_model, code);
    if (model !== config.whisper_model) values.whisper_model = model;
    set(values);
  };

  const voices = lang ? lang.voices : LANGUAGES.flatMap((l) => l.voices);
  const known = voices.some((v) => v.id === config.tts_voice);
  const speaking = languageOf(voiceLanguage(config.tts_voice));

  return (
    <section className="mt-3 border-t border-line pt-3">
      <h3 className="font-strip text-sm font-semibold text-muted">Language</h3>
      <Field
        label="Spoken language"
        htmlFor="spoken-language"
        hint={
          spoken === AUTO
            ? "Barnaby detects the language of each thing you say and answers in it. Picking your language is faster and more accurate."
            : "What you speak to Barnaby. Code, file names and commands stay as they are; the agent answers in this language."
        }
      >
        <select id="spoken-language" className={inputCls} value={spoken} onChange={(e) => chooseLanguage(e.target.value)}>
          <option value={AUTO}>Auto-detect</option>
          {LANGUAGES.map((l) => (
            <option key={l.code} value={l.code}>
              {l.native === l.name ? l.name : `${l.native} (${l.name})`}
            </option>
          ))}
        </select>
      </Field>
      <Field
        label="Reply voice"
        htmlFor="voice"
        hint={
          spoken === AUTO
            ? `Used when you speak ${speaking?.name ?? "its language"}; other languages get their own default voice.`
            : lang?.extra
              ? `${lang.name} voices need an extra package on this PC: pip install "${lang.extra}".`
              : undefined
        }
      >
        <select id="voice" className={inputCls} value={config.tts_voice} onChange={(e) => set({ tts_voice: e.target.value })}>
          {!known && <option value={config.tts_voice}>{config.tts_voice}</option>}
          {lang
            ? lang.voices.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.label}
                </option>
              ))
            : LANGUAGES.map((l) => (
                <optgroup key={l.code} label={l.native}>
                  {l.voices.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.label}
                    </option>
                  ))}
                </optgroup>
              ))}
        </select>
      </Field>
    </section>
  );
}

/** Settings → Listening → Recognition model, with download size, memory and install state. */
export function RecognitionModelField({ config, set }: { config: Config; set: Set }) {
  const installed = useApp((s) => s.sttInstalled);
  const spoken = config.spoken_language ?? "en";
  const used = modelFor(config.whisper_model, spoken);
  const info = modelInfo(used);
  const notes: string[] = [];
  if (used !== config.whisper_model) {
    notes.push(`${config.whisper_model} only understands English, so ${used} is used for ${languageOf(spoken)?.name ?? "other languages"}.`);
  }
  if (info) {
    notes.push(`${used}: ${sizeLabel(info.diskMb)} download, about ${sizeLabel(info.vramMb)} of GPU memory (half that in RAM on CPU).`);
  }
  if (installed[used] === false) notes.push("Not downloaded yet: Barnaby fetches it the first time it loads (needs internet once).");

  return (
    <Field label="Recognition model" htmlFor="whisper" hint={["Runs on your PC. Larger models hear technical words better but take longer.", ...notes].join(" ")}>
      <select id="whisper" className={inputCls} value={config.whisper_model} onChange={(e) => set({ whisper_model: e.target.value })}>
        {WHISPER_MODELS.filter((w) => w.id !== "tiny.en" || config.whisper_model === "tiny.en").map((w) => (
          <option key={w.id} value={w.id} disabled={w.englishOnly && spoken !== "en"}>
            {w.label} · {sizeLabel(w.diskMb)}
            {installed[w.id] === false ? " · not downloaded" : ""}
          </option>
        ))}
      </select>
    </Field>
  );
}
