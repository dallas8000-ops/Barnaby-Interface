import { ShieldAlert } from "lucide-react";
import { send } from "../lib/client";
import { useApp, type Approval } from "../store/app";
import { HoldButton } from "./HoldButton";

const RISK_COPY: Record<Approval["risk"], string> = {
  normal: "Needs your OK",
  high: "Changes things outside the sandbox",
  critical: "Destructive or hard to undo",
};

export function ApprovalCard({ approval }: { approval: Approval }) {
  const critical = approval.risk === "critical";
  const decided = approval.decided !== null;
  const tone = critical ? "border-red" : approval.risk === "high" ? "border-amber" : "border-cyan";

  return (
    <article
      className={`rounded-lg border-2 ${tone} bg-panel p-3 ${decided ? "opacity-60" : ""}`}
      data-testid="approval"
      data-risk={approval.risk}
      aria-label={`Approval request: ${approval.summary}`}
    >
      <header className="flex items-center gap-2">
        <ShieldAlert size={16} aria-hidden className={critical ? "text-red" : approval.risk === "high" ? "text-amber" : "text-cyan"} />
        <h3 className="font-strip text-[15px] font-semibold">{RISK_COPY[approval.risk]}</h3>
      </header>
      <p className="mt-1.5 text-sm text-ink">{approval.summary}</p>
      {approval.detail && approval.detail !== approval.summary && (
        <pre className="scroll-quiet mt-2 max-h-28 overflow-auto rounded-md bg-raised px-2.5 py-1.5 font-mono text-xs text-ink whitespace-pre-wrap break-all">
          {approval.detail}
        </pre>
      )}

      {decided ? (
        <p className="mt-2 text-sm text-muted">{approval.decided ? "Approved" : "Declined"}</p>
      ) : (
        <>
          {!approval.grantable && (
            <p className="mt-2 text-xs text-amber">
              This can't be approved as one exact command. Decline it and ask for it another way.
            </p>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {critical ? (
              <HoldButton disabled={!approval.grantable} onConfirm={() => send({ type: "approve", request_id: approval.requestId })}>
                Hold to approve
              </HoldButton>
            ) : (
              <button
                type="button"
                disabled={!approval.grantable}
                onClick={() => send({ type: "approve", request_id: approval.requestId })}
                className="rounded-md bg-cyan px-3 py-1.5 text-sm font-medium text-bg hover:brightness-110 disabled:opacity-40"
              >
                Approve
              </button>
            )}
            <button
              type="button"
              onClick={() => send({ type: "deny", request_id: approval.requestId })}
              className="rounded-md border border-line px-3 py-1.5 text-sm text-ink hover:border-ink"
            >
              Decline
            </button>
            {approval.voiceApprovable && <span className="text-xs text-muted">or say “yes” / “no”</span>}
            {critical && <span className="text-xs text-muted">Voice approval is off for this one.</span>}
          </div>
        </>
      )}
    </article>
  );
}

export function Approvals() {
  const approvals = useApp((s) => s.approvals);
  if (!approvals.length) return null;
  return (
    <section aria-label="Pending approvals" className="flex flex-col gap-2">
      {approvals.map((a) => (
        <ApprovalCard key={a.requestId} approval={a} />
      ))}
    </section>
  );
}
