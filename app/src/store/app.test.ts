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
});
