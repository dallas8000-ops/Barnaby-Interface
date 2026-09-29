import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

// Open modals, innermost last: only the top one handles Esc and Tab (the task board's
// New task dialog opens on top of the Tasks dialog on narrow screens).
const openModals: symbol[] = [];

/** Accessible modal: focus moves in, Tab is trapped, Esc closes, focus returns. */
export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId(); // unique: a dialog can open on top of another
  // Callers pass a fresh onClose each render. Depending on it re-ran the effect
  // below on every keystroke, which yanked focus back to the first button.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const me = Symbol("modal");
    openModals.push(me);
    const previous = document.activeElement as HTMLElement | null;
    const focusables = () =>
      Array.from(panel.current?.querySelectorAll<HTMLElement>("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])") ?? []).filter(
        (el) => !el.hasAttribute("disabled"),
      );
    focusables()[0]?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (openModals[openModals.length - 1] !== me) return;
      if (e.key === "Escape") {
        e.stopPropagation();
        closeRef.current();
      } else if (e.key === "Tab") {
        const items = focusables();
        if (!items.length) return;
        const first = items[0]!;
        const last = items[items.length - 1]!;
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      openModals.splice(openModals.indexOf(me), 1);
      previous?.focus?.();
    };
  }, []); // mount/unmount only: focus in, trap, restore focus on close

  // Portal to <body>: a dialog opened from inside another component's <form>
  // (the template picker lives in the composer) must not nest forms. A nested
  // submit triggered a native GET that reloaded the app without its engine URL.
  return createPortal(
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-bg/70 p-4 backdrop-blur-[2px]" onMouseDown={() => closeRef.current()}>
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(e) => e.stopPropagation()}
        className={`scroll-quiet max-h-[88vh] w-full ${wide ? "max-w-2xl" : "max-w-lg"} overflow-y-auto rounded-xl border border-line bg-panel shadow-2xl`}
      >
        <header className="sticky top-0 flex items-center justify-between border-b border-line bg-panel px-5 py-3">
          <h2 id={titleId} className="font-strip text-lg font-semibold">
            {title}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-muted hover:text-ink">
            <X size={18} aria-hidden />
          </button>
        </header>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
