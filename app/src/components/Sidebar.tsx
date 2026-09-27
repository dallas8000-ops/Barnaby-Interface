import { AudioLines, FolderOpen, Plus, Settings } from "lucide-react";
import { send } from "../lib/client";
import { pickFolder } from "../lib/platform";
import { useApp } from "../store/app";
import { WakeWordStatus } from "./WakeWordStatus";
import { ContinuousModeToggle } from "./ContinuousModeToggle";
import { CommandChainIndicator } from "./CommandChainIndicator";

const folderName = (p: string) => p.split(/[\\/]/).filter(Boolean).pop() ?? p;

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

export function Sidebar({ onSettings, onProfile }: { onSettings: () => void; onProfile: () => void }) {
  const sessions = useApp((s) => s.sessions);
  const sessionId = useApp((s) => s.sessionId);
  const workspace = useApp((s) => s.config?.workspace ?? "");
  const connection = useApp((s) => s.connection);
  const stt = useApp((s) => s.components.stt);
  const tts = useApp((s) => s.components.tts);

  const inWorkspace = sessions.filter((s) => !workspace || s.workspace === workspace);

  return (
    <nav className="flex h-full flex-col border-r border-line bg-bg" aria-label="Sessions">
      <div className="px-4 pt-4 pb-3">
        <p className="font-strip text-[22px] font-semibold leading-none tracking-tight">Barnaby</p>
        <button
          type="button"
          onClick={async () => {
            const folder = await pickFolder();
            if (folder) send({ type: "set_config", values: { workspace: folder } });
          }}
          className="mt-3 flex w-full items-center gap-2 rounded-md border border-line bg-panel px-2.5 py-1.5 text-left text-sm hover:border-cyan"
          title={workspace || "Choose project folder"}
        >
          <FolderOpen size={15} aria-hidden className="shrink-0 text-muted" />
          <span className="truncate">{workspace ? folderName(workspace) : "Choose project"}</span>
        </button>
      </div>

      <div className="flex items-center justify-between px-4 pb-1">
        <h2 className="text-xs font-medium text-muted">Sessions</h2>
        <button
          type="button"
          onClick={() => send({ type: "new_session" })}
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted hover:bg-panel hover:text-ink"
        >
          <Plus size={13} aria-hidden /> New
        </button>
      </div>
      <ul className="scroll-quiet flex-1 overflow-y-auto px-2 pb-2">
        {inWorkspace.length === 0 && <li className="px-2 py-2 text-sm text-muted">Your conversations in this project appear here.</li>}
        {inWorkspace.map((s) => (
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
                {relativeTime(s.updated_at)}, {s.turn_count} turn{s.turn_count === 1 ? "" : "s"}
              </span>
            </button>
          </li>
        ))}
      </ul>

      <div className="border-t border-line px-3 py-2">
        <div className="mb-2 flex flex-col gap-2">
          <WakeWordStatus />
          <ContinuousModeToggle />
          <CommandChainIndicator />
        </div>
        <div className="flex gap-1">
          <button type="button" onClick={onProfile} className="inline-flex flex-1 items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-muted hover:bg-panel hover:text-ink">
            <AudioLines size={15} aria-hidden /> Voice profile
          </button>
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
