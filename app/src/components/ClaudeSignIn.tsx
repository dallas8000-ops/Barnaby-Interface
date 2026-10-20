import { useEffect } from "react";
import { send } from "../lib/client";
import { useApp } from "../store/app";

const LABEL: Record<string, string> = {
  unknown: "Checking…",
  signed_in: "Signed in",
  signed_out: "Not signed in",
  expired: "Sign-in expired",
  signing_in: "Waiting for you to finish in the browser…",
  failed: "Sign-in didn't finish",
  unavailable: "Claude Code isn't available",
};

/**
 * Claude Code keeps its own login; Barnaby only starts its sign-in and shows the result.
 * The login window and the browser are Claude Code's, so nothing typed there reaches Barnaby.
 */
export function ClaudeSignIn() {
  const { state, detail, method } = useApp((s) => s.claudeAuth);
  const connected = useApp((s) => s.connection === "open");
  const waiting = state === "signing_in";
  const good = state === "signed_in";
  const bad = state === "expired" || state === "failed" || state === "unavailable";

  useEffect(() => {
    send({ type: "claude_auth_check" });
  }, []);

  return (
    <div>
      <div className="flex items-center gap-2">
        <span
          role="status"
          className={`flex-1 text-sm ${good ? "text-green" : bad ? "text-red" : "text-muted"}`}
          data-testid="claude-auth-status"
        >
          {LABEL[state] ?? state}
          {good && method ? ` (${method})` : ""}
        </span>
        {waiting ? (
          <button
            type="button"
            onClick={() => send({ type: "claude_login_cancel" })}
            className="rounded-md border border-line px-3 py-1 text-sm hover:border-red hover:text-red"
          >
            Cancel
          </button>
        ) : (
          state !== "unavailable" && (
            <button
              type="button"
              disabled={!connected}
              onClick={() => send({ type: "claude_login" })}
              className={
                good
                  ? "rounded-md border border-line px-3 py-1 text-sm hover:border-cyan disabled:opacity-40"
                  : "rounded-md bg-cyan px-3 py-1 text-sm font-medium text-bg hover:brightness-110 disabled:opacity-40"
              }
            >
              {good || state === "expired" || state === "failed" ? "Sign in again" : "Sign in to Claude"}
            </button>
          )
        )}
      </div>
      {detail && (
        <p className={`mt-1 text-xs ${bad ? "text-red" : "text-muted"}`} role={bad ? "alert" : undefined}>
          {detail}
        </p>
      )}
    </div>
  );
}
