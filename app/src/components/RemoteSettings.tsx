import { useState } from "react";
import { send } from "../lib/client";
import { forgetDevice } from "../lib/remote";
import { SPEECH_KEY } from "../lib/tabletSpeech";
import { useApp } from "../store/app";
import { Modal } from "./Modal";
import { StyleField } from "./SettingsDialog";

/**
 * Settings on a paired tablet: only what a tablet may change (the engine enforces
 * this too). Project folder, keys, microphone and tablet access stay on the PC.
 */
export function RemoteSettings({ onClose }: { onClose: () => void }) {
  const config = useApp((s) => s.config);
  const [speak, setSpeak] = useState(() => {
    try {
      return localStorage.getItem(SPEECH_KEY) !== "off";
    } catch {
      return true;
    }
  });
  if (!config) return null;

  const row = "flex items-center justify-between gap-4 py-3";
  return (
    <Modal title="Settings" onClose={onClose}>
      <div className="divide-y divide-line text-base">
        <div className={row}>
          <span className="text-ink">Project</span>
          <code className="min-w-0 truncate font-mono text-sm text-muted" title={config.workspace}>
            {config.workspace || "Not set"}
          </code>
        </div>
        <label className={row}>
          <span className="text-ink">
            Review before sending
            <span className="block text-sm text-muted">Check what the PC heard before it's sent.</span>
          </span>
          <input
            type="checkbox"
            className="h-6 w-6 accent-[var(--c-cyan)]"
            checked={config.review_transcripts}
            onChange={(e) => send({ type: "set_config", values: { review_transcripts: e.target.checked } })}
          />
        </label>
        <label className={row}>
          <span className="text-ink">
            Read replies aloud on this tablet
            <span className="block text-sm text-muted">Uses the tablet's own voice. The PC stays quiet.</span>
          </span>
          <input
            type="checkbox"
            className="h-6 w-6 accent-[var(--c-cyan)]"
            checked={speak}
            onChange={(e) => {
              setSpeak(e.target.checked);
              try {
                localStorage.setItem(SPEECH_KEY, e.target.checked ? "on" : "off");
              } catch {
                /* storage blocked: applies until reload */
              }
              window.location.reload();
            }}
          />
        </label>
        <div className="py-1">
          <StyleField />
        </div>
        <div className={row}>
          <span className="text-sm text-muted">
            Project folder, microphone, API keys and tablet access can only be changed on the PC.
          </span>
        </div>
        <div className={row}>
          <span className="text-ink">
            Forget this tablet
            <span className="block text-sm text-muted">You'll need to pair again from the PC.</span>
          </span>
          <button
            type="button"
            onClick={() => {
              forgetDevice();
              window.location.reload();
            }}
            className="rounded-md border border-line px-3 py-1.5 text-sm text-muted hover:border-red hover:text-red"
          >
            Forget
          </button>
        </div>
      </div>
    </Modal>
  );
}
