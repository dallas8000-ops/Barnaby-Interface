# Barnaby task board: implementation plan

Spec: `docs/superpowers/specs/2026-09-29-barnaby-task-board-design.md`

Method: test-first per task (failing test, implement, green, full suite). An independent reviewer
checks the engine after T4 and the whole feature after T7. Findings are fixed before moving on.

## Tasks

- [x] **T1: Lanes and scheduling (pure).** `tasks.py`: `validate_lanes`, `lanes_overlap`,
  `pick_next`, `parse_task_command`. Tests: `test_task_units.py`.
- [x] **T2: Scopes in the agents.** `safety.Scope`, `grant_for_task`; Claude settings/argv for
  task (`default` + lane allows) and conversation (`ask` busy lanes minus opened); `CursorGuard`
  lanes; Devin/mock accept `scope`. Tests: `test_task_scope.py`.
- [x] **T3: Store, config, protocol.** `tasks` table, interrupted-on-start; config keys (desktop
  only); commands/events; remote permissions. Tests: `test_task_protocol.py`.
- [x] **T4: TaskManager + orchestrator wiring.** Per-task agents, parallel runs, lane blocking,
  approvals routed by id, follow-up, cancel, timeout, speech on finish, conversation busy lanes,
  `__main__` factories (mock too). Tests: `test_task_manager.py`.
  **Checkpoint: independent engine review.**
- [x] **T5: UI store and protocol.** Tests: vitest.
- [x] **T6: Task board, assign dialog, Settings → Tasks, toasts.** Tests: vitest.
- [x] **T7: E2E and docs.** Playwright flow on the mock engine; README section.
  **Checkpoint: independent full review.**
- [ ] **T8: Live on the PC.** Sync, suites, relaunch, try a real two-task run, commit on approval.

## Progress log

- T1–T4 done test-first (engine 443 tests). Lane mechanics were probed on the PC first
  (Claude Code 2.1.282): `default` mode + `Edit(./lane/**)` fences a task; `ask` rules fence the
  conversation; ask-rule negation does not work, so it isn't used.
- Engine checkpoint review found 11 issues; 8 confirmed with probes. All fixed with regression
  tests (`test_task_manager.ReviewRegressionTest`), the three race fixes mutation-checked:
  1. Cancel/shutdown were recorded and spoken as failures (a killed CLI ends its stream cleanly).
     Fixed: per-turn generation numbers; stale turn ends change nothing.
  2. Two scheduler passes could start one task twice. Fixed: synchronous claim before any await.
  3. Cancel while approval cards were going out brought the task back. Fixed: group and status set
     before the cards are sent.
  4. Tablet could follow up a task from another project. Fixed in `remote_check`.
  5. Conversation fenced only from its next turn: documented as AC3.5a.
  6. Task cards said "outside the project folder" for in-project edits. Fixed, and they say when
     another task holds that folder.
  7. Two clients deciding at once raised KeyError. 8. Stale run entry, blank instruction crash,
     follow-up lost the original instruction. 9. Titles spoken unredacted. 10. Per-task Cursor
     folders never removed; task text not throttled. 11. User/project Claude settings can widen
     lanes: the task now carries a warning (AC3.5b). All fixed.
- T5–T7 done (UI 111 tests, E2E 12 flows including two task-board flows). A pre-existing E2E race
  in the chained-steps test (it could see the previous step's card) was fixed in the test.
- Full-feature review found 11 issues; 9 confirmed with probes. All fixed with tests:
  1. Task approval cards were lost on reconnect (tablet asleep), leaving the task stuck. Fixed:
     undecided cards ride in every `tasks` snapshot; the UI treats that list as authoritative.
  2. The "not a sandbox" help text was missing and the Claude note overpromised. Fixed.
  3. Folder chips never refreshed. Fixed: the dialog asks for a fresh list when it opens.
  4. Stale cards survived a snapshot. Fixed by 1.
  5. Nested dialog announced with the outer title (duplicate id). Fixed: `useId`.
  6. Tabs lacked tabpanel/aria-controls/arrow keys. Fixed.
  7. The hidden side panel cleared the badge. Fixed: only when the panel is on screen.
  8. "took X" counted idle time before a follow-up. Fixed: each run restarts the clock.
  9. Lane settings: "." accepted, case-duplicate names, silent 8-folder cut, lost input on refusal.
     Fixed in both the UI and the engine validator; the New task dialog stays open on refusal.
  10. Tablet could approve another project's task cards. Fixed in `remote_check`.
  11. Spec/plan drift and a stray screenshot spec. Fixed.
- T8 (part): synced to the PC; engine 446, UI 111 and E2E 12 pass there; release build rebuilt and
  relaunched. A live two-task run with real agents is left for the user (it spends agent usage and
  changes the project).
