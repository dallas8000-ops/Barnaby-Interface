//! Engine supervisor: spawns the Python voice engine, owns its secret, and
//! restarts it if it crashes.
//!
//! Handoff contract (see engine/barnaby_engine/__main__.py):
//! * token -> child env `BARNABY_TOKEN` (never argv: argv is readable by any
//!   process via the process list);
//! * child prints one `{"event":"ready","port":N}` line on stdout;
//! * `BARNABY_WATCH_STDIN=1`: the child exits when our end of its stdin
//!   closes, so a crashed shell can never leave the microphone owned by an
//!   orphaned engine.

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter};

#[derive(Clone, Serialize)]
pub struct EngineInfo {
    pub port: u16,
    pub token: String,
}

#[derive(Default)]
struct Inner {
    info: Option<EngineInfo>,
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    shutting_down: bool,
    restarts: Vec<Instant>,
}

#[derive(Clone, Default)]
pub struct Engine(Arc<Mutex<Inner>>);

const MAX_RESTARTS: usize = 3;
const RESTART_WINDOW: Duration = Duration::from_secs(120);

fn new_token() -> String {
    let mut buf = [0u8; 32];
    getrandom::fill(&mut buf).expect("OS RNG unavailable");
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

/// How to launch the engine, most specific first:
/// 1. `BARNABY_PYTHON` (+ optional `BARNABY_ENGINE_DIR`): explicit interpreter.
/// 2. Release builds: the bundled sidecar next to the app executable.
/// 3. Debug builds: `engine/.venv` in this repository.
fn engine_command() -> Result<Command, String> {
    let repo_engine = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..").join("engine");

    if let Ok(py) = std::env::var("BARNABY_PYTHON") {
        let dir = std::env::var("BARNABY_ENGINE_DIR").map(PathBuf::from).unwrap_or(repo_engine);
        let mut cmd = Command::new(py);
        cmd.args(["-m", "barnaby_engine"]).current_dir(dir);
        return Ok(cmd);
    }

    if !cfg!(debug_assertions) {
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        let name = if cfg!(windows) { "barnaby-engine.exe" } else { "barnaby-engine" };
        let sidecar = exe.parent().ok_or("no exe dir")?.join(name);
        if sidecar.exists() {
            return Ok(Command::new(sidecar));
        }
        // No bundled sidecar: this is a local release build of the repo
        // (desktop-shortcut install), so fall through to the repo's venv.
    }

    let py = if cfg!(windows) {
        repo_engine.join(".venv").join("Scripts").join("python.exe")
    } else {
        repo_engine.join(".venv").join("bin").join("python")
    };
    if !py.exists() {
        return Err(format!(
            "Engine virtualenv not found at {}. Create it: cd engine && python -m venv .venv && .venv\\Scripts\\pip install -e .[voice]",
            py.display()
        ));
    }
    let mut cmd = Command::new(py);
    cmd.args(["-m", "barnaby_engine"]).current_dir(repo_engine);
    Ok(cmd)
}

impl Engine {
    pub fn info(&self) -> Option<EngineInfo> {
        self.0.lock().ok()?.info.clone()
    }

    pub fn start(&self, app: AppHandle) {
        let token = new_token();
        let mut cmd = match engine_command() {
            Ok(c) => c,
            Err(e) => {
                log::error!("{e}");
                let _ = app.emit("engine-error", e);
                return;
            }
        };
        cmd.env("BARNABY_TOKEN", &token)
            .env("BARNABY_WATCH_STDIN", "1")
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null()); // engine writes its own rotating log file
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                let msg = format!("Could not start the voice engine: {e}");
                log::error!("{msg}");
                let _ = app.emit("engine-error", msg);
                return;
            }
        };
        let stdout = child.stdout.take().expect("piped stdout");
        {
            let mut inner = self.0.lock().unwrap();
            inner.stdin = child.stdin.take();
            inner.child = Some(child);
            inner.info = None;
        }

        let engine = self.clone();
        thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines().map_while(Result::ok) {
                let Ok(v) = serde_json::from_str::<serde_json::Value>(&line) else { continue };
                if v.get("event").and_then(|e| e.as_str()) == Some("ready") {
                    if let Some(port) = v.get("port").and_then(|p| p.as_u64()).and_then(|p| u16::try_from(p).ok()) {
                        let info = EngineInfo { port, token: token.clone() };
                        engine.0.lock().unwrap().info = Some(info);
                        let _ = app.emit("engine-ready", ());
                    }
                }
            }
            // stdout closed: the engine exited.
            engine.on_exit(app);
        });
    }

    fn on_exit(&self, app: AppHandle) {
        let code = {
            let mut inner = self.0.lock().unwrap();
            inner.info = None;
            inner.stdin = None;
            let code = inner.child.as_mut().and_then(|c| c.wait().ok()).and_then(|s| s.code());
            inner.child = None;
            if inner.shutting_down {
                return;
            }
            let now = Instant::now();
            inner.restarts.retain(|t| now.duration_since(*t) < RESTART_WINDOW);
            if inner.restarts.len() >= MAX_RESTARTS {
                let msg = format!("The voice engine stopped repeatedly (exit code {code:?}). See engine.log in %APPDATA%\\Barnaby.");
                let _ = app.emit("engine-error", msg);
                return;
            }
            inner.restarts.push(now);
            code
        };
        log::warn!("engine exited ({code:?}); restarting");
        thread::sleep(Duration::from_millis(800));
        self.start(app);
    }

    pub fn shutdown(&self) {
        let mut inner = self.0.lock().unwrap();
        inner.shutting_down = true;
        inner.stdin = None; // closes the pipe: engine exits cleanly (releases mic, flushes db)
        if let Some(mut child) = inner.child.take() {
            let deadline = Instant::now() + Duration::from_secs(3);
            while Instant::now() < deadline {
                if let Ok(Some(_)) = child.try_wait() {
                    return;
                }
                thread::sleep(Duration::from_millis(50));
            }
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}
