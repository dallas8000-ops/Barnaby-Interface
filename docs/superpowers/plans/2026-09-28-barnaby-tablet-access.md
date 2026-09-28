# Barnaby tablet access: implementation plan

Spec: `docs/superpowers/specs/2026-09-28-barnaby-tablet-access-design.md`

Method: each task is test-first (write the failing test, watch it fail, implement, watch it pass),
then the full suite runs. An independent reviewer (a subagent that didn't write the code) checks
the engine after T5 and the whole feature after T9, against the spec's acceptance criteria and
threat table. Review findings are fixed before moving on.

## Tasks

- [x] **T1: Hub bridge and roles.** `server.py`: client set with roles, broadcast emit,
  per-client locks, hello to the new client only (with `role`), one desktop and one connection
  per device, cap of 4 remote connections, `permit(role, cmd)` policy.
  Tests: `test_server.py` (existing tests stay green), new `test_hub.py`.
- [x] **T2: Protocol and config.** Commands `audio_clip`, `tablet_pair_start`, `tablet_revoke`;
  events `tablet_status`, `tablet_pairing`, `say`, `paired`. Config `tablet_access` and
  `tablet_port` are desktop-only (not in the remote set_config allowlist).
  Tests: protocol parsing and bounds.
- [x] **T3: TLS material, registry, pairing, lockout** (`remote.py`, pure logic).
  Tests: `test_remote_units.py`.
- [x] **T4: RemoteServer.** TLS websockets server, static files with CSP, CA download,
  private-peer check, Origin check, first-frame pair/auth, revoke closes the socket.
  Tests: `test_remote_server.py` over real TLS on 127.0.0.1.
- [x] **T5: Orchestrator wiring.** `say` events while remote clients are connected;
  `audio_clip`, `_process_audio`; `tablet_*` handlers; start and stop the RemoteServer when
  `tablet_access` changes; `tablet_status` in hello; `__main__` wiring (mock mode too, for
  Playwright). Tests: `test_tablet_orchestrator.py`.
  **Checkpoint: independent engine review.**
- [x] **T6: UI remote mode.** `platform.ts` endpoint and token; pairing screen; `EngineClient`
  first-frame hook; store (role, say, tablet_status, tablet_pairing).
  Tests: vitest.
- [x] **T7: Tablet mic and speech.** `lib/tabletMic.ts` (capture, resample to 16 k, int16,
  base64); hold-to-talk on remote uses it; `lib/tabletSpeech.ts` speaks `say` queue with Stop.
  Tests: vitest (encoder, queue).
- [x] **T8: PC Settings → Tablet access, plus the remote layout.** Toggle, addresses, QR (SVG
  from `qrcode-generator` matrix), device list with Remove, trust instructions; remote Settings
  hides PC-only fields; bigger touch targets at tablet widths.
  Tests: vitest.
- [x] **T9: E2E and docs.** Playwright tablet flow (mobile viewport, fake mic, TLS mock engine);
  README section; setup notes (firewall rule scoped to Private).
  **Checkpoint: independent full review.**
- [ ] **T10: Live on the PC and the Tab S10** (needs the PC linked). Sync, run suites, add the
  firewall rule, pair, trust the CA, talk, approve, revoke, commit.

## Progress log

- T1–T5 done test-first. The engine checkpoint review found 7 issues; 5 were confirmed with
  probes. All were fixed with regression tests, and each fix was mutation-checked
  (`test_hub.ReviewRegressionTest`, `test_remote_server.ReviewRegressionServerTest`,
  `test_tablet_orchestrator.ReviewRegressionOrchestratorTest`):
  1. A revoked tablet could stay connected (race while reconnecting). Fixed: synchronous
     registration plus an `admit` re-check.
  2. A stalled client froze every emit. Fixed: per-send timeout that drops the client.
  3. Remote `load_session` could switch the PC's project folder. Fixed: `remote_check` veto.
  4. Unauthenticated sockets were unlimited and 3 MB frames were accepted before auth.
     Fixed: 4 KB first-frame limit, raised after auth; pending cap of 4 per IP and 16 in total.
  5. A non-object first frame crashed the handler. Fixed: counted as a failure.
  6. The certificate wasn't reissued when the IP changed while running. Fixed:
     `refresh_tls()` on pairing and on the status check.
  7. A full registry burned the code; a failed token send left an orphan; two clips could
     transcribe at once. All fixed.
  - Spec gap: Public networks. Fixed: `network_problem()` refuses to start on a Public
    Windows network, and `scripts/tablet-firewall.ps1` adds a Private-only rule.
- Also found by my own smoke test: `lan_addresses()` accepted non-RFC 1918 "private" IPs
  (192.0.2.x), which would violate the CA's name constraints. Fixed and tested.
- T6–T9 done test-first. The final full-feature review found 9 issues; 3 were
  confirmed with probes. All were fixed with regression tests, and each fix was
  mutation-checked:
  1. A clean engine stop closed tablets with 4403 "removed", so tablets forgot their
     pairing whenever the PC closed Barnaby. Fixed: stop uses 1001; 4403 is for revoke only.
  2. Releasing during Android's mic permission prompt left the mic recording, and the next
     clip sent stale audio. Fixed: a start/stop generation counter in `TabletMic`.
  3. AC5.2: no Quiet control while the tablet was still reading. Fixed: tablet-side
     speaking state, Quiet stops locally and in the engine, and hold-to-talk barges in.
  4. 4429 was used for both "locked out" and "too many tablets", and the client retried
     forever. Fixed: 4430 for too many tablets; both are terminal with a reason, and the
     pairing is kept.
  5. Hold-to-confirm vs Android long-press. Fixed: touch-none/select-none, pointer
     capture, context menu suppressed.
  6. The QR code could pick a virtual adapter's address. Fixed: default-route address first.
  7. The pairing panel stayed after the code was used. Fixed: cleared when a device is added.
  8. PC-only controls showed on the tablet. Fixed: hidden (the engine still refuses them).
  9. Mic level updates came at ~375 Hz. Fixed: throttled to animation rate.
- Final counts: engine 345, UI 83, E2E 10 (including the full tablet flow over TLS with a
  fake microphone).
