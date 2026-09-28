import { beforeEach, describe, expect, it, vi } from "vitest";
import { DEVICE_KEY, deviceName, forgetDevice, isRemoteMode, remoteEndpoint, PairingNeeded } from "./remote";

function fakeWindow(href: string, stored: Record<string, string> = {}) {
  const url = new URL(href);
  const store = new Map(Object.entries(stored));
  const replaced: string[] = [];
  return {
    win: {
      location: { protocol: url.protocol, host: url.host, hash: url.hash, search: url.search, pathname: url.pathname },
      history: { replaceState: (_s: unknown, _t: string, u: string) => replaced.push(u) },
      localStorage: {
        getItem: (k: string) => store.get(k) ?? null,
        setItem: (k: string, v: string) => void store.set(k, v),
        removeItem: (k: string) => void store.delete(k),
      },
      navigator: { userAgent: "Mozilla/5.0 (Linux; Android 15; SM-X820) AppleWebKit Chrome/140 Safari" },
    },
    store,
    replaced,
  };
}

describe("remote (tablet) mode", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("is on only for an https page outside the desktop app and without dev params", () => {
    expect(isRemoteMode(fakeWindow("https://192.168.1.20:8766/").win as never)).toBe(true);
    expect(isRemoteMode(fakeWindow("http://localhost:1420/").win as never)).toBe(false);
    expect(isRemoteMode(fakeWindow("https://x/?engine=ws://a&token=b").win as never)).toBe(false);
    expect(isRemoteMode({ ...fakeWindow("https://x/").win, __TAURI_INTERNALS__: {} } as never)).toBe(false);
  });

  it("uses the stored device token against the same host", () => {
    const { win } = fakeWindow("https://192.168.1.20:8766/", { [DEVICE_KEY]: "tok123" });
    const ep = remoteEndpoint(win as never);
    expect(ep.url).toBe("wss://192.168.1.20:8766/ws");
    expect(ep.token).toBe("tok123");
    expect(ep.pair).toBeUndefined();
  });

  it("pairs from the QR fragment, strips it from the address bar, and stores the token", () => {
    const { win, store, replaced } = fakeWindow("https://192.168.1.20:8766/#pair=Code_abc-123XYZ");
    const ep = remoteEndpoint(win as never);
    expect(ep.pair).toEqual({ code: "Code_abc-123XYZ", name: "Galaxy Tab" });
    expect(replaced).toEqual(["/"]); // the one-time code doesn't linger in history
    ep.onPaired!("newtoken", "dev_1");
    expect(store.get(DEVICE_KEY)).toBe("newtoken");
  });

  it("asks to pair when there is neither a token nor a code", () => {
    const { win } = fakeWindow("https://192.168.1.20:8766/");
    expect(() => remoteEndpoint(win as never)).toThrow(PairingNeeded);
  });

  it("rejects a malformed pairing fragment", () => {
    const { win } = fakeWindow("https://192.168.1.20:8766/#pair=<script>");
    expect(() => remoteEndpoint(win as never)).toThrow(PairingNeeded);
  });

  it("names the device for the PC's list", () => {
    expect(deviceName("Mozilla/5.0 (Linux; Android 15; SM-X820) Chrome")).toBe("Galaxy Tab");
    expect(deviceName("Mozilla/5.0 (Linux; Android 14; Pixel Tablet) Chrome")).toBe("Android tablet");
    expect(deviceName("Mozilla/5.0 (iPad; CPU OS 18_0)")).toBe("Tablet");
    expect(deviceName("x".repeat(500))).toMatch(/^[\w .()'-]{1,60}$/);
  });

  it("forgets the device", () => {
    const { win, store } = fakeWindow("https://x/", { [DEVICE_KEY]: "t" });
    forgetDevice(win as never);
    expect(store.has(DEVICE_KEY)).toBe(false);
  });
});
