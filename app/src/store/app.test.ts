import { describe, expect, it } from "vitest";
import type { EngineEvent } from "../lib/protocol";
import { initialState, reduceEvent, type AppState } from "./app";

const run = (events: EngineEvent[], from: AppState = initialState) => events.reduce(reduceEvent, from);

describe("reduceEvent", () => {
  it("builds a turn from streamed events", () => {
    const s = run([
      { type: "turn_start", turn_id: "t", text: "run tests", session_id: "s", system: false },
      { type: "agent_text", turn_id: "t", delta: "Running. " },
      { type: "tool_start", turn_id: "t", tool_id: "a", name: "Bash", summary: "npx vitest run", path: null },
      { type: "tool_end", turn_id: "t", tool_id: "a", ok: false, preview: "1 failed" },
      { type: "agent_text", turn_id: "t", delta: "\n\nFixed." },
      { type: "turn_end", turn_id: "t", status: "ok", text: "Fixed.", cost_usd: 0.02, duration_ms: 900 },
    ]);
    const turn = s.turns[0]!;
    expect(s.sessionId).toBe("s");
    expect(turn.tools[0]).toMatchObject({ ok: false, preview: "1 failed" });
    // shorter final text must not clobber the fuller streamed text
    expect(turn.assistant).toBe("Running. \n\nFixed.");
    expect(turn).toMatchObject({ status: "ok", costUsd: 0.02, durationMs: 900 });
  });

  it("uses final text when nothing streamed", () => {
    const s = run([
      { type: "turn_start", turn_id: "t", text: "x", session_id: "s", system: false },
      { type: "turn_end", turn_id: "t", status: "ok", text: "Done." },
    ]);
    expect(s.turns[0]!.assistant).toBe("Done.");
  });

  it("only review transcripts become drafts; turn_start clears the draft", () => {
    let s = run([{ type: "transcript", draft_id: "d1", heard: "zoo stand", text: "Zustand", review: true }]);
    expect(s.draft).toEqual({ id: "d1", heard: "zoo stand", text: "Zustand" });
    s = run([{ type: "turn_start", turn_id: "t", text: "Zustand", session_id: "s", system: false }], s);
    expect(s.draft).toBeNull();
    s = run([{ type: "transcript", draft_id: null, heard: "yes", text: "yes", review: false }], s);
    expect(s.draft).toBeNull();
  });

  it("clears approvals once every item is decided", () => {
    const req = (id: string) =>
      ({ type: "approval_required", turn_id: "t", request_id: id, tool_name: "Bash", summary: id, risk: "high",
         voice_approvable: true, grantable: true, detail: "" }) as const;
    let s = run([req("a"), req("b"), { type: "approval_decided", request_id: "a", approved: true }]);
    expect(s.approvals.map((a) => a.decided)).toEqual([true, null]);
    s = run([{ type: "approval_decided", request_id: "b", approved: false }], s);
    expect(s.approvals).toEqual([]);
  });

  it("hello restores component status and listening level resets on state change", () => {
    let s = run([
      { type: "hello", state: "idle", config: {} as never, session_id: null, speaking: false, sessions: [], protocol: 1,
        components: { stt: { status: "ready" }, tts: { status: "error", detail: "pip install kokoro" } } },
      { type: "state", state: "listening", detail: null },
      { type: "level", rms: 0.7 },
    ]);
    expect(s.components.tts).toEqual({ status: "error", detail: "pip install kokoro" });
    expect(s.level).toBe(0.7);
    s = run([{ type: "state", state: "transcribing", detail: null }], s);
    expect(s.level).toBe(0);
  });

  it("caps toasts and records rejected config keys", () => {
    let s = initialState;
    for (let i = 0; i < 6; i++) s = reduceEvent(s, { type: "notice", message: `n${i}` });
    expect(s.toasts.map((t) => t.message)).toEqual(["n2", "n3", "n4", "n5"]);
    s = reduceEvent(s, { type: "config", config: {} as never, rejected: ["safe_bash"] });
    expect(s.toasts.at(-1)).toMatchObject({ kind: "error", message: "Not changed: safe_bash" });
  });

  it("loads stored sessions and marks approval turns as system", () => {
    const s = run([
      { type: "session_loaded", session: { id: "s", title: "t", workspace: "/w", updated_at: 0, turn_count: 2, claude_session_id: null,
        turns: [
          { id: "1", user_text: "hi", assistant_text: "hello", tools: [], status: "ok", cost_usd: null, created_at: 0 },
          { id: "2", user_text: "Approval decision", assistant_text: "pushed", tools: [], status: "ok", cost_usd: null, created_at: 1 },
        ] } },
    ]);
    expect(s.turns.map((t) => t.system)).toEqual([false, true]);
    expect(run([{ type: "session_loaded", session: null }], s).turns).toEqual([]);
  });

  it("tracks engines from hello and tags turns with their engine", () => {
    const s = run([
      {
        type: "hello", state: "idle", config: { engine: "cursor" } as never, session_id: null, speaking: false,
        sessions: [], components: { stt: { status: "ready" }, tts: { status: "ready" } }, protocol: 1,
        engines: [{ name: "claude", available: true }, { name: "cursor", available: true }],
      },
      { type: "turn_start", turn_id: "t", text: "lint", session_id: "s", system: false, engine: "cursor" },
    ]);
    expect(s.engines.map((e) => e.name)).toEqual(["claude", "cursor"]);
    expect(s.turns[0]!.engine).toBe("cursor");
    const loaded = reduceEvent(s, {
      type: "session_loaded",
      session: { id: "x", title: "t", workspace: "/w", engine: "cursor", updated_at: 1, turn_count: 1, claude_session_id: null,
        turns: [{ id: "a", user_text: "u", assistant_text: "a", tools: [], status: "ok", cost_usd: null, created_at: 1 }] },
    });
    expect(loaded.turns[0]!.engine).toBe("cursor");
  });

  it("tracks a chain through its steps and reports why it stopped", () => {
    const s = run([
      { type: "command_chain_start", chain_id: "c", steps: ["Lint", "Test"], name: null },
      { type: "command_chain_step", chain_id: "c", index: 1, total: 2, text: "Test" },
    ]);
    expect(s.chain).toEqual({ id: "c", name: null, steps: ["Lint", "Test"], index: 1 });
    const stale = reduceEvent(s, { type: "command_chain_step", chain_id: "old", index: 0, total: 1, text: "x" });
    expect(stale.chain?.index).toBe(1);
    const ended = reduceEvent(s, { type: "command_chain_end", chain_id: "c", success: false, reason: "declined", completed: 1, total: 2 });
    expect(ended.chain).toBeNull();
    expect(ended.toasts.at(-1)?.message).toBe("Stopped after step 2 of 2: you declined an action.");
    const done = reduceEvent(s, { type: "command_chain_end", chain_id: "c", success: true, reason: "completed", completed: 2, total: 2 });
    expect(done.toasts).toHaveLength(s.toasts.length);
  });

  it("learns hands-free state from hello and counts draft commands", () => {
    const s = run([
      { type: "hello", state: "idle", config: {} as never, session_id: null, speaking: false, sessions: [],
        components: { stt: { status: "ready" }, tts: { status: "ready" } }, protocol: 1, handsfree: true },
      { type: "draft_command", action: "send" },
      { type: "draft_command", action: "send" },
    ]);
    expect(s.continuousMode).toBe(true);
    expect(s.draftCommand).toEqual({ seq: 2, action: "send" });
  });
});

