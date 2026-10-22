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

## Requirements and supported platforms

| | Supported | Where it's checked |
|---|---|---|
| **OS** | **Windows 10 and 11 (64-bit)**, the only platform Barnaby ships for: installers are NSIS/MSI, and the shell, audio and agent launching are Windows-specific | CI on `windows-latest`: engine lint and tests; desktop-shell build (`npm run build`, `cargo clippy -D warnings`) |
| Ubuntu | Not a supported desktop. CI uses it to run the platform-neutral parts | CI on `ubuntu-latest`: engine lint and tests, UI typecheck, Vitest and Playwright E2E (real engine process, scripted agent) |
| **Python** | 3.11 or 3.12 (`requires-python = ">=3.11,<3.13"`) | CI runs 3.12 |
| **Node.js** | 20 or newer | CI runs 22 |
| **Rust / Tauri** | Rust 1.77+ (`rust-version`) with the MSVC C++ build tools; Tauri 2.11 | CI uses the latest stable Rust |
| **websockets** | 15.x (`websockets>=15,<16`). The tablet-server tests also pass on 16 | CI installs 15 |
| **Claude Code** | Signed in. Permission behaviour verified against Claude Code 2.1.282 | Stream parser pinned to captured `claude -p --output-format stream-json` output |
| **Cursor CLI** | Optional. Verified against Cursor CLI 2026.09.26 on Windows | Parser pinned to captured Cursor stream output |
| **Devin** | Optional. Devin API v1 (`https://api.devin.ai/v1`) with a personal key (`apk_user_…`) | Scripted fake of the v1 API, shapes captured from the real API; no live Devin call in CI |
| **Tablet** | Android tablet with Chrome, on the same private Wi-Fi as the PC | E2E and server tests over real TLS on localhost |

Real speech (microphone, Whisper, Kokoro) and the real agents aren't exercised in CI; [docs/manual-test-script.md](docs/manual-test-script.md) covers them on a real PC before each release.

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

### Claude Code sign-in

Barnaby has no Claude login of its own: it runs the `claude` program, which keeps the login. **Settings → Claude sign-in** shows whether you're signed in and has a **Sign in to Claude** button. The button runs Claude Code's own `claude auth login`: your browser opens, you approve, and the status turns green when it finishes, with no restart. A console window opens beside the browser for the rare case where Claude asks you to paste a code. Cancel stops it, and it gives up after 5 minutes.

- **An old `CLAUDE_CODE_OAUTH_TOKEN` is retired.** That Windows setting outranks the saved login, so a revoked one blocks every turn. The sign-in runs without it, and afterwards Barnaby ignores it for this session. Remove it from Windows settings too so it stays gone after a restart.
- **"Signed in" can lag.** Claude Code only reports that a login exists, not whether the server still accepts it. A turn rejected with a 401 flips the status to **Sign-in expired**, and the button changes to **Sign in again**.
- **PC only.** A paired tablet can't start a sign-in.

Cursor needs its command-line agent, installed and signed in once:

```powershell
irm 'https://cursor.com/install?win32=true' | iex
agent login
```

#### How Cursor is kept inside the same safety model

These behaviours were verified against Cursor CLI 2026.09.26 on Windows:

