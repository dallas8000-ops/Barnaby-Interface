import { useEffect, useRef } from "react";
import { send } from "../lib/client";
import { onGlobalPtt } from "../lib/platform";

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

  const press = () => {
    if (held.current) return;
    held.current = true;
    send({ type: "ptt_start" });
  };
  const release = () => {
    if (!held.current) return;
    held.current = false;
    send({ type: "ptt_stop" });
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
