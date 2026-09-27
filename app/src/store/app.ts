import { create } from "zustand";
import type { ConnectionStatus } from "../lib/engine";
import type {
  AgentEngine,
  ComponentStatus,
  Config,
  Correction,
  EngineEvent,
  EngineInfo,
  EngineState,
  Risk,
  SessionSummary,
  StoredTurn,
  TemplateSummary,
} from "../lib/protocol";

export interface ToolItem {
  id: string;
  name: string;
  summary: string;
  path: string | null;
  ok: boolean | null;
  preview?: string;
}

export interface Turn {
  id: string;
  text: string;
  system: boolean;
  engine?: AgentEngine;
  assistant: string;
  tools: ToolItem[];
  status: "running" | "ok" | "error" | "cancelled" | "needs_approval" | string;
  costUsd?: number | null;
  durationMs?: number | null;
}

export interface Approval {
  requestId: string;
  toolName: string;
  summary: string;
  detail: string;
  risk: Risk;
  voiceApprovable: boolean;
  grantable: boolean;
  decided: boolean | null;
}

export interface ChainView {
  id: string;
  name: string | null;
  steps: string[];
  index: number;
}

export interface Toast {
  id: number;
  kind: "error" | "info" | "learned";
  message: string;
}

export interface VoiceProfileView {
  corrections: Correction[];
  vocabulary: string[];
  projectTerms: string[];
  samples: number;
}

export interface AppState {
  connection: ConnectionStatus;
  engineState: EngineState;
  level: number;
  speaking: boolean;
  components: Record<"stt" | "tts", { status: ComponentStatus; detail?: string }>;
  config: Config | null;
  engines: EngineInfo[];
  sessions: SessionSummary[];
  sessionId: string | null;
  turns: Turn[];
  draft: { id: string; heard: string; text: string } | null;
  approvals: Approval[];
  toasts: Toast[];
  voiceProfile: VoiceProfileView | null;
  devices: { index: number; name: string; default: boolean; auto?: boolean; loopback?: boolean }[];
  
  // Voice features
  wakeWordActive: boolean;
  continuousMode: boolean;
  chain: ChainView | null;
  templates: TemplateSummary[];
  /** Spoken "send it" / "scratch that" for the review draft; seq makes repeats distinct. */
  draftCommand: { seq: number; action: "send" | "discard" } | null;
  
  // IDE bridge
  ideConnected: boolean;
  ideType: "vscode" | "cursor" | "claude-desktop" | null;
  
  // Workflows
  workflowActive: boolean;
  currentWorkflow: string | null;
  macroRecording: boolean;
}

export const initialState: AppState = {
  connection: "connecting",
  engineState: "idle",
  level: 0,
  speaking: false,
  components: { stt: { status: "loading" }, tts: { status: "loading" } },
  config: null,
  engines: [{ name: "claude", available: true }],
  sessions: [],
  sessionId: null,
  turns: [],
  draft: null,
  approvals: [],
  toasts: [],
  voiceProfile: null,
  devices: [],
  
  // Voice features
  wakeWordActive: false,
  continuousMode: false,
  chain: null,
  templates: [],
  draftCommand: null,
  
  // IDE bridge
  ideConnected: false,
  ideType: null,
  
  // Workflows
  workflowActive: false,
  currentWorkflow: null,
  macroRecording: false,
};

let toastSeq = 0;
const toast = (s: AppState, kind: Toast["kind"], message: string): Toast[] =>
  [...s.toasts, { id: ++toastSeq, kind, message }].slice(-4);

const fromStored = (t: StoredTurn, engine?: AgentEngine): Turn => ({
  id: t.id,
  text: t.user_text,
  system: t.user_text === "Approval decision",
  engine,
  assistant: t.assistant_text,
  tools: t.tools.map((x) => ({ ...x })),
  status: t.status,
  costUsd: t.cost_usd,
});

const updateTurn = (turns: Turn[], id: string, fn: (t: Turn) => Turn): Turn[] =>
  turns.map((t) => (t.id === id ? fn(t) : t));

