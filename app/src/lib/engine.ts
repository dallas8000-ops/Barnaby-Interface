import type { Command, EngineEvent } from "./protocol";

export type ConnectionStatus = "connecting" | "open" | "closed" | "unauthorized" | "unpaired";

export interface EngineEndpoint {
  url: string;
  /** Desktop: the shell's process token. Tablet: the stored device token. */
  token?: string;
  /** Tablet, first visit: the one-time code from the QR link, sent in place of a token. */
  pair?: { code: string; name: string };
  onPaired?: (token: string, deviceId: string) => void;
}

type Listener = (event: EngineEvent) => void;

/** Terminal refusals that aren't about the token: stop retrying, say why, keep the pairing. */
const REFUSED: Record<number, string> = {
  4429: "Too many failed attempts from this tablet. Try again in 10 minutes.",
  4430: "The PC already has 4 tablets connected. Close Barnaby on one of them, then reload this page.",
};
type StatusListener = (status: ConnectionStatus) => void;

/**
 * WebSocket client with first-frame auth and jittered exponential backoff.
 *
 * Close code 4401 (bad token) is terminal: retrying cannot succeed and would
 * hammer the engine. 4000 (replaced by a newer client) is also terminal, so two
 * windows don't ping-pong the single client slot forever. For a tablet, 4403
 * (device removed on the PC) and 4409 (no room to pair) are terminal too, and a
 * missing pairing ("unpaired") never retries.
 */
export class EngineClient {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private listeners = new Set<Listener>();
  private statusListeners = new Set<StatusListener>();

  constructor(
    private readonly resolveEndpoint: () => Promise<EngineEndpoint>,
    private readonly socketFactory: (url: string) => WebSocket = (url) => new WebSocket(url),
  ) {}

  onEvent(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  onStatus(fn: StatusListener): () => void {
    this.statusListeners.add(fn);
    return () => this.statusListeners.delete(fn);
  }

  start(): void {
    this.stopped = false;
    void this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.ws?.close(1000, "client stopped");
    this.ws = null;
  }

  /** Skip the backoff wait and connect now (the shell says the engine is ready). */
  kick(): void {
    if (this.stopped || this.ws) return;
    if (this.timer) clearTimeout(this.timer);
    this.attempt = 0;
    void this.connect();
  }

  /** Returns false when the command could not be sent (socket not open). */
  send(cmd: Command): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify(cmd));
    return true;
  }

  private emitStatus(status: ConnectionStatus): void {
    this.statusListeners.forEach((fn) => fn(status));
  }

  private async connect(): Promise<void> {
    if (this.stopped) return;
    this.emitStatus("connecting");
    let endpoint: EngineEndpoint;
    try {
      endpoint = await this.resolveEndpoint();
    } catch (err) {
      if (err instanceof Error && err.name === "PairingNeeded") {
        this.emitStatus("unpaired");
        return;
      }
      this.scheduleReconnect();
      return;
    }
    if (this.stopped) return;
    const ws = this.socketFactory(endpoint.url);
    this.ws = ws;
    let authed = false;

    ws.onopen = () => {
      const first = endpoint.pair
        ? { type: "pair", code: endpoint.pair.code, name: endpoint.pair.name }
        : { type: "auth", token: endpoint.token ?? "" };
      ws.send(JSON.stringify(first));
    };
    ws.onmessage = (msg) => {
      let event: EngineEvent;
      try {
        event = JSON.parse(String(msg.data)) as EngineEvent;
      } catch {
        return;
      }
      if (event.type === "paired") {
        endpoint.onPaired?.(event.token, event.device_id);
        return;
      }
      if (!authed && event.type === "hello") {
        authed = true;
        this.attempt = 0;
        this.emitStatus("open");
      }
      this.listeners.forEach((fn) => fn(event));
    };
    ws.onclose = (ev) => {
      if (this.ws !== ws) return;
      this.ws = null;
      const refused = REFUSED[ev.code];
      if (refused) {
        this.emitStatus("closed");
        this.listeners.forEach((fn) => fn({ type: "error", code: "connection", message: refused }));
        return;
      }
      if (ev.code === 4401 || ev.code === 4403 || ev.code === 4409 || ev.code === 4000) {
        this.emitStatus(ev.code === 4000 ? "closed" : "unauthorized");
        return;
      }
      this.emitStatus("closed");
      this.scheduleReconnect();
    };
    ws.onerror = () => {
      /* onclose follows and handles retry */
    };
  }

  private scheduleReconnect(): void {
    if (this.stopped) return;
    const base = Math.min(8000, 250 * 2 ** this.attempt);
    const delay = base / 2 + Math.random() * (base / 2);
    this.attempt += 1;
    this.timer = setTimeout(() => void this.connect(), delay);
  }
}
