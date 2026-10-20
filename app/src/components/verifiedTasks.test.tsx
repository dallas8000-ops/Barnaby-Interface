import { render, screen, within } from "@testing-library/react";
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

import type { CheckResult, TaskView } from "../lib/protocol";
import { initialState, useApp } from "../store/app";
import { AssignTaskDialog } from "./AssignTaskDialog";
import { TaskBoard } from "./TaskBoard";
import { TasksSettings } from "./TasksSettings";

const task = (over: Partial<TaskView> = {}): TaskView => ({
  id: "t1",
  workspace: "/proj",
  engine: "claude",
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

const passed: CheckResult = { command: "pytest -q", dir: "engine", exit_code: 0, duration_s: 3.1, output: "12 passed", passed: true, timed_out: false, error: "" };
const failed: CheckResult = {
  command: "npm test",
  dir: "app",
  exit_code: 1,
  duration_s: 9,
  output: "FAIL src/cart.test.ts\nexpected 30, received 0",
  passed: false,
  timed_out: false,
  error: "",
};

beforeEach(() => {
  sent.length = 0;
  useApp.setState({
    ...initialState,
    connection: "open",
    config: { workspace: "/proj", engine: "claude", task_lanes: {}, max_parallel_tasks: 3, task_check_retries: 2 } as never,
    engines: [
      { name: "claude", available: true },
      { name: "cursor", available: true },
      { name: "devin", available: true },
    ],
    taskFolders: ["api", "app", "engine"],
    savedLanes: {},
  });
});

describe("task cards: checks and worktrees", () => {
  it("shows the branch, which checks passed or failed, and each check's output", async () => {
    useApp.setState({
      tasks: [
        task({
          id: "a",
          status: "done",
          verification: "passed",
          isolation: "worktree",
          branch: "barnaby/task-a1-orders",
          worktree: "C:/wt/a",
          attempts: 2,
          check_results: [passed],
          notice: "Committed abc1234 on barnaby/task-a1-orders.",
        }),
        task({
          id: "b",
          title: "cart",
          status: "failed",
          verification: "failed",
          isolation: "worktree",
          branch: "barnaby/task-b1-cart",
          attempts: 3,
          check_results: [passed, failed],
          error: 'Check "npm test" exited with code 1. Still failing after 3 check runs.\n\n$ npm test\nFAIL',
        }),
      ],
    });
    render(<TaskBoard />);
    const a = screen.getByRole("listitem", { name: "Task: orders api" });
    expect(within(a).getByText("barnaby/task-a1-orders")).toBeInTheDocument();
    expect(within(a).getByText("Verified")).toBeInTheDocument();
    expect(within(a).getByText(/2 check runs/)).toBeInTheDocument();
    expect(within(a).getByText(/Committed abc1234/)).toBeInTheDocument();
    const b = screen.getByRole("listitem", { name: "Task: cart" });
    expect(within(b).getByText(/Still failing after 3 check runs\.$/)).toBeInTheDocument(); // the error's first line
    expect(within(b).queryByText(/received 0/)).toBeNull();
    const npm = within(b).getByRole("button", { name: /npm test/ });
    expect(npm).toHaveTextContent("(exit 1)");
    await userEvent.click(npm);
    expect(within(b).getByText(/expected 30, received 0/)).toBeInTheDocument();
  });

  it("a task being checked says so and can be cancelled; unverified and shared-folder tasks say so", () => {
    useApp.setState({
      tasks: [
        task({ id: "a", status: "verifying", attempts: 1, checks: [{ run: "pytest", dir: "" }, { run: "npm test", dir: "app" }] }),
        task({ id: "b", title: "plain", status: "done", verification: "unverified" }),
        task({ id: "c", title: "no git", status: "running", isolation: "shared", notice: "No worktree: this project isn't a git repository." }),
      ],
    });
    render(<TaskBoard />);
    const a = screen.getByRole("listitem", { name: "Task: orders api" });
    expect(within(a).getByText("Checking")).toBeInTheDocument();
    expect(within(a).getByText("Running 2 checks (run 1 of 3)…")).toBeInTheDocument();
    expect(within(a).getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(within(screen.getByRole("listitem", { name: "Task: plain" })).getByText("Unverified")).toBeInTheDocument();
    const c = screen.getByRole("listitem", { name: "Task: no git" });
    expect(within(c).getByText(/No worktree/)).toBeInTheDocument();
    expect(within(c).getByText(/shared folder/)).toBeInTheDocument();
    expect(screen.getByText(/2 working/)).toBeInTheDocument();
  });

  it("Open folder, and Discard only after confirming", async () => {
    useApp.setState({ tasks: [task({ status: "done", isolation: "worktree", branch: "barnaby/task-t1-x", worktree: "C:/wt/t1" })] });
    render(<TaskBoard />);
    await userEvent.click(screen.getByRole("button", { name: "Open folder" }));
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(sent).toEqual([{ type: "task_open_folder", task_id: "t1" }]); // nothing deleted yet
    const confirm = screen.getByRole("alertdialog", { name: /Discard/ });
    expect(within(confirm).getByText("barnaby/task-t1-x")).toBeInTheDocument();
    await userEvent.click(within(confirm).getByRole("button", { name: "Keep it" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    await userEvent.click(screen.getByRole("button", { name: "Discard work" }));
    expect(sent.at(-1)).toEqual({ type: "task_discard", task_id: "t1" });
  });

  it("the tablet can't open folders or discard, and a running task can't be discarded", () => {
    useApp.setState({ role: "remote", tasks: [task({ status: "done", isolation: "worktree", branch: "barnaby/task-t1-x" })] });
    const { unmount } = render(<TaskBoard />);
    expect(screen.queryByRole("button", { name: "Open folder" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
    unmount();
    useApp.setState({ role: "desktop", tasks: [task({ status: "running", isolation: "worktree", branch: "barnaby/task-t1-x" })] });
    render(<TaskBoard />);
    expect(screen.getByRole("button", { name: "Open folder" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
  });

  it("a failure toast shows only the first line of the error", () => {
    useApp.getState().apply({
      type: "task_done", task_id: "t1", status: "failed", title: "cart", engine: "claude", summary: "",
      error: 'Check "npm test" exited with code 1.\n\nFAIL lots of output', verification: "failed",
    });
    const last = useApp.getState().toasts.at(-1);
    expect(last?.message).toBe('Claude\'s task cart failed: Check "npm test" exited with code 1.');
  });
});

describe("New task: checks", () => {
  it("uses the project's checks, or this task's own", async () => {
    useApp.setState({ projectChecks: [{ run: "pytest -q", dir: "engine" }] });
    render(<AssignTaskDialog onClose={() => {}} />);
    expect(screen.getByText(/Done only when these pass: pytest -q \(in engine\/\)/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Change for this task" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove check pytest -q" }));
    await userEvent.type(screen.getByLabelText("Check command"), "npm test && rm -rf x");
    await userEvent.click(screen.getByRole("button", { name: "Add check" }));
    expect(screen.getByText(/One plain command per check/)).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Check command"));
    await userEvent.type(screen.getByLabelText("Check command"), "npm test");
    await userEvent.selectOptions(screen.getByLabelText("Check folder"), "app");
    await userEvent.click(screen.getByRole("button", { name: "Add check" }));
    await userEvent.type(screen.getByLabelText("What to do"), "Fix the cart");
    await userEvent.click(screen.getByRole("button", { name: "Start task" }));
    expect(sent.at(-1)).toEqual({
      type: "task_create",
      engine: "claude",
      instruction: "Fix the cart",
      lanes: [],
      title: null,
      checks: [{ run: "npm test", dir: "app" }],
    });
  });

  it("without an override no checks field is sent, and Devin isn't checked", async () => {
    render(<AssignTaskDialog onClose={() => {}} />);
    expect(screen.getByText(/No checks are set for this project/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: /Devin/ }));
    expect(screen.getByText(/doesn't run checks on it/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("What to do"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Start task" }));
    expect(sent.at(-1)).not.toHaveProperty("checks");
  });

  it("a tablet can't change a task's checks", () => {
    useApp.setState({ role: "remote", projectChecks: [{ run: "pytest", dir: "" }] });
    render(<AssignTaskDialog onClose={() => {}} />);
    expect(screen.queryByRole("button", { name: "Change for this task" })).toBeNull();
  });
});

describe("Settings: checks and worktrees", () => {
  it("project checks, fix-up tries and worktree options", async () => {
    const cfg = { workspace: "/proj", task_lanes: {}, task_checks: { "/other": [{ run: "go test", dir: "" }] } } as never;
    render(<TasksSettings config={cfg} />);
    await userEvent.type(screen.getByLabelText("Check command"), "pytest -q");
    await userEvent.selectOptions(screen.getByLabelText("Check folder"), "engine");
    await userEvent.click(screen.getByRole("button", { name: "Add check" }));
    expect(sent.at(-1)).toEqual({
      type: "set_config",
      values: { task_checks: { "/other": [{ run: "go test", dir: "" }], "/proj": [{ run: "pytest -q", dir: "engine" }] } },
    });
    await userEvent.selectOptions(screen.getByLabelText("Fix-up tries after failing checks"), "4");
    expect(sent.at(-1)).toEqual({ type: "set_config", values: { task_check_retries: 4 } });
    await userEvent.click(screen.getByRole("checkbox", { name: /own git worktree/ }));
    expect(sent.at(-1)).toEqual({ type: "set_config", values: { task_worktrees: false } });
    await userEvent.click(screen.getByRole("checkbox", { name: /overlapping folders/ }));
    expect(sent.at(-1)).toEqual({ type: "set_config", values: { task_worktree_parallel: true } });
  });

  it("removing the last check clears this project's entry", async () => {
    const cfg = { workspace: "/proj", task_lanes: {}, task_checks: { "/proj": [{ run: "pytest", dir: "" }] } } as never;
    render(<TasksSettings config={cfg} />);
    await userEvent.click(screen.getByRole("button", { name: "Remove check pytest" }));
    expect(sent.at(-1)).toEqual({ type: "set_config", values: { task_checks: {} } });
  });
});
