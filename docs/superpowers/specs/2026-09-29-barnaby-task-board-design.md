# Barnaby task board (parallel agents in folder lanes): design

Date: 2026-09-29. Status: approved to build (user: "assign a task to Devin, Claude or Cursor… it can
do multitasking and provide an update when completed… assign the backend, frontend or API";
isolation choice: **folder lanes**).

## Goal

Hand work to several coding agents at once and get told when each one is done. Each background
task has one engine, one instruction and a set of folders (its *lane*) that it may change. Tasks
run in parallel with each other and with the normal conversation; tasks whose lanes overlap wait
their turn.

## Non-goals (v1)

- Git worktrees, branches or extra copies of the project (user's standing rule: no duplicate
  local copies). Everything happens in the one project folder.
- Agents talking to each other or splitting one request into sub-tasks automatically. The user
  (or a voice command) assigns each task.
- Windows toast notifications (needs a new Tauri plugin and a Rust rebuild). Updates are spoken,
  shown on the board and as in-app toasts, and reach the tablet.
- Resuming a local task after Barnaby restarts. It is marked *interrupted*; the user can send a
  follow-up, which resumes the agent's own session.

## Verified mechanics (probes on the PC, Claude Code 2.1.282, 2026-09-29)

| Settings (headless `-p`) | Result |
|---|---|
| `defaultMode: default`, `allow: [Edit(./a/**)]` | `a/x.txt` written; `b/y.txt` reported in `permission_denials`, not written |
| same plus `Edit(./b/y.txt)` | both written (exact-file allow works) |
| `acceptEdits`, `ask: [Edit(./b/**)]` | `a/` written; `b/` reported in `permission_denials` |
| `acceptEdits`, `ask: [Edit(**), Edit(!a/**)]` | **both** denied: negation does not carve a lane out. Not used. |

`./path` is relative to the working directory (the project); `/path` would be relative to the
settings file (a temp dir), so it is never used. `Edit` rules also govern `Write` and `MultiEdit`.

Cursor in headless mode does not gate file edits at all (see `cursor_agent.py`), so its lane is
enforced by `CursorGuard`, which stops the turn when an edit outside the lane starts.

## User stories and acceptance criteria

1. **Assign a task.** On the board I choose an engine, pick folders (or a saved lane such as
   *backend*), type the instruction, and press *Start*. Or I say "task for Devin in backend: add
   the orders endpoint".
   - AC1.1 The task appears at once as *queued* or *running*, with engine, lanes and title.
   - AC1.2 Lanes are folders inside the project. Absolute paths, `..`, glob characters, missing
     folders and folders that resolve outside the project (links) are refused with a reason.
   - AC1.3 No lane means *whole project*: that task runs only when no other local task runs.
   - AC1.4 An engine that isn't available (not installed, no Devin key) is refused with its reason.
2. **Run in parallel.** Up to `max_parallel_tasks` (default 3, 1–6) tasks run at once.
   - AC2.1 Local tasks (Claude, Cursor) with overlapping lanes never run at the same time. Two
     lanes overlap when one equals or contains the other. A blocked task starts as soon as the
     lane frees; tasks behind it whose lanes are free may start first.
   - AC2.2 Devin tasks work in Devin's cloud on their own copy of the repo, so they don't hold
     local lanes, but they count toward the parallel limit.
   - AC2.3 Each task gets its own agent process and its own CLI session. Cursor tasks get their
     own config directory, so parallel Cursor runs don't overwrite each other's settings or history.
   - AC2.4 At most 20 tasks can be queued or running.
3. **Stay in the lane.**
   - AC3.1 Claude tasks run with `defaultMode: default` and `Edit(./<lane>/**)` allow rules. An
     edit outside the lane becomes an approval card for that task; approving allows exactly that
     file for the resumed turn.
   - AC3.2 Edits outside the project can't be approved in a task (the card says why).
   - AC3.3 Cursor tasks stop with a clear reason when an edit outside the lane starts. (Cursor
     can't pause for approval on edits; the edit may already have been applied, which the reason
     says.)
   - AC3.4 The normal conversation keeps working while tasks run. Its Claude turns get
     `ask: Edit(./<busy lane>/**)` for lanes held by running tasks in the same project, so an edit
     there becomes an approval card; approving opens that lane for the resumed turn only. A
     Cursor conversation turn that edits a busy lane is stopped with the reason.
   - AC3.5a Known gap: a conversation turn that was already running when a task started keeps the
     permissions it started with; the busy-lane rule applies from its next turn (approval resumes
     count as new turns).
   - AC3.5b Claude Code merges the user's and the project's own settings with Barnaby's per-turn
     file. A saved `Edit`/`Write` allow rule there widens every lane, so the task card warns when
     one is found.
   - AC3.5 Bash commands follow the existing rules (read-only/test allowlist, everything else is a
     card). Lanes are a coordination guard between agents, not a sandbox; the spec says so in the UI
     help text.
