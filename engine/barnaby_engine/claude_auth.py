"""Signing in to Claude Code from inside Barnaby.

Barnaby has no Claude login of its own: it runs the ``claude`` CLI, which keeps the login.
This module only starts the CLI's own ``auth login`` and reads ``auth status``.
Nothing the user types ever reaches us, and the argv is fixed.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass

from .agent import AgentError, resolve_claude

TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"
STATUS_TIMEOUT_S = 20.0
LOGIN_TIMEOUT_S = 300.0


@dataclass(frozen=True)
class AuthStatus:
    signed_in: bool
    method: str | None = None
    error: str | None = None  # why we couldn't tell (e.g. Claude Code isn't installed)


def _env_without_token() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k.upper() != TOKEN_VAR}


class ClaudeAuth:
    def __init__(self, claude_bin: str = "") -> None:
        self.claude_bin = claude_bin
        self._proc: asyncio.subprocess.Process | None = None

    async def status(self) -> AuthStatus:
        """What the CLI says right now. It reports whether a login exists, not whether the
        server still accepts it; a revoked token still reads as signed in."""
        try:
            claude = resolve_claude(self.claude_bin)
        except AgentError as exc:
            return AuthStatus(False, error=str(exc))
        try:
            proc = await asyncio.create_subprocess_exec(
                claude, "auth", "status", stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}))
            out, _ = await asyncio.wait_for(proc.communicate(), STATUS_TIMEOUT_S)
        except (OSError, asyncio.TimeoutError):
            return AuthStatus(False, error="Couldn't ask Claude Code whether you're signed in.")
        try:
            data = json.loads(out.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return AuthStatus(False, error="Claude Code's sign-in status wasn't readable.")
        if not isinstance(data, dict):
            return AuthStatus(False, error="Claude Code's sign-in status wasn't readable.")
        method = data.get("authMethod")
        return AuthStatus(bool(data.get("loggedIn")), method if isinstance(method, str) and method != "none" else None)

    async def login(self) -> bool:
        """Run ``claude auth login`` and wait for it. True if it finished successfully.

        It gets its own console window: the CLI opens the browser by itself, and if the
        browser can't hand the result back (another profile, a remote session) the user pastes
        the code there. A stale CLAUDE_CODE_OAUTH_TOKEN is withheld so it can't mask the new login.
        """
        claude = resolve_claude(self.claude_bin)
        kwargs: dict[str, object] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        else:
            kwargs.update(start_new_session=True, stdin=asyncio.subprocess.DEVNULL,
                          stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        self._proc = proc = await asyncio.create_subprocess_exec(
            claude, "auth", "login", "--claudeai", env=_env_without_token(), **kwargs)
        try:
            code = await asyncio.wait_for(proc.wait(), LOGIN_TIMEOUT_S)
        except asyncio.TimeoutError:
            await self.cancel()
            raise AgentError("Sign-in took longer than 5 minutes, so it was stopped.") from None
        finally:
            if self._proc is proc:
                self._proc = None
        return code == 0

    async def cancel(self) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(proc.pid), "/T", "/F", stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            await killer.wait()
        else:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            pass

    @staticmethod
    def drop_stale_token() -> bool:
        """After a fresh login: stop handing the old token to every ``claude`` turn, because it
        outranks the saved login. Only this process is affected; Windows settings are untouched."""
        return os.environ.pop(TOKEN_VAR, None) is not None
