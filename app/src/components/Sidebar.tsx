import { AudioLines, FolderOpen, FolderPlus, ListChecks, Plus, Settings } from "lucide-react";
import { useEffect, useState } from "react";
import { send } from "../lib/client";
import { pickFolder } from "../lib/platform";
import type { SessionSummary } from "../lib/protocol";
import { useApp } from "../store/app";

export const folderName = (p: string) => p.split(/[\\/]/).filter(Boolean).pop() ?? p;

/**
 * Which project the conversation works in. Any device picks from the projects the PC has
 * opened (the engine refuses anything else); only the PC can open a new folder.
 */
function ProjectPicker({ remote, workspace }: { remote: boolean; workspace: string }) {
  const projects = useApp((s) => s.projects);
  const choose = async () => {
    const folder = await pickFolder();
    if (folder) send({ type: "set_config", values: { workspace: folder } });
  };
  if (projects.length === 0) {
    return remote ? (
      <p className="mt-3 flex items-center gap-2 px-0.5 text-sm text-muted" title={workspace}>
        <FolderOpen size={15} aria-hidden className="shrink-0" />
        <span className="truncate">{workspace ? folderName(workspace) : "No project"}</span>
      </p>
    ) : (
      <button
        type="button"
        onClick={choose}
        className="mt-3 flex w-full items-center gap-2 rounded-md border border-line bg-panel px-2.5 py-1.5 text-left text-sm hover:border-cyan"
        title={workspace || "Choose project folder"}
      >
        <FolderOpen size={15} aria-hidden className="shrink-0 text-muted" />
        <span className="truncate">{workspace ? folderName(workspace) : "Choose project"}</span>
      </button>
    );
  }
  const current = projects.find((p) => p.current)?.path ?? "";
  return (
    <div className="mt-3 flex items-center gap-1.5">
      <label className="flex min-w-0 flex-1 items-center gap-2 rounded-md border border-line bg-panel px-2.5 py-1.5 text-sm focus-within:border-cyan">
        <FolderOpen size={15} aria-hidden className="shrink-0 text-muted" />
        <span className="sr-only">Project</span>
        <select
          value={current}
          onChange={(e) => send({ type: "select_project", workspace: e.target.value })}
          className="min-w-0 flex-1 truncate bg-transparent text-ink focus:outline-none"
          title={workspace}
        >
          {!current && <option value="">No project</option>}
          {projects.map((p) => (
            <option key={p.path} value={p.path}>
              {p.name}
              {p.active_tasks ? ` (${p.active_tasks} task${p.active_tasks === 1 ? "" : "s"})` : ""}
            </option>
          ))}
        </select>
      </label>
      {!remote && (
        <button
          type="button"
          onClick={choose}
          aria-label="Open another project folder"
          title="Open another project folder"
          className="rounded-md border border-line bg-panel p-1.5 text-muted hover:border-cyan hover:text-ink"
        >
          <FolderPlus size={15} aria-hidden />
        </button>
      )}
    </div>
  );
}

/** Recent sessions plus anything fetched while browsing this project, newest first, no repeats. */
function mergeSessions(a: SessionSummary[], b: SessionSummary[]): SessionSummary[] {
  const byId = new Map<string, SessionSummary>();
  for (const s of [...b, ...a]) byId.set(s.id, s);
  return [...byId.values()].sort((x, y) => y.updated_at - x.updated_at);
}

export function relativeTime(epochSeconds: number, now = Date.now()): string {
  const s = Math.max(0, Math.round(now / 1000 - epochSeconds));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  const d = Math.floor(s / 86400);
  return d === 1 ? "yesterday" : `${d} days ago`;
}

function Dot({ status }: { status: string }) {
  const cls = status === "ready" || status === "open" ? "bg-green" : status === "error" || status === "unauthorized" ? "bg-red" : "bg-amber animate-pulse";
  return <span className={`inline-block h-2 w-2 rounded-full ${cls}`} aria-hidden />;
}

