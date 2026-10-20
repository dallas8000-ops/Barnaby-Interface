import { Plus, X } from "lucide-react";
import { useState } from "react";
import type { CheckSpec } from "../lib/protocol";

const inputCls = "w-full rounded-md border border-line bg-raised px-2.5 py-1.5 text-sm text-ink focus:border-cyan focus:outline-none";
// Mirrors engine/barnaby_engine/checks.py: checks run without a shell.
const SHELL = /[&|<>^%!;$`'\r\n]/;
export const MAX_CHECKS = 8;

/** Why a check can't be added (null: fine). The engine has the final say (auto-approve list). */
export function checkProblem(run: string, dir: string): string | null {
  const cmd = run.trim();
  if (!cmd) return "Type a command, like npm test or pytest -q.";
  if (cmd.length > 300) return "That command is too long.";
  if (SHELL.test(cmd)) return "One plain command per check: no &&, |, >, ; or quotes other than \"…\". Checks run without a shell.";
  const d = dir.trim().replace(/\\/g, "/");
  if (d.startsWith("/") || /^[A-Za-z]:/.test(d) || d.split("/").includes("..")) return "The folder must be inside the project, like engine or app.";
  return null;
}

/** A list of check commands, each with the project folder it runs in. */
export function ChecksEditor({
  value,
  onChange,
  folders,
  label = "Checks",
}: {
  value: CheckSpec[];
  onChange: (next: CheckSpec[]) => void;
  folders: string[];
  label?: string;
}) {
  const [run, setRun] = useState("");
  const [dir, setDir] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const add = () => {
    const why = value.length >= MAX_CHECKS ? `At most ${MAX_CHECKS} checks.` : checkProblem(run, dir);
    if (why) return setProblem(why);
    setProblem(null);
    onChange([...value, { run: run.trim(), dir: dir.trim().replace(/\\/g, "/").replace(/^\.\/?/, "").replace(/\/+$/, "") }]);
    setRun("");
  };

  return (
    <div>
      <ul className="flex flex-col gap-1" aria-label={label}>
        {value.map((c, i) => (
          <li key={`${i}-${c.run}`} className="flex items-center gap-2 rounded-md border border-line px-2.5 py-1 text-sm">
            <code className="min-w-0 flex-1 truncate font-mono text-xs text-ink">{c.run}</code>
            <span className="shrink-0 font-mono text-xs text-muted">{c.dir ? `in ${c.dir}/` : "in project root"}</span>
            <button
              type="button"
              aria-label={`Remove check ${c.run}`}
              onClick={() => onChange(value.filter((_, j) => j !== i))}
              className="text-muted hover:text-red"
            >
              <X size={14} aria-hidden />
            </button>
          </li>
        ))}
      </ul>
      <div className="mt-1.5 grid gap-2 sm:grid-cols-[1fr_130px_auto]">
        <input
          aria-label="Check command"
          className={`${inputCls} font-mono`}
          placeholder="npm test"
          value={run}
          onChange={(e) => setRun(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <select aria-label="Check folder" className={inputCls} value={dir} onChange={(e) => setDir(e.target.value)}>
          <option value="">project root</option>
          {folders.map((f) => (
            <option key={f} value={f}>
              {f}/
            </option>
          ))}
        </select>
        <button type="button" onClick={add} aria-label="Add check" className="inline-flex items-center justify-center gap-1 rounded-md border border-line px-3 text-sm hover:border-cyan">
          <Plus size={13} aria-hidden /> Add
        </button>
      </div>
      {problem && <p className="mt-1 text-xs text-red">{problem}</p>}
    </div>
  );
}
