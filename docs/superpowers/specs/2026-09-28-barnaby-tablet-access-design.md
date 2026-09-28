# Barnaby tablet access: design

Date: 2026-09-28. Status: approved to build (user: "create a tablet version", "Superpower complete it").

## Goal

Use Barnaby from a Samsung Galaxy Tab S10 (Android, Chrome) on the same home Wi-Fi. The PC keeps
doing all the work (engine, Whisper, agents); the tablet is a thin, touch-first client that
captures the user's voice and shows and speaks the results.

## Non-goals (v1)

- Running Barnaby on Android itself (agents are local CLIs; not possible on Android).
- Access from outside the home network. No port forwarding, relay, or cloud hop.
- Hands-free (always-listening) mode on the tablet. v1 is hold-to-talk plus typing.
- Streaming Kokoro audio to the tablet. The tablet uses its own Android voice.

## User stories and acceptance criteria

1. **Turn it on.** As the user, I switch on *Tablet access* in the PC's Settings.
   - AC1.1 Off by default; while off, nothing listens on the network.
   - AC1.2 When on, Settings shows the address(es) to open and a **Pair a tablet** button.
   - AC1.3 If the port is taken or the certificate can't be created, Settings shows the reason and
     tablet access reports not running. The rest of Barnaby is unaffected.
2. **Pair.** I tap *Pair a tablet*, scan the QR code with the tablet, and it's paired.
   - AC2.1 The QR encodes `https://<pc-ip>:<port>/#pair=<code>`. The code is single-use, expires
     in 5 minutes, and is in the URL fragment, so it's never sent in an HTTP request line.
   - AC2.2 Pairing issues a random 256-bit device token, stored in the tablet's browser. The
     PC stores only its SHA-256 hash, with the device name, when it was paired, and when it was
     last seen.
   - AC2.3 A wrong, expired, or reused code fails. Five failures from one address within 10
     minutes block that address for 10 minutes.
3. **Trust the connection once.** The connection is HTTPS/WSS. Barnaby creates its own
   certificate authority once and a server certificate for the PC's current LAN addresses.
   - AC3.1 `/barnaby-ca.crt` downloads the CA certificate, so the tablet can trust it and stop
     warning. It's public, not a secret.
   - AC3.2 When the PC's LAN address changes, the server certificate is reissued from the same CA,
     so the one-time trust step isn't repeated.
4. **Talk from the tablet.** I hold the circle on the tablet and speak; on release, the PC
   transcribes it and the turn runs.
   - AC4.1 Audio is recorded on the tablet (16 kHz mono) and sent over the encrypted socket. It
     is transcribed on the PC exactly like push-to-talk audio (review drafts, vocabulary, corrections).
   - AC4.2 Clips over 60 s are rejected; very short or silent clips are ignored like on the PC.
   - AC4.3 If the tablet can't use its microphone (permission denied or not a secure page), the
     tablet says so and typing still works.
5. **Hear and see replies on the tablet.** Replies stream into the tablet's conversation view and
   are spoken by the tablet's own voice.
   - AC5.1 While any tablet is connected, the PC doesn't speak aloud. The engine sends the exact
     sentences it would have spoken (already length-capped and secret-redacted) as `say` events,
     and the tablet speaks those. Raw agent text is never spoken.
   - AC5.2 **Stop speaking** on the tablet silences it.
6. **Approve from the tablet.** Approval cards work on the tablet with the same rules as on the PC.
   Critical commands have no voice approval and need a hold to confirm.
7. **The tablet can't widen its own access.** Remote clients cannot change the project folder,
   API keys, the microphone, hands-free, tablet access or pairing, or open an editor on the PC.
   - AC7.1 These commands from a remote client are refused with an error and have no effect.
   - AC7.2 Remote `set_config` accepts only: engine, review_transcripts, claude_model, cursor_model.
8. **Revoke.** Settings lists paired tablets (connected or not, last seen) with **Remove**.
   - AC8.1 Removing a device deletes its token hash and immediately closes its connection.
9. **PC and tablet together.** The PC window and a tablet can be connected at the same time. Both
   see the same session and events.
   - AC9.1 At most one desktop connection (newer replaces older, as today) and at most one
     connection per paired device (newer replaces older). At most 4 remote connections in total.

## Architecture

```
 Tab S10 (Chrome)                            PC
 ┌─────────────────────┐   HTTPS + WSS    ┌───────────────────────────────────────────────┐
 │ same React UI       │ ───────────────▶ │ RemoteServer  (0.0.0.0:8766, off by default)  │
 │  remote mode:       │  private LAN     │  • static app/dist + CSP     • /barnaby-ca.crt  │
 │  • pairing page     │  only            │  • pairing, device tokens    • per-IP lockout   │
 │  • mic → PCM clip   │                  │  • Origin == own https origin                  │
 │  • speechSynthesis  │                  │           │ authenticated socket, role=remote   │
 └─────────────────────┘                  │           ▼                                      │
                                          │  Bridge (hub): clients{ws: role}, broadcast     │
 Barnaby window (Tauri) ── ws://127.0.0.1 ─▶│  role policy → Orchestrator.handle()           │
                            role=desktop  │  remote audio_clip → Orchestrator._process_audio│
                                          └───────────────────────────────────────────────┘
```

### Engine

- **`server.Bridge` becomes a hub.** It holds a set of authenticated clients with a role
  (`desktop` | `remote`, plus a device id). `emit` broadcasts to every client, each with its own
  send lock. `hello` is sent only to the connecting client and includes `role`. The local
  listener is unchanged in its security: 127.0.0.1 only, process token, Origin allowlist.
