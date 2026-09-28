import { FolderOpen } from "lucide-react";
import { useEffect, useRef } from "react";
import { send } from "../lib/client";
import { pickFolder } from "../lib/platform";
import { useApp, type Turn } from "../store/app";
import { ENGINE_LABEL } from "./EngineSwitch";
import { Markdown } from "./Markdown";

const folderName = (p: string) => p.split(/[\\/]/).filter(Boolean).pop() ?? p;

function StatusNote({ turn, pending }: { turn: Turn; pending: boolean }) {
  const failed = turn.tools.filter((t) => t.ok === false).length;
  const bits: string[] = [];
  if (turn.engine) bits.push(ENGINE_LABEL[turn.engine]);
  if (turn.tools.length) bits.push(`${turn.tools.length} action${turn.tools.length > 1 ? "s" : ""}${failed ? `, ${failed} failed` : ""}`);
  if (turn.durationMs) bits.push(`${(turn.durationMs / 1000).toFixed(1)}s`);
  if (turn.costUsd) bits.push(`$${turn.costUsd.toFixed(3)}`);
  const label =
    turn.status === "cancelled" ? "Stopped" : turn.status === "error" ? "Didn't finish" : turn.status === "needs_approval" ? (pending ? "Needs your OK" : "Asked for your OK") : null;
  if (!bits.length && !label) return null;
  return (
    <p className="mt-2 flex flex-wrap gap-x-3 text-xs text-muted">
      {label && <span className={turn.status === "error" ? "text-red" : turn.status === "needs_approval" && pending ? "text-amber" : ""}>{label}</span>}
      {bits.map((b) => (
        <span key={b}>{b}</span>
      ))}
    </p>
  );
}

export function Conversation() {
  const remote = useApp((s) => s.role === "remote");
  const turns = useApp((s) => s.turns);
  const hasPending = useApp((s) => s.approvals.length > 0);
  const workspace = useApp((s) => s.config?.workspace ?? "");
  const endRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const last = turns[turns.length - 1];

  // Follow new output only if the reader is already at the bottom.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 160) endRef.current?.scrollIntoView({ block: "end" });
  }, [turns.length, last?.assistant, last?.tools.length]);

  if (!workspace) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
        <p className="max-w-sm text-muted">Choose the project Barnaby should work in. It runs Claude Code inside that folder.</p>
        <button
          type="button"
          onClick={async () => {
            const folder = await pickFolder();
            if (folder) send({ type: "set_config", values: { workspace: folder } });
          }}
          className="inline-flex items-center gap-2 rounded-md bg-cyan px-4 py-2 font-medium text-bg hover:brightness-110"
        >
          <FolderOpen size={16} aria-hidden /> Choose project folder
        </button>
      </div>
    );
  }

  if (!turns.length) {
    return (
      <div className="flex flex-1 items-center justify-center px-6">
        <p className="max-w-md text-center text-muted">
          {remote ? "Hold the circle" : "Hold Space"} and say what you want done in{" "}
          <span className="text-ink">{folderName(workspace)}</span>. For
          example: “run the tests and fix whatever fails”.
        </p>
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="scroll-quiet flex-1 overflow-y-auto px-6 pt-6 pb-4" data-testid="conversation">
      <ol className="mx-auto flex max-w-3xl flex-col gap-6">
        {turns.map((turn, i) => (
          <li key={turn.id} className="flex flex-col gap-2">
            {turn.system ? (
              <p className="text-xs text-muted">You answered the approval request.</p>
            ) : (
              <p className="self-end max-w-[85%] rounded-lg rounded-br-sm bg-raised px-3.5 py-2 text-ink">{turn.text}</p>
            )}
            {(turn.assistant || turn.status !== "ok") && (
              <div className="border-l-2 border-cyan/60 pl-4">
                {turn.assistant ? (
                  <Markdown text={turn.assistant} />
                ) : turn.status === "running" ? (
                  <p className="text-muted">Working…</p>
                ) : null}
                <StatusNote turn={turn} pending={hasPending && i === turns.length - 1} />
              </div>
            )}
          </li>
        ))}
      </ol>
      <div ref={endRef} />
    </div>
  );
}
