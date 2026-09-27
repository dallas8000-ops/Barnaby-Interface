import { useState } from "react";
import { send } from "../lib/client";
import { useApp } from "../store/app";

// Mirrors the engine's check (vault.valid_key): printable ASCII, no spaces, 16-512 chars.
const KEY_RE = /^[\x21-\x7e]{16,512}$/;

/**
 * Write-only API key field. The key goes to the engine once, which stores it in
 * Windows Credential Manager. The UI never receives it back, only whether one
 * is saved, so there's nothing here to reveal.
 */
export function DevinKeyField() {
  const saved = useApp((s) => s.secrets.devin);
  const connected = useApp((s) => s.connection === "open");
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const trimmed = value.trim();
  const valid = KEY_RE.test(trimmed);
  const showInput = !saved || editing;

  const save = () => {
    if (!valid) return;
    send({ type: "set_secret", name: "devin", value: trimmed });
    setValue(""); // don't keep the key in UI state once it's sent
    setEditing(false);
  };

  return (
    <div>
      {showInput ? (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            e.stopPropagation();
            save();
          }}
        >
          <input
            id="devin-key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="apk_user_…"
            aria-describedby="devin-key-hint"
            className="w-full rounded-md border border-line bg-raised px-2.5 py-1.5 font-mono text-sm text-ink focus:border-cyan focus:outline-none"
          />
          <button
            type="submit"
            disabled={!valid || !connected}
            className="rounded-md bg-cyan px-3 text-sm font-medium text-bg hover:brightness-110 disabled:opacity-40"
          >
            Save
          </button>
          {saved && (
            <button type="button" onClick={() => { setEditing(false); setValue(""); }} className="rounded-md px-2 text-sm text-muted hover:text-ink">
              Cancel
            </button>
          )}
        </form>
      ) : (
        <div className="flex items-center gap-2">
          <span className="flex-1 text-sm text-green" role="status">
            Saved in Windows Credential Manager
          </span>
          <button type="button" onClick={() => setEditing(true)} className="rounded-md border border-line px-3 py-1 text-sm hover:border-cyan">
            Replace
          </button>
          <button
            type="button"
            disabled={!connected}
            onClick={() => send({ type: "delete_secret", name: "devin" })}
            className="rounded-md border border-line px-3 py-1 text-sm text-muted hover:border-red hover:text-red disabled:opacity-40"
          >
            Remove
          </button>
        </div>
      )}
      <p id="devin-key-hint" className="mt-1 text-xs text-muted">
        {value && !valid
          ? "That doesn't look like an API key (16 to 512 characters, no spaces)."
          : "Devin works in its own cloud machine on repositories connected in Devin, not on this PC's files. Personal keys start with apk_user_."}
      </p>
    </div>
  );
}
