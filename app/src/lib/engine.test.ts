import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EngineClient, type ConnectionStatus } from "./engine";

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
