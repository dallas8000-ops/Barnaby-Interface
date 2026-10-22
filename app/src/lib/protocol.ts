// Mirror of engine/barnaby_engine/protocol.py. Keep in sync; the engine
// validates every command, so drift shows up as `bad_command` errors, not UB.

export type EngineState =
  | "idle"
  | "listening"
  | "transcribing"
  | "thinking"
  | "speaking"
  | "awaiting_approval"
  | "error";

export type Risk = "normal" | "high" | "critical";
export type ComponentStatus = "loading" | "ready" | "error";
export type Editor = "vscode" | "cursor";
export type AgentEngine = "claude" | "cursor" | "devin";
export type SecretName = "devin";

/** Claude Code's sign-in, as the engine last saw it. "expired": Claude rejected a real turn. */
export interface ClaudeAuth {
  state: "unknown" | "signed_in" | "signed_out" | "expired" | "signing_in" | "failed" | "unavailable";
  detail?: string;
  method?: string;
}
export type ClientRole = "desktop" | "remote";

export interface TabletDevice {
  id: string;
  name: string;
  created: number;
  last_seen: number;
  connected: boolean;
}

export interface TabletStatus {
  enabled: boolean;
  running: boolean;
  port: number;
  urls: string[];
  devices: TabletDevice[];
  ca_fingerprint?: string;
  error?: string;
}

export interface EngineInfo {
  name: AgentEngine;
  available: boolean;
  reason?: string;
}

export type TaskStatus = "queued" | "running" | "needs_approval" | "done" | "failed" | "cancelled" | "interrupted";

/** A background task (engine: tasks.TaskRecord.public()). */
export interface TaskView {
  id: string;
  workspace: string;
  engine: AgentEngine;
  title: string;
  instruction: string;
  lanes: string[];
  status: TaskStatus;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  summary: string;
  result_text: string;
  error: string;
  cost_usd: number | null;
  cli_session_id: string | null;
  turns: number;
  warning?: string;
}

export interface TaskApprovalEvent {
  type: "task_approval_required";
  task_id: string;
  request_id: string;
  tool_name: string;
  summary: string;
  risk: Risk;
  voice_approvable: boolean;
  grantable: boolean;
  detail: string;
}

export interface TasksSnapshot {
  items: TaskView[];
  folders: string[];
  saved_lanes: Record<string, string[]>;
  max_parallel: number;
  /** Undecided task approval cards, so a client that reconnects can still answer them. */
  approvals?: TaskApprovalEvent[];
}

export interface TemplateSummary {
  id: string;
  name: string;
  description: string;
  step_count: number;
  custom: boolean;
}

export type ChainEndReason = "completed" | "failed" | "declined" | "cancelled" | "interrupted" | "limit";

export interface Config {
  tablet_access?: boolean;
  tablet_port?: number;
  workspace: string;
  claude_model: string;
  engine: AgentEngine;
  cursor_model: string;
  continuous_mode_enabled: boolean;
  continuous_mode_timeout: number;
  whisper_model: string;
  /** "auto" (detect per utterance) or a language code from lib/languages.ts. Older engines omit it: English. */
  spoken_language?: string;
  whisper_device: "auto" | "cpu" | "cuda";
  input_device: number | null;
  tts_enabled: boolean;
  tts_voice: string;
  tts_speed: number;
  max_spoken_chars: number;
  review_transcripts: boolean;
  save_voice_samples: boolean;
  safe_bash: string[];
  turn_timeout_s: number;
  max_parallel_tasks?: number;
  task_timeout_s?: number;
  task_lanes?: Record<string, Record<string, string[]>>;
}

export interface SessionSummary {
  id: string;
  title: string;
  workspace: string;
  engine?: AgentEngine;
  updated_at: number;
  turn_count: number;
}

export interface StoredTurn {
  id: string;
  user_text: string;
  assistant_text: string;
  tools: { id: string; name: string; summary: string; path: string | null; ok: boolean | null }[];
  status: string;
  cost_usd: number | null;
  created_at: number;
}

export interface Correction {
  heard: string;
  meant: string;
  count: number;
  disabled: number;
  last_seen: number;
}

