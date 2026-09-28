import type { EngineEndpoint } from "./engine";

/**
 * Tablet ("remote") mode: the page was served over HTTPS by the PC's engine to a
 * paired tablet. The device token lives in this origin's localStorage and is only
 * ever sent inside the encrypted socket. A pairing code arrives once, in the URL
 * fragment from the QR code (fragments aren't sent to the server), and is removed
 * from the address bar right away.
 */
export const DEVICE_KEY = "barnaby.device";
const CODE_RE = /^[A-Za-z0-9_-]{8,64}$/;

export class PairingNeeded extends Error {
  constructor() {
    super("This tablet isn't paired with Barnaby yet.");
    this.name = "PairingNeeded";
  }
}

type Win = Pick<Window, "location" | "history" | "localStorage" | "navigator"> & { __TAURI_INTERNALS__?: unknown };

export function isRemoteMode(win: Win = window): boolean {
  if ("__TAURI_INTERNALS__" in win) return false;
  if (win.location.protocol !== "https:") return false;
  const params = new URLSearchParams(win.location.search);
  return !params.get("engine");
}

export function deviceName(ua: string): string {
  if (/SM-[XTP]\d/i.test(ua)) return "Galaxy Tab";
  if (/Android/i.test(ua)) return "Android tablet";
  return "Tablet";
}

function storage(win: Win): Storage | null {
  try {
    return win.localStorage;
  } catch {
    return null; // storage blocked: pairing still works for this visit
  }
}

let pendingCode: string | null = null;

export function remoteEndpoint(win: Win = window): EngineEndpoint {
  const url = `wss://${win.location.host}/ws`;
  const hash = new URLSearchParams(win.location.hash.replace(/^#/, ""));
  const fromHash = hash.get("pair");
  if (fromHash !== null) {
    win.history.replaceState(null, "", win.location.pathname || "/");
    if (CODE_RE.test(fromHash)) pendingCode = fromHash;
  }
  const token = storage(win)?.getItem(DEVICE_KEY);
  if (token) return { url, token };
  if (pendingCode) {
    const code = pendingCode;
    return {
      url,
      pair: { code, name: deviceName(win.navigator.userAgent) },
      onPaired: (newToken: string) => {
        pendingCode = null;
        storage(win)?.setItem(DEVICE_KEY, newToken);
      },
    };
  }
  throw new PairingNeeded();
}

export function forgetDevice(win: Win = window): void {
  storage(win)?.removeItem(DEVICE_KEY);
}
