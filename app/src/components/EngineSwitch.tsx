import { send } from "../lib/client";
import type { AgentEngine } from "../lib/protocol";
import { useApp } from "../store/app";

export const ENGINE_LABEL: Record<AgentEngine, string> = { claude: "Claude", cursor: "Cursor" };

/**
 * Which coding agent runs the next instruction. Switching starts a fresh
 * session (a Claude session can't be resumed by Cursor) and clears pending
 * approvals, which belong to the engine that raised them. Saying "Cursor, …"
 * or "have Claude …" flips this too.
 */
export function EngineSwitch() {
  const engines = useApp((s) => s.engines);
  const current = useApp((s) => s.config?.engine ?? "claude");
  const connection = useApp((s) => s.connection);
  if (engines.length < 2) return null;

  return (
    <div role="radiogroup" aria-label="Coding agent" className="flex items-center gap-2 text-xs">
      <span className="text-muted" id="engine-label">
        Agent
      </span>
      <div className="flex rounded-md border border-line p-0.5" aria-labelledby="engine-label">
        {engines.map((e) => {
          const active = e.name === current;
          return (
            <button
              key={e.name}
              type="button"
              role="radio"
              aria-checked={active}
              disabled={!e.available || connection !== "open"}
              title={e.available ? `Run instructions with ${ENGINE_LABEL[e.name]}` : e.reason}
              onClick={() => !active && send({ type: "set_config", values: { engine: e.name } })}
              className={`rounded px-2.5 py-0.5 font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                active ? "bg-raised text-ink" : "text-muted hover:text-ink"
              }`}
            >
              {ENGINE_LABEL[e.name]}
            </button>
          );
        })}
      </div>
    </div>
  );
}
