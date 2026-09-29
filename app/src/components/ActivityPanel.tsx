import { ChevronDown } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { send } from "../lib/client";
import { useApp, type ToolItem } from "../store/app";
import { Approvals } from "./Approvals";
import { TaskBoard } from "./TaskBoard";

const VERB: Record<string, string> = {
  Bash: "Command",
  Read: "Read",
  Edit: "Edit",
  MultiEdit: "Edit",
  Write: "Write",
  Grep: "Search",
  Glob: "Find files",
  WebFetch: "Fetch",
  WebSearch: "Web search",
  Task: "Subagent",
  Agent: "Subagent",
  TodoWrite: "Plan",
  NotebookEdit: "Notebook",
};

function edgeClass(ok: boolean | null) {
  if (ok === null) return "bg-cyan animate-pulse";
  return ok ? "bg-green" : "bg-red";
}

function Strip({ tool }: { tool: ToolItem }) {
  const remote = useApp((s) => s.role === "remote"); // editors open on the PC, not the tablet
  const [open, setOpen] = useState(false);
  const isCommand = tool.name === "Bash";
  const detail = isCommand ? tool.summary : tool.path ?? tool.summary.replace(/^\S+\s*/, "");
  const status = tool.ok === null ? "running" : tool.ok ? "done" : "failed";

  return (
    <li className="flex overflow-hidden rounded-md border border-line bg-panel" data-testid="strip" data-status={status}>
      <span className={`w-1.5 shrink-0 ${edgeClass(tool.ok)}`} aria-hidden />
      <div className="min-w-0 flex-1 px-2.5 py-1.5">
        <div className="flex items-center gap-2">
          <span className="font-strip text-[13px] font-semibold text-muted">{VERB[tool.name] ?? tool.name}</span>
          <span className="sr-only">{status}</span>
          {tool.preview && (
            <button
              type="button"
              aria-expanded={open}
              aria-label={open ? "Hide output" : "Show output"}
              onClick={() => setOpen((v) => !v)}
              className="ml-auto text-muted hover:text-ink"
            >
              <ChevronDown size={14} aria-hidden className={open ? "rotate-180" : ""} />
            </button>
          )}
        </div>
        <p className={`truncate text-[13px] text-ink ${isCommand || tool.path ? "font-mono" : ""}`} title={detail}>
          {detail}
        </p>
        {tool.path && !remote && (
          <div className="mt-1 flex gap-1.5">
            {(["vscode", "cursor"] as const).map((editor) => (
              <button
                key={editor}
                type="button"
                onClick={() => send({ type: "open_in_editor", editor, path: tool.path! })}
                className="rounded border border-line px-1.5 text-[11px] text-muted hover:border-cyan hover:text-cyan"
              >
                Open in {editor === "vscode" ? "VS Code" : "Cursor"}
              </button>
            ))}
          </div>
        )}
        {open && tool.preview && (
          <pre className="scroll-quiet mt-1.5 max-h-48 overflow-auto rounded bg-raised px-2 py-1 font-mono text-[11.5px] text-ink whitespace-pre-wrap break-all">
            {tool.preview}
          </pre>
        )}
      </div>
    </li>
  );
}

export function ActivityPanel() {
  const [tab, setTab] = useState<"activity" | "tasks">("activity");
  const attention = useApp((s) => s.taskAttention);
  const seen = useApp((s) => s.seenTasks);
  const active = useApp((s) => s.tasks.filter((t) => t.status === "running" || t.status === "needs_approval").length);

  const panel = useRef<HTMLElement>(null);
  const tabs = ["activity", "tasks"] as const;

  const pick = (t: "activity" | "tasks") => {
    setTab(t);
    if (t === "tasks") seen();
  };
  const onTabKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    const i = tabs.indexOf(tab);
    const next = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : (i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    pick(tabs[next]!);
    document.getElementById(`side-tab-${tabs[next]}`)?.focus();
  };
  // Keep the badge clear while the board is open and on screen. Below the xl breakpoint this
  // panel stays mounted but hidden, and the sidebar's Tasks button shows the badge instead.
  useEffect(() => {
    if (tab === "tasks" && attention && panel.current?.offsetParent) seen();
  }, [tab, attention, seen]);

  return (
    <aside ref={panel} className="app-activity flex h-full flex-col gap-3 border-l border-line bg-bg p-3" aria-label="Activity">
      <Approvals />
      <div role="tablist" aria-label="Side panel" className="flex gap-1 border-b border-line">
        {tabs.map((t) => (
          <button
            key={t}
            id={`side-tab-${t}`}
            type="button"
            role="tab"
            aria-selected={tab === t}
            aria-controls={`side-panel-${t}`}
            tabIndex={tab === t ? 0 : -1}
            onKeyDown={onTabKey}
            onClick={() => pick(t)}
            className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-2 pb-1.5 font-strip text-base font-semibold ${tab === t ? "border-cyan text-ink" : "border-transparent text-muted hover:text-ink"}`}
          >
            {t === "activity" ? "Activity" : "Tasks"}
            {t === "tasks" && active > 0 && <span className="text-xs font-normal text-muted">{active}</span>}
            {t === "tasks" && attention > 0 && tab !== "tasks" && (
              <span className="rounded-full bg-amber px-1.5 text-[11px] leading-4 text-bg" aria-label={`${attention} new`}>
                {attention}
              </span>
            )}
          </button>
        ))}
      </div>
      <div id={`side-panel-${tab}`} role="tabpanel" aria-labelledby={`side-tab-${tab}`} className="flex min-h-0 flex-1 flex-col gap-3">
        {tab === "activity" ? <ActivityList /> : <TaskBoard />}
      </div>
    </aside>
  );
}

function ActivityList() {
  const turns = useApp((s) => s.turns);
  const [scope, setScope] = useState<"latest" | "all">("latest");
  const withTools = turns.filter((t) => t.tools.length);
  const shown = scope === "latest" ? withTools.slice(-1) : withTools;

  return (
    <>
      {withTools.length > 1 && (
        <div role="group" aria-label="Show activity for" className="flex self-end rounded-md border border-line text-xs">
          {(["latest", "all"] as const).map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={scope === v}
              onClick={() => setScope(v)}
              className={`px-2 py-0.5 ${scope === v ? "bg-raised text-ink" : "text-muted"}`}
            >
              {v === "latest" ? "This turn" : "Session"}
            </button>
          ))}
        </div>
      )}
      <div className="scroll-quiet -mr-1 flex-1 overflow-y-auto pr-1">
        {shown.length === 0 ? (
          <p className="text-sm text-muted">Commands, file edits and searches show up here as Claude works.</p>
        ) : (
          shown.map((turn) => (
            <section key={turn.id} className="mb-3">
              {scope === "all" && <h3 className="mb-1 truncate text-xs text-muted">{turn.system ? "After approval" : turn.text}</h3>}
              <ol className="flex flex-col gap-1.5">
                {turn.tools.map((t) => (
                  <Strip key={t.id} tool={t} />
                ))}
              </ol>
            </section>
          ))
        )}
      </div>
    </>
  );
}
