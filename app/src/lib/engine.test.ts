import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EngineClient, type ConnectionStatus, type EngineEndpoint } from "./engine";
import { PairingNeeded } from "./remote";

class FakeSocket {
  static OPEN = 1;
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {}
  send(d: string) {
    this.sent.push(d);
  }
  close() {}
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  receive(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
  drop(code: number) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
}

describe("EngineClient", () => {
  let sockets: FakeSocket[];
  let statuses: ConnectionStatus[];
  let client: EngineClient;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeSocket);
    sockets = [];
    statuses = [];
    client = new EngineClient(
      async () => ({ url: "ws://127.0.0.1:9", token: "secret" }),
      (url) => {
        const s = new FakeSocket(url);
        sockets.push(s);
        return s as unknown as WebSocket;
      },
    );
    client.onStatus((s) => statuses.push(s));
  });
  afterEach(() => {
    client.stop();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const flush = () => vi.advanceTimersByTimeAsync(0);

  it("sends auth as the first frame and reports open only after hello", async () => {
    client.start();
    await flush();
    sockets[0]!.open();
    expect(JSON.parse(sockets[0]!.sent[0]!)).toEqual({ type: "auth", token: "secret" });
    expect(statuses).toEqual(["connecting"]);
    sockets[0]!.receive({ type: "hello" });
    expect(statuses.at(-1)).toBe("open");
  });

  it("forwards events and refuses to send while closed", async () => {
    const got: string[] = [];
    client.onEvent((e) => got.push(e.type));
    expect(client.send({ type: "ping" })).toBe(false);
    client.start();
    await flush();
    sockets[0]!.open();
    sockets[0]!.receive({ type: "hello" });
    sockets[0]!.receive({ type: "state", state: "idle", detail: null });
    expect(got).toEqual(["hello", "state"]);
    expect(client.send({ type: "ping" })).toBe(true);
  });

  it("reconnects with backoff after an unexpected close", async () => {
    client.start();
    await flush();
    sockets[0]!.drop(1006);
    expect(statuses.at(-1)).toBe("closed");
    await vi.advanceTimersByTimeAsync(300);
    expect(sockets).toHaveLength(2);
  });

  it.each([4401, 4000])("does not retry after terminal close %i", async (code) => {
    client.start();
    await flush();
    sockets[0]!.drop(code);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(sockets).toHaveLength(1);
    expect(statuses.at(-1)).toBe(code === 4401 ? "unauthorized" : "closed");
  });

  it("kick() skips the backoff wait", async () => {
    client.start();
    await flush();
    sockets[0]!.drop(1006);
    client.kick();
    await flush();
    expect(sockets).toHaveLength(2);
  });

  it("ignores malformed frames", async () => {
    const got: unknown[] = [];
    client.onEvent((e) => got.push(e));
    client.start();
    await flush();
    sockets[0]!.onmessage?.({ data: "{not json" });
    expect(got).toEqual([]);
  });
});

describe("EngineClient pairing (tablet)", () => {
  let sockets: FakeSocket[];
  let statuses: ConnectionStatus[];
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeSocket);
    sockets = [];
    statuses = [];
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
  const make = (endpoint: () => Promise<EngineEndpoint>) => {
    const c = new EngineClient(endpoint, (url) => {
      const s = new FakeSocket(url);
      sockets.push(s);
      return s as unknown as WebSocket;
    });
    c.onStatus((s) => statuses.push(s));
    return c;
  };

  it("sends a pair frame, hands the token over, then opens on hello", async () => {
    const paired: string[] = [];
    const c = make(async () => ({
      url: "wss://pc/ws",
      pair: { code: "CODE", name: "Galaxy Tab" },
      onPaired: (t: string) => paired.push(t),
    }));
    c.start();
    await vi.advanceTimersByTimeAsync(0);
    sockets[0]!.open();
    expect(JSON.parse(sockets[0]!.sent[0]!)).toEqual({ type: "pair", code: "CODE", name: "Galaxy Tab" });
    sockets[0]!.receive({ type: "paired", token: "TOKEN", device_id: "dev_1" });
    expect(paired).toEqual(["TOKEN"]);
    sockets[0]!.receive({ type: "hello" });
    expect(statuses.at(-1)).toBe("open");
    c.stop();
  });

  it.each([4403, 4409])("treats close %i (removed / no room) as terminal", async (code) => {
    const c = make(async () => ({ url: "wss://pc/ws", token: "t" }));
    c.start();
    await vi.advanceTimersByTimeAsync(0);
    sockets[0]!.drop(code);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(sockets).toHaveLength(1);
    expect(statuses.at(-1)).toBe("unauthorized");
    c.stop();
  });

  it("does not retry while pairing is needed", async () => {
    const c = make(async () => {
      throw new PairingNeeded();
    });
    c.start();
    await vi.advanceTimersByTimeAsync(20_000);
    expect(sockets).toHaveLength(0);
    expect(statuses.at(-1)).toBe("unpaired");
    c.stop();
  });
});

describe("EngineClient close codes (second review)", () => {
  let sockets: FakeSocket[];
  let statuses: ConnectionStatus[];
  let events: { type: string; message?: string }[];
  let c: EngineClient;
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeSocket);
    sockets = [];
    statuses = [];
    events = [];
    c = new EngineClient(async () => ({ url: "wss://pc/ws", token: "t" }), (url) => {
      const s = new FakeSocket(url);
      sockets.push(s);
      return s as unknown as WebSocket;
    });
    c.onStatus((s) => statuses.push(s));
    c.onEvent((e) => events.push(e as never));
  });
  afterEach(() => {
    c.stop();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("1001 (the PC stopped tablet access) reconnects and is not a removal", async () => {
    c.start();
    await vi.advanceTimersByTimeAsync(0);
    sockets[0]!.drop(1001);
    expect(statuses.at(-1)).toBe("closed");
    await vi.advanceTimersByTimeAsync(10_000);
    expect(sockets.length).toBeGreaterThan(1);
    expect(statuses).not.toContain("unauthorized");
  });

  it.each([
    [4429, /failed attempts/],
    [4430, /tablets connected/],
  ])("%i stops retrying and says why, without dropping the pairing", async (code, message) => {
    c.start();
    await vi.advanceTimersByTimeAsync(0);
    sockets[0]!.drop(code);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(sockets).toHaveLength(1);
    expect(statuses.at(-1)).toBe("closed");
    expect(events.at(-1)?.message).toMatch(message);
  });
});
