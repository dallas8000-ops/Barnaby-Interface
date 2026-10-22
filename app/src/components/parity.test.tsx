// PC and tablet share one Barnaby: projects, sessions across projects, tasks in any
// project, and a snapshot that restores everything a device missed.
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const sent: unknown[] = [];
vi.mock("../lib/client", () => ({
  send: (cmd: unknown) => {
    sent.push(cmd);
    return true;
  },
  client: { onEvent: () => () => {}, onStatus: () => () => {}, start: () => {}, stop: () => {} },
}));

import type { EngineEvent, LiveSnapshot, ProjectInfo, TaskView } from "../lib/protocol";
import { initialState, reduceEvent, useApp, type AppState } from "../store/app";
import { AssignTaskDialog } from "./AssignTaskDialog";
import { Sidebar } from "./Sidebar";

const run = (events: EngineEvent[], from: AppState = initialState) => events.reduce(reduceEvent, from);

const project = (over: Partial<ProjectInfo> = {}): ProjectInfo => ({
  path: "/shop",
  name: "shop",
  current: true,
  sessions: 2,
  last_used: 10,
  active_tasks: 0,
  folders: ["api", "app"],
  saved_lanes: {},
  ...over,
});
const PROJECTS = [project(), project({ path: "/blog", name: "blog", current: false, active_tasks: 1, folders: ["posts"], saved_lanes: { writing: ["posts"] } })];

const emptyLive: LiveSnapshot = { session: null, turn: null, approvals: [], chain: null, draft: null, task_live: {}, recent: [] };
const hello = (live: Partial<LiveSnapshot>, over: Record<string, unknown> = {}): EngineEvent =>
  ({ type: "hello", state: "idle", config: { workspace: "/shop" } as never, session_id: "s1", speaking: false, sessions: [],
     components: { stt: { status: "ready" }, tts: { status: "ready" } }, protocol: 1, projects: PROJECTS,
     live: { ...emptyLive, ...live }, ...over }) as EngineEvent;

beforeEach(() => {
  sent.length = 0;
  useApp.setState({
    ...initialState,
    connection: "open",
    config: { workspace: "/shop", engine: "claude" } as never,
    engines: [{ name: "claude", available: true }],
    taskFolders: ["api", "app"],
    savedLanes: {},
  });
});

describe("snapshot on (re)connect", () => {
  it("rebuilds the conversation, the turn in flight, open cards, chain and draft", () => {
    const s = run([
      hello({
        session: { id: "s1", title: "t", workspace: "/shop", updated_at: 1, turn_count: 1, claude_session_id: null,
                   turns: [{ id: "a", user_text: "hi", assistant_text: "Hello.", tools: [], status: "ok", cost_usd: null, created_at: 1 }] },
        turn: { turn_id: "t2", text: "run tests", session_id: "s1", system: false, engine: "claude", assistant: "Running. ",
                tools: [{ id: "x", name: "Bash", summary: "npm test", path: null, ok: null }] },
        approvals: [{ type: "approval_required", turn_id: "t1", request_id: "r1", tool_name: "Write", summary: "Write x",
                      risk: "normal", voice_approvable: true, grantable: true, detail: "" }],
        chain: { chain_id: "c", name: null, steps: ["a", "b"], index: 1 },
        draft: { draft_id: "d1", heard: "zoo stand", text: "Zustand" },
        task_live: { k1: { tool: "Edit", path: "api/x.py", text: "editing" } },
      }),
    ]);
    expect(s.sessionId).toBe("s1");
    expect(s.turns.map((t) => [t.text, t.status])).toEqual([["hi", "ok"], ["run tests", "running"]]);
    expect(s.turns[1]!.tools[0]!.summary).toBe("npm test");
    expect(s.approvals.map((a) => a.requestId)).toEqual(["r1"]);
    expect(s.chain).toMatchObject({ id: "c", index: 1 });
    expect(s.draft).toEqual({ id: "d1", heard: "zoo stand", text: "Zustand" });
    expect(s.taskActivity.k1).toEqual({ tool: "Edit", path: "api/x.py" });
    expect(s.taskText.k1).toBe("editing");
    expect(s.projects.map((p) => p.name)).toEqual(["shop", "blog"]);
  });

  it("a resent snapshot drops cards that were decided on another device", () => {
    let s = run([{ type: "approval_required", turn_id: "t", request_id: "r1", tool_name: "Bash", summary: "x", risk: "high",
                   voice_approvable: true, grantable: true, detail: "" }]);
    expect(s.approvals).toHaveLength(1);
    s = run([hello({ approvals: [] })], s);
    expect(s.approvals).toEqual([]);
  });

  it("shows only notifications this device missed", () => {
    const recent = [
      { type: "notice", message: "old news", nid: 5, age_s: 4000 },
      { type: "task_done", task_id: "k", status: "done", title: "orders", engine: "claude", summary: "Added it.", error: "", nid: 6, age_s: 30 },
    ] as LiveSnapshot["recent"];
    let s = run([hello({ recent })]);
    expect(s.toasts.map((t) => t.message)).toEqual(["Claude finished orders. Added it."]); // fresh device: recent only
    expect(s.lastNid).toBe(6);
    s = run([{ type: "notice", message: "live one", nid: 7 }], s);
    s = run([hello({ recent: [...recent, { type: "error", code: "x", message: "missed while asleep", nid: 8, age_s: 5 }] })], s);
    expect(s.toasts.map((t) => t.message)).toEqual(["Claude finished orders. Added it.", "live one", "missed while asleep"]);
  });

  it("a hello without a live snapshot (older engine) keeps the conversation", () => {
    const s = run([{ type: "turn_start", turn_id: "t", text: "x", session_id: "s", system: false },
                   { type: "hello", state: "idle", config: {} as never, session_id: "s", speaking: false, sessions: [],
                     components: { stt: { status: "ready" }, tts: { status: "ready" } }, protocol: 1 }]);
    expect(s.turns).toHaveLength(1);
  });
});

