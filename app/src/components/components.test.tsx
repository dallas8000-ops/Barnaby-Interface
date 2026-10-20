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
import { ClaudeSignIn } from "./ClaudeSignIn";
import { DevinKeyField } from "./DevinKeyField";
import { EngineSwitch } from "./EngineSwitch";
import { HandsFreeToggle } from "./HandsFreeToggle";
import { ScopeConsole } from "./ScopeConsole";
import { SettingsDialog } from "./SettingsDialog";
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

describe("Hands-free", () => {
  it("toggle sends the setting and reflects it", async () => {
    useApp.setState({ config: { workspace: "/proj", continuous_mode_enabled: false } as never });
    render(<HandsFreeToggle />);
    const sw = screen.getByRole("switch", { name: /hands-free/i });
    expect(sw).toHaveAttribute("aria-checked", "false");
    await userEvent.click(sw);
    expect(sent).toEqual([{ type: "set_config", values: { continuous_mode_enabled: true } }]);
  });

  it("warns when enabled but the mic couldn't open", () => {
    useApp.setState({ config: { workspace: "/proj", continuous_mode_enabled: true } as never, continuousMode: false });
    render(<HandsFreeToggle />);
    expect(screen.getByRole("switch")).toHaveAttribute("title", expect.stringMatching(/couldn't be opened/));
  });

  it("caption tells you to just talk, and to pause to send", () => {
    useApp.setState({ continuousMode: true, engineState: "idle", components: { stt: { status: "ready" }, tts: { status: "ready" } } });
    const { rerender } = render(<ScopeConsole />);
    expect(screen.getByTestId("caption")).toHaveTextContent("Hands-free: just start talking");
    useApp.setState({ engineState: "listening" });
    rerender(<ScopeConsole />);
    expect(screen.getByTestId("caption")).toHaveTextContent("Listening. Pause to send.");
  });

  it("spoken 'send it' submits the draft with typed edits; 'scratch that' discards", async () => {
    useApp.setState({ draft: { id: "d1", heard: "open doc ops", text: "open DBOps" }, continuousMode: true });
    render(<Composer />);
    expect(screen.getByText(/say “send it”/)).toBeInTheDocument();
    act(() => useApp.getState().apply({ type: "draft_command", action: "send" }));
    expect(sent).toEqual([{ type: "text_submit", text: "open DBOps", draft_id: "d1" }]);
    act(() => useApp.getState().apply({ type: "draft_command", action: "discard" }));
    expect(sent.at(-1)).toEqual({ type: "discard_draft", draft_id: "d1" });
    expect(useApp.getState().draft).toBeNull();
  });
});

describe("SettingsDialog microphone", () => {
  it("names the mic Automatic really uses and flags speaker loopbacks", () => {
    useApp.setState({
      config: {
        workspace: "/proj", input_device: null, whisper_model: "small.en", continuous_mode_timeout: 1.2,
        claude_model: "", cursor_model: "", tts_enabled: true, tts_speed: 1.05, tts_voice: "am_michael",
        max_spoken_chars: 400, review_transcripts: false, save_voice_samples: false,
      } as never,
      devices: [
        { index: 1, name: '"What U Hear" (Sound Blaster)', default: true, auto: false, loopback: true },
        { index: 2, name: "Microphone (HD Pro Webcam C920)", default: false, auto: true, loopback: false },
      ],
    });
    render(<SettingsDialog onClose={() => {}} />);
    const mic = screen.getByLabelText("Microphone") as HTMLSelectElement;
    const labels = Array.from(mic.options).map((o) => o.textContent);
    expect(labels).toEqual([
      "Automatic (Microphone (HD Pro Webcam C920))",
      '"What U Hear" (Sound Blaster) (records speaker output)',
      "Microphone (HD Pro Webcam C920)",
    ]);
    expect(sent).toContainEqual({ type: "list_devices" });
    fireEvent.change(mic, { target: { value: "2" } });
    expect(sent).toContainEqual({ type: "set_config", values: { input_device: 2 } });
  });
});

describe("DevinKeyField", () => {
  const KEY = "apk_user_" + "Ab3/+=".repeat(10);

  it("sends a valid key once, trimmed, and clears it from the field", async () => {
    render(<DevinKeyField />);
    const input = screen.getByPlaceholderText("apk_user_…") as HTMLInputElement;
    expect(input.type).toBe("password");
    const save = screen.getByRole("button", { name: "Save" });
    await userEvent.type(input, "not a key");
    expect(save).toBeDisabled();
    expect(screen.getByText(/doesn't look like an API key/)).toBeInTheDocument();
    fireEvent.change(input, { target: { value: `  ${KEY}\n` } });
    expect(save).toBeEnabled();
    await userEvent.click(save);
    expect(sent).toEqual([{ type: "set_secret", name: "devin", value: KEY }]);
    expect(input.value).toBe("");
  });

  it("when saved, shows status only and can remove or replace", async () => {
    useApp.setState({ secrets: { devin: true } });
    render(<DevinKeyField />);
    expect(screen.getByRole("status")).toHaveTextContent("Saved in Windows Credential Manager");
    expect(screen.queryByPlaceholderText("apk_user_…")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(sent).toEqual([{ type: "delete_secret", name: "devin" }]);
    await userEvent.click(screen.getByRole("button", { name: "Replace" }));
    expect(screen.getByPlaceholderText("apk_user_…")).toBeInTheDocument();
  });
});

describe("ClaudeSignIn", () => {
  it("offers Sign in to Claude when signed out, and asks the engine to start it", async () => {
    useApp.setState({ claudeAuth: { state: "signed_out" } });
    render(<ClaudeSignIn />);
    expect(sent).toContainEqual({ type: "claude_auth_check" });
    expect(screen.getByTestId("claude-auth-status")).toHaveTextContent("Not signed in");
    await userEvent.click(screen.getByRole("button", { name: "Sign in to Claude" }));
    expect(sent).toContainEqual({ type: "claude_login" });
  });

  it("while waiting for the browser, shows Cancel instead of a second sign-in button", async () => {
    useApp.setState({ claudeAuth: { state: "signing_in", detail: "Finish signing in in your browser." } });
    render(<ClaudeSignIn />);
    expect(screen.queryByRole("button", { name: /Sign in/ })).toBeNull();
    expect(screen.getByText("Finish signing in in your browser.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(sent).toContainEqual({ type: "claude_login_cancel" });
  });

  it("when signed in, shows the method and a quieter Sign in again", () => {
    useApp.setState({ claudeAuth: { state: "signed_in", method: "claude.ai" } });
    render(<ClaudeSignIn />);
    expect(screen.getByTestId("claude-auth-status")).toHaveTextContent("Signed in (claude.ai)");
    expect(screen.getByRole("button", { name: "Sign in again" })).toBeInTheDocument();
  });

  it("an expired sign-in is called out as an error and offers Sign in again", () => {
    useApp.setState({ claudeAuth: { state: "expired", detail: "Claude rejected your sign-in. Sign in again." } });
    render(<ClaudeSignIn />);
    expect(screen.getByRole("alert")).toHaveTextContent("Claude rejected your sign-in");
    expect(screen.getByRole("button", { name: "Sign in again" })).toBeEnabled();
  });

  it("hides the button when Claude Code isn't installed, and disables it while disconnected", () => {
    useApp.setState({ claudeAuth: { state: "unavailable", detail: "Claude Code was not found." } });
    const { unmount } = render(<ClaudeSignIn />);
    expect(screen.queryByRole("button")).toBeNull();
    unmount();
    useApp.setState({ claudeAuth: { state: "signed_out" }, connection: "closed" as never });
    render(<ClaudeSignIn />);
    expect(screen.getByRole("button", { name: "Sign in to Claude" })).toBeDisabled();
  });

  it("is part of Settings", () => {
    useApp.setState({
      config: {
        workspace: "/proj", input_device: null, whisper_model: "small.en", continuous_mode_timeout: 1.2,
        claude_model: "", cursor_model: "", tts_enabled: true, tts_speed: 1.05, tts_voice: "am_michael",
        max_spoken_chars: 400, review_transcripts: false, save_voice_samples: false,
      } as never,
    });
    render(<SettingsDialog onClose={() => {}} />);
    expect(screen.getByTestId("claude-auth-status")).toBeInTheDocument();
  });
});

describe("EngineSwitch with Devin", () => {
  it("shows Devin, disabled with the reason until a key is saved", () => {
    useApp.setState({
      engines: [
        { name: "claude", available: true },
        { name: "devin", available: false, reason: "Add your Devin API key in Settings → Devin first." },
      ],
    });
    render(<EngineSwitch />);
    const devin = screen.getByRole("radio", { name: "Devin" });
    expect(devin).toBeDisabled();
    expect(devin).toHaveAttribute("title", expect.stringContaining("Settings → Devin"));
  });
});

describe("Tablet access (PC Settings)", () => {
  const running = { enabled: true, running: true, port: 8766, urls: ["https://192.168.1.20:8766/"],
    devices: [{ id: "dev_1", name: "Galaxy Tab", created: 1, last_seen: Date.now() / 1000, connected: true }],
    ca_fingerprint: "AB:CD" };

  it("pairs with a QR code, shows its countdown, and removes a tablet", async () => {
    const { TabletAccessSection } = await import("./TabletAccessSection");
    useApp.setState({ tablet: running, tabletPairing: null });
    const toggled: boolean[] = [];
    const { rerender } = render(<TabletAccessSection enabled onToggle={(v) => toggled.push(v)} />);
    expect(screen.getByText("https://192.168.1.20:8766/")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Pair a tablet" }));
    expect(sent).toContainEqual({ type: "tablet_pair_start" });

    act(() => useApp.setState({ tabletPairing: { url: "https://192.168.1.20:8766/#pair=abcdefghijklmnop", expiresAt: Date.now() / 1000 + 300 } }));
    rerender(<TabletAccessSection enabled onToggle={(v) => toggled.push(v)} />);
    const qr = screen.getByTestId("pair-qr");
    expect(qr.querySelector("path")!.getAttribute("d")!.length).toBeGreaterThan(100);
    expect(screen.getByText(/Works once, for [45]:\d\d more/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Remove Galaxy Tab" }));
    expect(sent).toContainEqual({ type: "tablet_revoke", device_id: "dev_1" });
    await userEvent.click(screen.getByRole("switch", { name: "Tablet access" }));
    expect(toggled).toEqual([false]);
  });

  it("an expired pairing link falls back to the Pair button", async () => {
    const { TabletAccessSection } = await import("./TabletAccessSection");
    useApp.setState({ tablet: running, tabletPairing: { url: "https://x/#pair=old", expiresAt: Date.now() / 1000 - 1 } });
    render(<TabletAccessSection enabled onToggle={() => {}} />);
    expect(screen.queryByTestId("pair-qr")).toBeNull();
    expect(screen.getByRole("button", { name: "Pair a tablet" })).toBeInTheDocument();
  });

  it("shows why it couldn't start", async () => {
    const { TabletAccessSection } = await import("./TabletAccessSection");
    useApp.setState({ tablet: { ...running, running: false, urls: [], error: "Your network is set to Public in Windows." } });
    render(<TabletAccessSection enabled onToggle={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Public");
    expect(screen.queryByRole("button", { name: "Pair a tablet" })).toBeNull();
  });
});

describe("Settings on a tablet", () => {
  it("offers only what a tablet may change", async () => {
    useApp.setState({ role: "remote", config: { workspace: "C:/proj", review_transcripts: true } as never });
    render(<SettingsDialog onClose={() => {}} />);
    expect(screen.queryByLabelText("Microphone")).toBeNull();
    expect(screen.queryByPlaceholderText("apk_user_…")).toBeNull();
    expect(screen.queryByRole("switch", { name: "Tablet access" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Change" })).toBeNull();
    await userEvent.click(screen.getByRole("checkbox", { name: /Review before sending/ }));
    expect(sent).toEqual([{ type: "set_config", values: { review_transcripts: false } }]);
  });
});

describe("PairScreen", () => {
  it("explains a failed pairing", async () => {
    const { PairScreen } = await import("./PairScreen");
    useApp.setState({ connection: "unauthorized", pairError: null });
    render(<PairScreen onRetry={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/expired or was already used/);
    expect(screen.getByRole("link", { name: "Barnaby's certificate" })).toHaveAttribute("href", "/barnaby-ca.crt");
  });

  it("shows the PC's reason when pairing is refused", async () => {
    const { PairScreen } = await import("./PairScreen");
    useApp.setState({ connection: "unauthorized", pairError: "This PC already has 10 paired tablets." });
    render(<PairScreen onRetry={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("10 paired tablets");
  });
});

describe("Second review: touch and tablet-only UI", () => {
  it("HoldButton survives an Android long-press (no context menu, no scroll/selection)", async () => {
    const { HoldButton } = await import("./HoldButton");
    render(<HoldButton onConfirm={() => {}}>Push</HoldButton>);
    const btn = screen.getByRole("button", { name: /Press and hold/ });
    expect(btn.className).toMatch(/touch-none/);
    expect(btn.className).toMatch(/select-none/);
    const ev = new MouseEvent("contextmenu", { bubbles: true, cancelable: true });
    btn.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
  });

  it("the tablet doesn't show PC-only controls", async () => {
    const { Sidebar } = await import("./Sidebar");
    useApp.setState({ role: "remote", engines: [{ name: "claude", available: true }, { name: "cursor", available: true }] });
    render(<Sidebar onSettings={() => {}} onProfile={() => {}} />);
    expect(screen.queryByRole("button", { name: /proj/ })).toBeNull(); // the folder picker
    expect(screen.getByText("proj")).toBeInTheDocument(); // shown, read-only
    expect(screen.queryByRole("button", { name: /Voice profile/ })).toBeNull();
    render(<HandsFreeToggle />);
    expect(screen.queryByRole("switch", { name: /Hands-free/ })).toBeNull();
  });

  it("Quiet on the tablet stops its own voice and the engine's queue", async () => {
    const tabletSpeech = await import("../lib/tabletSpeech");
    const stop = vi.fn();
    vi.spyOn(tabletSpeech, "getTabletSpeech").mockReturnValue({ stop } as never);
    useApp.setState({ role: "remote", tabletSpeaking: true, speaking: false });
    render(<ScopeConsole />);
    await userEvent.click(screen.getByRole("button", { name: /Quiet/ }));
    expect(stop).toHaveBeenCalled();
    expect(sent).toContainEqual({ type: "stop_speaking" });
  });
});

describe("Style picker", () => {
  it("is offered on the tablet too and applies at once", async () => {
    useApp.setState({ role: "remote", config: { workspace: "C:/proj", review_transcripts: true } as never });
    render(<SettingsDialog onClose={() => {}} />);
    const style = screen.getByLabelText("Style") as HTMLSelectElement;
    fireEvent.change(style, { target: { value: "sunrise" } });
    expect(document.documentElement.dataset.skin).toBe("sunrise");
    expect(localStorage.getItem("barnaby.skin")).toBe("sunrise");
    localStorage.clear();
  });
});
