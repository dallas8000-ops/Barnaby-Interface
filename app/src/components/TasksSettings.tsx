import { X } from "lucide-react";
import { useState } from "react";
import { send } from "../lib/client";
import type { CheckSpec, Config } from "../lib/protocol";
import { useApp } from "../store/app";
import { ChecksEditor } from "./ChecksEditor";

const inputCls = "w-full rounded-md border border-line bg-raised px-2.5 py-1.5 text-sm text-ink focus:border-cyan focus:outline-none";
const NAME_OK = /^[A-Za-z0-9 _-]{1,32}$/;

/** Settings → Tasks (PC only): how many run at once, checks, worktrees, and named folder lanes for this project. */
export function TasksSettings({ config }: { config: Config }) {
  const folders = useApp((s) => s.taskFolders);
  const ws = config.workspace;
  const all = config.task_lanes ?? {};
  const lanes = all[ws] ?? {};
  const [name, setName] = useState("");
  const [paths, setPaths] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const allChecks = config.task_checks ?? {};
  const checks = allChecks[ws] ?? [];
  const saveChecks = (next: CheckSpec[]) => {
    const merged = { ...allChecks };
    if (next.length) merged[ws] = next;
    else delete merged[ws];
    send({ type: "set_config", values: { task_checks: merged } });
  };
  const worktrees = config.task_worktrees ?? true;

  const saveLanes = (next: Record<string, string[]>) => {
    const merged = { ...all };
    if (Object.keys(next).length) merged[ws] = next;
    else delete merged[ws];
    send({ type: "set_config", values: { task_lanes: merged } });
  };

  const add = () => {
    const n = name.trim();
    const list = paths
      .split(",")
      .map((p) => p.trim().replace(/\\/g, "/").replace(/^\.\//, "").replace(/\/+$/, ""))
      .filter(Boolean);
    if (!NAME_OK.test(n)) return setProblem("Use a short name: letters, numbers, spaces, - or _.");
    if (Object.keys(lanes).some((k) => k.toLowerCase() === n.toLowerCase() && k !== n))
      return setProblem(`There's already a lane called “${Object.keys(lanes).find((k) => k.toLowerCase() === n.toLowerCase())}”.`);
    if (!list.length) return setProblem("Add at least one folder, e.g. engine or app/src.");
    if (list.length > 8) return setProblem("A lane can have at most 8 folders.");
    if (list.some((p) => p === "." || p === "")) return setProblem("That's the whole project. Pick folders inside it.");
    if (!(n in lanes) && Object.keys(lanes).length >= 20) return setProblem("A project can have at most 20 saved lanes.");
    if (list.some((p) => p.startsWith("/") || /^[A-Za-z]:/.test(p) || p.split("/").includes("..") || /[*?[\]{}()!~$`"']/.test(p)))
      return setProblem("Folders must be inside the project, like engine or app/src.");
    setProblem(null);
    saveLanes({ ...lanes, [n]: list });
    setName("");
    setPaths("");
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <label htmlFor="max-parallel" className="text-sm text-ink">
          At most
        </label>
        <select
          id="max-parallel"
          className="rounded-md border border-line bg-raised px-2 py-1 text-sm text-ink"
          value={config.max_parallel_tasks ?? 3}
          onChange={(e) => send({ type: "set_config", values: { max_parallel_tasks: Number(e.target.value) } })}
        >
          {[1, 2, 3, 4, 5, 6].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <span className="text-sm text-muted">tasks at once. Each uses its own agent, and its own usage.</span>
      </div>

      <div>
        <p className="text-sm text-ink">Checks for this project</p>
        <p className="text-xs text-muted">
          When a Claude or Cursor task finishes, Barnaby runs these itself. The task is only done if they all pass; otherwise the agent gets the
          failing output and tries again. With no checks, finished tasks say “unverified”. Checks must be on the auto-approve list (test and lint
          commands such as npm test or pytest).
        </p>
        {ws ? (
          <div className="mt-2 flex flex-col gap-2">
            <ChecksEditor value={checks} onChange={saveChecks} folders={folders} label="Project checks" />
            <div className="flex items-center gap-3">
              <label htmlFor="check-retries" className="text-sm text-ink">
                Fix-up tries after failing checks
              </label>
              <select
                id="check-retries"
                className="rounded-md border border-line bg-raised px-2 py-1 text-sm text-ink"
                value={config.task_check_retries ?? 2}
                onChange={(e) => send({ type: "set_config", values: { task_check_retries: Number(e.target.value) } })}
              >
                {[0, 1, 2, 3, 4, 5].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </div>
          </div>
        ) : (
          <p className="mt-1 text-xs text-muted">Pick a project folder first.</p>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <p className="text-sm text-ink">Worktrees</p>
        <label className="flex items-start gap-2 text-sm text-ink">
          <input
            type="checkbox"
            className="mt-1 accent-[var(--c-cyan)]"
            checked={worktrees}
            onChange={(e) => send({ type: "set_config", values: { task_worktrees: e.target.checked } })}
          />
          <span>
            Give each Claude or Cursor task its own git worktree and branch
            <span className="block text-xs text-muted">
              Tasks work on a copy of your last commit, so they never touch your files or each other's. Barnaby commits each task's changes to
              its branch (barnaby/task-…); you merge them. Projects that aren't git repositories use the project folder itself.
            </span>
          </span>
        </label>
        <label className={`flex items-start gap-2 text-sm text-ink ${worktrees ? "" : "opacity-50"}`}>
          <input
            type="checkbox"
            className="mt-1 accent-[var(--c-cyan)]"
            disabled={!worktrees}
            checked={Boolean(config.task_worktree_parallel)}
            onChange={(e) => send({ type: "set_config", values: { task_worktree_parallel: e.target.checked } })}
          />
          <span>
            Let worktree tasks with overlapping folders run at the same time
            <span className="block text-xs text-muted">
              They can't overwrite each other's files, but two branches that change the same files will need their conflicts merged by you. Off:
              they wait their turn, as without worktrees.
            </span>
          </span>
        </label>
      </div>

      <div>
        <p className="text-sm text-ink">Saved lanes for this project</p>
        <p className="text-xs text-muted">Name a set of folders once, then say “task for Claude in backend: …”.</p>
        {ws ? (
          <>
            <ul className="mt-2 flex flex-col gap-1" aria-label="Saved lanes">
              {Object.entries(lanes).map(([n, p]) => (
                <li key={n} className="flex items-center gap-2 rounded-md border border-line px-2.5 py-1 text-sm">
                  <span className="font-medium text-ink">{n}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted">{p.join(", ")}</span>
                  <button
                    type="button"
                    aria-label={`Remove lane ${n}`}
                    onClick={() => {
                      const { [n]: _gone, ...rest } = lanes;
                      saveLanes(rest);
                    }}
                    className="text-muted hover:text-red"
                  >
                    <X size={14} aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
            <div className="mt-2 grid gap-2 sm:grid-cols-[140px_1fr_auto]">
              <input aria-label="Lane name" className={inputCls} placeholder="backend" value={name} onChange={(e) => setName(e.target.value)} />
              <input
                aria-label="Lane folders"
                className={inputCls}
                placeholder={folders.length ? folders.slice(0, 2).join(", ") : "engine, api"}
                value={paths}
                onChange={(e) => setPaths(e.target.value)}
                list="lane-folders"
              />
              <datalist id="lane-folders">
                {folders.map((f) => (
                  <option key={f} value={f} />
                ))}
              </datalist>
              <button type="button" onClick={add} className="rounded-md border border-line px-3 text-sm hover:border-cyan">
                Add
              </button>
            </div>
            {problem && <p className="mt-1 text-xs text-red">{problem}</p>}
          </>
        ) : (
          <p className="mt-1 text-xs text-muted">Pick a project folder first.</p>
        )}
      </div>
    </div>
  );
}
