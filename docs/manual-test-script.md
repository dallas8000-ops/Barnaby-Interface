# Manual test script (installed app, real PC)

CI covers the engine, the UI and the protocol with a scripted agent and fake audio. This script covers what CI can't: the **installed** build, a real microphone and speakers, Whisper and Kokoro, the real Claude Code, Cursor CLI and Devin, and a real tablet. Run it on every release candidate (see [release-checklist.md](release-checklist.md)), and after any change to audio, agents, approvals or tablet access.

**How to use it.** Copy this file into the release PR (or print it), and for each step do the *Action*, compare with *Expected*, and tick **Pass** or **Fail**. For a Fail, write what happened in *Notes* (a screenshot and `%APPDATA%\Barnaby\engine.log` help). Don't skip a step: if it can't be run, tick Fail and say why.

Release / commit: ____________ Tester: ____________ Date: ____________
PC (Windows version, CPU/GPU): ______________________ Mic / speakers: ______________________
Claude Code version (`claude --version`): ________ Cursor CLI version (`agent --version`): ________ Tablet model: ________

## 0. Preparation

| # | Action | Expected | Pass | Fail | Notes |
|---|---|---|:-:|:-:|---|
| 0.1 | Install the release build (NSIS installer). Start Barnaby from the Start menu. | Window opens; the sidebar connects (no engine error banner). | ☐ | ☐ | |
| 0.2 | Create a throwaway git repo for testing: `git init C:\bt\demo`, add a `README.md` and one small source file with a test (e.g. `pytest` or `npm test`), commit. Pick it as the project folder. | Folder is shown as the project; dependency names from its manifest appear in the voice profile vocabulary later. | ☐ | ☐ | |
| 0.3 | Wait for the sidebar's **Hearing** and **Voice** status. | Both go from *Loading* to ready (first run downloads about 250 MB). | ☐ | ☐ | |
| 0.4 | Settings → Claude sign-in. | Shows *Signed in* (or use **Sign in to Claude**: browser opens, status turns green without a restart). | ☐ | ☐ | |

## 1. Microphone and Whisper transcription

| # | Action | Expected | Pass | Fail | Notes |
|---|---|---|:-:|:-:|---|
| 1.1 | Settings → Microphone: leave on *Automatic*. | Settings shows which mic was picked; it's a real microphone, not a "Stereo Mix" / "What U Hear" loopback. | ☐ | ☐ | |
| 1.2 | With *Review before sending* on, hold **Space** in the window and say "list the files in this project", release. | The scope reacts while you talk; the review box shows the words correctly within a few seconds. Nothing is sent yet. | ☐ | ☐ | |
| 1.3 | Edit one word in the review box, press **Esc**. | Draft is discarded; nothing is sent. | ☐ | ☐ | |
| 1.4 | Say a project-specific word Whisper gets wrong (e.g. a library name), correct it in the review box, press **Enter**. Repeat the same correction a second time. | After the second correction, **Voice profile** lists an automatic rewrite for it; saying it a third time is transcribed correctly. | ☐ | ☐ | |
| 1.5 | Pick a specific microphone in Settings, then switch back to *Automatic*. | Recording works with each; your explicit choice wins over automatic. | ☐ | ☐ | |

## 2. Kokoro speech

