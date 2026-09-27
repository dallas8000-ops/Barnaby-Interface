import { X } from "lucide-react";
import { useEffect } from "react";
import { useApp, type Toast } from "../store/app";

function ToastItem({ toast }: { toast: Toast }) {
  const dismiss = useApp((s) => s.dismissToast);
  useEffect(() => {
    // Errors stay until dismissed: they usually carry a fix the user needs to read.
    if (toast.kind === "error") return;
    const t = setTimeout(() => dismiss(toast.id), 4500);
    return () => clearTimeout(t);
  }, [toast, dismiss]);

  const edge = toast.kind === "error" ? "border-l-red" : toast.kind === "learned" ? "border-l-green" : "border-l-cyan";
  return (
    <li role={toast.kind === "error" ? "alert" : "status"} className={`flex items-start gap-3 rounded-md border border-line border-l-4 ${edge} bg-panel px-3 py-2 shadow-lg`}>
      <p className="flex-1 text-sm text-ink">{toast.message}</p>
      <button type="button" onClick={() => dismiss(toast.id)} aria-label="Dismiss" className="text-muted hover:text-ink">
        <X size={14} aria-hidden />
      </button>
    </li>
  );
}

export function Toasts() {
  const toasts = useApp((s) => s.toasts);
  return (
    <ol className="pointer-events-none fixed right-4 bottom-4 z-50 flex w-[min(380px,calc(100vw-2rem))] flex-col gap-2 [&>li]:pointer-events-auto">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} />
      ))}
    </ol>
  );
}