describe("secrets and engines events", () => {
  it("tracks whether a key is saved and refreshes engine availability", () => {
    let s = reduceEvent(initialState, { type: "secrets", items: { devin: true } });
    expect(s.secrets).toEqual({ devin: true });
    s = reduceEvent(s, { type: "engines", items: [{ name: "claude", available: true }, { name: "devin", available: true }] });
    expect(s.engines.map((e) => e.name)).toEqual(["claude", "devin"]);
  });
});

describe("claude sign-in state", () => {
  it("starts unknown, takes hello's value, and follows claude_auth events", () => {
    expect(initialState.claudeAuth).toEqual({ state: "unknown" });
    let s = reduceEvent(initialState, { type: "hello", state: "idle", config: {} as never, session_id: null,
      speaking: false, sessions: [], components: {} as never, protocol: 1, claude_auth: { state: "signed_out" } });
    expect(s.claudeAuth.state).toBe("signed_out");
    s = reduceEvent(s, { type: "claude_auth", state: "signing_in", detail: "Finish in your browser." });
    expect(s.claudeAuth).toEqual({ state: "signing_in", detail: "Finish in your browser.", method: undefined });
    s = reduceEvent(s, { type: "claude_auth", state: "signed_in", method: "claude.ai" });
    expect(s.claudeAuth.state).toBe("signed_in");
    expect(s.claudeAuth.detail).toBeUndefined();
  });

  it("a hello without claude_auth (older engine) keeps what we had", () => {
    const had = { ...initialState, claudeAuth: { state: "expired" as const } };
    const s = reduceEvent(had, { type: "hello", state: "idle", config: {} as never, session_id: null,
      speaking: false, sessions: [], components: {} as never, protocol: 1 });
    expect(s.claudeAuth.state).toBe("expired");
  });
});

describe("tablet access state", () => {
  const status = { enabled: true, running: true, port: 8766, urls: ["https://192.168.1.20:8766/"], devices: [] };

  it("tracks role from hello and tablet status", () => {
    const s = reduceEvent(initialState, { type: "hello", state: "idle", config: {} as never, session_id: null,
      speaking: false, sessions: [], components: {} as never, protocol: 1, role: "remote", tablet: status });
    expect(s.role).toBe("remote");
    expect(s.tablet?.urls).toEqual(["https://192.168.1.20:8766/"]);
  });

  it("keeps the pairing link while running and drops it when tablet access stops", () => {
    let s = reduceEvent(initialState, { type: "tablet_status", ...status });
    s = reduceEvent(s, { type: "tablet_pairing", url: "https://x/#pair=abc", expires_at: 99 });
    expect(s.tabletPairing).toEqual({ url: "https://x/#pair=abc", expiresAt: 99 });
    s = reduceEvent(s, { type: "tablet_status", ...status, running: false });
    expect(s.tabletPairing).toBeNull();
  });

  it("records a pairing error and ignores token and speech events", () => {
    let s = reduceEvent(initialState, { type: "pair_error", message: "No room." });
    expect(s.pairError).toBe("No room.");
    const before = s;
    for (const e of [{ type: "paired", token: "t", device_id: "d" }, { type: "say", text: "hi" }, { type: "say_stop" }] as const) {
      s = reduceEvent(s, e);
    }
    expect(s).toBe(before);
  });
});

describe("tablet pairing panel (second review)", () => {
  it("drops the pairing link once a new tablet has paired with it", () => {
    const base = { enabled: true, running: true, port: 8766, urls: [], devices: [] };
    let s = reduceEvent(initialState, { type: "tablet_status", ...base });
    s = reduceEvent(s, { type: "tablet_pairing", url: "https://x/#pair=abc", expires_at: 9e9 });
    s = reduceEvent(s, { type: "tablet_status", ...base, devices: [{ id: "d", name: "Galaxy Tab", created: 1, last_seen: 1, connected: true }] });
    expect(s.tabletPairing).toBeNull();
  });
});
