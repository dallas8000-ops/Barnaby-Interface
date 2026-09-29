import { useEffect, useState, type ReactNode } from "react";
import { send } from "../lib/client";
import { pickFolder } from "../lib/platform";
import type { Config } from "../lib/protocol";
import { SKINS, applySkin, readSkin, saveSkin, type Skin } from "../lib/skin";
import { useApp } from "../store/app";
import { DevinKeyField } from "./DevinKeyField";
import { RemoteSettings } from "./RemoteSettings";
import { TabletAccessSection } from "./TabletAccessSection";
import { TasksSettings } from "./TasksSettings";
import { Modal } from "./Modal";

const VOICES: { id: string; label: string }[] = [
  { id: "am_michael", label: "Michael (US)" },
  { id: "am_adam", label: "Adam (US)" },
  { id: "am_fenrir", label: "Fenrir (US)" },
  { id: "af_heart", label: "Heart (US)" },
  { id: "af_bella", label: "Bella (US)" },
  { id: "af_sarah", label: "Sarah (US)" },
  { id: "bm_george", label: "George (UK)" },
  { id: "bm_lewis", label: "Lewis (UK)" },
  { id: "bf_emma", label: "Emma (UK)" },
];

const WHISPER: { id: string; label: string }[] = [
  { id: "base.en", label: "Fast (base.en)" },
  { id: "small.en", label: "Balanced (small.en)" },
  { id: "medium.en", label: "Accurate (medium.en, needs a strong CPU or GPU)" },
  { id: "distil-large-v3", label: "Most accurate (distil-large-v3, GPU recommended)" },
];

type Theme = "system" | "dark" | "light";
export function applyTheme(theme: Theme) {
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}
const readTheme = (): Theme => {
  try {
    return (localStorage.getItem("barnaby.theme") as Theme) || "system";
  } catch {
    return "system";
  }
};

