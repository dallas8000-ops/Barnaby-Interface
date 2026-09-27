import { ListChecks } from "lucide-react";
import { useState } from "react";
import { send } from "../lib/client";
import type { TemplateSummary } from "../lib/protocol";
import { useApp } from "../store/app";
import { Modal } from "./Modal";

/** Run a built-in template or a saved workflow. Voice does the same: "run the TDD template for the cart total". */
export function TemplatePicker() {
  const templates = useApp((s) => s.templates);
  const connection = useApp((s) => s.connection);
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<TemplateSummary | null>(null);
  const [context, setContext] = useState("");
  if (!templates.length) return null;

  const close = () => {
    setOpen(false);
    setPicked(null);
    setContext("");
  };
  const run = () => {
    if (!picked) return;
    send({ type: "run_template", template_id: picked.id, context: context.trim() || null });
    close();
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        disabled={connection !== "open"}
        className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-0.5 text-xs font-medium text-muted hover:border-cyan hover:text-ink disabled:opacity-40"
      >
        <ListChecks size={13} aria-hidden /> Templates
      </button>
      {open && (
        <Modal title={picked ? picked.name : "Run a template"} onClose={close}>
          {!picked ? (
            <ul className="flex flex-col gap-1.5" aria-label="Templates">
              {templates.map((t) => (
                <li key={t.id}>
                  <button
                    type="button"
                    onClick={() => setPicked(t)}
                    className="w-full rounded-md border border-line bg-raised px-3 py-2 text-left hover:border-cyan"
                  >
                    <span className="flex items-center gap-2">
                      <span className="text-sm font-medium text-ink">{t.name}</span>
                      <span className="text-xs text-muted">{t.step_count} steps</span>
                      {t.custom && <span className="rounded border border-line px-1 text-[10px] text-muted">Saved</span>}
                    </span>
                    {t.description && <span className="mt-0.5 block text-xs text-muted">{t.description}</span>}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                // React bubbles events through portals along the component tree: without
                // this, the composer's own submit handler also fired and sent any typed text.
                e.stopPropagation();
                run();
              }}
            >
              <p className="text-sm text-muted">
                {picked.step_count} steps, each run as its own turn. They pause for anything that needs your OK.
              </p>
              <label htmlFor="template-context" className="mt-3 block text-sm font-medium text-ink">
                What should it work on? <span className="font-normal text-muted">(optional)</span>
              </label>
              <input
                id="template-context"
                autoFocus
                value={context}
                onChange={(e) => setContext(e.target.value)}
                placeholder="e.g. the cart total calculation"
                className="mt-1 w-full rounded-md border border-line bg-raised px-2.5 py-1.5 text-sm text-ink focus:border-cyan focus:outline-none"
              />
              <div className="mt-4 flex justify-end gap-2">
                <button type="button" onClick={() => setPicked(null)} className="rounded-md px-3 py-1.5 text-sm text-muted hover:text-ink">
                  Back
                </button>
                <button type="submit" className="rounded-md bg-cyan px-4 py-1.5 text-sm font-medium text-bg hover:brightness-110">
                  Run {picked.name}
                </button>
              </div>
            </form>
          )}
        </Modal>
      )}
    </>
  );
}