| # | Action | Expected | Pass | Fail | Notes |
|---|---|---|:-:|:-:|---|
| 2.1 | Send "what does this project do?" (review on: press Enter). | Claude answers; Barnaby reads a short summary aloud in a natural voice, not the raw reply. | ☐ | ☐ | |
| 2.2 | While Barnaby is speaking, start talking (hold Space). | Speech stops at once (barge-in). | ☐ | ☐ | |
| 2.3 | While it's speaking, press **Quiet**. | Speech stops. | ☐ | ☐ | |
| 2.4 | Ask Claude to "print the value of a fake API key sk-test-1234567890abcdef1234 in your reply". | The key is shown on screen but not read aloud (it's redacted from speech). | ☐ | ☐ | |

## 3. Push-to-talk

| # | Action | Expected | Pass | Fail | Notes |
|---|---|---|:-:|:-:|---|
| 3.1 | Focus another app (e.g. Notepad). Hold **Ctrl+Alt+Space**, say an instruction, release. | Barnaby records and transcribes even though it isn't focused. | ☐ | ☐ | |
| 3.2 | In Barnaby, press and hold the scope with the mouse, talk, release. | Same as holding Space. | ☐ | ☐ | |
| 3.3 | Click into the typed-instruction box and press Space. | A space is typed; recording doesn't start. | ☐ | ☐ | |
| 3.4 | Type an instruction and press **Enter**. | It's sent without a review draft. | ☐ | ☐ | |

## 4. Hands-free

| # | Action | Expected | Pass | Fail | Notes |
|---|---|---|:-:|:-:|---|
| 4.1 | Turn on **Hands-free**. Say an instruction and pause. | About 1.2 s after you stop, it becomes a draft (review on) or is sent (review off). | ☐ | ☐ | |
| 4.2 | With review on, say an instruction, then say "send it". | The draft is sent. | ☐ | ☐ | |
| 4.3 | Say an instruction, then "scratch that". | The draft is discarded. | ☐ | ☐ | |
| 4.4 | Say "send the report to finance". | Treated as an ordinary instruction, not as "send it". | ☐ | ☐ | |
| 4.5 | Let Barnaby speak a reply through the speakers (not headphones). | It doesn't transcribe its own voice. | ☐ | ☐ | |
| 4.6 | Turn on a fan or play steady background noise for 30 s, without talking. | Nothing is transcribed. | ☐ | ☐ | |
| 4.7 | Hold Space and talk while hands-free is on. | Push-to-talk takes that recording; hands-free resumes afterwards. | ☐ | ☐ | |
| 4.8 | Settings → Hands-free pause: raise it to 2.5 s, talk with a 2 s pause mid-sentence. | Not cut off at the pause. | ☐ | ☐ | |

## 5. Approvals (including hold-to-approve for critical actions)

| # | Action | Expected | Pass | Fail | Notes |
|---|---|---|:-:|:-:|---|
| 5.1 | Say "run the tests". | Tests run without an approval card (allowlisted). | ☐ | ☐ | |
| 5.2 | Say "install the left-pad package with npm". | An approval card appears for exactly that command. | ☐ | ☐ | |
| 5.3 | Say "yes". | Approved by voice; the session resumes and the command runs. | ☐ | ☐ | |
| 5.4 | Trigger another non-critical command, click **Decline** (or say "no"). | The command doesn't run; Claude finishes its reply without it. | ☐ | ☐ | |
| 5.5 | Say "delete the build folder with rm -rf build" (create `build\` first). | The card is marked critical. Saying "yes" does **not** approve it. | ☐ | ☐ | |
| 5.6 | Click the critical **Approve** briefly (shorter than 1.2 s). | Nothing happens. | ☐ | ☐ | |
| 5.7 | Press and hold **Approve** for 1.2 s. | Approved; the command runs. | ☐ | ☐ | |
| 5.8 | Ask for something that needs a subshell, e.g. "run echo $(whoami) > out.txt". | The card says it can't be approved as one exact command; Approve is disabled. | ☐ | ☐ | |
| 5.9 | Ask Claude to edit a file outside the project (e.g. `C:\bt\outside.txt`). | An approval card appears before any edit; after declining, the file is unchanged. | ☐ | ☐ | |

## 6. Background tasks: one each for Claude, Cursor and Devin

Prerequisites: Cursor CLI installed and `agent login` done; a Devin API key saved in Settings and the test repo connected in Devin (it needs an `origin` remote Devin can reach).

| # | Action | Expected | Pass | Fail | Notes |
|---|---|---|:-:|:-:|---|
| 6.1 | Settings → Background tasks → Saved lanes: add *docs* = `docs`. Create `docs\`. | Lane saved. | ☐ | ☐ | |
| 6.2 | Say "task for Claude in docs: write a short usage guide". | A task card appears in Tasks with *Claude* and `docs/`; status goes Waiting → Working → Done; Barnaby announces "Claude finished …"; card shows summary, time and cost. | ☐ | ☐ | |
| 6.3 | While 6.2 runs, talk to Barnaby about something else. | The conversation keeps working alongside the task. | ☐ | ☐ | |
| 6.4 | Give the Claude task a follow-up that edits a file outside `docs` (e.g. `README.md`). | An approval card appears **on the task card**, not in the conversation. Saying "yes" doesn't approve it; clicking does, for exactly that file. | ☐ | ☐ | |
| 6.5 | Select **Cursor** in the agent switch. | An info note appears under the switch: Cursor's guard stops a turn as an out-of-project/out-of-lane tool starts, so the first write may land. **How it works** opens the README section in the browser; hovering the note shows the full explanation. | ☐ | ☐ | |
| 6.6 | Say "assign docs to Cursor: add a FAQ section to the usage guide". | Cursor task card shows *Cursor*, `docs/`, and the same guard note while it's working. Task completes; no cost shown. | ☐ | ☐ | |
| 6.7 | Give a Cursor task in `docs` an instruction that edits `README.md` (outside its lane). | Task stops with *Stopped Cursor: this task tried to change … outside its folders*, followed by either "Barnaby stopped it before the file changed" or "Cursor had already saved that change … check the file". Check `README.md` in the task's worktree (**Open folder**; with worktrees off, in the project): what's on disk matches what the message said. Your own checkout's `README.md` is untouched. | ☐ | ☐ | |
| 6.8 | In the conversation with Cursor selected, ask it to write `C:\bt\outside.txt`. | Turn stops with "it tried to change a file outside your project", plus whether the file was created. Check: the file's existence matches the message. | ☐ | ☐ | |
| 6.9 | Say "task for Devin: add a health check endpoint". | Card shows *Devin*, a link to the Devin session; Devin's messages stream into the task; it ends when Devin finishes or is blocked. | ☐ | ☐ | |
| 6.10 | Cancel a running task. | Only that task stops (Devin: Barnaby says the Devin session keeps running in Devin). | ☐ | ☐ | |
| 6.11 | Start 4 tasks with non-overlapping lanes (max parallel = 3). | 3 run at once, the 4th waits; tasks with overlapping lanes wait their turn. | ☐ | ☐ | |
| 6.12 | Quit Barnaby while a task runs, restart. | The task shows *Interrupted*; a follow-up resumes it. | ☐ | ☐ | |

## 7. Task checks and per-task worktrees

These shipped with PR #1 (merged into `master`). Use the git test repo from 0.2, with a test that currently passes.

| # | Action | Expected | Pass | Fail | Notes |
|---|---|---|:-:|:-:|---|
| 7.1 | Settings → Background tasks → **Checks for this project**: add your test command (e.g. `pytest -q`, or `npm test` in its folder). | Saved. A command with shell syntax (`&&`, a pipe, a redirect) or one not on the auto-approve list is refused with a reason. | ☐ | ☐ | |
| 7.2 | Start a Claude task whose work should keep the tests passing. | Card goes Working → *Checking* → Done; it shows each check as passed with its output and the number of check runs; Barnaby says "Its checks passed." | ☐ | ☐ | |
| 7.3 | Start a task whose first attempt is likely to fail a check (e.g. add a failing test and ask the agent to make it pass without editing the test). | After a failing check, the agent gets the failing command and output in the same session and checks run again (up to 2 more times by default). It ends Done, or Failed with the last check output. | ☐ | ☐ | |
| 7.4 | Remove the project's checks and run a task. | The finished card says **Unverified**. A Devin task always says Unverified. | ☐ | ☐ | |
| 7.5 | **New task** on the PC: give one task its own check. | That task uses its own check instead of the project's. | ☐ | ☐ | |
| 7.6 | With an uncommitted change in your checkout, run a Claude task. | The task runs in `%APPDATA%\Barnaby\worktrees\…` on a `barnaby/task-<id>-<title>` branch. Your checkout and its uncommitted change are untouched, and the card says the task can't see that change. | ☐ | ☐ | |
| 7.7 | When it's done, check `git log barnaby/task-…` and **Open folder**. | The task's changes are committed on its branch (your hooks/signing apply); nothing is merged or pushed. Open folder shows the worktree in Explorer. | ☐ | ☐ | |
| 7.8 | Run a Cursor task with an out-of-lane edit (as 6.7). | The stop message says whether the edit landed; if it did, it's in the task's worktree, not in your checkout. | ☐ | ☐ | |
| 7.9 | **Follow up** on a finished worktree task, and restart Barnaby with one interrupted. | Both reuse the same worktree and agent session. | ☐ | ☐ | |
| 7.10 | **Clear finished** with one merged-and-clean task and one with unmerged commits. | The merged one's worktree and branch are removed; the unmerged one stays on the board and Barnaby says why. | ☐ | ☐ | |
| 7.11 | **Discard** a finished task, confirm. | Its worktree and `barnaby/task-…` branch are deleted; no other branch is touched. | ☐ | ☐ | |
| 7.12 | Start two worktree tasks with overlapping folders, then turn on **Let worktree tasks with overlapping folders run at the same time** and repeat. | Off (default): the second waits. On: both run at once. | ☐ | ☐ | |
| 7.13 | Add `task_worktree_setup` for the project in `config.json` (e.g. `npm ci` in `app`), start a new task. | The setup command runs once when the worktree is created; checks that need dependencies then pass. | ☐ | ☐ | |
| 7.14 | Pick a project folder that isn't a git repository and run a task. | The task runs in the project folder itself and the card warns that there's no worktree. | ☐ | ☐ | |
| 7.15 | Turn worktrees off in Settings and run a task. | The task runs in the project folder. | ☐ | ☐ | |
| 7.16 | From the tablet, create a task (and try to give it checks). | The task uses the project's checks; the tablet can't set checks. **Open folder** isn't offered on the tablet. | ☐ | ☐ | |

## 8. Tablet pairing and approve-from-tablet

| # | Action | Expected | Pass | Fail | Notes |
|---|---|---|:-:|:-:|---|
| 8.1 | Settings → Tablet access: switch on (Windows network set to *Private*). Run `scripts\tablet-firewall.ps1` as administrator. | Settings shows the tablet address. With the network set to *Public*, it refuses to start and says why. | ☐ | ☐ | |
| 8.2 | On the tablet, open `https://<pc-ip>:8766/barnaby-ca.crt`, install it as a CA certificate. | Installs; later pages load with no certificate warning. | ☐ | ☐ | |
| 8.3 | On the PC, **Pair a tablet**; scan the QR code with the tablet within 5 minutes. | Tablet opens Barnaby and is paired; Settings lists it. | ☐ | ☐ | |
| 8.4 | Scan the same QR code again (or after 5 minutes). | Refused: codes are single-use and expire. | ☐ | ☐ | |
| 8.5 | On the tablet, hold the circle, say an instruction, release. | The PC transcribes it; the reply appears on the tablet and is spoken by the tablet; the PC stays quiet while the tablet is connected. | ☐ | ☐ | |
| 8.6 | Trigger a non-critical approval (as 5.2) from the tablet; approve on the tablet. | Card appears on the tablet and the PC; approving on the tablet runs it. | ☐ | ☐ | |
| 8.7 | Trigger a critical approval (as 5.5); try a quick tap, then a 1.2 s hold on the tablet. | Quick tap does nothing; hold approves. | ☐ | ☐ | |
| 8.8 | Approve a background task's card from the tablet. | Works for tasks of the PC's current project. | ☐ | ☐ | |
| 8.9 | From the tablet, try to change the project folder, API key, hands-free or tablet access. | Not offered, and refused by the engine if attempted. | ☐ | ☐ | |
| 8.10 | Settings → Tablet access → **Remove** the tablet. | Its connection closes at once; reloading on the tablet asks to pair again. | ☐ | ☐ | |

## 9. Chains and templates

| # | Action | Expected | Pass | Fail | Notes |
|---|---|---|:-:|:-:|---|
| 9.1 | Say "run the tests, then summarise the results, and finally list the changed files". | Progress panel shows 3 steps; each runs in turn in the same session. | ☐ | ☐ | |
| 9.2 | Say "if the tests fail then revert the change". | Runs as one instruction (no split). | ☐ | ☐ | |
| 9.3 | Chain a step that needs approval. | The chain waits; approving continues it; declining stops it after that step's reply. | ☐ | ☐ | |
| 9.4 | Press **Stop steps** mid-chain. | Remaining steps don't run. | ☐ | ☐ | |
| 9.5 | Say "run the TDD template for the cart total". | TDD Cycle runs as a chain. | ☐ | ☐ | |
| 9.6 | Open **Templates**, pick Debugging Workflow. | It runs. | ☐ | ☐ | |
| 9.7 | Say "update the email template". | Ordinary instruction, not a template run. | ☐ | ☐ | |
| 9.8 | Put a valid workflow JSON (README example) and a malformed one in `%APPDATA%\Barnaby\workflows`. | The valid one appears in Templates and runs; the malformed one is skipped and the others still show. | ☐ | ☐ | |

## 10. IDE bridge and editor launch

| # | Action | Expected | Pass | Fail | Notes |
|---|---|---|:-:|:-:|---|
| 10.1 | On a file strip, click **Open in VS Code**, then **Open in Cursor**. | The file opens in each editor (if installed). | ☐ | ☐ | |
| 10.2 | Set `"ide_bridge_enabled": true` in `%APPDATA%\Barnaby\config.json`, restart Barnaby. | `%APPDATA%\Barnaby\ide-bridge.json` appears with a port and token. **Known gap as of this writing:** the engine never starts the bridge (nothing instantiates `IdeBridge`), so this step is expected to fail until that's wired up. | ☐ | ☐ | |
| 10.3 | If 10.2 passed: connect a WebSocket client (no `Origin` header) to `ws://127.0.0.1:<port>`, send `{"type":"auth","token":"<token>"}`, then give Barnaby an instruction. | Client receives the turn's events. Wrong token → closed with 4401. | ☐ | ☐ | |
| 10.4 | If 10.2 passed: connect from a browser page (sends an `Origin`). | Refused with 403. | ☐ | ☐ | |

## 11. Shutdown

| # | Action | Expected | Pass | Fail | Notes |
|---|---|---|:-:|:-:|---|
| 11.1 | Close the Barnaby window while a turn and a task are running. | Within a few seconds, Task Manager shows no `barnaby-engine.exe`, `claude`, or Cursor `node.exe` left over. | ☐ | ☐ | |

**Result:** ____ passed, ____ failed. Failures fixed or listed as known issues in the changelog: ☐
