import { EngineClient } from "./engine";
import { resolveEndpoint } from "./platform";
import type { Command } from "./protocol";
import { useApp } from "../store/app";

export const client = new EngineClient(resolveEndpoint);

/** Send a command; surfaces a toast instead of failing silently when offline. */
export function send(cmd: Command): boolean {
  const ok = client.send(cmd);
  if (!ok && cmd.type !== "ptt_stop") {
    useApp.getState().apply({ type: "error", code: "offline", message: "The voice engine isn't connected yet." });
  }
  return ok;
}