4. **Approve per task.** Cards (`task_approval_required` events, kept apart from the
   conversation's `approval_required`) show the task. Approve/Deny use the existing commands; the
   engine routes by request id. Voice "yes" applies only to the conversation's own
   cards, never to background tasks (you might be answering something else).
   - AC4.1 While a task waits for approval it keeps its lanes but frees its parallel slot.
   - AC4.2 Barnaby says "<engine> task <title> needs your OK." once per group.
5. **Get told when it's done.**
   - AC5.1 On finish, Barnaby speaks one sentence ("Devin finished orders endpoint.") plus the first
     sentence of the agent's summary, redacted and capped like all speech.
   - AC5.2 The board shows status, duration, cost when known, the summary, and the full reply on
     expand. A failure shows the reason. The UI shows an in-app toast.
   - AC5.3 The tablet gets the same events and toasts.
6. **Follow up, cancel, clear.**
   - AC6.1 *Follow up* on a finished, failed or interrupted task queues a new turn in the same
     agent session with the same lanes.
   - AC6.2 *Cancel* stops that task only (process tree killed; a Devin session keeps running in
     the cloud, as with Stop today). *Stop* in the conversation never cancels tasks.
   - AC6.3 *Clear finished* removes done/failed/cancelled/interrupted tasks from the board.
7. **Survive restarts.** Tasks are stored in the local database. On start, tasks that were
   queued, running or waiting are marked *interrupted*.
8. **Saved lanes.** Settings → Tasks lets me name lanes per project (e.g. *backend* = `engine`,
   *frontend* = `app`). Voice and the dialog accept a saved name or a top-level folder name.
9. **Tablet.** A paired tablet may create, cancel, follow up, clear and approve tasks of the PC's
   current project (another project's tasks: view and cancel only, like its sessions). Saved lanes,
   the parallel limit and the task timeout are PC-only settings. Undecided cards are in every
   `tasks` snapshot, so a tablet that wakes up after a card was sent can still answer it.

## Design

### Engine

- `tasks.py` (pure logic, unit-tested): `validate_lanes`, `lanes_overlap`, `parse_task_command`
  (voice grammar), `TaskRecord` dataclass, `TaskScheduler.pick_next(queued, running)`.
- `TaskManager` (in `tasks.py`), owned by the orchestrator:
  - `create`, `cancel`, `follow_up`, `clear_finished`, `decide(request_id, approve)`,
    `busy_lanes(workspace)`, `snapshot()`, `shutdown()`.
  - An `agent_factory(engine, task_id)` builds a fresh agent per task (the shared agents hold
    one process each and can't run two turns).
  - Runs each task in its own asyncio task; events are `task_update` (full record) and
    `task_text` (streamed delta, throttled) plus `approval_required` with `task_id`.
  - Prompt preamble tells the agent it runs in parallel, lists its folders, and asks for a short
    summary of what changed and how it was checked.
- `Scope` (in `safety.py`): `allow_lanes` (task) or `ask_lanes` (conversation), plus
  `open_lanes` from an approval. `CliAgent.run(..., scope=None)`; Claude builds settings from it;
  Cursor passes it to `CursorGuard`; Devin and the mock accept and ignore it (Devin gets the
  lanes in its prompt).
- `grant_for_task(tool, input, workspace)`: an edit inside the project → `Edit(./rel/path)`
  exact; outside or with glob characters → None.
- Store: `tasks` table; result text capped at 20 000 characters.
- Config: `max_parallel_tasks` (3), `task_timeout_s` (3600), `task_lanes` ({workspace: {name:
  [folders]}}). All desktop-only.
- Protocol: `task_create`, `task_cancel`, `task_follow_up`, `task_clear`, `task_list`; events
  `tasks` (snapshot with top-level folders, saved lanes and undecided approval cards; also in
  `hello`), `task_update`, `task_text` (throttled), `task_activity`, `task_done`,
  `task_approval_required`, `task_approval_decided`.

### UI

- Store: `tasks`, `taskText`, `taskApprovals`.
- `TaskBoard` as a *Tasks* tab in the activity panel: task cards (status, engine, lanes, elapsed,
  summary, expand for the reply, inline approval cards, Cancel / Follow up / Remove), a
  *New task* button and *Clear finished*.
- `AssignTaskDialog`: engine (unavailable ones disabled with the reason), lane chips (saved lanes
  and top-level folders), instruction, optional title.
- Settings → Tasks: parallel limit, saved lanes editor.
- Toast and a count badge on the Tasks tab when a task finishes or needs approval.

## Threats and failure modes

| Threat / failure | Mitigation |
|---|---|
| Two agents edit the same files | Lane overlap check; conversation gets `ask` rules on busy lanes; Cursor guard |
| Lane string widens a permission rule (`**`, `..`, `(`) | `validate_lanes` rejects glob/paren/`..`/absolute; path resolved inside the project |
| Symlink lane points outside the project | Resolved path must be inside the resolved project root |
| Approval for one task applied to another | Request ids are per task group; grant goes only to that task's resumed turn |
| Voice "yes" approves a background risky action | Voice approval is conversation-only |
| Tablet raises its own limits or edits lanes | Lane/limit/timeout settings are PC-only config keys |
| Runaway cost | Parallel cap, queue cap, per-task timeout, cancel per task, costs shown |
| Shared Cursor config races | Per-task Cursor config directory |
| Transcript injection into argv | Unchanged: Claude prompt via stdin; Cursor via `--` with no shell |
| Engine restart mid-task | Marked interrupted; follow-up resumes the agent session |
| Secrets read aloud in summaries | Summary goes through the existing speech redaction and cap |

## Testing

Engine (unittest): lane validation and overlap; scheduler ordering and limits; Claude settings for
task and conversation scopes; task grants; Cursor guard lanes; TaskManager with scripted agents
(parallel run, lane blocking, approvals routed by id, follow-up resume, cancel, timeout,
restart → interrupted, speech on finish, remote permissions). UI (Vitest): store reducers, board,
dialog validation. E2E (Playwright, mock engine): assign two tasks, see both run, approve one, see
completion toasts.
