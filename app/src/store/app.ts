import { create } from "zustand";
import type { ConnectionStatus } from "../lib/engine";
import type {
  AgentEngine,
  ComponentStatus,
  Config,
  Correction,
  EngineEvent,
  EngineInfo,
  SecretName,
  ClaudeAuth,
  ClientRole,
  TabletStatus,
  EngineState,
  ApprovalRequiredEvent,
  LiveSnapshot,
  ProjectInfo,
  RecentEvent,
  Risk,
  SessionSummary,
  StoredTurn,
  TaskApprovalEvent,
  TaskView,
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

export interface TaskApproval extends Approval {
  taskId: string;
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
  secrets: Record<SecretName, boolean>;
  claudeAuth: ClaudeAuth;
  role: ClientRole;
  tablet: TabletStatus | null;
  tabletPairing: { url: string; expiresAt: number } | null;
  pairError: string | null;
  tabletRecording: boolean;
  tabletSpeaking: boolean;
  sessions: SessionSummary[];
  /** Sessions of one project, fetched when browsing it (beyond the recent list). */
  projectSessions: Record<string, SessionSummary[]>;
  /** Projects the PC has opened; any device may switch between them. */
  projects: ProjectInfo[];
  /** Newest notification this device has seen, so a reconnect shows only what it missed. */
  lastNid: number;
  sessionId: string | null;
  turns: Turn[];
  draft: { id: string; heard: string; text: string } | null;
  approvals: Approval[];
  toasts: Toast[];
  // Background tasks (task board)
  tasks: TaskView[];
  taskText: Record<string, string>;
  taskActivity: Record<string, { tool: string; path: string | null }>;
  taskApprovals: TaskApproval[];
  taskFolders: string[];
  savedLanes: Record<string, string[]>;
  maxParallel: number;
  /** Finished tasks and approval requests since the board was last looked at. */
  taskAttention: number;
  voiceProfile: VoiceProfileView | null;
  devices: { index: number; name: string; default: boolean; auto?: boolean; loopback?: boolean }[];
  
  // Voice features
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
}

export const initialState: AppState = {
  connection: "connecting",
  engineState: "idle",
  level: 0,
  speaking: false,
  components: { stt: { status: "loading" }, tts: { status: "loading" } },
  config: null,
  engines: [{ name: "claude", available: true }],
  secrets: { devin: false },
  claudeAuth: { state: "unknown" },
  role: "desktop",
  tablet: null,
  tabletPairing: null,
  pairError: null,
  tabletRecording: false,
  tabletSpeaking: false,
  sessions: [],
  projectSessions: {},
  projects: [],
  lastNid: 0,
  sessionId: null,
  turns: [],
  draft: null,
  approvals: [],
  toasts: [],
  tasks: [],
  taskText: {},
  taskActivity: {},
  taskApprovals: [],
  taskFolders: [],
  savedLanes: {},
  maxParallel: 3,
  taskAttention: 0,
  voiceProfile: null,
  devices: [],
  
  // Voice features
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

const ENGINE_NAME: Record<string, string> = { claude: "Claude", cursor: "Cursor", devin: "Devin" };
const newestFirst = (items: TaskView[]) => [...items].sort((a, b) => b.created_at - a.created_at);
const without = <T,>(rec: Record<string, T>, id: string): Record<string, T> => {
  if (!(id in rec)) return rec;
  const { [id]: _gone, ...rest } = rec;
  return rest;
};

const toTaskApproval = (e: TaskApprovalEvent): TaskApproval => ({
  taskId: e.task_id,
  requestId: e.request_id,
  toolName: e.tool_name,
  summary: e.summary,
  detail: e.detail,
  risk: e.risk,
  voiceApprovable: false, // background tasks are never approved by voice
  grantable: e.grantable,
  decided: null,
});

function applySnapshot(
  s: AppState,
  snap: { items: TaskView[]; folders: string[]; saved_lanes: Record<string, string[]>; max_parallel: number; approvals?: TaskApprovalEvent[] },
): AppState {
  const ids = new Set(snap.items.map((t) => t.id));
  const keep = <T,>(rec: Record<string, T>) => Object.fromEntries(Object.entries(rec).filter(([k]) => ids.has(k)));
  return {
    ...s,
    tasks: newestFirst(snap.items),
    taskFolders: snap.folders,
    savedLanes: snap.saved_lanes,
    maxParallel: snap.max_parallel,
    taskText: keep(s.taskText),
    taskActivity: keep(s.taskActivity),
    // The engine's list of undecided cards is authoritative: it restores cards sent while this
    // client was away and drops ones answered elsewhere.
    taskApprovals: snap.approvals
      ? snap.approvals.map(toTaskApproval)
      : s.taskApprovals.filter((a) => snap.items.some((t) => t.id === a.taskId && t.status === "needs_approval")),
  };
}

const toApproval = (e: ApprovalRequiredEvent): Approval => ({
  requestId: e.request_id,
  toolName: e.tool_name,
  summary: e.summary,
  detail: e.detail,
  risk: e.risk,
  voiceApprovable: e.voice_approvable,
  grantable: e.grantable,
  decided: null,
});

/** On a first connect, older notifications than this are history, not news. */
const FRESH_RECENT_S = 600;

function recentToasts(s: AppState, recent: RecentEvent[]): { toasts: Toast[]; lastNid: number } {
  let toasts = s.toasts;
  let lastNid = s.lastNid;
  for (const r of recent) {
    if (r.nid <= s.lastNid || (s.lastNid === 0 && r.age_s > FRESH_RECENT_S)) continue;
    lastNid = Math.max(lastNid, r.nid);
    if (r.type === "notice") toasts = toast({ ...s, toasts }, "info", r.message);
    else if (r.type === "error") toasts = toast({ ...s, toasts }, "error", r.message);
    else toasts = toast({ ...s, toasts }, r.status === "done" ? "info" : "error", taskDoneMessage(r));
  }
  return { toasts, lastNid: Math.max(lastNid, ...recent.map((r) => r.nid)) };
}

const taskDoneMessage = (e: { engine: string; title: string; status: string; summary: string; error: string }) => {
  const who = ENGINE_NAME[e.engine] ?? e.engine;
  return e.status === "done"
    ? `${who} finished ${e.title}.${e.summary ? ` ${e.summary}` : ""}`
    : `${who}'s task ${e.title} failed${e.error ? `: ${e.error}` : "."}`;
};

/** The engine's snapshot is the truth: conversation, the turn in flight, open cards, chain,
 *  draft and task activity are rebuilt from it, so nothing missed while away is lost. */
function applyLive(s: AppState, live: LiveSnapshot, sessionId: string | null): AppState {
  const stored = live.session ? live.session.turns.map((t) => fromStored(t, live.session?.engine)) : [];
  const turn = live.turn;
  const turns: Turn[] = turn
    ? [...stored, { id: turn.turn_id, text: turn.text, system: turn.system, engine: turn.engine, assistant: turn.assistant,
        tools: turn.tools.map((x) => ({ ...x })), status: "running" }]
    : stored;
  const taskText = { ...s.taskText };
  const taskActivity = { ...s.taskActivity };
  for (const [id, v] of Object.entries(live.task_live)) {
    if (v.text) taskText[id] = v.text;
    if (v.tool) taskActivity[id] = { tool: v.tool, path: v.path ?? null };
  }
  const { toasts, lastNid } = recentToasts(s, live.recent);
  return {
    ...s,
    sessionId,
    turns,
    approvals: live.approvals.map(toApproval),
    chain: live.chain ? { id: live.chain.chain_id, name: live.chain.name, steps: live.chain.steps, index: live.chain.index } : null,
    draft: live.draft ? { id: live.draft.draft_id, heard: live.draft.heard, text: live.draft.text } : null,
    taskText,
    taskActivity,
    toasts,
    lastNid,
  };
}

const seen = (s: AppState, nid: number | undefined) => (nid ? Math.max(s.lastNid, nid) : s.lastNid);

const updateTurn = (turns: Turn[], id: string, fn: (t: Turn) => Turn): Turn[] =>
  turns.map((t) => (t.id === id ? fn(t) : t));

/** Pure reducer: every engine event maps to one state transition. Unit-tested directly. */
export function reduceEvent(s: AppState, e: EngineEvent): AppState {
  switch (e.type) {
    case "hello": {
      const tasks = e.tasks ? applySnapshot(s, e.tasks) : s;
      const base = e.live ? applyLive(tasks, e.live, e.session_id) : tasks;
      return {
        ...base,
        engineState: e.state,
        config: e.config,
        engines: e.engines ?? s.engines,
        templates: e.templates ?? s.templates,
        continuousMode: e.handsfree ?? s.continuousMode,
        secrets: e.secrets ?? s.secrets,
        claudeAuth: e.claude_auth ?? s.claudeAuth,
        role: e.role ?? s.role,
        tablet: e.tablet ?? s.tablet,
        pairError: null,
        sessions: e.sessions,
        projects: e.projects ?? s.projects,
        speaking: e.speaking,
        components: e.components ?? s.components,
      };
    }
    case "tasks":
      return applySnapshot(s, e);
    case "task_update": {
      const t = e.task;
      const exists = s.tasks.some((x) => x.id === t.id);
      const tasks = exists ? s.tasks.map((x) => (x.id === t.id ? t : x)) : newestFirst([...s.tasks, t]);
      return {
        ...s,
        tasks,
        // A fresh turn starts with fresh live text; the final reply is in result_text.
        taskText: t.status === "queued" ? without(s.taskText, t.id) : s.taskText,
        taskActivity: t.status === "running" ? s.taskActivity : without(s.taskActivity, t.id),
        taskApprovals: t.status === "needs_approval" ? s.taskApprovals : s.taskApprovals.filter((a) => a.taskId !== t.id),
      };
    }
    case "task_text":
      return { ...s, taskText: { ...s.taskText, [e.task_id]: (s.taskText[e.task_id] ?? "") + e.delta } };
    case "task_activity":
      return { ...s, taskActivity: { ...s.taskActivity, [e.task_id]: { tool: e.tool, path: e.path } } };
    case "task_done": {
      const toasts = toast(s, e.status === "done" ? "info" : "error", taskDoneMessage(e));
      return { ...s, toasts, taskAttention: s.taskAttention + 1, lastNid: seen(s, e.nid) };
    }
    case "task_approval_required":
      if (s.taskApprovals.some((a) => a.requestId === e.request_id)) return s;
      return { ...s, taskAttention: s.taskAttention + 1, taskApprovals: [...s.taskApprovals, toTaskApproval(e)] };
    case "task_approval_decided":
      return {
        ...s,
        taskApprovals: s.taskApprovals.map((a) => (a.requestId === e.request_id ? { ...a, decided: e.approved } : a)),
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
      if (s.approvals.some((a) => a.requestId === e.request_id)) return s;
      return { ...s, approvals: [...s.approvals, toApproval(e)] };
    case "approval_decided": {
      const approvals = s.approvals.map((a) => (a.requestId === e.request_id ? { ...a, decided: e.approved } : a));
      return { ...s, approvals: approvals.every((a) => a.decided !== null) ? [] : approvals };
    }
    case "approvals_cleared":
      return { ...s, approvals: [] };
    case "sessions":
      return e.workspace !== undefined
        ? { ...s, projectSessions: { ...s.projectSessions, [e.workspace]: e.items } }
        : { ...s, sessions: e.items };
    case "projects":
      return { ...s, projects: e.items };
    case "draft_closed":
      return s.draft?.id === e.draft_id ? { ...s, draft: null } : s;
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
    case "secrets":
      return { ...s, secrets: e.items };
    case "claude_auth":
      return { ...s, claudeAuth: { state: e.state, detail: e.detail, method: e.method } };
    case "tablet_status": {
      const { type: _t, ...status } = e;
      // A new device means the one-time link was just used: stop showing it.
      const paired = status.devices.length > (s.tablet?.devices.length ?? 0);
      return { ...s, tablet: status, tabletPairing: status.running && !paired ? s.tabletPairing : null };
    }
    case "tablet_pairing":
      return { ...s, tabletPairing: { url: e.url, expiresAt: e.expires_at } };
    case "pair_error":
      return { ...s, pairError: e.message };
    case "paired":
    case "say":
    case "say_stop":
      return s; // handled outside the store (token storage, tablet speech)
    case "engines":
      return { ...s, engines: e.items };
    case "notice":
      return { ...s, toasts: toast(s, "info", e.message), lastNid: seen(s, e.nid) };
    case "error":
      return { ...s, toasts: toast(s, "error", e.message), lastNid: seen(s, e.nid) };
    case "pong":
      return s;
    // Voice feature events
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
  }
}

interface Actions {
  apply: (e: EngineEvent) => void;
  setConnection: (c: ConnectionStatus) => void;
  dismissToast: (id: number) => void;
  setDraftText: (text: string) => void;
  seenTasks: () => void;
}

export const useApp = create<AppState & Actions>()((set) => ({
  ...initialState,
  apply: (e) => set((s) => reduceEvent(s, e)),
  setConnection: (connection) => set({ connection }),
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  setDraftText: (text) => set((s) => (s.draft ? { draft: { ...s.draft, text } } : {})),
  seenTasks: () => set({ taskAttention: 0 }),
}));
