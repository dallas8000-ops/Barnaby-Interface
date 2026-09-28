import { useEffect, useState } from "react";
import { client } from "./lib/client";
import { onShellEvents } from "./lib/platform";
import { forgetDevice, isRemoteMode } from "./lib/remote";
import { getTabletSpeech } from "./lib/tabletSpeech";
import { useApp } from "./store/app";
import { ActivityPanel } from "./components/ActivityPanel";
import { ChainProgress } from "./components/ChainProgress";
import { Composer } from "./components/Composer";
import { Conversation } from "./components/Conversation";
import { ScopeConsole } from "./components/ScopeConsole";
import { SettingsDialog } from "./components/SettingsDialog";
import { Sidebar } from "./components/Sidebar";
import { Toasts } from "./components/Toasts";
import { VoiceProfileDialog } from "./components/VoiceProfileDialog";
import { PairScreen } from "./components/PairScreen";

export function App() {
  const [dialog, setDialog] = useState<"settings" | "profile" | null>(null);
  const remote = isRemoteMode();
  const connection = useApp((s) => s.connection);

  useEffect(() => {
    const speech = remote ? getTabletSpeech((speaking) => useApp.setState({ tabletSpeaking: speaking })) : null;
    const offEvent = client.onEvent((e) => {
      if (speech && e.type === "say") speech.say(e.text);
      else if (speech && e.type === "say_stop") speech.stop();
      useApp.getState().apply(e);
    });
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

  useEffect(() => {
    // The PC removed this tablet, or its token is no longer valid: forget it so the
    // pairing screen shows instead of retrying a dead token.
    if (remote && connection === "unauthorized") forgetDevice();
  }, [remote, connection]);

  if (remote && (connection === "unpaired" || connection === "unauthorized")) {
    return (
      <>
        <PairScreen onRetry={() => window.location.reload()} />
        <Toasts />
      </>
    );
  }

  return (
    <div className="app-shell grid h-full grid-cols-1 md:grid-cols-[240px_1fr] xl:grid-cols-[240px_1fr_340px]">
      <div className="hidden md:block">
        <Sidebar onSettings={() => setDialog("settings")} onProfile={() => setDialog("profile")} />
      </div>
      <main className="app-main flex min-h-0 min-w-0 flex-col">
        <Conversation />
        <div className="app-console flex flex-col items-center gap-4 border-t border-line bg-bg px-4 pt-5 pb-4">
          <ChainProgress />
          <ScopeConsole size={remote ? 240 : undefined} />
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