export function Sidebar({ onSettings, onProfile, onTasks }: { onSettings: () => void; onProfile: () => void; onTasks?: () => void }) {
  const sessions = useApp((s) => s.sessions);
  const sessionId = useApp((s) => s.sessionId);
  const workspace = useApp((s) => s.config?.workspace ?? "");
  const connection = useApp((s) => s.connection);
  const stt = useApp((s) => s.components.stt);
  const tts = useApp((s) => s.components.tts);

  const remote = useApp((s) => s.role === "remote");
  const attention = useApp((s) => s.taskAttention);
  const activeTasks = useApp((s) => s.tasks.filter((t) => t.status === "running" || t.status === "needs_approval").length);
  const multiProject = useApp((s) => s.projects.length > 1);
  const fetched = useApp((s) => s.projectSessions[workspace]);
  const [scope, setScope] = useState<"project" | "all">("project");
  useEffect(() => {
    // The recent list holds 50 sessions across all projects; fetch this project's own history.
    if (workspace && multiProject && connection === "open") send({ type: "list_sessions", workspace });
  }, [workspace, multiProject, connection]);
  const showAll = scope === "all" && multiProject;
  const shown = showAll
    ? sessions
    : mergeSessions(sessions.filter((s) => !workspace || s.workspace === workspace), fetched ?? []);

  return (
    <nav className="app-sidebar flex h-full flex-col border-r border-line bg-bg" aria-label="Sessions">
      <div className="px-4 pt-4 pb-3">
        <p className="brand font-strip text-[22px] font-semibold leading-none tracking-tight">Barnaby</p>
        <ProjectPicker remote={remote} workspace={workspace} />
      </div>

      {onTasks && (
        // The task board lives in the side panel on wide screens; narrower ones open it from here.
        <div className="px-4 pb-3 xl:hidden">
          <button
            type="button"
            onClick={onTasks}
            className="flex w-full items-center gap-2 rounded-md border border-line bg-panel px-2.5 py-1.5 text-left text-sm hover:border-cyan"
          >
            <ListChecks size={15} aria-hidden className="shrink-0 text-muted" />
            <span className="flex-1">Tasks</span>
            {activeTasks > 0 && <span className="text-xs text-muted">{activeTasks} active</span>}
            {attention > 0 && (
              <span className="rounded-full bg-amber px-1.5 text-[11px] leading-4 text-bg" aria-label={`${attention} new`}>
                {attention}
              </span>
            )}
          </button>
        </div>
      )}

      <div className="flex items-center justify-between gap-2 px-4 pb-1">
        <h2 className="text-xs font-medium text-muted">Sessions</h2>
        {multiProject && (
          <div role="group" aria-label="Show sessions for" className="ml-auto flex rounded-md border border-line text-[11px]">
            {(["project", "all"] as const).map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={scope === v}
                onClick={() => setScope(v)}
                className={`px-1.5 py-0.5 ${scope === v ? "bg-raised text-ink" : "text-muted"}`}
              >
                {v === "project" ? "This project" : "All"}
              </button>
            ))}
          </div>
        )}
        <button
          type="button"
          onClick={() => send({ type: "new_session" })}
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted hover:bg-panel hover:text-ink"
        >
          <Plus size={13} aria-hidden /> New
        </button>
      </div>
      <ul className="scroll-quiet flex-1 overflow-y-auto px-2 pb-2">
        {shown.length === 0 && <li className="px-2 py-2 text-sm text-muted">Your conversations in this project appear here.</li>}
        {shown.map((s) => (
          <li key={s.id}>
            <button
              type="button"
              aria-current={s.id === sessionId ? "true" : undefined}
              onClick={() => send({ type: "load_session", session_id: s.id })}
              className={`w-full rounded-md px-2 py-1.5 text-left ${s.id === sessionId ? "bg-panel" : "hover:bg-panel/60"}`}
            >
              <span className="flex items-center gap-1.5">
                <span className="truncate text-sm text-ink">{s.title}</span>
                {s.engine === "cursor" && (
                  <span className="shrink-0 rounded border border-line px-1 text-[10px] leading-4 text-muted">Cursor</span>
                )}
              </span>
              <span className="block text-xs text-muted">
                {showAll && s.workspace !== workspace && <span title={s.workspace}>{folderName(s.workspace)} · </span>}
                {relativeTime(s.updated_at)}, {s.turn_count} turn{s.turn_count === 1 ? "" : "s"}
              </span>
            </button>
          </li>
        ))}
      </ul>

      <div className="border-t border-line px-3 py-2">
        <div className="flex gap-1">
          {remote ? (
            <span className="flex-1" />
          ) : (
            <button type="button" onClick={onProfile} className="inline-flex flex-1 items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-muted hover:bg-panel hover:text-ink">
              <AudioLines size={15} aria-hidden /> Voice profile
            </button>
          )}
          <button type="button" onClick={onSettings} aria-label="Settings" className="rounded-md px-2 py-1.5 text-muted hover:bg-panel hover:text-ink">
            <Settings size={16} aria-hidden />
          </button>
        </div>
        <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 px-2 text-xs text-muted" aria-label="System status">
          <dt className="flex items-center gap-1.5">
            <Dot status={connection} /> Engine
          </dt>
          <dd className="text-right">{connection === "open" ? "Connected" : connection === "unauthorized" ? "Rejected" : "Connecting"}</dd>
          <dt className="flex items-center gap-1.5" title={stt.detail}>
            <Dot status={stt.status} /> Hearing
          </dt>
          <dd className="text-right" title={stt.detail}>{stt.status === "ready" ? "Ready" : stt.status === "error" ? "Unavailable" : "Loading"}</dd>
          <dt className="flex items-center gap-1.5" title={tts.detail}>
            <Dot status={tts.status} /> Voice
          </dt>
          <dd className="text-right" title={tts.detail}>{tts.status === "ready" ? "Ready" : tts.status === "error" ? "Unavailable" : "Loading"}</dd>
        </dl>
      </div>
    </nav>
  );
}
