import { useEffect, useRef, useState } from "react";
import { send } from "../lib/client";
import type { AgentEngine, CheckSpec } from "../lib/protocol";
import { useApp } from "../store/app";
import { ChecksEditor } from "./ChecksEditor";
import { Modal } from "./Modal";

const ENGINE_LABEL: Record<AgentEngine, string> = { claude: "Claude", cursor: "Cursor", devin: "Devin" };
const ENGINE_NOTE: Record<AgentEngine, string> = {
  claude: "On this PC. Its file edits outside its folders come to you for approval first.",
  cursor: "On this PC. Stops if it edits outside its folders (Cursor can't ask first).",
  devin: "In Devin's cloud on its own copy of the repo; opens a pull request.",
};

const MAX_LANES = 8;

const chip = (on: boolean) =>
  `rounded-full border px-2.5 py-1 text-xs ${on ? "border-cyan bg-cyan/15 text-ink" : "border-line text-muted hover:text-ink"}`;

export function AssignTaskDialog({ onClose }: { onClose: () => void }) {
  const engines = useApp((s) => s.engines);
  const folders = useApp((s) => s.taskFolders);
  const saved = useApp((s) => s.savedLanes);
  const current = useApp((s) => s.config?.engine ?? "claude");
  const firstUsable = engines.find((e) => e.name === current && e.available)?.name ?? engines.find((e) => e.available)?.name ?? "claude";
  const [engine, setEngine] = useState<AgentEngine>(firstUsable);
  const [lanes, setLanes] = useState<string[]>([]);
  const [instruction, setInstruction] = useState("");
  const [title, setTitle] = useState("");
  const projectChecks = useApp((s) => s.projectChecks);
  const desktop = useApp((s) => s.role === "desktop");
  // null: use the project's checks (Settings); a list: this task's own (only the PC may choose).
  const [checks, setChecks] = useState<CheckSpec[] | null>(null);
  const tasks = useApp((s) => s.tasks);
  const toasts = useApp((s) => s.toasts);
  // Waiting for the engine: close once the new task shows up; stay open (keeping what was
  // typed) if it refuses, so the error toast can be fixed and retried.
  const pending = useRef<{ ids: Set<string>; toast: number } | null>(null);
  const [waiting, setWaiting] = useState(false);

  useEffect(() => {
    send({ type: "task_list" }); // fresh folder list: the project may have gained folders
  }, []);

  useEffect(() => {
    const p = pending.current;
    if (!p) return;
    if (tasks.some((t) => !p.ids.has(t.id))) {
      pending.current = null;
      onClose();
    } else if (toasts.some((t) => t.kind === "error" && t.id > p.toast)) {
      pending.current = null;
      setWaiting(false);
    }
  }, [tasks, toasts, onClose]);

  const toggle = (paths: string[]) =>
    setLanes((cur) => (paths.every((p) => cur.includes(p)) ? cur.filter((p) => !paths.includes(p)) : [...new Set([...cur, ...paths])]));
  const savedOn = (paths: string[]) => paths.length > 0 && paths.every((p) => lanes.includes(p));

  const tooMany = lanes.length > MAX_LANES;
  const submit = () => {
    const text = instruction.trim();
    if (!text || tooMany || waiting) return;
    const lastToast = toasts.reduce((m, t) => Math.max(m, t.id), 0);
    pending.current = { ids: new Set(tasks.map((t) => t.id)), toast: lastToast };
    const own = engine !== "devin" && checks !== null ? { checks } : {};
    if (send({ type: "task_create", engine, instruction: text, lanes, title: title.trim() || null, ...own })) setWaiting(true);
    else pending.current = null;
  };

  return (
    <Modal title="New task" onClose={onClose}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        className="flex flex-col gap-4"
      >
        <fieldset>
          <legend className="text-sm font-medium text-ink">Who does it</legend>
          <div className="mt-1.5 flex flex-col gap-1.5">
            {engines.map((e) => (
              <label key={e.name} className={`flex items-start gap-2 rounded-md border px-2.5 py-1.5 ${engine === e.name ? "border-cyan" : "border-line"} ${e.available ? "" : "opacity-50"}`}>
                <input
                  type="radio"
                  name="engine"
                  value={e.name}
                  checked={engine === e.name}
                  disabled={!e.available}
                  onChange={() => setEngine(e.name)}
                  className="mt-1 accent-[var(--c-cyan)]"
                />
                <span>
                  <span className="text-sm text-ink">{ENGINE_LABEL[e.name]}</span>
                  <span className="block text-xs text-muted">{e.available ? ENGINE_NOTE[e.name] : e.reason}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset>
          <legend className="text-sm font-medium text-ink">Folders it may change</legend>
          {Object.keys(saved).length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1.5" role="group" aria-label="Saved lanes">
              {Object.entries(saved).map(([name, paths]) => (
                <button key={name} type="button" aria-pressed={savedOn(paths)} onClick={() => toggle(paths)} className={chip(savedOn(paths))} title={paths.join(", ")}>
                  {name}
                </button>
              ))}
            </div>
          )}
          <div className="mt-1.5 flex flex-wrap gap-1.5" role="group" aria-label="Folders">
            {folders.map((f) => (
              <button key={f} type="button" aria-pressed={lanes.includes(f)} onClick={() => toggle([f])} className={`${chip(lanes.includes(f))} font-mono`}>
                {f}/
              </button>
            ))}
          </div>
          <p className={`mt-1.5 text-xs ${tooMany ? "text-red" : "text-muted"}`}>
            {tooMany
              ? `Pick at most ${MAX_LANES} folders.`
              : lanes.length
                ? `Only ${lanes.map((l) => `${l}/`).join(", ")}. Tasks that need the same folders wait their turn.`
                : "None picked: the whole project. It runs when no other task is working on this PC."}
          </p>
          <p className="mt-1 text-xs text-muted">
            Folders keep agents out of each other's way. They aren't a sandbox: test and build commands you've allowed can
            still write anywhere in the project.
          </p>
        </fieldset>

        <div>
          <label htmlFor="task-instruction" className="text-sm font-medium text-ink">
            What to do
          </label>
          <textarea
            id="task-instruction"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            rows={4}
            maxLength={20000}
            placeholder="Add a paginated GET /orders endpoint with tests"
            className="mt-1 w-full rounded-md border border-line bg-raised px-2.5 py-1.5 text-sm text-ink focus:border-cyan focus:outline-none"
          />
        </div>
        <fieldset>
          <legend className="text-sm font-medium text-ink">How Barnaby checks it</legend>
          {engine === "devin" ? (
            <p className="mt-1 text-xs text-muted">Devin's work is a pull request in its cloud, so Barnaby doesn't run checks on it (it's shown as unverified).</p>
          ) : checks === null ? (
            <p className="mt-1 text-xs text-muted">
              {projectChecks.length ? (
                <>
                  Done only when these pass: {projectChecks.map((c) => (c.dir ? `${c.run} (in ${c.dir}/)` : c.run)).join(", ")}.
                </>
              ) : (
                "No checks are set for this project, so the result will be unverified."
              )}{" "}
              {desktop && (
                <button type="button" onClick={() => setChecks([...projectChecks])} className="text-cyan underline-offset-2 hover:underline">
                  Change for this task
                </button>
              )}
            </p>
          ) : (
            <div className="mt-1.5">
              <ChecksEditor value={checks} onChange={setChecks} folders={folders} label="Checks for this task" />
              <button type="button" onClick={() => setChecks(null)} className="mt-1 text-xs text-muted hover:text-ink">
                Use the project's checks instead
              </button>
            </div>
          )}
        </fieldset>

        <div>
          <label htmlFor="task-title" className="text-sm font-medium text-ink">
            Short name <span className="font-normal text-muted">(optional)</span>
          </label>
          <input
            id="task-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={120}
            placeholder="orders endpoint"
            className="mt-1 w-full rounded-md border border-line bg-raised px-2.5 py-1.5 text-sm text-ink focus:border-cyan focus:outline-none"
          />
        </div>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-md border border-line px-3 py-1.5 text-sm text-ink hover:border-ink">
            Cancel
          </button>
          <button type="submit" disabled={!instruction.trim() || tooMany || waiting} className="rounded-md bg-cyan px-3 py-1.5 text-sm font-medium text-bg hover:brightness-110 disabled:opacity-40">
            {waiting ? "Starting…" : "Start task"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
