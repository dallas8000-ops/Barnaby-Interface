import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

/** Accessible modal: focus moves in, Tab is trapped, Esc closes, focus returns. */
export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const focusables = () =>
      Array.from(panel.current?.querySelectorAll<HTMLElement>("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])") ?? []).filter(
        (el) => !el.hasAttribute("disabled"),
      );
    focusables()[0]?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
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
      previous?.focus?.();
    };
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-bg/70 p-4 backdrop-blur-[2px]" onMouseDown={onClose}>
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        onMouseDown={(e) => e.stopPropagation()}
        className={`scroll-quiet max-h-[88vh] w-full ${wide ? "max-w-2xl" : "max-w-lg"} overflow-y-auto rounded-xl border border-line bg-panel shadow-2xl`}
      >
        <header className="sticky top-0 flex items-center justify-between border-b border-line bg-panel px-5 py-3">
          <h2 id="modal-title" className="font-strip text-lg font-semibold">
            {title}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-muted hover:text-ink">
            <X size={18} aria-hidden />
          </button>
        </header>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  );
}
