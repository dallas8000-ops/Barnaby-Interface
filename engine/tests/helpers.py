import json
import os
import stat
import sys
import tempfile
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines() if line.strip()]


def make_fake_claude(tmp: Path, *, mode: str = "replay", fixture: str = "denied_bash.jsonl") -> Path:
    """An executable standing in for `claude`. Records argv, stdin and the
    --settings file contents to record.json, then behaves per `mode`."""
    record = tmp / "record.json"
    script = tmp / "fake_claude.py"
    script.write_text(f"""#!{sys.executable}
import json, sys, time, os
argv = sys.argv[1:]
stdin = sys.stdin.read()
settings = None
if "--settings" in argv:
    settings = json.load(open(argv[argv.index("--settings") + 1]))
sysprompt = None
if "--append-system-prompt-file" in argv:
    sysprompt = open(argv[argv.index("--append-system-prompt-file") + 1]).read()
json.dump({{"argv": argv, "stdin": stdin, "settings": settings, "sysprompt": sysprompt,
           "cwd": os.getcwd(), "pid": os.getpid()}}, open({str(record)!r}, "w"))
mode = {mode!r}
if mode == "replay":
    for line in open({str(FIXTURES / fixture)!r}):
        sys.stdout.write(line)
        sys.stdout.flush()
elif mode == "noise":
    print("not json at all")
    print(json.dumps({{"type": "result", "subtype": "success", "result": "ok", "session_id": None,
                      "is_error": False, "permission_denials": []}}))
elif mode == "huge":
    big = "x" * (200 * 1024)
    print(json.dumps({{"type": "user", "message": {{"content": [{{"type": "tool_result", "tool_use_id": "t",
                      "content": big, "is_error": False}}]}}}}))
    print(json.dumps({{"type": "result", "subtype": "success", "result": "done", "session_id": None,
                      "is_error": False, "permission_denials": []}}))
elif mode == "no_result":
    sys.stderr.write("Invalid API key . Please run /login\\n")
    sys.exit(1)
elif mode == "hang":
    print(json.dumps({{"type": "system", "subtype": "init", "session_id": "11111111-2222-4333-8444-555555555555"}}), flush=True)
    time.sleep(60)
""")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if sys.platform == "win32":
        # Windows can't exec a shebang script. A .cmd shim is also exactly how an
        # npm-installed claude.cmd runs, so this exercises the real cmd.exe path.
        shim = tmp / "fake_claude.cmd"
        shim.write_text(f'@"{sys.executable}" "{script}" %*\r\n')
        return shim
    return script


def pid_alive(pid: int) -> bool:
    """Portable liveness probe. Never use os.kill(pid, 0) for this on Windows:
    there it calls TerminateProcess instead of probing."""
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def record_of(tmp: Path) -> dict:
    return json.loads((tmp / "record.json").read_text())


class TempDataDir:
    """Points BARNABY_DATA_DIR at a temp dir for the duration of a test."""

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("BARNABY_DATA_DIR")
        os.environ["BARNABY_DATA_DIR"] = self._tmp.name
        return Path(self._tmp.name)

    def __exit__(self, *exc) -> None:
        if self._old is None:
            os.environ.pop("BARNABY_DATA_DIR", None)
        else:
            os.environ["BARNABY_DATA_DIR"] = self._old
        self._tmp.cleanup()