/** Pure reducer: every engine event maps to one state transition. Unit-tested directly. */
export function reduceEvent(s: AppState, e: EngineEvent): AppState {
  switch (e.type) {
    case "hello":
      return {
        ...s,
        engineState: e.state,
        config: e.config,
        engines: e.engines ?? s.engines,
        templates: e.templates ?? s.templates,
        continuousMode: e.handsfree ?? s.continuousMode,
        sessions: e.sessions,
        speaking: e.speaking,
        components: e.components ?? s.components,
      };
    case "state":
      return { ...s, engineState: e.state, level: e.state === "listening" ? s.level : 0 };
    case "level":
      return { ...s, level: e.rms };
    case "speaking":
      return { ...s, speaking: e.active };
    case "component":
      return { ...s, components: { ...s.components, [e.name]: { status: e.status, detail: e.detail } } };
    case "transcript":
      return e.review && e.draft_id ? { ...s, draft: { id: e.draft_id, heard: e.heard, text: e.text } } : { ...s, draft: null };
    case "turn_start":
      return {
        ...s,
        sessionId: e.session_id,
        draft: null,
        turns: [...s.turns, { id: e.turn_id, text: e.text, system: e.system, engine: e.engine, assistant: "", tools: [], status: "running" }],
      };
    case "agent_text":
      return { ...s, turns: updateTurn(s.turns, e.turn_id, (t) => ({ ...t, assistant: t.assistant + e.delta })) };
    case "tool_start":
      return {
        ...s,
        turns: updateTurn(s.turns, e.turn_id, (t) => ({
          ...t,
          tools: [...t.tools, { id: e.tool_id, name: e.name, summary: e.summary, path: e.path, ok: null }],
        })),
      };
    case "tool_end":
      return {
        ...s,
        turns: updateTurn(s.turns, e.turn_id, (t) => ({
          ...t,
          tools: t.tools.map((x) => (x.id === e.tool_id ? { ...x, ok: e.ok, preview: e.preview } : x)),
        })),
      };
    case "turn_end":
      return {
        ...s,
        turns: updateTurn(s.turns, e.turn_id, (t) => ({
          ...t,
          status: e.status,
          // Final text is authoritative when present (covers non-streamed output).
          assistant: e.text && e.text.length >= t.assistant.trim().length ? e.text : t.assistant,
          costUsd: e.cost_usd ?? t.costUsd,
          durationMs: e.duration_ms ?? t.durationMs,
        })),
      };
    case "approval_required":
      return {
        ...s,
        approvals: [
          ...s.approvals,
          {
            requestId: e.request_id,
            toolName: e.tool_name,
            summary: e.summary,
            detail: e.detail,
            risk: e.risk,
            voiceApprovable: e.voice_approvable,
            grantable: e.grantable,
            decided: null,
          },
        ],
      };
    case "approval_decided": {
      const approvals = s.approvals.map((a) => (a.requestId === e.request_id ? { ...a, decided: e.approved } : a));
      return { ...s, approvals: approvals.every((a) => a.decided !== null) ? [] : approvals };
    }
    case "approvals_cleared":
      return { ...s, approvals: [] };
    case "sessions":
      return { ...s, sessions: e.items };
    case "session_loaded":
      return {
        ...s,
        sessionId: e.session?.id ?? null,
        turns: e.session ? e.session.turns.map((t) => fromStored(t, e.session?.engine)) : [],
        approvals: [],
        draft: null,
      };
    case "config":
      return {
        ...s,
        config: e.config,
        toasts: e.rejected?.length ? toast(s, "error", `Not changed: ${e.rejected.join(", ")}`) : s.toasts,
      };
    case "voice_profile":
      return {
        ...s,
        voiceProfile: { corrections: e.corrections, vocabulary: e.vocabulary, projectTerms: e.project_terms, samples: e.samples },
      };
    case "learned":
      return { ...s, toasts: toast(s, "learned", `Learned: “${e.heard}” means ${e.meant}`) };
    case "devices":
      return { ...s, devices: e.items };
    case "notice":
      return { ...s, toasts: toast(s, "info", e.message) };
    case "error":
      return { ...s, toasts: toast(s, "error", e.message) };
    case "pong":
      return s;
    // Voice feature events
    case "wake_word_detected":
      return { ...s, wakeWordActive: true };
    case "continuous_mode_changed":
      return { ...s, continuousMode: e.active };
    case "draft_command":
      return { ...s, draftCommand: { seq: (s.draftCommand?.seq ?? 0) + 1, action: e.action } };
    case "command_chain_start":
      return { ...s, chain: { id: e.chain_id, name: e.name, steps: e.steps, index: 0 } };
    case "command_chain_step":
      return s.chain?.id === e.chain_id ? { ...s, chain: { ...s.chain, index: e.index } } : s;
    case "command_chain_end": {
      const why =
        e.reason === "declined" ? "you declined an action" : e.reason === "failed" ? "a step didn't finish" : null;
      return {
        ...s,
        chain: s.chain?.id === e.chain_id ? null : s.chain,
        toasts: why ? toast(s, "info", `Stopped after step ${e.completed + 1} of ${e.total}: ${why}.`) : s.toasts,
      };
    }
    case "templates":
      return { ...s, templates: e.items };
    // IDE bridge events
    case "ide_connected":
      return { ...s, ideConnected: true, ideType: e.ide_type };
    case "ide_context_update":
      return s;
    // Workflow events
    case "workflow_started":
      return { ...s, workflowActive: true, currentWorkflow: e.workflow_name };
    case "workflow_step_complete":
      return s;
    case "workflow_error":
      return { ...s, workflowActive: false, currentWorkflow: null };
    case "macro_recorded":
      return { ...s, macroRecording: false };
  }
}

interface Actions {
  apply: (e: EngineEvent) => void;
  setConnection: (c: ConnectionStatus) => void;
  dismissToast: (id: number) => void;
  setDraftText: (text: string) => void;
}

export const useApp = create<AppState & Actions>()((set) => ({
  ...initialState,
  apply: (e) => set((s) => reduceEvent(s, e)),
  setConnection: (connection) => set({ connection }),
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  setDraftText: (text) => set((s) => (s.draft ? { draft: { ...s.draft, text } } : {})),
}));
