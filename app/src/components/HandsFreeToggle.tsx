import { Mic } from "lucide-react";
import { send } from "../lib/client";
import { useApp } from "../store/app";

/**
 * Hands-free: the mic stays open, you just talk, and a pause ends your turn.
 * Barnaby stops listening while it speaks, so it doesn't hear itself. Holding
 * Space still works and takes over the mic for that recording.
 */
export function HandsFreeToggle() {
  const enabled = useApp((s) => Boolean(s.config?.continuous_mode_enabled));
  const active = useApp((s) => s.continuousMode);
  const connection = useApp((s) => s.connection);
  const failed = enabled && !active && connection === "open";

  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      disabled={connection !== "open"}
      onClick={() => send({ type: "set_config", values: { continuous_mode_enabled: !enabled } })}
      title={
        failed
          ? "Hands-free is on but the microphone couldn't be opened. Check Settings → Microphone."
          : "Talk without holding a key. A pause sends what you said."
      }
      className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-0.5 text-xs font-medium disabled:opacity-40 ${
        failed
          ? "border-amber text-amber"
          : enabled
            ? "border-cyan bg-raised text-ink"
            : "border-line text-muted hover:border-cyan hover:text-ink"
      }`}
    >
      <Mic size={13} aria-hidden className={active ? "text-cyan" : undefined} /> Hands-free
    </button>
  );
}
