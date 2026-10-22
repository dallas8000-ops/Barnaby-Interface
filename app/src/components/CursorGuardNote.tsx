import { Info } from "lucide-react";
import { openExternal } from "../lib/platform";

export const CURSOR_GUARD_DOCS =
  "https://github.com/dallas8000-ops/Barnaby-Interface#how-cursor-is-kept-inside-the-same-safety-model";

const DETAIL =
  "Cursor's headless mode can't ask before editing a file, so Barnaby watches each Cursor tool call as it starts. " +
  "If one reaches outside the project (or a task's folders), or runs a command beyond your rules, Barnaby stops " +
  "the turn at once and tells you whether that first write reached the disk. Claude asks before such an edit instead.";

/**
 * Cursor is fully supported, but its guard reacts as a tool starts rather than
 * before it, so say so wherever Cursor is about to work: under the agent switch
 * and on Cursor task cards.
 */
export function CursorGuardNote({ compact = false }: { compact?: boolean }) {
  return (
    <p className="flex items-start gap-1.5 text-xs text-muted" title={DETAIL} data-testid="cursor-guard-note">
      <Info size={13} aria-hidden className="mt-0.5 shrink-0" />
      <span>
        {compact
          ? "Cursor is stopped as an out-of-lane edit starts; that first write may land."
          : "Cursor's guard stops a turn as an out-of-project or out-of-lane tool starts, so that first write may land."}{" "}
        <button
          type="button"
          onClick={() => void openExternal(CURSOR_GUARD_DOCS)}
          className="underline decoration-dotted underline-offset-2 hover:text-ink"
        >
          How it works
        </button>
      </span>
    </p>
  );
}
