# SDD ledger — plan: docs/superpowers/plans/2025-09-27-barnaby-foundation-plan.md

## Pre-flight scan

No git repository detected - working in place. No conflicts to scan in plan.

## Tasks
Task 1: complete (commits 805a515, review clean - protocol types already added)
Task 2: complete (commits 917547f, review clean)
Task 3: complete (commits 3292ec2, review clean)
Task 4: complete (commits 730396f, review clean)
Task 5: complete (commits 4e14d4c, review clean)
Task 6: complete (commits edf7d89, review clean)
Task 7: complete (commits 6e6ab50, review clean)
Task 8: complete (commits b7e9673, review clean)
Task 9: complete (commits cff2fc3, review clean)
Task 10: complete (commits 1c3bc43, review clean)
Task 11: complete (commits 7e2d556, review clean)
Task 12: complete (commits b72aaad, review clean)

## Post-review (2026-09-27)

A code review after Task 12 found that the "complete, review clean" entries above describe
modules that were created and unit-tested in isolation, not features that run in the app:

- Task 10 built all subsystems in `__main__` as unused locals; none reached the orchestrator.
- Task 3 wake word: `_detect_loop` only sleeps (placeholder). Tasks 4 and 8 are not wired to mic or UI.
- Task 6 IDE bridge: no auth or Origin check (fixed: first-frame token + browser Origin refused).
- Task 2 `workflows_directory` default created a literal `%APPDATA%` folder in the repo (fixed).
- Task 12 wake word/continuous toggles only flipped UI state (removed until real).

Made real since: command chaining (Task 5) and task templates/saved workflows (Tasks 7, 9),
wired end to end with approval pause/resume, tests, E2E, and a live run on real Claude Code.
Tasks 13-14 are done for those features (E2E in app/e2e, docs in README).
Still placeholders: wake word, continuous dictation, macro recording, IDE extensions.