- **No shell in the launch path.** Cursor installs as `agent.cmd`, a wrapper around `node.exe`, and only accepts the prompt as a command-line argument. Barnaby runs the bundled `node.exe` directly, with the prompt after `--`, so `cmd.exe` never re-parses a transcript.
- **Shell commands are allowlisted per turn.** Each turn, Barnaby writes a private Cursor config (`%APPDATA%\Barnaby\cursor`, via `CURSOR_CONFIG_DIR`) in allowlist mode. Your own Cursor settings are never touched. A command outside the list is refused by Cursor, reported in the stream, and shown as an approval card. Approving grants exactly that command using Cursor's exact form, `Shell(cmd:args)`. The plain `Shell(cmd)` form would also match `cmd --force`. Chained commands (`&&`, `;`, `|`) are refused unless every part is allowed.
- **File edits are not restricted by Cursor.** In headless mode, Cursor ignored both `Write()` scopes and explicit `deny` rules, and its `--sandbox` option isn't available on Windows. Barnaby's guard watches every Cursor tool call as it starts, and stops the turn immediately if Cursor reads or edits a file outside the project, or runs a command that goes beyond your rules.
- **The trade-off.** Because the guard reacts to Cursor's own "tool started" event, it can't promise the first out-of-project write never lands: it stops Cursor at that point, not before. Claude Code, by contrast, refuses such an edit before it happens. For work that touches files outside the project folder, use Claude. The app says this under the agent switch whenever Cursor is selected, and on Cursor task cards.
- **You're told whether it landed.** Once Cursor is stopped, Barnaby checks the file it was reaching for and says which happened: *stopped before the file changed* (or *was created*), or *Cursor had already saved that change; check the file*. A file modified within two seconds before the turn started also counts as changed, so the report errs towards "check it", never towards a false all-clear.
- **What was looked at, and why it stops there.** "Tool started" is the earliest event Cursor's stream offers. Nothing earlier names the file, so there is no pre-flight check to add. A file watcher that reverts can't cover "anywhere outside the project", and reverting inside the project could undo another task's or your own work. What does contain damage is where Cursor runs: a Claude or Cursor background task runs in its own git worktree (on by default; see [Background tasks](#background-tasks-several-agents-at-once)), so an out-of-lane edit lands in that task's copy, not in your checkout. Edits outside the project folder are still only stopped, not prevented.
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

## Background tasks (several agents at once)

Hand work to Claude, Cursor or Devin and keep talking to Barnaby about something else. Each task has one agent, one instruction, and the folders it may change (its *lane*). Open the **Tasks** tab in the side panel (or **Tasks** in the sidebar on narrower screens) and press **New task**, or say it:

- "task for Devin in backend: add the orders endpoint"
- "assign the frontend to Cursor: fix the login form"

**Lanes keep agents out of each other's way.** Up to 3 tasks run at once by default (Settings → Background tasks, 1–6). Tasks whose folders overlap wait their turn; the others go ahead. No folders means the whole project, and that task waits until no other task is working on this PC. Name folder sets once under **Settings → Background tasks → Saved lanes** (for example *backend* = `engine`, *frontend* = `app`) so voice commands can use the names.

**Each local task gets its own git worktree.** When the project is a git repository, a Claude or Cursor task works in its own copy of your last commit, on its own branch (`barnaby/task-<id>-<title>`), in `%APPDATA%\Barnaby\worktrees\<repo>-<hash>\<task id>`. Your checkout, and your uncommitted changes in it, are never touched; the card says when you had uncommitted changes the task can't see. When the task is done, Barnaby commits its changes to that branch (your hooks and signing settings apply). **Barnaby never merges or pushes:** merge the branch yourself (`git merge barnaby/task-…`) when you're happy with it. The card shows the branch, **Open folder** (the worktree in Explorer) and **Discard** (deletes the worktree and the branch, after you confirm). Follow-ups and restarts reuse the same worktree, so the agent's session resumes. **Clear finished** removes a task's worktree and branch only when nothing would be lost; a task with uncommitted changes or commits that aren't on any other branch stays on the board, and Barnaby says which. A project that isn't a git repository (or has no commits yet) falls back to the project folder itself, and the card warns you. You can turn worktrees off in Settings.

Worktrees live outside the project on purpose: inside it, test runners and globbing (vitest, pytest), your IDE and file watchers, and agents searching the project would all pick up every task's copy of every file. A fresh worktree has no `node_modules` or virtualenv; to prepare one, add commands to `task_worktree_setup` in `%APPDATA%\Barnaby\config.json` (for example `{"C:\\path\\to\\project": [{"run": "npm ci", "dir": "app"}]}`). They run once, without a shell, when a worktree is created. Only `config.json` can set them, because they aren't limited to the auto-approve list.

Even with worktrees, tasks whose folders overlap still wait for each other by default. **Settings → Background tasks → Let worktree tasks with overlapping folders run at the same time** lets them run in parallel: they can't overwrite each other's files, but two branches that change the same files will need their conflicts merged by you. It is off by default because a conflict you have to untangle afterwards is easy to underestimate.

