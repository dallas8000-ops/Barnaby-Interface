# Barnaby

A desktop voice interface for Claude Code and Cursor. Hold a key, say what you want done in your project, and Barnaby transcribes it locally, runs it through Claude Code in that folder, shows every command and file edit as it happens, and reads the outcome back to you. Anything that changes state outside the project, or can't be undone, waits for your approval.

```
 ┌──────────────── Tauri shell (Rust) ────────────────┐
 │ spawns + supervises engine · global hotkey · token │
 └───────┬──────────────────────────────┬─────────────┘
         │ env: BARNABY_TOKEN           │ engine_info (IPC)
         ▼                              ▼
 ┌──────────────── Voice engine (Python) ──┐   ws://127.0.0.1:<port>   ┌──── UI (React) ────┐
 │ mic → faster-whisper → lexicon/profile  │◄──── first-frame auth ────│ scope · transcript │
 │ → claude -p (stream-json, stdin prompt) │      + Origin allowlist   │ strips · approvals │
 │ → speech composer → Kokoro → speaker    │────── typed events ──────►│ sessions · profile │
 │ SQLite: sessions, turns, voice profile  │                           └────────────────────┘
 └─────────────────────────────────────────┘
```

All speech processing runs on your PC. The only network traffic is Claude Code itself.

## Setup (Windows)