export type EngineEvent =
  | {
      type: "hello";
      state: EngineState;
      config: Config;
      engines?: EngineInfo[];
      templates?: TemplateSummary[];
      handsfree?: boolean;
      secrets?: Record<SecretName, boolean>;
      tablet?: TabletStatus;
      role?: ClientRole;
      session_id: string | null;
      speaking: boolean;
      sessions: SessionSummary[];
      components: Record<"stt" | "tts", { status: ComponentStatus; detail?: string }>;
      tasks?: { type: "tasks" } & TasksSnapshot;
      claude_auth?: ClaudeAuth;
      /** Conversation language right now (follows the speaker in Auto). */
      language?: string;
      /** Recognition model id -> already downloaded on the PC. */
      stt_installed?: Record<string, boolean>;
      protocol: number;
    }
  | { type: "state"; state: EngineState; detail: string | null }
  | { type: "level"; rms: number }
  | { type: "speaking"; active: boolean }
  | { type: "component"; name: "stt" | "tts"; status: ComponentStatus; detail?: string }
  | { type: "transcript"; draft_id: string | null; heard: string; text: string; review: boolean }
  | { type: "turn_start"; turn_id: string; text: string; session_id: string; system: boolean; engine?: AgentEngine }
  | { type: "agent_text"; turn_id: string; delta: string }
  | { type: "tool_start"; turn_id: string; tool_id: string; name: string; summary: string; path: string | null }
  | { type: "tool_end"; turn_id: string; tool_id: string; ok: boolean; preview: string }
  | { type: "turn_end"; turn_id: string; status: string; text?: string; cost_usd?: number | null; duration_ms?: number | null }
  | { type: "approval_required"; turn_id: string; request_id: string; tool_name: string; summary: string; risk: Risk; voice_approvable: boolean; grantable: boolean; detail: string }
  | { type: "approval_decided"; request_id: string; approved: boolean }
  | { type: "approvals_cleared" }
  | { type: "sessions"; items: SessionSummary[] }
  | { type: "session_loaded"; session: (SessionSummary & { claude_session_id: string | null; turns: StoredTurn[] }) | null }
  | { type: "config"; config: Config; rejected?: string[] }
  | { type: "voice_profile"; corrections: Correction[]; vocabulary: string[]; project_terms: string[]; samples: number }
  | { type: "learned"; heard: string; meant: string }
  | { type: "devices"; items: { index: number; name: string; default: boolean; auto?: boolean; loopback?: boolean }[] }
  | { type: "secrets"; items: Record<SecretName, boolean> }
  | ({ type: "claude_auth" } & ClaudeAuth)
  | { type: "paired"; token: string; device_id: string }
  | { type: "pair_error"; message: string }
  | { type: "say"; text: string; lang?: string }
  | { type: "stt_models"; installed: Record<string, boolean> }
  | { type: "say_stop" }
  | ({ type: "tablet_status" } & TabletStatus)
  | { type: "tablet_pairing"; url: string; expires_at: number }
  | { type: "engines"; items: EngineInfo[] }
  | { type: "notice"; message: string }
  | { type: "error"; code: string; message: string }
  | { type: "pong"; t: number }
  // Background tasks
  | ({ type: "tasks" } & TasksSnapshot)
  | { type: "task_update"; task: TaskView }
  | { type: "task_text"; task_id: string; delta: string }
  | { type: "task_activity"; task_id: string; tool: string; path: string | null }
  | { type: "task_done"; task_id: string; status: TaskStatus; title: string; engine: AgentEngine; summary: string; error: string }
  | TaskApprovalEvent
  | { type: "task_approval_decided"; task_id: string; request_id: string; approved: boolean }
  // Voice feature events
  | { type: "continuous_mode_changed"; active: boolean }
  | { type: "draft_command"; action: "send" | "discard" }
  | { type: "command_chain_start"; chain_id: string; steps: string[]; name: string | null }
  | { type: "command_chain_step"; chain_id: string; index: number; total: number; text: string }
  | { type: "command_chain_end"; chain_id: string; success: boolean; reason: ChainEndReason; completed: number; total: number }
  | { type: "templates"; items: TemplateSummary[] }
  // IDE bridge events
  | { type: "ide_connected"; ide_type: "vscode" | "cursor" | "claude-desktop"; ide_version: string }
  | { type: "ide_context_update"; file_path: string | null; selection: string | null }
  // Workflow events
  | { type: "workflow_started"; workflow_id: string; workflow_name: string }
  | { type: "workflow_step_complete"; workflow_id: string; step_number: number; success: boolean }
  | { type: "workflow_error"; workflow_id: string; error_message: string };

export type Command =
  | { type: "auth"; token: string }
  | { type: "ptt_start" }
  | { type: "ptt_stop" }
  | { type: "text_submit"; text: string; draft_id?: string | null }
  | { type: "discard_draft"; draft_id: string }
  | { type: "cancel" }
  | { type: "stop_speaking" }
  | { type: "approve"; request_id: string }
  | { type: "deny"; request_id: string }
  | { type: "new_session" }
  | { type: "load_session"; session_id: string }
  | { type: "list_sessions" }
  | { type: "set_config"; values: Partial<Config> }
  | { type: "get_voice_profile" }
  | { type: "update_correction"; heard: string; meant: string; action: "enable" | "disable" | "delete" }
  | { type: "delete_vocab"; term: string }
  | { type: "open_in_editor"; editor: Editor; path: string; line?: number | null }
  | { type: "list_devices" }
  | { type: "list_templates" }
  | { type: "run_template"; template_id: string; context?: string | null }
  // The key goes to the engine once and is never sent back: only whether one is saved.
  | { type: "set_secret"; name: SecretName; value: string }
  | { type: "delete_secret"; name: SecretName }
  | { type: "claude_login" }
  | { type: "claude_login_cancel" }
  | { type: "claude_auth_check" }
  | { type: "audio_clip"; pcm_b64: string }
  | { type: "tablet_pair_start" }
  | { type: "tablet_revoke"; device_id: string }
  | { type: "task_create"; engine: AgentEngine; instruction: string; lanes: string[]; title?: string | null }
  | { type: "task_cancel"; task_id: string }
  | { type: "task_follow_up"; task_id: string; text: string }
  | { type: "task_clear" }
  | { type: "task_list" }
  | { type: "ping" };