function Field({ label, hint, children, htmlFor }: { label: string; hint?: string; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="grid gap-1 py-2.5 sm:grid-cols-[180px_1fr] sm:items-start sm:gap-4">
      <label htmlFor={htmlFor} className="pt-1.5 text-sm font-medium text-ink">
        {label}
      </label>
      <div className="min-w-0">
        {children}
        {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
      </div>
    </div>
  );
}

function Toggle({ id, checked, onChange, label }: { id: string; checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative mt-1 block h-6 w-11 shrink-0 rounded-full transition-colors ${checked ? "bg-cyan" : "bg-line"}`}
    >
      <span className={`absolute top-0.5 left-0 h-5 w-5 rounded-full bg-panel shadow transition-transform ${checked ? "translate-x-5.5" : "translate-x-0.5"}`} />
    </button>
  );
}

const inputCls = "w-full rounded-md border border-line bg-raised px-2.5 py-1.5 text-sm text-ink focus:border-cyan focus:outline-none";

export function SettingsDialog({ onClose }: { onClose: () => void }) {
  const role = useApp((s) => s.role);
  return role === "remote" ? <RemoteSettings onClose={onClose} /> : <DesktopSettings onClose={onClose} />;
}

/** Visual style: palette plus layout touches (skins.css). Saved on this device only. */
export function StyleField() {
  const [skin, setSkin] = useState<Skin>(readSkin);
  return (
    <Field label="Style" htmlFor="skin" hint="Saved on this device, so the PC and a tablet can differ.">
      <select
        id="skin"
        className={inputCls}
        value={skin}
        onChange={(e) => {
          const s = e.target.value as Skin;
          setSkin(s);
          saveSkin(s);
        }}
      >
        {SKINS.map((s) => (
          <option key={s.id} value={s.id}>
            {s.label}
          </option>
        ))}
      </select>
    </Field>
  );
}

function DesktopSettings({ onClose }: { onClose: () => void }) {
  const config = useApp((s) => s.config);
  const devices = useApp((s) => s.devices);
  const autoMic = devices.find((d) => d.auto);
  const [theme, setTheme] = useState<Theme>(readTheme);

  useEffect(() => {
    send({ type: "list_devices" });
  }, []);

  if (!config) return null;
  const set = (values: Partial<Config>) => send({ type: "set_config", values });

  return (
    <Modal title="Settings" onClose={onClose} wide>
      <section>
        <h3 className="font-strip text-sm font-semibold text-muted">Project</h3>
        <Field label="Project folder" hint="Claude Code runs inside this folder. Switching starts a new session.">
          <div className="flex gap-2">
            <code className="min-w-0 flex-1 truncate rounded-md border border-line bg-raised px-2.5 py-1.5 font-mono text-xs leading-5" title={config.workspace}>
              {config.workspace || "Not set"}
            </code>
            <button
              type="button"
              className="rounded-md border border-line px-3 text-sm hover:border-cyan"
              onClick={async () => {
                const folder = await pickFolder();
                if (folder) set({ workspace: folder });
              }}
            >
              Change
            </button>
          </div>
        </Field>
        <Field label="Claude model" htmlFor="model" hint="Leave empty to use your Claude Code default. Examples: opus, sonnet.">
          <input
            id="model"
            className={inputCls}
            defaultValue={config.claude_model}
            placeholder="Default"
            onBlur={(e) => e.target.value !== config.claude_model && set({ claude_model: e.target.value.trim() })}
          />
        </Field>
        <Field label="Cursor model" htmlFor="cursor-model" hint="Used when the agent is set to Cursor. Leave empty for Cursor's Auto.">
          <input
            id="cursor-model"
            className={inputCls}
            defaultValue={config.cursor_model}
            placeholder="Auto"
            onBlur={(e) => e.target.value !== config.cursor_model && set({ cursor_model: e.target.value.trim() })}
          />
        </Field>
        <Field label="Devin API key" htmlFor="devin-key">
          <DevinKeyField />
        </Field>
      </section>

      <section className="mt-3 border-t border-line pt-3">
        <h3 className="font-strip text-sm font-semibold text-muted">Background tasks</h3>
        <div className="py-2.5">
          <TasksSettings config={config} />
        </div>
      </section>

      <section className="mt-3 border-t border-line pt-3">
        <h3 className="font-strip text-sm font-semibold text-muted">Listening</h3>
        <Field label="Microphone" htmlFor="mic">
          <select
            id="mic"
            className={inputCls}
            value={config.input_device ?? ""}
            onChange={(e) => set({ input_device: e.target.value === "" ? null : Number(e.target.value) })}
          >
            <option value="">{autoMic ? `Automatic (${autoMic.name})` : "System default"}</option>
            {devices.map((d) => (
              <option key={d.index} value={d.index}>
                {d.loopback ? `${d.name} (records speaker output)` : d.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Recognition model" htmlFor="whisper" hint="Runs on your PC. Larger models hear technical words better but take longer.">
          <select id="whisper" className={inputCls} value={config.whisper_model} onChange={(e) => set({ whisper_model: e.target.value })}>
            {WHISPER.map((w) => (
              <option key={w.id} value={w.id}>
                {w.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Hands-free pause" htmlFor="hf-pause" hint="In hands-free mode, how long a pause ends your turn. Raise it if you get cut off mid-thought.">
          <div className="flex items-center gap-3">
            <input
              id="hf-pause"
              type="range"
              min={0.6}
              max={3}
              step={0.1}
              defaultValue={config.continuous_mode_timeout}
              onPointerUp={(e) => set({ continuous_mode_timeout: Number((e.target as HTMLInputElement).value) })}
              onKeyUp={(e) => set({ continuous_mode_timeout: Number((e.target as HTMLInputElement).value) })}
              className="flex-1 accent-[var(--c-cyan)]"
            />
            <span className="w-12 text-right text-sm tabular-nums text-muted">{config.continuous_mode_timeout.toFixed(1)} s</span>
          </div>
        </Field>
        <Field label="Review before sending" hint="Shows what I heard so you can fix words first. Fixes train your voice profile.">
          <Toggle id="review" label="Review before sending" checked={config.review_transcripts} onChange={(v) => set({ review_transcripts: v })} />
        </Field>
        <Field
          label="Keep voice samples"
          hint="Saves your recordings with the corrected text on this PC, to fine-tune recognition to your voice later. Off by default."
        >
          <Toggle id="samples" label="Keep voice samples" checked={config.save_voice_samples} onChange={(v) => set({ save_voice_samples: v })} />
        </Field>
      </section>

      <section className="mt-3 border-t border-line pt-3">
        <h3 className="font-strip text-sm font-semibold text-muted">Speaking</h3>
        <Field label="Speak replies">
          <Toggle id="tts" label="Speak replies" checked={config.tts_enabled} onChange={(v) => set({ tts_enabled: v })} />
        </Field>
        <Field label="Voice" htmlFor="voice">
          <select id="voice" className={inputCls} value={config.tts_voice} onChange={(e) => set({ tts_voice: e.target.value })}>
            {VOICES.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Speed" htmlFor="speed">
          <div className="flex items-center gap-3">
            <input
              id="speed"
              type="range"
              min={0.8}
              max={1.4}
              step={0.05}
              defaultValue={config.tts_speed}
              onPointerUp={(e) => set({ tts_speed: Number((e.target as HTMLInputElement).value) })}
              onKeyUp={(e) => set({ tts_speed: Number((e.target as HTMLInputElement).value) })}
              className="flex-1 accent-[var(--c-cyan)]"
            />
            <span className="w-10 text-right text-sm tabular-nums text-muted">{config.tts_speed.toFixed(2)}×</span>
          </div>
        </Field>
        <Field label="Spoken length" htmlFor="len" hint="Longer replies are read up to this many characters. The rest stays on screen.">
          <select id="len" className={inputCls} value={config.max_spoken_chars} onChange={(e) => set({ max_spoken_chars: Number(e.target.value) })}>
            {[300, 700, 1500, 3000].map((n) => (
              <option key={n} value={n}>
                {n === 300 ? "Short" : n === 700 ? "Normal" : n === 1500 ? "Long" : "Very long"} ({n} characters)
              </option>
            ))}
          </select>
        </Field>
      </section>

      <section className="mt-3 border-t border-line pt-3">
        <h3 className="font-strip text-sm font-semibold text-muted">Appearance</h3>
        <Field label="Theme" htmlFor="theme">
          <select
            id="theme"
            className={inputCls}
            value={theme}
            onChange={(e) => {
              const t = e.target.value as Theme;
              setTheme(t);
              applyTheme(t);
              try {
                localStorage.setItem("barnaby.theme", t);
              } catch {
                /* storage unavailable: theme applies for this run only */
              }
            }}
          >
            <option value="system">Match Windows</option>
            <option value="dark">Night</option>
            <option value="light">Day</option>
          </select>
        </Field>
        <StyleField />
      </section>

      <section className="mt-3 border-t border-line pt-3">
        <h3 className="font-strip text-sm font-semibold text-muted">Tablet access</h3>
        <Field label="Use from a tablet" hint="A paired tablet on your home Wi-Fi can talk to Barnaby. Off unless you turn it on.">
          <TabletAccessSection enabled={Boolean(config.tablet_access)} onToggle={(v) => set({ tablet_access: v })} />
        </Field>
      </section>
    </Modal>
  );
}

export function initTheme() {
  applyTheme(readTheme());
  applySkin(readSkin());
}
