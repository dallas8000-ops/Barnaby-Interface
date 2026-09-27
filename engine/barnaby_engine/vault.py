"""API keys for cloud agents, kept in Windows Credential Manager.

A key never lives in config.json, the session store, logs or the UI. The UI can
save or remove one, and learns only whether one is saved. Entries are generic
credentials named ``Barnaby-<name>``, the same ones ``scripts/set-secret.ps1``
writes, stored per user on this PC (CRED_PERSIST_LOCAL_MACHINE).

Off Windows (development and CI) keys come read-only from the environment
variable ``BARNABY_SECRET_<NAME>``.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Protocol

NAMES = frozenset({"devin"})
_TARGET = "Barnaby-{}"
# Printable ASCII with no whitespace: API keys are tokens, and this rejects a
# pasted sentence, a trailing newline, or two keys pasted together.
_KEY_RE = re.compile(r"^[\x21-\x7e]{16,512}$")


class VaultError(RuntimeError):
    """Messages are safe to show: they never contain the key."""


def valid_key(value: str) -> bool:
    return bool(_KEY_RE.match(value))


class Backend(Protocol):
    def read(self, target: str) -> str | None: ...
    def write(self, target: str, user: str, value: str) -> None: ...
    def delete(self, target: str) -> None: ...


class WindowsCredentials:
    """advapi32 CredReadW / CredWriteW / CredDeleteW via ctypes, no extra dependency."""

    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as w

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [("Flags", w.DWORD), ("Type", w.DWORD), ("TargetName", w.LPWSTR),
                        ("Comment", w.LPWSTR), ("LastWritten", w.FILETIME),
                        ("CredentialBlobSize", w.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                        ("Persist", w.DWORD), ("AttributeCount", w.DWORD), ("Attributes", ctypes.c_void_p),
                        ("TargetAlias", w.LPWSTR), ("UserName", w.LPWSTR)]

        self._ct = ctypes
        self._CREDENTIAL = CREDENTIAL
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        self._read = adv.CredReadW
        self._read.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
        self._read.restype = w.BOOL
        self._write = adv.CredWriteW
        self._write.argtypes = [ctypes.POINTER(CREDENTIAL), w.DWORD]
        self._write.restype = w.BOOL
        self._delete = adv.CredDeleteW
        self._delete.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD]
        self._delete.restype = w.BOOL
        self._free = adv.CredFree
        self._free.argtypes = [ctypes.c_void_p]
        self._free.restype = None

    def read(self, target: str) -> str | None:
        ct = self._ct
        ptr = ct.POINTER(self._CREDENTIAL)()
        if not self._read(target, self.CRED_TYPE_GENERIC, 0, ct.byref(ptr)):
            if ct.get_last_error() == self.ERROR_NOT_FOUND:
                return None
            raise VaultError(f"Couldn't read {target} from Windows Credential Manager (error {ct.get_last_error()}).")
        try:
            cred = ptr.contents
            if not cred.CredentialBlobSize:
                return None
            raw = ct.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        finally:
            self._free(ptr)
        try:
            return raw.decode("utf-16-le")
        except UnicodeDecodeError:
            raise VaultError(f"{target} in Windows Credential Manager isn't a text key; save it again.") from None

    def write(self, target: str, user: str, value: str) -> None:
        ct = self._ct
        data = value.encode("utf-16-le")
        buf = (ct.c_ubyte * len(data)).from_buffer_copy(data)
        cred = self._CREDENTIAL()
        cred.Type = self.CRED_TYPE_GENERIC
        cred.TargetName = target
        cred.UserName = user
        cred.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        cred.CredentialBlobSize = len(data)
        cred.CredentialBlob = ct.cast(buf, ct.POINTER(ct.c_ubyte))
        try:
            if not self._write(ct.byref(cred), 0):
                raise VaultError(f"Couldn't save to Windows Credential Manager (error {ct.get_last_error()}).")
        finally:
            ct.memset(buf, 0, len(data))  # don't leave a copy of the key in this buffer

    def delete(self, target: str) -> None:
        if not self._delete(target, self.CRED_TYPE_GENERIC, 0) and \
                self._ct.get_last_error() != self.ERROR_NOT_FOUND:
            raise VaultError(f"Couldn't remove {target} (error {self._ct.get_last_error()}).")


class EnvCredentials:
    """Read-only fallback off Windows: BARNABY_SECRET_<NAME>."""

    def read(self, target: str) -> str | None:
        name = target.removeprefix("Barnaby-").upper()
        return os.environ.get(f"BARNABY_SECRET_{name}") or None

    def write(self, target: str, user: str, value: str) -> None:
        raise VaultError("Saving keys is only supported on Windows. Set BARNABY_SECRET_<NAME> instead.")

    def delete(self, target: str) -> None:
        raise VaultError("Removing keys is only supported on Windows.")


class MemoryCredentials:
    """In-process store for --mock runs and tests."""

    def __init__(self) -> None:
        self.items: dict[str, tuple[str, str]] = {}

    def read(self, target: str) -> str | None:
        item = self.items.get(target)
        return item[1] if item else None

    def write(self, target: str, user: str, value: str) -> None:
        self.items[target] = (user, value)

    def delete(self, target: str) -> None:
        self.items.pop(target, None)


def default_backend() -> Backend:
    return WindowsCredentials() if sys.platform == "win32" else EnvCredentials()


class Vault:
    def __init__(self, backend: Backend | None = None) -> None:
        self._backend = backend or default_backend()

    @staticmethod
    def _target(name: str) -> str:
        if name not in NAMES:
            raise VaultError(f"Unknown key name {name!r}.")
        return _TARGET.format(name)

    def get(self, name: str) -> str | None:
        value = self._backend.read(self._target(name))
        return value.strip() if value and value.strip() else None

    def has(self, name: str) -> bool:
        try:
            return self.get(name) is not None
        except VaultError:
            return False

    def set(self, name: str, value: str) -> None:
        value = value.strip()
        if not valid_key(value):
            raise VaultError("That doesn't look like an API key (16 to 512 characters, no spaces).")
        self._backend.write(self._target(name), name, value)

    def delete(self, name: str) -> None:
        self._backend.delete(self._target(name))

    def status(self) -> dict[str, bool]:
        return {name: self.has(name) for name in sorted(NAMES)}
