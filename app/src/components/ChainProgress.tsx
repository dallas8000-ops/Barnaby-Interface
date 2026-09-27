import { Check, Square } from "lucide-react";
import { send } from "../lib/client";
import { useApp } from "../store/app";

/**
 * A multi-step request in flight ("run the tests, then fix what fails", or a
 * template). Steps run as separate turns in one session. The chain pauses on
 * approval cards, and stops on a failure, a decline, Stop, or a new instruction.
 */
export function ChainProgress() {
  const chain = useApp((s) => s.chain);
  const awaiting = useApp((s) => s.engineState === "awaiting_approval");
  if (!chain) return null;

  const total = chain.steps.length;
  return (
    <section
      aria-label="Multi-step request"
      data-testid="chain"
      className="mx-auto w-full max-w-3xl rounded-lg border border-line bg-panel px-3.5 py-2.5"
    >
      <div className="flex items-center justify-between gap-3">
        <p className="min-w-0 text-sm" role="status" aria-live="polite">
          <span className="font-strip font-semibold text-ink">
            Step {chain.index + 1} of {total}
          </span>
          <span className="text-muted">
            {" · "}
            {chain.name ?? "Your steps"}
            {awaiting ? " · waiting for your OK" : ""}
          </span>
        </p>
        <button
          type="button"
          onClick={() => send({ type: "cancel" })}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-line px-2.5 py-1 text-xs text-muted hover:border-red hover:text-red"
        >
          <Square size={11} aria-hidden /> Stop steps
        </button>
      </div>
      <ol className="mt-2 flex flex-col gap-1">
        {chain.steps.map((step, i) => {
          const state = i < chain.index ? "done" : i === chain.index ? "current" : "pending";
          return (
            <li key={i} className="flex items-start gap-2 text-sm" data-state={state}>
              <span
                aria-hidden
                className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border text-[10px] ${
                  state === "done"
                    ? "border-green bg-green text-bg"
                    : state === "current"
                      ? "border-cyan text-cyan animate-pulse"
                      : "border-line text-muted"
                }`}
              >
                {state === "done" ? <Check size={10} strokeWidth={3} /> : i + 1}
              </span>
              <span className={state === "pending" ? "text-muted" : "text-ink"}>
                <span className="sr-only">{state === "done" ? "Done: " : state === "current" ? "Running: " : "Next: "}</span>
                {step}
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