**Checks decide when a task is done, not the agent.** Under **Settings → Background tasks → Checks for this project**, list the commands that prove the work is right (`pytest -q` in `engine/`, `npm test` in `app/`). When a Claude or Cursor task's turn ends, Barnaby runs them itself in the task's worktree and marks the task **done** only if every one exits 0. If one fails, Barnaby sends the agent the failing command and the end of its output, in the same session, and runs the checks again when it's finished, up to 2 more times (configurable, 0–5). After that, the task **fails** with the check output. While checks run, the task shows *Checking* and keeps its slot and its folders. The card shows which checks passed or failed, with each one's output, and how many check runs it took. An agent saying "all tests pass" never makes a task done. You can give one task its own checks in **New task** (on the PC). With no checks, a finished task says **Unverified**; Devin's tasks are always unverified, because their work is a pull request in Devin's cloud. Checks run without a shell (no `&&`, pipes or redirection, one command each, 10-minute timeout each), and each must match the auto-approve list (`safe_bash` in `config.json`), so Settings can't make Barnaby run anything an agent couldn't already run without asking. Add a rule there to use something else, such as `npm run build:*`.

**How each agent is fenced in.** For a worktree task, "the project" below means its worktree:

- **Claude** runs task turns in Claude Code's `default` mode with `Edit(./<lane>/**)` rules, relative to the folder it runs in. An edit outside its folders becomes an approval card on that task; approving allows exactly that one file. Edits outside the project can't be approved in a task, and for a worktree task that includes your own checkout.
- **Cursor** can't pause for approval on file edits, so Barnaby stops the task when it starts editing outside its folders and says so, including whether that edit reached the disk before Cursor was stopped. In a worktree task, an edit that did land is in the task's worktree, not your checkout. See [how Cursor is kept inside the same safety model](#how-cursor-is-kept-inside-the-same-safety-model).
- **Devin** works in its own cloud copy of the repo and opens a pull request, so it never competes for local folders. It still counts toward the limit.
- **Your conversation** keeps working while tasks run. If it tries to edit a folder a task in the project folder is using, you get an approval card first (tasks in their own worktree never make it ask). A conversation turn that was already running when a task started keeps its permissions until its next turn.
- **Commands** follow the normal rules: tests and read-only commands run, and anything else is a card. Lanes and worktrees keep agents out of each other's way; they aren't a sandbox, because a test or build command you've allowed (or a check) runs project code that can still write anywhere on this PC.
- **Your own Claude settings can widen lanes.** Claude Code merges your user and project settings with Barnaby's. If they contain an `Edit` or `Write` allow rule, the task card warns you.

**Updates.** When a task finishes, Barnaby says so ("Devin finished orders endpoint.") with the first sentence of its summary (and "Its checks passed." when they did), shows a toast, and the card shows the summary, time and cost. A task that needs your OK says so once, and its card appears on the task, never mixed into the conversation's cards. Saying "yes" never approves a background task; click it (on the PC or the tablet; the tablet can act on tasks of the PC's current project, and cards sent while it slept are still there when it wakes). **Follow up** on a finished task continues the same agent session with the same folders; **Cancel** stops only that task (a Devin session keeps running in Devin's app). Tasks survive a restart as *interrupted*, and a follow-up picks them up again.

## Security model

- **Prompt over stdin, never argv.** An npm-installed `claude.cmd` runs through `cmd.exe`, which re-parses arguments. A transcript containing `&` would otherwise be command injection. Every argv element is a fixed flag, a validated UUID or model name, or a path Barnaby generated.
- **Permissions per turn.** Barnaby runs Claude Code in `acceptEdits` mode, so edits inside the project folder are automatic. Shell commands are denied unless they're on a read-only/test allowlist (`engine/barnaby_engine/config.py`). A denied call becomes an approval card, and approving it resumes the session with a grant for **exactly that command**, written to a temp `--settings` file that's deleted after the turn. Nothing is added to your Claude Code settings.
- **Risk tiers.** `rm -rf`, force-push, `reset --hard`, `DROP TABLE`, `curl | sh`, prod deploys, `terraform destroy` and similar are *critical*: no voice approval, hold to confirm. Commands that can't be expressed as one exact rule (subshells, multi-line) can't be approved at all; ask for them another way.
- **Engine socket.** Bound to 127.0.0.1 on a random port. It needs a 256-bit token as the first frame (constant-time compare) and refuses browser origins outside the app, which blocks cross-site WebSocket hijacking. The token travels by environment variable and IPC, never argv. Every inbound frame is schema-validated.
- **The UI can't widen permissions.** `set_config` only accepts whitelisted keys, so the auto-approve list and the Claude binary path are editable only in `%APPDATA%\Barnaby\config.json`.
- **Task checks and worktrees.** Barnaby runs a task's checks itself, as argv with no shell; commands with shell syntax are refused, and each must match the auto-approve list, so the UI (or a compromised renderer) can't make Barnaby run anything new. Only the PC sets checks; a tablet's tasks use the project's. Worktree setup commands can be set only in `config.json`. git runs as argv, commit messages go over stdin, and only branches named `barnaby/task-…` are ever deleted (Discard, after you confirm; or Clear finished, only when they're merged and clean).
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
cd engine; .venv\Scripts\python -m unittest discover -s tests -t .   # 496 tests
cd app;    npm run typecheck; npm test                                   # 129 tests
.\scripts\e2e.ps1                     # 13 E2E flows: real engine process, scripted agent and fake audio (tests/e2e_engine.py)
```

The scripted agent and fake audio live only under `engine/tests/`. The shipped engine has no mock mode: it always uses the microphone, Whisper, Kokoro and your real `claude` / `agent` / Devin.

The engine suite drives the real subprocess path with a fake `claude` executable. It checks that the prompt arrives on stdin and not in argv, that the settings file is scoped and deleted, that tree-kill works on cancel, that 200 KB stream lines are handled, and that a missing result surfaces stderr. The stream parser is pinned to a fixture captured from real `claude -p --output-format stream-json` output. CI runs all of it on Windows and Ubuntu (`.github/workflows/ci.yml`).

## GPU

With an NVIDIA GPU, install the CUDA build of torch instead of the CPU one in `setup.ps1`, then choose **Most accurate (distil-large-v3)** in Settings. Recognition on the GPU is several times faster.

## Packaging

`.\scripts\build-release.ps1` freezes the engine with PyInstaller and builds NSIS/MSI installers with it bundled as a sidecar. Expect a large engine binary (the Kokoro TTS stack depends on torch) and a slower first start while it unpacks. Swapping to `kokoro-onnx` would cut this substantially. That's the planned next step for distribution.

## Status and roadmap

**Releasing:** [docs/release-checklist.md](docs/release-checklist.md) lists the pre-release steps, and [docs/manual-test-script.md](docs/manual-test-script.md) is the hands-on script for the installed app.

**Verified in the build sandbox:** engine, UI, and E2E suites; the real Tauri binary spawning the engine, handing off the token, and the webview connecting under the CSP; and clean engine shutdown when the app closes.

**Written for Windows, first exercised on your machine:** `taskkill /T` tree-kill, `CREATE_NO_WINDOW`, WASAPI sample-rate fallback, `.cmd` launcher handling, and live Whisper/Kokoro inference.

**Planned, not built yet** (see `docs/superpowers/specs/2025-09-27-barnaby-premier-tier-design.md`). Nothing for these ships in the engine until it works:
- Wake word ("Barnaby" to start listening): needs a real on-device model such as openWakeWord.
- Macro recording and replay.
- IDE extensions for VS Code, Cursor and Claude Desktop: the engine side, the secured bridge, is ready. The extensions themselves aren't written.

Next:
- Codex as a third engine: the adapter can be built from Codex's documented `codex exec --json` format, but it's waiting on a Codex run that isn't blocked by the account's ChatGPT usage limit, so the parser can be pinned to real output.
- Configurable global hotkey.
- Whisper fine-tuning script over the saved voice samples.

## License

Copyright (c) 2026 dallas8000-ops. All rights reserved.

Barnaby is proprietary software. The source is visible in this repository, but no rights to use, copy, modify or distribute it are granted without the copyright holder's written permission. See [LICENSE](LICENSE). Third-party components keep their own licenses.
