import { describe, expect, it } from "vitest";
import type { EngineEvent, TaskView } from "../lib/protocol";
import { initialState, reduceEvent, type AppState } from "./app";

const run = (events: EngineEvent[], from: AppState = initialState) => events.reduce(reduceEvent, from);

const task = (over: Partial<TaskView> = {}): TaskView => ({
  id: "t1",
  workspace: "/proj",
  engine: "devin",
  title: "orders api",
  instruction: "add the orders endpoint",
  lanes: ["engine"],
  status: "running",
  created_at: 10,
  started_at: 11,
  finished_at: null,
  summary: "",
  result_text: "",
  error: "",
  cost_usd: null,
  cli_session_id: null,
  turns: 0,
  ...over,
});

const snapshot = (items: TaskView[]): EngineEvent => ({
  type: "tasks",
  items,
  folders: ["app", "engine"],
  saved_lanes: { backend: ["engine"] },
  max_parallel: 3,
});

describe("task board reducer", () => {
  it("loads the snapshot from hello and from tasks events, newest first", () => {
    const s = run([snapshot([task({ id: "a", created_at: 1 }), task({ id: "b", created_at: 2 })])]);
    expect(s.tasks.map((t) => t.id)).toEqual(["b", "a"]);
    expect(s.taskFolders).toEqual(["app", "engine"]);
    expect(s.savedLanes).toEqual({ backend: ["engine"] });
    expect(s.maxParallel).toBe(3);
    const h = run([
      {
        type: "hello",
        state: "idle",
        config: { workspace: "/proj" } as never,
        session_id: null,
        speaking: false,
        sessions: [],
        components: initialState.components,
        protocol: 1,
        tasks: { type: "tasks", items: [task()], folders: ["api"], saved_lanes: {}, max_parallel: 2 },
      },
    ]);
    expect(h.tasks).toHaveLength(1);
    expect(h.taskFolders).toEqual(["api"]);
  });

  it("upserts task updates and streams text and activity", () => {
    let s = run([
      { type: "task_update", task: task({ status: "queued" }) },
      { type: "task_update", task: task({ status: "running" }) },
      { type: "task_text", task_id: "t1", delta: "Working " },
      { type: "task_text", task_id: "t1", delta: "on it." },
      { type: "task_activity", task_id: "t1", tool: "Edit", path: "engine/x.py" },
    ]);
    expect(s.tasks).toHaveLength(1);
    expect(s.tasks[0]!.status).toBe("running");
    expect(s.taskText.t1).toBe("Working on it.");
    expect(s.taskActivity.t1).toEqual({ tool: "Edit", path: "engine/x.py" });
    s = run([{ type: "task_update", task: task({ status: "done", result_text: "All done." }) }], s);
    expect(s.taskActivity.t1).toBeUndefined();
  });

  it("a finished task raises a toast and the attention count; failures are errors", () => {
    let s = run([
      { type: "task_update", task: task() },
      { type: "task_done", task_id: "t1", status: "done", title: "orders api", engine: "devin", summary: "Added it.", error: "" },
    ]);
    expect(s.toasts.at(-1)).toMatchObject({ kind: "info", message: "Devin finished orders api. Added it." });
    expect(s.taskAttention).toBe(1);
    s = run([{ type: "task_done", task_id: "t1", status: "failed", title: "orders api", engine: "devin", summary: "", error: "It crashed." }], s);
    expect(s.toasts.at(-1)).toMatchObject({ kind: "error", message: "Devin's task orders api failed: It crashed." });
    expect(s.taskAttention).toBe(2);
  });

  it("task approvals stay apart from the conversation's and clear when the task moves on", () => {
    let s = run([
      { type: "task_update", task: task({ status: "needs_approval" }) },
      {
        type: "task_approval_required",
        task_id: "t1",
        request_id: "r1",
        tool_name: "Bash",
        summary: "Install zod",
        risk: "high",
        voice_approvable: false,
        grantable: true,
        detail: "npm install zod",
      },
    ]);
    expect(s.approvals).toEqual([]);
    expect(s.taskApprovals).toEqual([
      expect.objectContaining({ taskId: "t1", requestId: "r1", summary: "Install zod", decided: null }),
    ]);
    expect(s.taskAttention).toBe(1);
    s = run([{ type: "task_approval_decided", task_id: "t1", request_id: "r1", approved: true }], s);
    expect(s.taskApprovals[0]!.decided).toBe(true);
    s = run([{ type: "task_update", task: task({ status: "queued" }) }], s);
    expect(s.taskApprovals).toEqual([]);
  });

  it("a snapshot restores cards sent while this client was away and drops answered ones", () => {
    const card = {
      type: "task_approval_required" as const,
      task_id: "t1",
      request_id: "r7",
      tool_name: "Bash",
      summary: "Install zod",
      risk: "high" as const,
      voice_approvable: false,
      grantable: true,
      detail: "npm install zod",
    };
    const withCards = (items: TaskView[], approvals: (typeof card)[]): EngineEvent => ({
      type: "tasks",
      items,
      folders: [],
      saved_lanes: {},
      max_parallel: 3,
      approvals,
    });
    let s = run([withCards([task({ status: "needs_approval" })], [card])]);
    expect(s.taskApprovals).toEqual([expect.objectContaining({ taskId: "t1", requestId: "r7", decided: null, voiceApprovable: false })]);
    s = run([card], s); // live copy of the same card: no duplicate
    expect(s.taskApprovals).toHaveLength(1);
    s = run([withCards([task({ status: "done" })], [])], s);
    expect(s.taskApprovals).toEqual([]);
  });

  it("a snapshot drops approvals and text of tasks that are gone", () => {
    let s = run([
      { type: "task_update", task: task({ id: "gone", status: "needs_approval" }) },
      { type: "task_text", task_id: "gone", delta: "x" },
      {
        type: "task_approval_required",
        task_id: "gone",
        request_id: "r1",
        tool_name: "Bash",
        summary: "s",
        risk: "normal",
        voice_approvable: false,
        grantable: true,
        detail: "",
      },
    ]);
    s = run([snapshot([task({ id: "kept" })])], s);
    expect(s.taskApprovals).toEqual([]);
    expect(s.taskText.gone).toBeUndefined();
  });
});
