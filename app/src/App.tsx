import { useEffect, useState } from "react";
import { client } from "./lib/client";
import { onShellEvents } from "./lib/platform";
import { useApp } from "./store/app";
import { ActivityPanel } from "./components/ActivityPanel";
import { Composer } from "./components/Composer";
import { Conversation } from "./components/Conversation";
import { ScopeConsole } from "./components/ScopeConsole";
import { SettingsDialog } from "./components/SettingsDialog";
import { Sidebar } from "./components/Sidebar";
import { Toasts } from "./components/Toasts";
import { VoiceProfileDialog } from "./components/VoiceProfileDialog";

export function App() {
  const [dialog, setDialog] = useState<"settings" | "profile" | null>(null);

  useEffect(() => {
    const offEvent = client.onEvent((e) => useApp.getState().apply(e));
    const offStatus = client.onStatus((c) => useApp.getState().setConnection(c));
    client.start();
    let offShell: (() => void) | undefined;
    let disposed = false;
    void onShellEvents({
      ready: () => client.kick(),
      error: (message) => useApp.getState().apply({ type: "error", code: "engine", message }),
      shortcutUnavailable: () =>
        useApp.getState().apply({
          type: "notice",
          message: "Ctrl+Alt+Space is taken by another app, so push-to-talk works only while Barnaby is focused.",
        }),
    }).then((off) => (disposed ? off() : (offShell = off)));
    return () => {
      disposed = true;
      offEvent();
      offStatus();
      offShell?.();
      client.stop();
    };
  }, []);

  return (
    <div className="grid h-full grid-cols-1 md:grid-cols-[240px_1fr] xl:grid-cols-[240px_1fr_340px]">
      <div className="hidden md:block">
        <Sidebar onSettings={() => setDialog("settings")} onProfile={() => setDialog("profile")} />
      </div>
      <main className="flex min-h-0 min-w-0 flex-col">
        <Conversation />
        <div className="flex flex-col items-center gap-4 border-t border-line bg-bg px-4 pt-5 pb-4">
          <ScopeConsole />
          <Composer />
        </div>
      </main>
      <div className="hidden min-h-0 xl:block">
        <ActivityPanel />
      </div>
      {dialog === "settings" && <SettingsDialog onClose={() => setDialog(null)} />}
      {dialog === "profile" && <VoiceProfileDialog onClose={() => setDialog(null)} />}
      <Toasts />
    </div>
  );
}
