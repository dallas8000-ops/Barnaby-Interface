import { ChevronDown, Plus, Send, Square, Trash2, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { send } from "../lib/client";
import type { TaskStatus, TaskView } from "../lib/protocol";
import { useApp } from "../store/app";
import { ApprovalCard } from "./Approvals";
import { AssignTaskDialog } from "./AssignTaskDialog";
import { folderName } from "./Sidebar";

const ENGINE: Record<string, string> = { claude: "Claude", cursor: "Cursor", devin: "Devin" };
const ACTIVE: TaskStatus[] = ["queued", "running", "needs_approval"];

const STATUS: Record<TaskStatus, { label: string; cls: string }> = {
  queued: { label: "Waiting", cls: "border-line text-muted" },
  running: { label: "Working", cls: "border-cyan text-cyan" },
  needs_approval: { label: "Needs your OK", cls: "border-amber text-amber" },
  done: { label: "Done", cls: "border-green text-green" },
  failed: { label: "Failed", cls: "border-red text-red" },
  cancelled: { label: "Cancelled", cls: "border-line text-muted" },
  interrupted: { label: "Interrupted", cls: "border-line text-muted" },
};

const VERB: Record<string, string> = { Edit: "Editing", Write: "Writing", MultiEdit: "Editing", Read: "Reading", Bash: "Running a command", Grep: "Searching", Glob: "Finding files" };

/** "3 min", "1 h 5 min", "40 s". */
export function duration(fromS: number, toS: number): string {
  const s = Math.max(0, Math.round(toS - fromS));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now() / 1000), 5000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

function TaskCard({ task }: { task: TaskView }) {
  const text = useApp((s) => s.taskText[task.id] ?? "");
  const activity = useApp((s) => s.taskActivity[task.id]);
  const allApprovals = useApp((s) => s.taskApprovals); // select the array; a filtered copy per render would loop
  const approvals = allApprovals.filter((a) => a.taskId === task.id);
  const workspace = useApp((s) => s.config?.workspace ?? "");
  const [open, setOpen] = useState(false);
  const [followUp, setFollowUp] = useState("");
  const active = ACTIVE.includes(task.status);
  const now = useNow(task.status === "running");
  const status = STATUS[task.status];
  const otherProject = task.workspace !== workspace;
  const time = task.started_at != null
    ? task.finished_at != null
      ? `took ${duration(task.started_at, task.finished_at)}`
      : task.status === "running"
        ? `${duration(task.started_at, now)} so far`
        : null
    : null;
  const live = text.trim().slice(-220);

  return (
    <li className="rounded-lg border border-line bg-panel p-3" data-testid="task" data-status={task.status} aria-label={`Task: ${task.title}`}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold text-ink" title={task.instruction}>
            {task.title}
          </h3>
          <p className="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-muted">
            <span className="rounded border border-line px-1 leading-4">{ENGINE[task.engine] ?? task.engine}</span>
            {task.lanes.length ? (
              task.lanes.map((l) => (
                <span key={l} className="rounded bg-raised px-1 font-mono leading-4">
                  {l}/
                </span>
              ))
            ) : (
              <span className="rounded bg-raised px-1 leading-4">whole project</span>
            )}
            {otherProject && <span title={task.workspace}>· {folderName(task.workspace)}</span>}
            {time && <span>· {time}</span>}
            {task.cost_usd != null && <span>· ${task.cost_usd.toFixed(2)}</span>}
          </p>
        </div>
        <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium ${status.cls}`}>{status.label}</span>
      </div>

      {task.warning && (
        <p className="mt-2 flex gap-1.5 text-xs text-amber">
          <TriangleAlert size={13} aria-hidden className="mt-0.5 shrink-0" />
          {task.warning}
        </p>
      )}
      {task.status === "running" && (activity || live) && (
        <p className="mt-2 line-clamp-2 text-xs text-muted" aria-live="polite">
          {activity ? `${VERB[activity.tool] ?? activity.tool}${activity.path ? ` ${activity.path}` : ""}` : live}
        </p>
      )}
      {task.status === "done" && task.summary && <p className="mt-2 text-sm text-ink">{task.summary}</p>}
      {task.error && task.status !== "done" && <p className="mt-2 text-sm text-red">{task.error}</p>}

      {approvals.length > 0 && (
        <div className="mt-2 flex flex-col gap-2">
          {approvals.map((a) => (
            <ApprovalCard key={a.requestId} approval={a} />
          ))}
        </div>
      )}

      {task.result_text && !active && (
        <>
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="mt-2 inline-flex items-center gap-1 text-xs text-muted hover:text-ink"
          >
            <ChevronDown size={13} aria-hidden className={open ? "rotate-180" : ""} /> {open ? "Hide reply" : "Full reply"}
          </button>
          {open && (
            <pre className="scroll-quiet mt-1 max-h-64 overflow-auto rounded-md bg-raised px-2.5 py-1.5 text-xs text-ink whitespace-pre-wrap break-words">
              {task.result_text}
            </pre>
          )}
        </>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        {active ? (
          <button
            type="button"
            onClick={() => send({ type: "task_cancel", task_id: task.id })}
            className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs text-ink hover:border-red hover:text-red"
          >
            <Square size={12} aria-hidden /> Cancel
          </button>
        ) : (
          <form
            className="flex min-w-0 flex-1 gap-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              const text = followUp.trim();
              if (!text) return;
              if (send({ type: "task_follow_up", task_id: task.id, text })) setFollowUp("");
            }}
          >
            <input
              value={followUp}
              onChange={(e) => setFollowUp(e.target.value)}
              placeholder="Follow up…"
              aria-label={`Follow up on ${task.title}`}
              className="min-w-0 flex-1 rounded-md border border-line bg-raised px-2 py-1 text-xs text-ink focus:border-cyan focus:outline-none"
            />
            <button type="submit" aria-label="Send follow-up" disabled={!followUp.trim()} className="rounded-md border border-line px-2 text-muted hover:text-ink disabled:opacity-40">
              <Send size={12} aria-hidden />
            </button>
          </form>
        )}
      </div>
    </li>
  );
}

export function TaskBoard() {
  const tasks = useApp((s) => s.tasks);
  const maxParallel = useApp((s) => s.maxParallel);
  const [assigning, setAssigning] = useState(false);
  const running = tasks.filter((t) => t.status === "running").length;
  const waiting = tasks.filter((t) => t.status === "queued").length;
  const needs = tasks.filter((t) => t.status === "needs_approval").length;
  const finished = tasks.filter((t) => !ACTIVE.includes(t.status)).length;
  const counts = [
    running && `${running} working`,
    needs && `${needs} need${needs === 1 ? "s" : ""} your OK`,
    waiting && `${waiting} waiting`,
  ].filter(Boolean);

  return (
    <section aria-label="Tasks" className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex items-center justify-end gap-2">
        {finished > 0 && (
          <button
            type="button"
            onClick={() => send({ type: "task_clear" })}
            className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted hover:bg-raised hover:text-ink"
          >
            <Trash2 size={12} aria-hidden /> Clear finished
          </button>
        )}
        <button
          type="button"
          onClick={() => setAssigning(true)}
          className="inline-flex items-center gap-1 rounded-md bg-cyan px-2.5 py-1 text-xs font-medium text-bg hover:brightness-110"
        >
          <Plus size={13} aria-hidden /> New task
        </button>
      </div>
      <p className="text-xs text-muted" aria-live="polite">
        {counts.length ? counts.join(" · ") : "Hand work to Claude, Cursor or Devin and keep going."}
        {running > 0 && ` · up to ${maxParallel} at once`}
      </p>
      <ol className="scroll-quiet -mr-1 flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-1">
        {tasks.length === 0 ? (
          <li className="text-sm text-muted">
            No tasks yet. Try saying “task for Devin in backend: add the orders endpoint”, or press New task.
          </li>
        ) : (
          tasks.map((t) => <TaskCard key={t.id} task={t} />)
        )}
      </ol>
      {assigning && <AssignTaskDialog onClose={() => setAssigning(false)} />}
    </section>
  );
}
