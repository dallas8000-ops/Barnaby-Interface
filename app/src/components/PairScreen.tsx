import { ShieldCheck, Smartphone } from "lucide-react";
import { useApp } from "../store/app";

/** Shown on a tablet that isn't paired (or was removed on the PC). */
export function PairScreen({ onRetry }: { onRetry: () => void }) {
  const pairError = useApp((s) => s.pairError);
  const rejected = useApp((s) => s.connection === "unauthorized");
  return (
    <main className="mx-auto flex h-full max-w-xl flex-col justify-center gap-6 p-8 text-ink" data-testid="pair-screen">
      <div className="flex items-center gap-3">
        <Smartphone size={32} className="text-cyan" aria-hidden />
        <h1 className="font-strip text-2xl font-semibold">Pair this tablet with Barnaby</h1>
      </div>
      {(pairError || rejected) && (
        <p role="alert" className="rounded-md border border-red px-4 py-3 text-red">
          {pairError ??
            "That didn't work: the pairing link expired or was already used, or this tablet was removed on the PC. Make a new link on the PC."}
        </p>
      )}
      <ol className="list-decimal space-y-3 pl-6 text-lg">
        <li>On your PC, open Barnaby, then <strong>Settings → Tablet access</strong>.</li>
        <li>
          Tap <strong>Pair a tablet</strong>.
        </li>
        <li>Scan the QR code with this tablet's camera. The link is good for 5 minutes and works once.</li>
      </ol>
      <div className="flex gap-3 rounded-md border border-line bg-panel p-4 text-sm text-muted">
        <ShieldCheck size={20} className="shrink-0 text-green" aria-hidden />
        <p>
          The first time, Chrome warns that the connection isn't private, because your PC made its own
          certificate. To trust it once, download{" "}
          <a className="text-cyan underline" href="/barnaby-ca.crt">
            Barnaby's certificate
          </a>{" "}
          and install it under Android Settings → Security and privacy → More security settings → Install from
          device storage → CA certificate. It's limited to your home network addresses.
        </p>
      </div>
      <button
        type="button"
        onClick={onRetry}
        className="self-start rounded-md bg-cyan px-5 py-3 text-base font-medium text-bg hover:brightness-110"
      >
        I've scanned it. Try again
      </button>
    </main>
  );
}
