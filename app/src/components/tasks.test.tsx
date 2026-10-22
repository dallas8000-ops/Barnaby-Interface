import { act, render, screen, within } from "@testing-library/react";
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

import type { TaskView } from "../lib/protocol";
import { initialState, useApp } from "../store/app";
import { ActivityPanel } from "./ActivityPanel";
import { AssignTaskDialog } from "./AssignTaskDialog";
import { duration, TaskBoard } from "./TaskBoard";
import { TasksSettings } from "./TasksSettings";

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

beforeEach(() => {
  sent.length = 0;
  useApp.setState({
    ...initialState,
    connection: "open",
    config: { workspace: "/proj", engine: "claude", task_lanes: {}, max_parallel_tasks: 3 } as never,
    engines: [
      { name: "claude", available: true },
      { name: "cursor", available: true },
      { name: "devin", available: false, reason: "Add your Devin API key in Settings first." },
    ],
    taskFolders: ["api", "app", "engine"],
    savedLanes: { backend: ["engine", "api"] },
  });
});

describe("TaskBoard", () => {
  it("shows each task's engine, folders, status and result", () => {
    useApp.setState({
      tasks: [
        task({ id: "a", title: "orders api", status: "running" }),
        task({ id: "b", title: "login page", engine: "cursor", lanes: [], status: "done", summary: "Fixed the form.", started_at: 0, finished_at: 125, cost_usd: 0.42 }),
        task({ id: "c", title: "broken", engine: "claude", status: "failed", error: "The CLI crashed." }),
      ],
      taskActivity: { a: { tool: "Edit", path: "engine/orders.py" } },
    });
    render(<TaskBoard />);
    const a = screen.getByRole("listitem", { name: "Task: orders api" });
    expect(within(a).getByText("Working")).toBeInTheDocument();
    expect(within(a).getByText("Devin")).toBeInTheDocument();
    expect(within(a).getByText("engine/")).toBeInTheDocument();
    expect(within(a).getByText("Editing engine/orders.py")).toBeInTheDocument();
    const b = screen.getByRole("listitem", { name: "Task: login page" });
    expect(within(b).getByText("whole project")).toBeInTheDocument();
    expect(within(b).getByText("Fixed the form.")).toBeInTheDocument();
    expect(within(b).getByText(/took 2 min/)).toBeInTheDocument();
    expect(within(b).getByText(/\$0\.42/)).toBeInTheDocument();
    expect(within(screen.getByRole("listitem", { name: "Task: broken" })).getByText("The CLI crashed.")).toBeInTheDocument();
    expect(screen.getByText(/1 working/)).toBeInTheDocument();
  });

  it("explains Cursor's guard on Cursor cards that are still working", () => {
    useApp.setState({
      tasks: [
        task({ id: "a", title: "cursor job", engine: "cursor", status: "running" }),
        task({ id: "b", title: "devin job", engine: "devin", status: "running" }),
        task({ id: "c", title: "cursor done", engine: "cursor", status: "done", finished_at: 20 }),
      ],
    });
    render(<TaskBoard />);
    const a = screen.getByRole("listitem", { name: "Task: cursor job" });
    expect(within(a).getByTestId("cursor-guard-note")).toHaveTextContent(/first write may land/);
    expect(within(a).getByRole("button", { name: "How it works" })).toBeInTheDocument();
    expect(within(screen.getByRole("listitem", { name: "Task: devin job" })).queryByTestId("cursor-guard-note")).toBeNull();
    expect(within(screen.getByRole("listitem", { name: "Task: cursor done" })).queryByTestId("cursor-guard-note")).toBeNull();
  });

  it("cancel, follow up and clear finished send the right commands", async () => {
    useApp.setState({ tasks: [task({ id: "a", status: "running" }), task({ id: "b", title: "done one", status: "done", result_text: "Full reply here." })] });
    render(<TaskBoard />);
    await userEvent.click(within(screen.getByRole("listitem", { name: "Task: orders api" })).getByRole("button", { name: "Cancel" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Follow up on done one" }), "add pagination{Enter}");
    await userEvent.click(screen.getByRole("button", { name: "Clear finished" }));
    expect(sent).toEqual([
      { type: "task_cancel", task_id: "a" },
      { type: "task_follow_up", task_id: "b", text: "add pagination" },
      { type: "task_clear" },
    ]);
    await userEvent.click(screen.getByRole("button", { name: /Full reply/ }));
    expect(screen.getByText("Full reply here.")).toBeInTheDocument();
  });

  it("shows a task's approval card inline; approving routes by request id", async () => {
    useApp.setState({
      tasks: [task({ status: "needs_approval" })],
      taskApprovals: [
        { taskId: "t1", requestId: "r9", toolName: "Bash", summary: "Install zod", detail: "npm install zod", risk: "high", voiceApprovable: false, grantable: true, decided: null },
      ],
    });
    render(<TaskBoard />);
    expect(screen.getByText("Needs your OK")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(sent).toEqual([{ type: "approve", request_id: "r9" }]);
    expect(screen.queryByText(/say “yes”/)).toBeNull(); // background tasks are never voice-approved
  });

  it("warns when the user's own Claude settings widen the lanes", () => {
    useApp.setState({ tasks: [task({ warning: "Your Claude settings allow edits (Edit(./**))." })] });
    render(<TaskBoard />);
    expect(screen.getByText(/Your Claude settings allow edits/)).toBeInTheDocument();
  });

  it("empty board explains how to start", () => {
    render(<TaskBoard />);
    expect(screen.getByText(/task for Devin in backend/)).toBeInTheDocument();
  });

  it("duration formatting", () => {
    expect(duration(0, 40)).toBe("40 s");
    expect(duration(0, 180)).toBe("3 min");
    expect(duration(0, 3900)).toBe("1 h 5 min");
  });
});

describe("AssignTaskDialog", () => {
  it("builds a task from engine, saved lane, folders and instruction", async () => {
    const onClose = vi.fn();
    render(<AssignTaskDialog onClose={onClose} />);
    expect(screen.getByRole("radio", { name: /Devin/ })).toBeDisabled();
    expect(screen.getByText("Add your Devin API key in Settings first.")).toBeInTheDocument();
    const start = screen.getByRole("button", { name: "Start task" });
    expect(start).toBeDisabled();
    expect(screen.getByText(/whole project/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: /Cursor/ }));
    await userEvent.click(screen.getByRole("button", { name: "backend" }));
    await userEvent.click(screen.getByRole("button", { name: "app/" }));
    expect(screen.getByRole("button", { name: "backend" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.type(screen.getByLabelText("What to do"), "Tidy the styles");
    await userEvent.type(screen.getByLabelText(/Short name/), "styles");
    await userEvent.click(start);
    expect(sent).toEqual([
      { type: "task_list" }, // fresh folder chips on open
      { type: "task_create", engine: "cursor", instruction: "Tidy the styles", lanes: ["engine", "api", "app"], title: "styles" },
    ]);
    expect(onClose).not.toHaveBeenCalled(); // waits for the engine to accept it
    expect(screen.getByRole("button", { name: "Starting…" })).toBeDisabled();
    act(() => useApp.getState().apply({ type: "task_update", task: task({ id: "new1", title: "styles" }) }));
    expect(onClose).toHaveBeenCalled();
  });

  it("stays open with the text kept when the engine refuses the task", async () => {
    const onClose = vi.fn();
    render(<AssignTaskDialog onClose={onClose} />);
    await userEvent.type(screen.getByLabelText("What to do"), "Do the thing");
    await userEvent.click(screen.getByRole("button", { name: "Start task" }));
    act(() => useApp.getState().apply({ type: "error", code: "tasks", message: "There's no folder \"api\" in the project." }));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByLabelText("What to do")).toHaveValue("Do the thing");
    expect(screen.getByRole("button", { name: "Start task" })).toBeEnabled();
  });

  it("says lanes are not a sandbox and limits the folder count", async () => {
    useApp.setState({ taskFolders: Array.from({ length: 9 }, (_, i) => `f${i}`), savedLanes: {} });
    render(<AssignTaskDialog onClose={() => {}} />);
    expect(screen.getByText(/aren't a sandbox/)).toBeInTheDocument();
    for (let i = 0; i < 9; i++) await userEvent.click(screen.getByRole("button", { name: `f${i}/` }));
    await userEvent.type(screen.getByLabelText("What to do"), "x");
    expect(screen.getByText("Pick at most 8 folders.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start task" })).toBeDisabled();
  });

  it("toggling a saved lane off removes its folders", async () => {
    render(<AssignTaskDialog onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "backend" }));
    await userEvent.click(screen.getByRole("button", { name: "backend" }));
    expect(screen.getByRole("button", { name: "engine/" })).toHaveAttribute("aria-pressed", "false");
  });
});

describe("ActivityPanel tabs", () => {
  it("the Tasks tab shows a badge until opened", async () => {
    useApp.setState({ taskAttention: 2, tasks: [task()] });
    render(<ActivityPanel />);
    expect(screen.getByLabelText("2 new")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /Tasks/ }));
    expect(useApp.getState().taskAttention).toBe(0);
    expect(screen.getByRole("listitem", { name: "Task: orders api" })).toBeInTheDocument();
  });
});

describe("ActivityPanel keyboard tabs", () => {
  it("arrow keys move between tabs and the panel is labelled by its tab", async () => {
    render(<ActivityPanel />);
    const activity = screen.getByRole("tab", { name: "Activity" });
    activity.focus();
    await userEvent.keyboard("{ArrowRight}");
    const tasks = screen.getByRole("tab", { name: /Tasks/ });
    expect(tasks).toHaveAttribute("aria-selected", "true");
    expect(tasks).toHaveFocus();
    expect(screen.getByRole("tabpanel", { name: /Tasks/ })).toBeInTheDocument();
  });
});

describe("nested dialogs", () => {
  it("New task opened from the Tasks dialog is announced as New task", async () => {
    const { TasksDialog } = await import("./TasksDialog");
    render(<TasksDialog onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "New task" }));
    expect(screen.getByRole("dialog", { name: "New task" })).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Tasks" })).toBeInTheDocument();
  });
});

