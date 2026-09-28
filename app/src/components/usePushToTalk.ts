import { useEffect, useRef } from "react";
import { send } from "../lib/client";
import { onGlobalPtt } from "../lib/platform";
import { isRemoteMode } from "../lib/remote";
import { MicError, TabletMic } from "../lib/tabletMic";
import { getTabletSpeech } from "../lib/tabletSpeech";
import { useApp } from "../store/app";

let tabletMic: TabletMic | null = null;
let lastLevel = 0;
// The worklet delivers ~375 frames/s at 48 kHz; the scope only needs animation-rate updates.
const mic = () =>
  (tabletMic ??= new TabletMic((rms) => {
    const now = performance.now();
    if (rms === 0 || now - lastLevel >= 33) {
      lastLevel = now;
      useApp.setState({ level: rms });
    }
  }));

/** Tablet: record locally, send one clip on release. The PC transcribes it. */
async function tabletPress(onLimit: () => void) {
  // Barge-in: stop the tablet's voice (so the mic doesn't hear it) and drop queued replies.
  getTabletSpeech().stop();
  if (useApp.getState().tabletSpeaking || useApp.getState().speaking) send({ type: "stop_speaking" });
  useApp.setState({ tabletRecording: true });
  try {
    await mic().start(onLimit);
  } catch (err) {
    useApp.setState({ tabletRecording: false });
    const message = err instanceof MicError ? err.message : "Couldn't start the microphone.";
    useApp.getState().apply({ type: "error", code: "mic", message });
  }
}

async function tabletRelease() {
  useApp.setState({ tabletRecording: false });
  const clip = await mic().stop();
  if (clip) send({ type: "audio_clip", pcm_b64: clip });
}

const isTypingTarget = (el: EventTarget | null): boolean => {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
};

/**
 * Hold-to-talk from three sources: the in-window Space key, the OS-wide
 * shortcut (via the Tauri shell), and pointer holds on the scope. All funnel
 * through one `held` flag so overlapping sources can't double-start or leave
 * the mic open. Blur / visibility loss force a release: a keyup that happens
 * while another window has focus is never delivered, which would otherwise
 * leave the microphone recording indefinitely.
 */
export function usePushToTalk() {
  const held = useRef(false);

  const remote = isRemoteMode();
  const press = () => {
    if (held.current) return;
    held.current = true;
    if (remote) void tabletPress(release);
    else send({ type: "ptt_start" });
  };
  const release = () => {
    if (!held.current) return;
    held.current = false;
    if (remote) void tabletRelease();
    else send({ type: "ptt_stop" });
  };

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code !== "Space" || e.repeat || e.ctrlKey || e.altKey || e.metaKey || isTypingTarget(e.target)) return;
      if (e.target instanceof HTMLButtonElement) return; // Space activates buttons; don't hijack
      e.preventDefault();
      press();
    };
    const up = (e: KeyboardEvent) => {
      if (e.code !== "Space") return;
      if (held.current) e.preventDefault();
      release();
    };
    const lost = () => release();
    const vis = () => document.visibilityState === "hidden" && release();

    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", lost);
    document.addEventListener("visibilitychange", vis);

    let unlisten: (() => void) | undefined;
    let disposed = false;
    void onGlobalPtt((pressed) => (pressed ? press() : release())).then((fn) => {
      if (disposed) fn();
      else unlisten = fn;
    });

    return () => {
      disposed = true;
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", lost);
      document.removeEventListener("visibilitychange", vis);
      unlisten?.();
      release();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { press, release };
}
