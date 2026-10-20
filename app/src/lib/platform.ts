import type { EngineEndpoint } from "./engine";
import { isRemoteMode, remoteEndpoint } from "./remote";

export const isTauri = (): boolean =>
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/**
 * Desktop: the Rust shell spawned the engine and holds port + token; ask it.
 * Browser (dev / E2E against `python -m barnaby_engine --dev`): take
 * them from the URL or Vite env. The URL-token path is dev-only by design.
 */
export async function resolveEndpoint(): Promise<EngineEndpoint> {
  if (isTauri()) {
    const { invoke } = await import("@tauri-apps/api/core");
    const info = await invoke<{ port: number; token: string } | null>("engine_info");
    if (!info) throw new Error("engine not ready");
    return { url: `ws://127.0.0.1:${info.port}`, token: info.token };
  }
  if (isRemoteMode()) return remoteEndpoint();
  const params = new URLSearchParams(window.location.search);
  const url = params.get("engine") ?? import.meta.env.VITE_ENGINE_URL;
  const token = params.get("token") ?? import.meta.env.VITE_ENGINE_TOKEN;
  if (!url || !token) throw new Error("No engine endpoint. Start the engine with --dev and open ?engine=ws://127.0.0.1:PORT&token=TOKEN");
  return { url, token };
}

export async function pickFolder(): Promise<string | null> {
  if (!isTauri()) return window.prompt("Project folder path") || null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const picked = await open({ directory: true, multiple: false, title: "Choose project folder" });
  return typeof picked === "string" ? picked : null;
}

export async function openExternal(url: string): Promise<void> {
  if (!/^https?:\/\//i.test(url)) return;
  if (!isTauri()) {
    window.open(url, "_blank", "noopener,noreferrer");
    return;
  }
  const { openUrl } = await import("@tauri-apps/plugin-opener");
  await openUrl(url);
}

/** Global push-to-talk: the Rust shell emits `ptt` with {pressed} from the OS-level shortcut. */
export async function onGlobalPtt(handler: (pressed: boolean) => void): Promise<() => void> {
  if (!isTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen<{ pressed: boolean }>("ptt", (e) => handler(e.payload.pressed));
}

/** Supervisor events from the Rust shell (engine lifecycle, hotkey registration). */
export async function onShellEvents(handlers: {
  ready: () => void;
  error: (message: string) => void;
  shortcutUnavailable: (message: string) => void;
}): Promise<() => void> {
  if (!isTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  const offs = await Promise.all([
    listen("engine-ready", () => handlers.ready()),
    listen<string>("engine-error", (e) => handlers.error(e.payload)),
    listen<string>("shortcut-unavailable", (e) => handlers.shortcutUnavailable(e.payload)),
  ]);
  return () => offs.forEach((off) => off());
}
