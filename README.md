# Barnaby

A desktop voice interface for Claude Code, Cursor and Devin. Hold a key, say what you want done in your project, and Barnaby transcribes it locally, runs it through Claude Code in that folder, shows every command and file edit as it happens, and reads the outcome back to you. Anything that changes state outside the project, or can't be undone, waits for your approval.

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
| Talk hands-free | Turn on **Hands-free** under the instruction box. Just talk; a pause sends it |
| Chain steps | Say them in order: "run the tests, **then** fix anything failing" |
| Run a template | "Run the TDD template for the cart total", or the **Templates** button |
| Open a touched file | **Open in VS Code** / **Open in Cursor** on any file strip |

## Agents: Claude, Cursor and Devin

The switch under the instruction box picks which coding agent runs the next instruction. You can also say it: "Cursor, fix the build", "have Claude run the tests", "Devin, add a health check", or just "use Cursor". Each session belongs to the agent that started it (a Claude session can't be resumed by Cursor), so switching starts a new session and clears pending approvals. Reopening an old session switches back to its agent.

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

### Devin

Devin works differently from the other two. It runs in Devin's cloud on its own machine, against repositories you've connected in Devin, **not on the files on this PC**. A Devin turn starts a Devin session (later turns in the same Barnaby session message that same Devin session), then Barnaby checks it every few seconds and streams each of Devin's messages into the conversation and reads it aloud. The Activity panel shows a card linking to the session in Devin.

- **Your key.** Devin's API needs a key: a personal key (`apk_user_…`) works with the v1 API Barnaby uses. Paste it into **Settings → Devin API key**. It's saved in **Windows Credential Manager** as `Barnaby-devin`, never in `config.json`, logs or the session history, and the UI never receives it back, only whether one is saved. You can also save it from PowerShell without opening Barnaby: copy the key, then run `.\scripts\set-secret.ps1 devin`. Remove it from Settings, or from Control Panel → Credential Manager.
- **When a turn ends.** When Devin is *blocked* (waiting for you: its last message is the question, so just answer) or *finished*. If Devin is still going after 30 minutes, Barnaby stops watching and says where to follow it; Devin keeps working.
- **Stop.** Devin's v1 API has no documented way to end a session, so Stop ends Barnaby's turn but Devin's session keeps running in the cloud. Barnaby says so. End it in Devin if you need to.
- **Which repository.** A new Devin session is told your project's `origin` remote, with any credentials embedded in the URL (`https://user:token@…`) stripped first, so a token in a remote never reaches Devin.
- **No approvals.** Devin's actions happen on Devin's machine under Devin's own controls, so Barnaby's per-command approval cards don't apply to Devin turns.
- **Cost.** Devin bills in ACUs against your Devin plan and the API reports no per-turn cost, so Devin turns show none.

## Tablet access (Galaxy Tab and other Android tablets)

Use Barnaby from a tablet on your home Wi-Fi. Your PC keeps doing all the work (agents,
speech recognition); the tablet opens Barnaby in Chrome, records your voice, and shows and
speaks the replies.

**Set up once:**
1. On the PC: **Settings → Tablet access**, switch it on.
2. As administrator, allow the tablet port on *Private* networks only:
   `powershell -ExecutionPolicy Bypass -File .\scripts\tablet-firewall.ps1`
3. On the tablet, open `https://<your-pc-ip>:8766/barnaby-ca.crt` (Settings shows the exact
   address). Chrome warns once; tap Advanced, then Proceed. Then install the file under
   Android Settings → Security and privacy → More security settings → Install from device
   storage → CA certificate. This is Barnaby's own certificate authority. It's limited to
   home-network addresses and your PC's name, so it can't be used to impersonate real websites.
4. On the PC, tap **Pair a tablet** and scan the QR code with the tablet. The link works once,
   for 5 minutes.

**Using it:** hold the circle on the tablet to talk and release to send. The PC transcribes it
exactly like push-to-talk, including review drafts and learned corrections. Replies appear on
the tablet and are read by the tablet's own voice. While a tablet is connected the PC stays
quiet. You can approve and decline from the tablet under the same rules as the PC. The PC
window and a tablet can be connected at the same time.

**What stays on the PC:** the project folder, API keys, the microphone, hands-free, tablet
access and pairing. The tablet can't change them, and the engine enforces this, not just the
UI. A tablet can open past sessions of the current project only.

**Removing a tablet:** Settings → Tablet access → **Remove**. Its connection closes at once and
its key stops working.

**How it's protected:**
- Off by default. The tablet port only listens while tablet access is on.
- Everything is encrypted with TLS.
- Only paired devices get in. Each has a random 256-bit token; the PC stores only its SHA-256
  hash. Pairing codes are single-use, expire in 5 minutes, and travel in the URL fragment, so
  they're never sent in a request line.
- Five failed attempts from one address lock it out for 10 minutes. Unauthenticated
  connections are capped, and limited to 4 KB until they authenticate.
- Only private (home-network) addresses may connect. Barnaby refuses to start tablet access
  while Windows marks your network as Public, and the firewall rule only opens the port on
  Private networks.
- The socket accepts only the page's own `https://` origin. The page is served with a strict
  Content Security Policy.
- The tablet speaks only the sentences the PC would have spoken, already shortened and with
  secrets redacted, never raw agent output.

Design and threat model:
`docs/superpowers/specs/2026-09-28-barnaby-tablet-access-design.md`.

## Hands-free mode

Turn on **Hands-free** (next to the agent switch) and the microphone stays open. You just talk, and a pause ends your turn. The default pause is 1.2 seconds; change it in **Settings → Hands-free pause** if you get cut off mid-thought.

- **Barnaby doesn't hear itself.** Listening pauses while Barnaby speaks, plus half a second afterwards, so it never transcribes its own voice through your speakers.
- **Background noise is filtered.** An adaptive noise floor ignores steady noise. A fan or AC switching on is learned as background rather than transcribed, and coughs and clicks are too short to count. It checks for the energy dips between syllables that speech has and steady noise doesn't.
- **Voice control of drafts.** With *Review before sending* on, each thing you say becomes a draft. Say **"send it"** to send it, including anything you typed into it, or **"scratch that"** to discard it. Only those short phrases count, so "send the report to finance" is an ordinary instruction. For fully hands-free use, turn review off.
- **Push-to-talk still works.** Holding Space takes over the mic for that recording, and hands-free resumes afterwards.
- **It listens to a microphone, not your speakers.** Some sound cards make a speaker-loopback input ("What U Hear", "Stereo Mix") the Windows default recording device. With the microphone left on *Automatic*, Barnaby skips those and uses the first real mic on the same audio system. Settings shows which one it picked and labels loopback inputs. Picking a device yourself always wins.

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
- **Tablet access (off by default).** A second, TLS-only listener for paired tablets with its own role policy; see [Tablet access](#tablet-access-galaxy-tab-and-other-android-tablets).
- **Cloud agent keys.** Stored only in Windows Credential Manager. The engine reads the key for each Devin request and sends it only in the `Authorization` header, over HTTPS, to Devin's fixed API host. The `set_secret` command carries the key as a pydantic `SecretStr`, so it never shows up in a log or validation message, and no event ever contains it.
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
cd engine; .venv\Scripts\python -m unittest discover -s tests -t .   # 345 tests
cd app;    npm run typecheck; npm test                                   # 83 tests
.\scripts\e2e.ps1                     # 10 E2E flows against the real engine in --mock mode
```

The engine suite drives the real subprocess path with a fake `claude` executable. It checks that the prompt arrives on stdin and not in argv, that the settings file is scoped and deleted, that tree-kill works on cancel, that 200 KB stream lines are handled, and that a missing result surfaces stderr. The stream parser is pinned to a fixture captured from real `claude -p --output-format stream-json` output. CI runs all of it on Windows and Ubuntu (`.github/workflows/ci.yml`).

## GPU

With an NVIDIA GPU, install the CUDA build of torch instead of the CPU one in `setup.ps1`, then choose **Most accurate (distil-large-v3)** in Settings. Recognition on the GPU is several times faster.

## Packaging

`.\scripts\build-release.ps1` freezes the engine with PyInstaller and builds NSIS/MSI installers with it bundled as a sidecar. Expect a large engine binary (the Kokoro TTS stack depends on torch) and a slower first start while it unpacks. Swapping to `kokoro-onnx` would cut this substantially. That's the planned next step for distribution.

## Status and roadmap

**Verified in the build sandbox:** engine, UI, and E2E suites; the real Tauri binary spawning the engine, handing off the token, and the webview connecting under the CSP; and clean engine shutdown when the app closes.

**Written for Windows, first exercised on your machine:** `taskkill /T` tree-kill, `CREATE_NO_WINDOW`, WASAPI sample-rate fallback, `.cmd` launcher handling, and live Whisper/Kokoro inference.

**Planned, not built yet** (see `docs/superpowers/specs/2025-09-27-barnaby-premier-tier-design.md`). The engine has placeholder modules for the first two (`wake_word.py`, `macro_recorder.py`), but neither is connected to the microphone or the UI. Their toggles are hidden until they work.
- Wake word ("Barnaby" to start listening): needs a real on-device model such as openWakeWord.
- Macro recording and replay.
- IDE extensions for VS Code, Cursor and Claude Desktop: the engine side, the secured bridge, is ready. The extensions themselves aren't written.

Next:
- Codex as a third engine: the adapter can be built from Codex's documented `codex exec --json` format, but it's waiting on a Codex run that isn't blocked by the account's ChatGPT usage limit, so the parser can be pinned to real output.
- Configurable global hotkey.
- Whisper fine-tuning script over the saved voice samples.
