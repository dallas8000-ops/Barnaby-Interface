# Release checklist

Work through this top to bottom for every release. Tick each box in the release PR description (copy the list), so the record of what was checked stays with the release.

Version numbers live in five places and must all match: `app/package.json` (and the root entries of `app/package-lock.json`), `app/src-tauri/Cargo.toml`, `app/src-tauri/tauri.conf.json`, and `engine/pyproject.toml`.

## 1. Code is ready

- [ ] Everything meant for this release is merged into `master`. Open PRs that aren't going in are noted in the changelog under *Not in this release*.
- [ ] CI is green on the `master` commit you'll tag: all four jobs in `.github/workflows/ci.yml`, **Engine (windows-latest)**, **Engine (ubuntu-latest)**, **UI (typecheck, Vitest, Playwright E2E)** and **Desktop shell (Windows)**.
- [ ] The same checks pass locally on the release PC (the one you'll build on):
  ```powershell
  cd engine; .venv\Scripts\python -m unittest discover -s tests -t .
  .venv\Scripts\python -m pip install ruff; .venv\Scripts\ruff check barnaby_engine tests --select E,F,W,B --ignore E501,B011
  cd ..\app; npm ci; npm run typecheck; npm test; npm run build
  cd ..; .\scripts\e2e.ps1
  cd app\src-tauri; cargo clippy -- -D warnings
  ```
- [ ] The engine venv has the pinned dependency versions (`pip install -e "engine[voice]"` from a clean venv if in doubt). In particular `websockets` must be 15.x, the version CI tests.

## 2. Version and changelog

- [ ] Pick the version (semver: breaking config or protocol change = major, new feature = minor, fixes only = patch).
- [ ] Bump it in all five places listed above. `git grep -n '"version": "\|^version = '` should show the same number everywhere.
- [ ] `CHANGELOG.md` at the repo root (create it on the first release) has a section for this version with the date, and *Added*, *Changed*, *Fixed*, *Security* and *Known issues* subsections where they apply. Known issues include anything that failed in the manual test script and was accepted for this release.
- [ ] README is still true: the test counts in **Tests**, **Requirements and supported platforms** (bump the "verified against" versions if you re-verified Claude Code, Cursor CLI or Devin), and **Status and roadmap**.

## 3. License

- [ ] `LICENSE` at the repo root is the proprietary "All rights reserved" notice, and its year is current.
- [ ] Package metadata still says proprietary: `"license": "UNLICENSED"` in `app/package.json`, `license-file = "../../LICENSE"` in `Cargo.toml`, `license = "LicenseRef-Proprietary"` in `engine/pyproject.toml`, and the `copyright` line in `tauri.conf.json`.
- [ ] No new dependency has a license that conflicts with closed-source distribution (GPL/AGPL in the bundled engine or app). Check what changed since the last release:
  ```powershell
  cd app; npx license-checker --production --summary
  cd ..\engine; .venv\Scripts\python -m pip install pip-licenses; .venv\Scripts\pip-licenses --order=license
  ```

## 4. Build the installer

- [ ] On the release PC, from a clean checkout of the tagged commit:
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
  powershell -ExecutionPolicy Bypass -File .\scripts\build-release.ps1
  ```
- [ ] The script ends with `Installers: app\src-tauri\target\release\bundle\` and both `bundle\nsis\Barnaby_<version>_x64-setup.exe` and `bundle\msi\Barnaby_<version>_x64_en-US.msi` exist with the new version in their names.
- [ ] Note the installer sizes in the release notes (the engine sidecar is large because of torch).

## 5. Installer smoke test

On a Windows 10 or 11 PC (ideally a clean user account or VM, not the build tree):

- [ ] Uninstall any previous Barnaby first, then run the NSIS installer. It installs per user, without an admin prompt.
- [ ] Barnaby starts from the Start menu. The first start is slower while the engine unpacks; the window opens and the sidebar connects (no "engine not running" banner).
- [ ] The installed version is the new one: Windows Settings → Apps → Installed apps → Barnaby shows `<version>` (the app itself has no About screen yet).
- [ ] Pick a project folder; **Hearing** and **Voice** go from *Loading* to ready (the speech models download once, about 250 MB).
- [ ] One spoken instruction runs through Claude and is read back.
- [ ] Close the window: no `barnaby-engine.exe` is left running in Task Manager.
- [ ] Install the MSI on a second machine or after uninstalling, and repeat the start/close check.
- [ ] Upgrade path: install the previous release, use it once (so `%APPDATA%\Barnaby` has sessions and a voice profile), then install the new one over it. Old sessions and the voice profile are still there.

## 6. Full manual test

- [ ] Run [manual-test-script.md](manual-test-script.md) on the installed build, on a real PC with a microphone and speakers, with a tablet for the tablet section. Attach the filled-in copy to the release PR. Any *Fail* is either fixed before release or listed under *Known issues* in the changelog.

## 7. Publish

- [ ] Tag the release commit: `git tag -a v<version> -m "Barnaby <version>"` and `git push origin v<version>`.
- [ ] Create the GitHub release from the tag, paste the changelog section, and attach both installers and their SHA-256 hashes (`Get-FileHash <file> -Algorithm SHA256`).
- [ ] After publishing, download the installer from the release page on another PC and confirm the hash matches.