Prerequisites: Python 3.12 or 3.11, Node 20+, Rust with the MSVC C++ build tools, and [Claude Code](https://docs.claude.com/en/docs/claude-code) signed in (run `claude` once in a terminal).

```powershell
cd "C:\Software Projects\Barnaby Interface"
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1   # venv + voice stack + UI deps; runs the engine tests
powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1     # launches the desktop app
```

To launch Barnaby from a **Desktop / Start menu icon** instead of PowerShell, build it once:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-shortcut.ps1
```

The icon runs a standalone build that uses this folder's engine, so keep the folder where it is, and re-run the script after updating Barnaby.

On first launch, pick your project folder. The speech models (about 250 MB) download once, and the sidebar shows **Hearing** and **Voice** as *Loading* until they're ready.

## Using it

| Action | How |
|---|---|
| Talk | Hold **Space** in the window, hold **Ctrl+Alt+Space** anywhere, or press and hold the scope |
| Interrupt Barnaby speaking | Start talking (barge-in), or **Quiet** |
| Stop a running task | **Stop**, or just give a new instruction; it cancels and resumes the same Claude session with your new words |
| Fix a misheard word | Edit the review box, then **Enter**. **Esc** discards |
| Approve an action | **Approve**, or say "yes" / "no". Critical actions need a 1.2-second press-and-hold and can't be voice-approved |
| Chain steps | Say them in order: "run the tests, **then** fix anything failing" |
| Run a template | "Run the TDD template for the cart total", or the **Templates** button |
| Open a touched file | **Open in VS Code** / **Open in Cursor** on any file strip |

## Agents: Claude and Cursor

The switch under the instruction box picks which coding agent runs the next instruction. You can also say it: "Cursor, fix the build", "have Claude run the tests", or just "use Cursor". Each session belongs to the agent that started it (a Claude session can't be resumed by Cursor), so switching starts a new session and clears pending approvals. Reopening an old session switches back to its agent.

Cursor needs its command-line agent, installed and signed in once:

```powershell
irm 'https://cursor.com/install?win32=true' | iex
agent login
```

**How Cursor is kept inside the same safety model.** These behaviours were verified against Cursor CLI 2026.09.26 on Windows:

- **No shell in the launch path.** Cursor installs as `agent.cmd`, a wrapper around `node.exe`, and only accepts the prompt as a command-line argument. Barnaby runs the bundled `node.exe` directly, with the prompt after `--`, so `cmd.exe` never re-parses a transcript.
- **Shell commands are allowlisted per turn.** Each turn, Barnaby writes a private Cursor config (`%APPDATA%\Barnaby\cursor`, via `CURSOR_CONFIG_DIR`) in allowlist mode. Your own Cursor settings are never touched. A command outside the list is refused by Cursor, reported in the stream, and shown as an approval card. Approving grants exactly that command using Cursor's exact form, `Shell(cmd:args)`. The plain `Shell(cmd)` form would also match `cmd --force`. Chained commands (`&&`, `;`, `|`) are refused unless every part is allowed.
- **File edits are not restricted by Cursor.** In headless mode, Cursor ignored both `Write()` scopes and explicit `deny` rules, and its `--sandbox` option isn't available on Windows. Barnaby's guard watches every Cursor tool call as it starts, and stops the turn immediately if Cursor reads or edits a file outside the project, or runs a command that goes beyond your rules.
- **The trade-off.** Because the guard reacts to Cursor's own "tool started" event, it can't promise the first out-of-project write never lands: it stops Cursor at that point, not before. Claude Code, by contrast, refuses such an edit before it happens. For work that touches files outside the project folder, use Claude.
- **Cost.** Cursor bills against your Cursor plan and reports no per-turn cost, so Cursor turns show none.

## Chained steps and templates

**Chaining.** Say several steps in one breath: "run the tests, then fix anything failing, and finally commit". Each step runs as its own turn in the same session, so later steps see what earlier ones did. A progress panel above the push-to-talk scope shows each step as done, running or next, with a **Stop steps** button. Chaining follows three rules:

- **It only splits on explicit sequencing words:** *then*, *and then*, *after that*, *finally*, and *next* after a comma. The *then* of a conditional ("if the tests fail then revert") doesn't split, and neither does anything inside `backticks`.
- **It pauses for approvals.** When a step needs your OK, the chain waits. Approve and it carries on. Decline and the agent finishes that step's reply, then the chain stops.
- **It stops for anything else:** a failed step, **Stop**, Cancel, or a new instruction.

The limit is 5 steps per request (`command_chain_max_depth` in `config.json`). Turn chaining off with `command_chain_enabled: false`.

**Templates.** Barnaby ships three: TDD Cycle, Debugging Workflow and Code Review Checklist. Run one by voice ("run the TDD template for the cart total", "start the debugging workflow") or from **Templates** next to the agent switch, optionally saying what it should work on. A template runs as a chain, so the same approval and stop rules apply. Only an explicit "run/start/use the *name* template/workflow/checklist" phrasing triggers one, so "update the email template" stays an ordinary instruction.

**Saved workflows.** JSON files in `%APPDATA%\Barnaby\workflows` appear in the same list and run the same way:

```json
{ "id": "release-prep", "name": "Release prep", "description": "Before tagging",
  "steps": [{ "instruction": "Bump the version" }, { "instruction": "Update the changelog" }] }
```

A malformed file is skipped with a log line and doesn't hide the others.

## Security model

- **Prompt over stdin, never argv.** An npm-installed `claude.cmd` runs through `cmd.exe`, which re-parses arguments. A transcript containing `&` would otherwise be command injection. Every argv element is a fixed flag, a validated UUID or model name, or a path Barnaby generated.
- **Permissions per turn.** Barnaby runs Claude Code in `acceptEdits` mode, so edits inside the project folder are automatic. Shell commands are denied unless they're on a read-only/test allowlist (`engine/barnaby_engine/config.py`). A denied call becomes an approval card, and approving it resumes the session with a grant for **exactly that command**, written to a temp `--settings` file that's deleted after the turn. Nothing is added to your Claude Code settings.
- **Risk tiers.** `rm -rf`, force-push, `reset --hard`, `DROP TABLE`, `curl | sh`, prod deploys, `terraform destroy` and similar are *critical*: no voice approval, hold to confirm. Commands that can't be expressed as one exact rule (subshells, multi-line) can't be approved at all; ask for them another way.
- **Engine socket.** Bound to 127.0.0.1 on a random port. It needs a 256-bit token as the first frame (constant-time compare) and refuses browser origins outside the app, which blocks cross-site WebSocket hijacking. The token travels by environment variable and IPC, never argv. Every inbound frame is schema-validated.
- **The UI can't widen permissions.** `set_config` only accepts whitelisted keys, so the auto-approve list and the Claude binary path are editable only in `%APPDATA%\Barnaby\config.json`.
- **IDE bridge (not started by default).** Any request with a browser `Origin` is refused, and the first frame must carry a secret token, compared in constant time. The port and token are published in an owner-only `ide-bridge.json` in the data directory, for extensions to read. Only authenticated clients receive events.
- **Editor launch.** File paths must resolve inside the project, and paths with `cmd.exe` metacharacters are refused, because `code.cmd` / `cursor.cmd` re-parse argv too.
- **Speech hygiene.** API keys, tokens, JWTs, private keys and `password=` values are redacted before anything is spoken aloud.
- **Webview.** Strict CSP (`script-src 'self'`, connections only to `ws://127.0.0.1`). Model output can't navigate the window, links open in your browser (http/https only), remote images are dropped, and Tauri capabilities are limited to events, the folder picker and opening URLs.

**Known trade-off:** test runners (`npm test`, `pytest`, `go test`, …) are auto-approved because voice coding without them is painful. They execute project code, so an agent-written test can run without a prompt. Remove them from `safe_bash` in `config.json` if you want every execution gated.

## How it learns your voice

1. Every transcript appears in an editable box before sending (turn this off in Settings once it's reliable).
2. When you correct it, the word-level diff is stored (for example, "doc ops" → "DBOps").
3. The second time the same correction happens, it becomes an automatic rewrite, and the word joins your vocabulary. That vocabulary is fed to Whisper as a decoding prompt, so the recognizer itself leans toward it.
4. Dependency names from your project's `package.json`, `pyproject.toml`, `requirements.txt`, `go.mod`, `Cargo.toml` and `composer.json` are added automatically.
5. **Voice profile** in the sidebar shows every rule. Disable or delete anything wrong.

For acoustic adaptation to your accent, turn on **Keep voice samples**. Your audio and the corrected text are saved to `%APPDATA%\Barnaby\voice_samples` as training data for fine-tuning Whisper. This is off by default because it stores recordings of your voice.

## Tests

```powershell
cd engine; .venv\Scripts\python -m unittest discover -s tests -t .   # 189 tests
cd app;    npm run typecheck; npm test                                   # 35 tests
cd app;    npx playwright test     # 7 E2E flows against the real engine in --mock mode
```

The engine suite drives the real subprocess path with a fake `claude` executable. It checks that the prompt arrives on stdin and not in argv, that the settings file is scoped and deleted, that tree-kill works on cancel, that 200 KB stream lines are handled, and that a missing result surfaces stderr. The stream parser is pinned to a fixture captured from real `claude -p --output-format stream-json` output. CI runs all of it on Windows and Ubuntu (`.github/workflows/ci.yml`).

## GPU

With an NVIDIA GPU, install the CUDA build of torch instead of the CPU one in `setup.ps1`, then choose **Most accurate (distil-large-v3)** in Settings. Recognition on the GPU is several times faster.

## Packaging

`.\scripts\build-release.ps1` freezes the engine with PyInstaller and builds NSIS/MSI installers with it bundled as a sidecar. Expect a large engine binary (the Kokoro TTS stack depends on torch) and a slower first start while it unpacks. Swapping to `kokoro-onnx` would cut this substantially. That's the planned next step for distribution.

## Status and roadmap

**Verified in the build sandbox:** engine, UI, and E2E suites; the real Tauri binary spawning the engine, handing off the token, and the webview connecting under the CSP; and clean engine shutdown when the app closes.

**Written for Windows, first exercised on your machine:** `taskkill /T` tree-kill, `CREATE_NO_WINDOW`, WASAPI sample-rate fallback, `.cmd` launcher handling, and live Whisper/Kokoro inference.

**Planned, not built yet** (see `docs/superpowers/specs/2025-09-27-barnaby-premier-tier-design.md`). The engine has placeholder modules for these (`wake_word.py`, `continuous.py`, `macro_recorder.py`), but none of them is connected to the microphone or the UI. Their toggles are hidden until they work.
- Wake word ("Barnaby" to start listening): needs a real on-device model such as openWakeWord.
- Continuous dictation (no key to hold, silence ends your turn): the silence timer exists, but isn't wired to the mic.
- Macro recording and replay.
- IDE extensions for VS Code, Cursor and Claude Desktop: the engine side, the secured bridge, is ready. The extensions themselves aren't written.

Next:
- Codex as a third engine: the adapter can be built from Codex's documented `codex exec --json` format, but it's waiting on a Codex run that isn't blocked by the account's ChatGPT usage limit, so the parser can be pinned to real output.
- A Devin hand-off command: dispatch a task through Devin's API and show completion in the activity panel.
- Configurable global hotkey.
- Whisper fine-tuning script over the saved voice samples.
