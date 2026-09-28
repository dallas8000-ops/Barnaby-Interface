import { Square, VolumeX } from "lucide-react";
import { send } from "../lib/client";
import type { EngineState } from "../lib/protocol";
import { getTabletSpeech } from "../lib/tabletSpeech";
import { useApp } from "../store/app";
import { Scope } from "./Scope";
import { usePushToTalk } from "./usePushToTalk";

const CAPTION: Record<EngineState, string> = {
  idle: "Hold Space to talk",
  listening: "Listening. Release to send.",
  transcribing: "Transcribing",
  thinking: "Working",
  speaking: "Speaking",
  awaiting_approval: "Waiting for your OK",
  error: "Something went wrong",
};

export function ScopeConsole({ size = 188 }: { size?: number }) {
  const engineState = useApp((s) => s.engineState);
  const level = useApp((s) => s.level);
  const speaking = useApp((s) => s.speaking);
  const connection = useApp((s) => s.connection);
  const stt = useApp((s) => s.components.stt);
  const handsFree = useApp((s) => s.continuousMode);
  const remote = useApp((s) => s.role === "remote");
  const tabletRecording = useApp((s) => s.tabletRecording);
  const tabletSpeaking = useApp((s) => s.tabletSpeaking);
  const { press, release } = usePushToTalk();

  const busy = engineState === "thinking" || engineState === "transcribing";
  const caption =
    connection !== "open"
      ? connection === "unauthorized"
        ? "Engine rejected this window. Restart Barnaby."
        : "Connecting to the voice engine"
      : remote && tabletRecording
        ? "Listening. Release to send."
        : remote && engineState === "idle"
          ? "Hold the circle to talk"
      : stt.status === "loading" && engineState === "idle"
        ? "Loading speech model"
        : speaking && engineState === "thinking"
          ? "Working and speaking"
          : handsFree && engineState === "idle"
            ? "Hands-free: just start talking"
            : handsFree && engineState === "listening"
              ? "Listening. Pause to send."
              : CAPTION[engineState];

  return (
    <div className="flex flex-col items-center gap-3">
      <button
        type="button"
        aria-label="Push to talk. Hold to record."
        aria-pressed={engineState === "listening" || tabletRecording}
        data-testid="ptt"
        disabled={connection !== "open"}
        className="rounded-full touch-none select-none disabled:opacity-50 transition-transform active:scale-[0.985]"
        onPointerDown={(e) => {
          (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
          press();
        }}
        onPointerUp={release}
        onPointerCancel={release}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !e.repeat) {
            e.preventDefault();
            press();
          }
        }}
        onKeyUp={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            release();
          }
        }}
      >
        <Scope state={remote && tabletRecording ? "listening" : engineState} level={level} speaking={speaking} size={size} />
      </button>

      <p className="font-strip text-[15px] font-medium text-muted h-6" aria-live="polite" data-testid="caption">
        {caption}
      </p>

      <div className="flex gap-2 h-8">
        {busy && (
          <button
            type="button"
            onClick={() => send({ type: "cancel" })}
            className="inline-flex items-center gap-1.5 rounded-md border border-line px-3 text-sm text-ink hover:border-red hover:text-red"
          >
            <Square size={13} aria-hidden /> Stop
          </button>
        )}
        {(speaking || (remote && tabletSpeaking)) && (
          <button
            type="button"
            onClick={() => {
              if (remote) getTabletSpeech().stop(); // silence now, even if say_stop is late
              send({ type: "stop_speaking" });
            }}
            className="inline-flex items-center gap-1.5 rounded-md border border-line px-3 text-sm text-ink hover:border-amber hover:text-amber"
          >
            <VolumeX size={14} aria-hidden /> Quiet
          </button>
        )}
      </div>
    </div>
  );
}
