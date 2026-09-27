import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Press-and-hold confirmation. A click (or a stray Enter) can't trigger it;
 * the user must hold for `ms`. Keyboard: hold Space/Enter. Releasing early
 * resets. This is the only way to approve a CRITICAL action.
 */
export function HoldButton({ ms = 1200, onConfirm, children, disabled }: {
  ms?: number;
  onConfirm: () => void;
  children: ReactNode;
  disabled?: boolean;
}) {
  const [progress, setProgress] = useState(0);
  const start = useRef<number | null>(null);
  const raf = useRef(0);
  const fired = useRef(false);

  const stop = () => {
    cancelAnimationFrame(raf.current);
    start.current = null;
    if (!fired.current) setProgress(0);
  };
  const tick = (t: number) => {
    if (start.current === null) start.current = t;
    const p = Math.min(1, (t - start.current) / ms);
    setProgress(p);
    if (p >= 1) {
      fired.current = true;
      onConfirm();
      return;
    }
    raf.current = requestAnimationFrame(tick);
  };
  const begin = () => {
    if (disabled || fired.current || start.current !== null) return;
    raf.current = requestAnimationFrame(tick);
  };

  useEffect(() => () => cancelAnimationFrame(raf.current), []);

  return (
    <button
      type="button"
      disabled={disabled}
      aria-label={`${typeof children === "string" ? children : "Confirm"}. Press and hold to confirm.`}
      onPointerDown={begin}
      onPointerUp={stop}
      onPointerLeave={stop}
      onPointerCancel={stop}
      onKeyDown={(e) => {
        if ((e.key === " " || e.key === "Enter") && !e.repeat) {
          e.preventDefault();
          begin();
        }
      }}
      onKeyUp={stop}
      onClick={(e) => e.preventDefault()}
      className="relative overflow-hidden rounded-md border border-red px-3 py-1.5 text-sm font-medium text-red disabled:opacity-50"
    >
      <span
        aria-hidden
        className="absolute inset-y-0 left-0 bg-red/25"
        style={{ width: `${progress * 100}%` }}
      />
      <span className="relative">{children}</span>
    </button>
  );
}
