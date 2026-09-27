import * as React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
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

import { initialState, useApp, type Approval } from "../store/app";
import { ApprovalCard } from "./Approvals";
import { Composer } from "./Composer";
import { ChainProgress } from "./ChainProgress";
import { EngineSwitch } from "./EngineSwitch";
import { TemplatePicker } from "./TemplatePicker";
import { usePushToTalk } from "./usePushToTalk";

const approval = (over: Partial<Approval> = {}): Approval => ({
  requestId: "r1",
  toolName: "Bash",
  summary: "Push the fix",
  detail: "git push origin main",
  risk: "high",
  voiceApprovable: true,
  grantable: true,
  decided: null,
  ...over,
});

beforeEach(() => {
  sent.length = 0;
  useApp.setState({ ...initialState, connection: "open", config: { workspace: "/proj" } as never });
});

describe("ApprovalCard", () => {
  it("approves a normal request with one click", async () => {
    render(<ApprovalCard approval={approval()} />);
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(sent).toEqual([{ type: "approve", request_id: "r1" }]);
    expect(screen.getByText(/say “yes”/)).toBeInTheDocument();
  });

  it("critical: a click does nothing, only a completed hold approves", async () => {
    vi.useFakeTimers();
    let now = 0;
    const cbs: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => cbs.push(cb));
    vi.stubGlobal("cancelAnimationFrame", () => {});
    const frame = (ms: number) => {
      now += ms;
      const pending = cbs.splice(0);
      act(() => pending.forEach((cb) => cb(now)));
    };

    render(<ApprovalCard approval={approval({ risk: "critical", voiceApprovable: false, detail: "git push --force" })} />);
    const hold = screen.getByRole("button", { name: /press and hold/i });
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.getByText("Voice approval is off for this one.")).toBeInTheDocument();

    fireEvent.click(hold);
    expect(sent).toEqual([]);

    fireEvent.pointerDown(hold);
    frame(0);
    frame(600);
    fireEvent.pointerUp(hold); // released early
    frame(1000);
    expect(sent).toEqual([]);

    fireEvent.pointerDown(hold);
    frame(0);
    frame(1300);
    expect(sent).toEqual([{ type: "approve", request_id: "r1" }]);
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("ungrantable requests cannot be approved", () => {
    render(<ApprovalCard approval={approval({ grantable: false })} />);
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByText(/can't be approved as one exact command/)).toBeInTheDocument();
  });
});

describe("Composer draft review", () => {
  it("submits the corrected text with the draft id so the engine can learn", async () => {
    useApp.setState({ draft: { id: "d1", heard: "open doc ops", text: "open doc ops" } });
    render(<Composer />);
    const box = screen.getByLabelText(/check what i heard/i);
    await userEvent.clear(box);
    await userEvent.type(box, "open DBOps");
    expect(screen.getByText("Your fixes teach Barnaby how you talk.")).toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    expect(sent).toEqual([{ type: "text_submit", text: "open DBOps", draft_id: "d1" }]);
  });

  it("Escape discards the draft", async () => {
    useApp.setState({ draft: { id: "d1", heard: "x", text: "x" } });
    render(<Composer />);
    await userEvent.type(screen.getByLabelText(/check what i heard/i), "{Escape}");
    expect(sent).toEqual([{ type: "discard_draft", draft_id: "d1" }]);
    expect(useApp.getState().draft).toBeNull();
  });

  it("typed instructions send without a draft id", async () => {
    render(<Composer />);
    await userEvent.type(screen.getByLabelText("Type an instruction"), "run lint{Enter}");
    expect(sent).toEqual([{ type: "text_submit", text: "run lint" }]);
  });
});

function PttHarness() {
  usePushToTalk();
  return <input aria-label="field" />;
}

describe("usePushToTalk", () => {
  it("Space hold starts and release stops, ignoring key repeat", () => {
    render(<PttHarness />);
    fireEvent.keyDown(window, { code: "Space" });
    fireEvent.keyDown(window, { code: "Space", repeat: true });
    fireEvent.keyUp(window, { code: "Space" });
    expect(sent).toEqual([{ type: "ptt_start" }, { type: "ptt_stop" }]);
  });

  it("does not hijack Space while typing", () => {
    render(<PttHarness />);
    fireEvent.keyDown(screen.getByLabelText("field"), { code: "Space" });
    expect(sent).toEqual([]);
  });

  it("window blur releases a held key so the mic can't stay open", () => {
    render(<PttHarness />);
    fireEvent.keyDown(window, { code: "Space" });
    fireEvent.blur(window);
    fireEvent.keyUp(window, { code: "Space" }); // late keyup must not double-stop
    expect(sent).toEqual([{ type: "ptt_start" }, { type: "ptt_stop" }]);
  });

  it("unmount while held releases", () => {
    const { unmount } = render(<PttHarness />);
    fireEvent.keyDown(window, { code: "Space" });
    unmount();
    expect(sent.at(-1)).toEqual({ type: "ptt_stop" });
  });
});

describe("EngineSwitch", () => {
  it("switches engine via set_config and marks the current one", async () => {
    useApp.setState({
      config: { workspace: "/proj", engine: "claude" } as never,
      engines: [
        { name: "claude", available: true },
        { name: "cursor", available: true },
      ],
    });
    render(<EngineSwitch />);
    expect(screen.getByRole("radio", { name: "Claude" })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByRole("radio", { name: "Cursor" }));
    expect(sent).toEqual([{ type: "set_config", values: { engine: "cursor" } }]);
    await userEvent.click(screen.getByRole("radio", { name: "Claude" }));
    expect(sent).toHaveLength(1); // already active: no-op
  });

  it("disables an engine that isn't installed and explains why", () => {
    useApp.setState({
      engines: [
        { name: "claude", available: true },
        { name: "cursor", available: false, reason: "Cursor's command-line agent was not found." },
      ],
    });
    render(<EngineSwitch />);
    const cursor = screen.getByRole("radio", { name: "Cursor" });
    expect(cursor).toBeDisabled();
    expect(cursor).toHaveAttribute("title", "Cursor's command-line agent was not found.");
  });

  it("hides itself when only one engine exists", () => {
    useApp.setState({ engines: [{ name: "claude", available: true }] });
    const { container } = render(<EngineSwitch />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ChainProgress", () => {
  it("shows each step's state and Stop cancels", async () => {
    useApp.setState({ chain: { id: "c", name: null, steps: ["Run the tests", "Fix anything failing", "Commit"], index: 1 } });
    render(<ChainProgress />);
    expect(screen.getByRole("status")).toHaveTextContent("Step 2 of 3 · Your steps");
    const items = screen.getAllByRole("listitem");
    expect(items.map((li) => li.getAttribute("data-state"))).toEqual(["done", "current", "pending"]);
    expect(items[1]).toHaveTextContent("Running: Fix anything failing");
    await userEvent.click(screen.getByRole("button", { name: /stop steps/i }));
    expect(sent).toEqual([{ type: "cancel" }]);
  });

  it("says when it's waiting for approval, and hides with no chain", () => {
    useApp.setState({ chain: { id: "c", name: "TDD Cycle", steps: ["a", "b"], index: 0 }, engineState: "awaiting_approval" });
    const { rerender } = render(<ChainProgress />);
    expect(screen.getByRole("status")).toHaveTextContent("TDD Cycle · waiting for your OK");
    useApp.setState({ chain: null });
    rerender(<ChainProgress />);
    expect(screen.queryByTestId("chain")).toBeNull();
  });
});

describe("TemplatePicker", () => {
  const templates = [
    { id: "tdd-cycle", name: "TDD Cycle", description: "Test-driven development workflow", step_count: 4, custom: false },
    { id: "wf1", name: "Release prep", description: "", step_count: 2, custom: true },
  ];

  it("runs a template with what it should work on", async () => {
    useApp.setState({ templates });
    render(<TemplatePicker />);
    await userEvent.click(screen.getByRole("button", { name: /templates/i }));
    expect(screen.getByText("Saved")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /TDD Cycle/ }));
    await userEvent.type(screen.getByLabelText(/what should it work on/i), "the cart total{Enter}");
    expect(sent).toEqual([{ type: "run_template", template_id: "tdd-cycle", context: "the cart total" }]);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opened from inside a form, it neither nests nor submits that form", async () => {
    useApp.setState({ templates });
    const outerSubmit = vi.fn((e: React.FormEvent) => e.preventDefault());
    render(
      <form onSubmit={outerSubmit} data-testid="outer">
        <TemplatePicker />
      </form>,
    );
    await userEvent.click(screen.getByRole("button", { name: /templates/i }));
    expect(screen.getByTestId("outer").contains(screen.getByRole("dialog"))).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: /TDD Cycle/ }));
    await userEvent.click(screen.getByRole("button", { name: "Run TDD Cycle" }));
    expect(outerSubmit).not.toHaveBeenCalled();
    expect(sent).toEqual([{ type: "run_template", template_id: "tdd-cycle", context: null }]);
  });

  it("context is optional; hidden when the engine lists no templates", async () => {
    useApp.setState({ templates });
    const { unmount } = render(<TemplatePicker />);
    await userEvent.click(screen.getByRole("button", { name: /templates/i }));
    await userEvent.click(screen.getByRole("button", { name: /Release prep/ }));
    await userEvent.click(screen.getByRole("button", { name: "Run Release prep" }));
    expect(sent).toEqual([{ type: "run_template", template_id: "wf1", context: null }]);
    unmount();
    useApp.setState({ templates: [] });
    const { container } = render(<TemplatePicker />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("Modal", () => {
  it("keeps focus in a field across re-renders (onClose changes every render)", async () => {
    const { Modal } = await import("./Modal");
    function Harness() {
      const [v, setV] = React.useState("");
      return (
        <Modal title="T" onClose={() => {}}>
          <button type="button">First</button>
          <input aria-label="field" value={v} onChange={(e) => setV(e.target.value)} />
        </Modal>
      );
    }
    render(<Harness />);
    const field = screen.getByLabelText("field");
    await userEvent.type(field, "abc");
    expect(field).toHaveValue("abc");
    expect(field).toHaveFocus();
  });
});
