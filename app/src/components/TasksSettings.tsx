import { X } from "lucide-react";
import { useState } from "react";
import { send } from "../lib/client";
import type { Config } from "../lib/protocol";
import { useApp } from "../store/app";

const inputCls = "w-full rounded-md border border-line bg-raised px-2.5 py-1.5 text-sm text-ink focus:border-cyan focus:outline-none";
const NAME_OK = /^[A-Za-z0-9 _-]{1,32}$/;

/** Settings → Tasks (PC only): how many run at once, and named folder lanes for this project. */
export function TasksSettings({ config }: { config: Config }) {
  const folders = useApp((s) => s.taskFolders);
  const ws = config.workspace;
  const all = config.task_lanes ?? {};
  const lanes = all[ws] ?? {};
  const [name, setName] = useState("");
  const [paths, setPaths] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

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
