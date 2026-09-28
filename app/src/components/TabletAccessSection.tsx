import qrcode from "qrcode-generator";
import { useEffect, useMemo, useState } from "react";
import { send } from "../lib/client";
import type { TabletDevice } from "../lib/protocol";
import { useApp } from "../store/app";

/** QR code as one SVG path built from the module matrix: no innerHTML, CSP-safe. */
export function QrCode({ text, size = 208 }: { text: string; size?: number }) {
  const { path, n } = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(text);
    qr.make();
    const count = qr.getModuleCount();
    let d = "";
    for (let r = 0; r < count; r++) for (let c = 0; c < count; c++) if (qr.isDark(r, c)) d += `M${c + 4},${r + 4}h1v1h-1z`;
    return { path: d, n: count + 8 };
  }, [text]);
  return (
    <svg role="img" aria-label="Pairing QR code" data-testid="pair-qr" width={size} height={size}
         viewBox={`0 0 ${n} ${n}`} shapeRendering="crispEdges" className="rounded-md bg-white">
      <path d={path} fill="#000" />
    </svg>
  );
}

function useCountdown(expiresAt: number | undefined): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!expiresAt) return;
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [expiresAt]);
  return expiresAt ? Math.max(0, Math.round(expiresAt - now)) : 0;
}

const ago = (t: number) => {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
};

function DeviceRow({ d }: { d: TabletDevice }) {
  return (
    <li className="flex items-center gap-3 py-1.5" data-testid="tablet-device">
      <span aria-hidden className={`h-2 w-2 rounded-full ${d.connected ? "bg-green" : "bg-line"}`} />
      <span className="min-w-0 flex-1 text-sm text-ink">
        {d.name}
        <span className="ml-2 text-xs text-muted">{d.connected ? "connected" : `last seen ${ago(d.last_seen)}`}</span>
      </span>
      <button
        type="button"
        onClick={() => send({ type: "tablet_revoke", device_id: d.id })}
        className="rounded-md border border-line px-2.5 py-0.5 text-xs text-muted hover:border-red hover:text-red"
        aria-label={`Remove ${d.name}`}
      >
        Remove
      </button>
    </li>
  );
}

/** PC-only: turn tablet access on, pair a tablet with a QR code, remove paired tablets. */
export function TabletAccessSection({ enabled, onToggle }: { enabled: boolean; onToggle: (v: boolean) => void }) {
  const tablet = useApp((s) => s.tablet);
  const pairing = useApp((s) => s.tabletPairing);
  const left = useCountdown(pairing?.expiresAt);
  const live = pairing && left > 0;
  const firstUrl = tablet?.urls[0];

  return (
    <div className="space-y-3" data-testid="tablet-access">
      <div className="flex items-center gap-3">
        <button
          id="tablet-access"
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-label="Tablet access"
          onClick={() => onToggle(!enabled)}
          className={`relative block h-6 w-11 shrink-0 rounded-full transition-colors ${enabled ? "bg-cyan" : "bg-line"}`}
        >
          <span className={`absolute top-0.5 left-0 h-5 w-5 rounded-full bg-panel shadow transition-transform ${enabled ? "translate-x-5.5" : "translate-x-0.5"}`} />
        </button>
        <span className="text-sm text-muted" role="status">
          {tablet?.error ? "" : tablet?.running ? `On, port ${tablet.port}` : enabled ? "Starting…" : "Off"}
        </span>
      </div>
      {tablet?.error && (
        <p role="alert" className="text-sm text-red">
          {tablet.error}
        </p>
      )}

      {tablet?.running && (
        <>
          <p className="text-sm text-muted">
            On the tablet, open{" "}
            {tablet.urls.length ? (
              tablet.urls.map((u, i) => (
                <span key={u}>
                  {i > 0 && " or "}
                  <code className="font-mono text-ink">{u}</code>
                </span>
              ))
            ) : (
              <span className="text-amber">no home-network address was found on this PC</span>
            )}
            .
          </p>

          {live ? (
            <div className="flex flex-wrap items-start gap-4 rounded-md border border-line bg-raised p-3">
              <QrCode text={pairing.url} />
              <div className="min-w-0 flex-1 space-y-2 text-sm">
                <p className="text-ink">Scan this with the tablet's camera.</p>
                <p className="text-muted">
                  Works once, for {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")} more.
                </p>
                <p className="break-all font-mono text-xs text-muted">{pairing.url}</p>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => send({ type: "tablet_pair_start" })}
              className="rounded-md bg-cyan px-3 py-1.5 text-sm font-medium text-bg hover:brightness-110"
            >
              Pair a tablet
            </button>
          )}

          {tablet.devices.length > 0 && (
            <ul aria-label="Paired tablets">{tablet.devices.map((d) => <DeviceRow key={d.id} d={d} />)}</ul>
          )}

          <details className="text-sm text-muted">
            <summary className="cursor-pointer text-ink">First time on the tablet: trust the connection</summary>
            <ol className="mt-2 list-decimal space-y-1 pl-5">
              <li>
                On the tablet, open{" "}
                <code className="font-mono text-ink">{firstUrl ? `${firstUrl}barnaby-ca.crt` : "…/barnaby-ca.crt"}</code>{" "}
                (Chrome warns once; tap Advanced, then Proceed).
              </li>
              <li>
                Android Settings → Security and privacy → More security settings → Install from device storage → CA
                certificate, and pick <em>barnaby-ca.crt</em>.
              </li>
              <li>Reopen the Barnaby address. The warning is gone.</li>
            </ol>
            <p className="mt-2">
              The certificate only covers home-network addresses and this PC's name, so it can't be used to
              impersonate real websites.
              {tablet.ca_fingerprint && (
                <>
                  {" "}Fingerprint (SHA-256): <code className="break-all font-mono text-xs">{tablet.ca_fingerprint}</code>
                </>
              )}
            </p>
          </details>
        </>
      )}
    </div>
  );
}
