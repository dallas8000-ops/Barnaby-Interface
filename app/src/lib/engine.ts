import type { Command, EngineEvent } from "./protocol";

export type ConnectionStatus = "connecting" | "open" | "closed" | "unauthorized";

export interface EngineEndpoint {
  url: string;
  token: string;
}

type Listener = (event: EngineEvent) => void;
type StatusListener = (status: ConnectionStatus) => void;

/**
 * WebSocket client with first-frame auth and jittered exponential backoff.
 *
 * Close code 4401 (bad token) is terminal: retrying cannot succeed and would
 * hammer the engine. 4000 (replaced by a newer client) is also terminal, so two
 * windows don't ping-pong the single client slot forever.
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
    } catch {
      this.scheduleReconnect();
      return;
    }
    if (this.stopped) return;
    const ws = this.socketFactory(endpoint.url);
    this.ws = ws;
    let authed = false;

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "auth", token: endpoint.token } satisfies Command));
    };
    ws.onmessage = (msg) => {
      let event: EngineEvent;
      try {
        event = JSON.parse(String(msg.data)) as EngineEvent;
      } catch {
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
      if (ev.code === 4401 || ev.code === 4000) {
        this.emitStatus(ev.code === 4401 ? "unauthorized" : "closed");
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