- **Role policy** (`server.permit(role, cmd)`) runs before dispatch. Remote clients get the
  AC7 denials. `audio_clip` is accepted only from remote clients.
- **`remote.py`**
  - `TlsMaterial`: CA (10 years) and server certificate (1 year; SANs are the LAN IPs, the
    hostname, `<hostname>.local` and `localhost`), stored in `data_dir()/tablet/`, with private
    keys 0600 on POSIX. The server certificate is reissued when the SANs no longer match the
    current LAN addresses. Uses `cryptography`.
  - `DeviceRegistry`: `devices.json` holding `{id, name, token_sha256, created, last_seen}`.
    It pairs, authenticates with a constant-time compare, and revokes.
  - `Pairing`: at most one live code, 5-minute expiry, single use.
  - `Lockout`: failures per peer IP in a sliding 10-minute window. Five failures lock the IP out
    for 10 minutes.
  - `RemoteServer`: `websockets` server with TLS. `process_request` serves static files from
    `app/dist` (path-confined, CSP and security headers) and `/barnaby-ca.crt`. It rejects
    non-private peers and cross-origin WebSocket upgrades. The first frame is either
    `{"type":"auth","token"}` or `{"type":"pair","code","name"}`; pairing answers with
    `{"type":"paired","token"}` and continues as authenticated.
- **Orchestrator**: `set_remote_listeners(n)`. While n > 0, `_enqueue_speech` emits
  `ev("say", text)` in place of local playback. New handlers for the desktop-only `tablet_*`
  commands and for remote `audio_clip`.
- **Config**: `tablet_access: bool = False` and `tablet_port: int = 8766`. Both are changeable
  only from the desktop.

### Protocol additions

Commands:
- `audio_clip{pcm_b64}` (remote only): 16 kHz mono int16 LE, at most 60 s.
- `tablet_pair_start`, `tablet_revoke{device_id}` (desktop only).
- Remote-only first frame: `pair{code,name}`.

Events:
- `tablet_status{enabled, running, error?, urls[], port, devices[{id,name,created,last_seen,connected}]}`
- `tablet_pairing{url, expires_at}`
- `say{text}`
- `paired{token}`
- `hello.role`

### UI

- `platform.ts` gains a remote mode: the page is served over https and isn't inside Tauri. The
  endpoint is `wss://<location.host>/ws`, and the token comes from `localStorage`
  (`barnaby.device`). If there's no token, the pairing screen shows (it reads `#pair=` from the
  URL).
- `EngineClient` supports a first-frame override so the pairing handshake can happen.
- **Tablet mic**: `getUserMedia({audio})`, then an AudioContext, then an AudioWorklet or
  ScriptProcessor that captures Float32 and resamples to 16 kHz Int16. On release, it sends one
  `audio_clip`.
- **Tablet speech**: `say` events are queued into `speechSynthesis`; Stop cancels them.
- **Layout**: at tablet widths, a bigger hold-to-talk circle and larger touch targets. Settings on
  a remote client hides the PC-only fields.
- **PC Settings → Tablet access**: toggle, address list, "Pair a tablet" (QR rendered as SVG
  from a QR matrix, no innerHTML), paired-device list with Remove, and the one-time trust
  instructions.

## Security analysis

| Threat | Mitigation |
|---|---|
| Stranger on the same Wi-Fi connects | Needs a device token (256-bit) or a live single-use pairing code shown only on the PC screen; per-IP lockout |
| Traffic sniffing on Wi-Fi | TLS for everything; tokens only inside the encrypted socket |
| Malicious web page in the tablet's browser drives the socket | Origin must equal the server's own https origin; token in localStorage of that origin only |
| Public network (café) exposure | Off by default; non-private peers rejected; Windows Firewall rule scoped to Private networks |
| Pairing code leakage via logs/Referer | Code in URL fragment; Referrer-Policy no-referrer; codes expire in 5 min and are single-use |
| Stolen tablet | Remove the device in Settings; closes its socket and deletes its hash |
| Tablet widening its power | Role policy (AC7); remote set_config allowlist |
| Oversized frames / audio bombs | max_size 3 MB on the remote listener; clip ≤ 60 s; base64 validated |
| Path traversal on static files | Resolved path must stay inside `app/dist`; only GET/HEAD |
| Secrets read aloud | Tablet speaks only engine-produced `say` sentences (redacted, capped) |

Accepted risk: anyone holding the unlocked, paired tablet can drive Barnaby with the same
approval rules as the PC. That's the point of the feature, and it's mitigated by revocation.

## Testing

- Engine unittest: registry (hash-only storage, revoke), pairing (expiry, single use), lockout,
  cert issue and reissue on SAN change, role policy matrix, hub broadcast and per-device
  replacement, remote server end to end over real TLS (static, CA download, pair then auth,
  wrong Origin, non-private peer, revoked token closes, audio clip, say events while remote
  connected).
- Vitest: remote endpoint resolution, pairing screen, PCM encoder (resample and int16), say
  queue, Settings tablet section (QR present, revoke sends command), remote Settings hides
  PC-only fields.
- Playwright: mobile viewport (Galaxy Tab S-like) against the mock engine with tablet access on
  over TLS. The flow pairs via the QR URL, then hold-to-talk with Chromium's fake microphone,
  then the transcript and reply appear, then Remove on the desktop page disconnects the tablet.
- Live, on the PC and the Tab S10: pair, trust the CA, talk, approve, revoke.
