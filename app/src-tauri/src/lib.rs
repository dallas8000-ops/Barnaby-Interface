mod engine;

use engine::{Engine, EngineInfo};
use serde::Serialize;
use tauri::{Emitter, Manager, RunEvent, State};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

#[derive(Clone, Serialize)]
struct Ptt {
    pressed: bool,
}

/// OS-wide push-to-talk. Ctrl+Alt+Space rarely collides with editor bindings
/// (VS Code and Cursor use Ctrl+Space for suggestions, not Ctrl+Alt+Space).
fn ptt_shortcut() -> Shortcut {
    Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::Space)
}

/// The only way the UI learns the engine's port and secret. Returns None
/// until the engine has announced readiness; the UI retries with backoff.
#[tauri::command]
fn engine_info(engine: State<'_, Engine>) -> Option<EngineInfo> {
    engine.info()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let engine = Engine::default();

    let app = tauri::Builder::default()
        // Must be first: a second launch focuses the running window instead of
        // spawning a second engine that would fight over the microphone.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if shortcut == &ptt_shortcut() {
                        let pressed = event.state() == ShortcutState::Pressed;
                        let _ = app.emit("ptt", Ptt { pressed });
                    }
                })
                .build(),
        )
        .manage(engine.clone())
        .invoke_handler(tauri::generate_handler![engine_info])
        .setup(move |app| {
            if let Err(e) = app.global_shortcut().register(ptt_shortcut()) {
                // Another app owns the chord; in-window Space still works.
                log::warn!("global push-to-talk unavailable: {e}");
                let _ = app.emit("shortcut-unavailable", e.to_string());
            }
            engine.start(app.handle().clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Barnaby");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            handle.state::<Engine>().shutdown();
        }
    });
}