describe("TasksSettings", () => {
  const config = () => useApp.getState().config as never;

  it("changes the parallel limit", async () => {
    render(<TasksSettings config={config()} />);
    await userEvent.selectOptions(screen.getByLabelText("At most"), "5");
    expect(sent).toEqual([{ type: "set_config", values: { max_parallel_tasks: 5 } }]);
  });

  it("adds and removes saved lanes for this project only", async () => {
    const cfg = { workspace: "/proj", task_lanes: { "/other": { web: ["site"] }, "/proj": { docs: ["docs"] } } } as never;
    render(<TasksSettings config={cfg} />);
    await userEvent.type(screen.getByLabelText("Lane name"), "backend");
    await userEvent.type(screen.getByLabelText("Lane folders"), "engine, .\\api\\");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(sent.at(-1)).toEqual({
      type: "set_config",
      values: { task_lanes: { "/other": { web: ["site"] }, "/proj": { docs: ["docs"], backend: ["engine", "api"] } } },
    });
    await userEvent.click(screen.getByRole("button", { name: "Remove lane docs" }));
    expect(sent.at(-1)).toEqual({ type: "set_config", values: { task_lanes: { "/other": { web: ["site"] } } } });
  });

  it("refuses the project root and a name that differs only in case", async () => {
    const cfg = { workspace: "/proj", task_lanes: { "/proj": { backend: ["engine"] } } } as never;
    render(<TasksSettings config={cfg} />);
    await userEvent.type(screen.getByLabelText("Lane name"), "Backend");
    await userEvent.type(screen.getByLabelText("Lane folders"), "api");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByText(/already a lane called/)).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Lane name"));
    await userEvent.type(screen.getByLabelText("Lane name"), "all");
    await userEvent.clear(screen.getByLabelText("Lane folders"));
    await userEvent.type(screen.getByLabelText("Lane folders"), ".");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByText(/whole project/)).toBeInTheDocument();
    expect(sent).toEqual([]);
  });

  it("refuses paths that leave the project", async () => {
    render(<TasksSettings config={config()} />);
    await userEvent.type(screen.getByLabelText("Lane name"), "x");
    await userEvent.type(screen.getByLabelText("Lane folders"), "../secrets");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByText(/inside the project/)).toBeInTheDocument();
    expect(sent).toEqual([]);
  });
});