describe("two devices, one engine", () => {
  it("a draft sent or discarded elsewhere closes here", () => {
    let s = run([{ type: "transcript", draft_id: "d1", heard: "x", text: "x", review: true }]);
    s = run([{ type: "draft_closed", draft_id: "other" }], s);
    expect(s.draft).not.toBeNull();
    s = run([{ type: "draft_closed", draft_id: "d1" }], s);
    expect(s.draft).toBeNull();
  });

  it("a card isn't shown twice when the snapshot and the live event overlap", () => {
    const card = { type: "approval_required", turn_id: "t", request_id: "r1", tool_name: "Bash", summary: "x", risk: "high",
                   voice_approvable: true, grantable: true, detail: "" } as const;
    const s = run([card, card]);
    expect(s.approvals).toHaveLength(1);
  });

  it("one project's sessions are kept apart from the recent list", () => {
    const row = { id: "o", title: "old", workspace: "/blog", updated_at: 1, turn_count: 3 };
    const s = run([{ type: "sessions", items: [row], workspace: "/blog" }]);
    expect(s.projectSessions["/blog"]).toEqual([row]);
    expect(s.sessions).toEqual([]);
  });

  it("task fields the board doesn't know yet (branch, check results) are kept", () => {
    const t = { id: "k", workspace: "/shop", engine: "claude", title: "x", instruction: "x", lanes: [], status: "done",
                created_at: 1, started_at: 1, finished_at: 2, summary: "", result_text: "", error: "", cost_usd: null,
                cli_session_id: null, turns: 1, branch: "barnaby/task-k", verification: "passed" } as TaskView;
    const s = run([{ type: "task_update", task: t }]);
    expect(s.tasks[0]).toMatchObject({ branch: "barnaby/task-k", verification: "passed" });
  });
});

describe("projects in the sidebar", () => {
  it("the tablet picks among the PC's projects and can't open a new folder", () => {
    useApp.setState({ role: "remote", projects: PROJECTS });
    render(<Sidebar onSettings={() => {}} onProfile={() => {}} />);
    expect(screen.queryByRole("button", { name: /Open another project folder/ })).toBeNull();
    const picker = screen.getByLabelText("Project");
    expect(screen.getByRole("option", { name: "blog (1 task)" })).toBeInTheDocument();
    fireEvent.change(picker, { target: { value: "/blog" } });
    expect(sent).toContainEqual({ type: "select_project", workspace: "/blog" });
    expect(sent).toContainEqual({ type: "list_sessions", workspace: "/shop" }); // this project's full history
  });

  it("the PC can still open a new folder", () => {
    useApp.setState({ role: "desktop", projects: PROJECTS });
    render(<Sidebar onSettings={() => {}} onProfile={() => {}} />);
    expect(screen.getByRole("button", { name: /Open another project folder/ })).toBeInTheDocument();
  });

  it("lists sessions of this project or of all projects", async () => {
    useApp.setState({
      projects: PROJECTS,
      sessions: [
        { id: "a", title: "shop chat", workspace: "/shop", updated_at: 2, turn_count: 1 },
        { id: "b", title: "blog chat", workspace: "/blog", updated_at: 1, turn_count: 1 },
      ],
      projectSessions: { "/shop": [{ id: "c", title: "older shop chat", workspace: "/shop", updated_at: 0, turn_count: 4 }] },
    });
    render(<Sidebar onSettings={() => {}} onProfile={() => {}} />);
    expect(screen.getByText("shop chat")).toBeInTheDocument();
    expect(screen.getByText("older shop chat")).toBeInTheDocument();
    expect(screen.queryByText("blog chat")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    await userEvent.click(screen.getByText("blog chat"));
    expect(sent).toContainEqual({ type: "load_session", session_id: "b" });
  });
});

describe("tasks in any project", () => {
  it("the new-task form targets the chosen project with its own folders", async () => {
    useApp.setState({ projects: PROJECTS });
    render(<AssignTaskDialog onClose={() => {}} />);
    expect(screen.getByRole("button", { name: "api/" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Project"), { target: { value: "/blog" } });
    expect(screen.queryByRole("button", { name: "api/" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "writing" }));
    await userEvent.type(screen.getByLabelText("What to do"), "Fix the feed");
    await userEvent.click(screen.getByRole("button", { name: "Start task" }));
    expect(sent).toContainEqual({ type: "task_create", engine: "claude", instruction: "Fix the feed", lanes: ["posts"], title: null, workspace: "/blog" });
  });
});
