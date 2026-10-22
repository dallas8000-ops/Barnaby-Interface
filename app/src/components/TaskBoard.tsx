import { Check, ChevronDown, FolderOpen, GitBranch, Plus, Send, Square, Trash2, TriangleAlert, X } from "lucide-react";
import { useEffect, useState } from "react";
import { send } from "../lib/client";
import type { CheckResult, TaskStatus, TaskView } from "../lib/protocol";
import { useApp } from "../store/app";
import { ApprovalCard } from "./Approvals";
import { AssignTaskDialog } from "./AssignTaskDialog";
import { CursorGuardNote } from "./CursorGuardNote";

const ENGINE: Record<string, string> = { claude: "Claude", cursor: "Cursor", devin: "Devin" };
const ACTIVE: TaskStatus[] = ["queued", "running", "verifying", "needs_approval"];

const STATUS: Record<TaskStatus, { label: string; cls: string }> = {
  queued: { label: "Waiting", cls: "border-line text-muted" },
  running: { label: "Working", cls: "border-cyan text-cyan" },
  verifying: { label: "Checking", cls: "border-cyan text-cyan" },
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

/** Which checks passed or failed on the last run, each one's output on expand. */
function CheckResults({ task, maxRuns }: { task: TaskView; maxRuns: number }) {
  const results = task.check_results ?? [];
  const [shown, setShown] = useState<number | null>(null);
  const attempts = task.attempts ?? 0;
  const count = task.checks?.length ?? 0;
  if (task.status === "verifying")
    return (
      <p className="mt-2 text-xs text-cyan" aria-live="polite">
        Running {count} check{count === 1 ? "" : "s"} (run {attempts} of {maxRuns})…
      </p>
    );
  if (!results.length) return null;
  return (
    <div className="mt-2" aria-label="Checks">
      <ul className="flex flex-wrap gap-1 text-xs">
        {results.map((r: CheckResult, i) => (
          <li key={`${i}-${r.command}`}>
            <button
              type="button"
              onClick={() => setShown(shown === i ? null : i)}
              aria-expanded={shown === i}
              title={r.dir ? `in ${r.dir}/` : undefined}
              className={`inline-flex items-center gap-1 rounded border px-1.5 font-mono leading-5 ${r.passed ? "border-green/50 text-green" : "border-red/60 text-red"}`}
            >
              {r.passed ? <Check size={11} aria-label="passed" /> : <X size={11} aria-label="failed" />}
              {r.command}
              {!r.passed && <span className="font-sans">{r.error ? " (couldn't run)" : r.timed_out ? " (timed out)" : ` (exit ${r.exit_code})`}</span>}
            </button>
          </li>
        ))}
        {attempts > 1 && <li className="self-center text-muted">· {attempts} check runs</li>}
      </ul>
      {shown != null && results[shown] && (
        <pre className="scroll-quiet mt-1 max-h-48 overflow-auto rounded-md bg-raised px-2.5 py-1.5 text-xs text-ink whitespace-pre-wrap break-words">
          {results[shown].error || results[shown].output || "(no output)"}
        </pre>
      )}
    </div>
  );
}

function TaskCard({ task }: { task: TaskView }) {
  const text = useApp((s) => s.taskText[task.id] ?? "");
  const activity = useApp((s) => s.taskActivity[task.id]);
  const allApprovals = useApp((s) => s.taskApprovals); // select the array; a filtered copy per render would loop
  const approvals = allApprovals.filter((a) => a.taskId === task.id);
  const workspace = useApp((s) => s.config?.workspace ?? "");
  const desktop = useApp((s) => s.role === "desktop");
  const maxRuns = useApp((s) => (s.config?.task_check_retries ?? 2) + 1);
  const [open, setOpen] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const worktree = task.isolation === "worktree" && Boolean(task.branch);
  const unverified = task.status === "done" && task.verification === "unverified";
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
            {worktree && (
              <span className="inline-flex items-center gap-0.5 rounded bg-raised px-1 font-mono leading-4" title={task.run_dir || task.worktree}>
                <GitBranch size={11} aria-hidden /> {task.branch}
              </span>
            )}
            {task.isolation === "shared" && <span title="Works in the project folder itself, not a worktree">· shared folder</span>}
            {otherProject && <span title={task.workspace}>· other project</span>}
            {time && <span>· {time}</span>}
            {task.cost_usd != null && <span>· ${task.cost_usd.toFixed(2)}</span>}
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${status.cls}`}>{status.label}</span>
          {task.status === "done" && task.verification === "passed" && (
            <span className="inline-flex items-center gap-0.5 text-[11px] text-green">
              <Check size={11} aria-hidden /> Verified
            </span>
          )}
          {unverified && (
            <span
              className="text-[11px] text-muted"
              title={task.engine === "devin" ? "Devin's work is a pull request; Barnaby didn't run checks on it." : "No checks are set for this task, so Barnaby didn't verify it."}
            >
              Unverified
            </span>
          )}
        </div>
      </div>

      {task.engine === "cursor" && active && (
        <div className="mt-2">
          <CursorGuardNote compact />
        </div>
      )}
      {task.warning && (
        <p className="mt-2 flex gap-1.5 text-xs text-amber">
          <TriangleAlert size={13} aria-hidden className="mt-0.5 shrink-0" />
          {task.warning}
        </p>
      )}
      {task.notice && (
        <p className={`mt-2 flex gap-1.5 text-xs ${task.isolation === "shared" ? "text-amber" : "text-muted"}`}>
          {task.isolation === "shared" && <TriangleAlert size={13} aria-hidden className="mt-0.5 shrink-0" />}
          {task.notice}
        </p>
      )}
      <CheckResults task={task} maxRuns={maxRuns} />
      {task.status === "running" && (activity || live) && (
        <p className="mt-2 line-clamp-2 text-xs text-muted" aria-live="polite">
          {activity ? `${VERB[activity.tool] ?? activity.tool}${activity.path ? ` ${activity.path}` : ""}` : live}
        </p>
      )}
      {task.status === "done" && task.summary && <p className="mt-2 text-sm text-ink">{task.summary}</p>}
      {/* The rest of a check failure's error is the output, shown under Checks. */}
      {task.error && task.status !== "done" && <p className="mt-2 text-sm text-red">{task.error.split("\n")[0]}</p>}

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
        {worktree && desktop && (
          <button
            type="button"
            onClick={() => send({ type: "task_open_folder", task_id: task.id })}
            className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs text-ink hover:border-cyan"
            title={task.run_dir || task.worktree}
          >
            <FolderOpen size={12} aria-hidden /> Open folder
          </button>
        )}
        {worktree && desktop && !active && !confirmDiscard && (
          <button
            type="button"
            onClick={() => setConfirmDiscard(true)}
            className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs text-ink hover:border-red hover:text-red"
          >
            <Trash2 size={12} aria-hidden /> Discard
          </button>
        )}
      </div>
      {confirmDiscard && (
        <div role="alertdialog" aria-label="Discard this task's work?" className="mt-2 rounded-md border border-red/60 bg-raised p-2 text-xs text-ink">
          <p>
            Delete branch <code className="font-mono">{task.branch}</code> and its worktree? Work on it that you haven't merged is lost.
          </p>
          <div className="mt-1.5 flex gap-2">
            <button
              type="button"
              onClick={() => {
                if (send({ type: "task_discard", task_id: task.id })) setConfirmDiscard(false);
              }}
              className="rounded-md bg-red px-2 py-1 font-medium text-bg hover:brightness-110"
            >
              Discard work
            </button>
            <button type="button" onClick={() => setConfirmDiscard(false)} className="rounded-md border border-line px-2 py-1 hover:border-ink">
              Keep it
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

export function TaskBoard() {
  const tasks = useApp((s) => s.tasks);
  const maxParallel = useApp((s) => s.maxParallel);
  const [assigning, setAssigning] = useState(false);
  const running = tasks.filter((t) => t.status === "running" || t.status === "verifying").length;
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
