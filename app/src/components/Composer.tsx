import { useEffect, useRef, useState } from "react";
import { send } from "../lib/client";
import { useApp } from "../store/app";
import { EngineSwitch } from "./EngineSwitch";
import { HandsFreeToggle } from "./HandsFreeToggle";
import { TemplatePicker } from "./TemplatePicker";

/**
 * Two modes. With a voice draft pending, it's an editable review box: fixing
 * a misheard word here is what teaches the voice profile. Otherwise it's a
 * plain typed-instruction field.
 */
export function Composer() {
  const draft = useApp((s) => s.draft);
  const setDraftText = useApp((s) => s.setDraftText);
  const connection = useApp((s) => s.connection);
  const hasWorkspace = useApp((s) => Boolean(s.config?.workspace));
  const [typed, setTyped] = useState("");
  const draftCommand = useApp((s) => s.draftCommand);
  const handsFreeOn = useApp((s) => s.continuousMode);
  const draftRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (draft) {
      draftRef.current?.focus();
      const len = draftRef.current?.value.length ?? 0;
      draftRef.current?.setSelectionRange(len, len);
    }
  }, [draft?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Hands-free "send it" / "scratch that": act on the draft as shown, including typed edits.
  useEffect(() => {
    const cmd = draftCommand;
    const current = useApp.getState().draft;
    if (!cmd || !current) return;
    if (cmd.action === "send") {
      const text = current.text.trim();
      if (text) send({ type: "text_submit", text, draft_id: current.id });
    } else {
      send({ type: "discard_draft", draft_id: current.id });
      useApp.setState({ draft: null });
    }
  }, [draftCommand?.seq]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!hasWorkspace) return null;

  if (draft) {
    const submit = () => {
      const text = draft.text.trim();
      if (text) send({ type: "text_submit", text, draft_id: draft.id });
    };
    const changed = draft.text.trim() !== draft.heard.trim();
    return (
      <form
        className="mx-auto w-full max-w-3xl rounded-lg border border-cyan/60 bg-panel p-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        data-testid="draft"
      >
        <label htmlFor="draft-text" className="mb-1.5 block text-sm text-muted">
          Check what I heard, fix any words, then send
        </label>
        <textarea
          id="draft-text"
          ref={draftRef}
          value={draft.text}
          onChange={(e) => setDraftText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            } else if (e.key === "Escape") {
              send({ type: "discard_draft", draft_id: draft.id });
              useApp.setState({ draft: null });
            }
          }}
          rows={Math.min(6, Math.max(2, Math.ceil(draft.text.length / 80)))}
          className="w-full resize-none bg-transparent text-[15px] text-ink outline-none"
        />
        <div className="mt-2 flex items-center justify-between gap-3">
          <p className="text-xs text-muted">
            {changed ? "Your fixes teach Barnaby how you talk." : "Enter sends. Esc discards."}
            {handsFreeOn && " Or say “send it” / “scratch that”."}
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => {
                send({ type: "discard_draft", draft_id: draft.id });
                useApp.setState({ draft: null });
              }}
              className="rounded-md px-3 py-1.5 text-sm text-muted hover:text-ink"
            >
              Discard
            </button>
            <button type="submit" className="rounded-md bg-cyan px-4 py-1.5 text-sm font-medium text-bg hover:brightness-110">
              Send
            </button>
          </div>
        </div>
      </form>
    );
  }

  return (
    <form
      className="mx-auto w-full max-w-3xl"
      onSubmit={(e) => {
        e.preventDefault();
        const text = typed.trim();
        if (text && send({ type: "text_submit", text })) setTyped("");
      }}
    >
      <label htmlFor="typed" className="sr-only">
        Type an instruction
      </label>
      <input
        id="typed"
        value={typed}
        onChange={(e) => setTyped(e.target.value)}
        disabled={connection !== "open"}
        placeholder="Or type an instruction and press Enter"
        autoComplete="off"
        className="w-full rounded-lg border border-line bg-panel px-3.5 py-2.5 text-[15px] text-ink placeholder:text-muted focus:border-cyan focus:outline-none disabled:opacity-60"
      />
      <div className="mt-2 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <EngineSwitch />
          <TemplatePicker />
          <HandsFreeToggle />
        </div>
        <p className="hidden text-xs text-muted lg:block">Chain steps with “then”. Switch agents with “Cursor, …” or “Devin, …”.</p>
      </div>
    </form>
  );
}
