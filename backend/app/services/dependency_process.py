"""Run updater commands with Linux-local ownership of their entire child tree.

The private supervisor is the only process made a subreaper. The backend's
signal handlers and child ownership are unchanged. No shell or logging is used.
"""
from __future__ import annotations

import asyncio
import ctypes
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


CLEANUP_SECONDS = 4.0
SUPERVISOR_CLEANUP_SECONDS = 2.0
PROTOCOL_LIMIT = 512


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: bytes


class DependencyProcessTimeout(TimeoutError):
    pass


class DependencyProcessError(RuntimeError):
    pass


def _record(raw: bytes) -> dict:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise DependencyProcessError("Invalid dependency supervisor protocol.") from exc
    if len(raw) > PROTOCOL_LIMIT or not isinstance(value, dict):
        raise DependencyProcessError("Invalid dependency supervisor protocol.")
    return value


async def _read_control(reader: asyncio.StreamReader, supervisor_pid: int, state: dict) -> None:
    ready = _record(await reader.readline())
    pid = ready.get("command_pid")
    if (set(ready) != {"command_pid"} or type(pid) is not int
            or pid <= 1 or pid in {os.getpid(), supervisor_pid}):
        raise DependencyProcessError("Dependency supervisor did not start a valid command.")
    state["command_pid"] = pid
    terminal = _record(await reader.readline())
    if set(terminal) != {"cleaned"} or type(terminal["cleaned"]) is not bool:
        raise DependencyProcessError("Invalid dependency supervisor completion protocol.")
    if await reader.read(1):
        raise DependencyProcessError("Unexpected dependency supervisor protocol data.")
    state["cleaned"] = terminal["cleaned"]


def _owned_group(command_pid: int | None, supervisor_pid: int) -> bool:
    """A control value alone never authorizes signalling a process group."""
    if not command_pid or command_pid <= 1 or command_pid == os.getpgrp():
        return False
    # The leader may have exited while grandchildren retain its group. /proc
    # exposes group/session IDs without reading command lines or environment.
    for index, entry in enumerate(Path("/proc").iterdir()):
        if index >= 20000:
            return False
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == command_pid and int(fields[3]) == supervisor_pid:
                return True
        except (OSError, ValueError, IndexError):
            continue
    return False


async def _cleanup(process, communication, control, state: dict) -> None:
    if process.returncode is None:
        try:
            process.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), CLEANUP_SECONDS)
    except TimeoutError as exc:
        # Only a group demonstrably inside this supervisor's session can be
        # signalled. A broken supervisor cannot turn arbitrary protocol data
        # into authority over another process group.
        if _owned_group(state.get("command_pid"), process.pid):
            try:
                os.killpg(state["command_pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.returncode is None:
            process.kill()
        await asyncio.wait_for(process.wait(), 1.0)
        raise DependencyProcessError("Dependency process cleanup exceeded its deadline.") from exc
    finally:
        try:
            await asyncio.wait_for(asyncio.gather(communication, control, return_exceptions=True), 1.0)
        except TimeoutError:
            # wait_for cancels and joins these Python I/O tasks; it never leaves
            # a second communicate/read task running after this owner returns.
            pass
    if not state.get("cleaned"):
        raise DependencyProcessError("Dependency process cleanup could not be verified.")


async def _finish_owned(task: asyncio.Task) -> tuple[object, bool]:
    """Defer repeated caller cancellation until bounded owned cleanup finishes."""
    cancelled = False
    while True:
        try:
            return await asyncio.shield(task), cancelled
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
        except Exception as exc:
            if cancelled:
                raise asyncio.CancelledError from exc
            raise


async def run_dependency_process(command: list[str], cwd: Path, timeout: float) -> Completed:
    if sys.platform != "linux" or not Path("/proc/self/task").is_dir():
        raise DependencyProcessError("Dependency commands require the Linux process supervisor.")
    if (not command or any(not isinstance(arg, str) or "\0" in arg for arg in command)
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("Invalid dependency command or timeout.")
    read_fd, write_fd = os.pipe()
    reader = asyncio.StreamReader(limit=PROTOCOL_LIMIT)
    transport = None
    process = None
    communication = control = None
    state: dict = {}
    try:
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(read_fd, "rb", buffering=0),
        )
        read_fd = -1
        starting = asyncio.create_task(asyncio.create_subprocess_exec(
            sys.executable, "-I", str(Path(__file__).resolve()), "--supervise", str(write_fd), *command,
            cwd=str(cwd), pass_fds=(write_fd,), start_new_session=True,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        ))
        process, cancelled_during_start = await _finish_owned(starting)
        os.close(write_fd)
        write_fd = -1
        communication = asyncio.create_task(process.communicate())
        control = asyncio.create_task(_read_control(reader, process.pid, state))
        completion = asyncio.gather(communication, control)
        try:
            if cancelled_during_start:
                raise asyncio.CancelledError
            (stdout, _), _ = await asyncio.wait_for(asyncio.shield(completion), timeout)
            if not state.get("cleaned"):
                raise DependencyProcessError("Dependency command descendants were not reaped.")
            return Completed(process.returncode, stdout or b"")
        except BaseException as original:
            cleanup = asyncio.create_task(_cleanup(process, communication, control, state))
            cancelled = isinstance(original, asyncio.CancelledError)
            try:
                _, during_cleanup = await _finish_owned(cleanup)
                cancelled = cancelled or during_cleanup
            except Exception as cleanup_error:
                if cancelled:
                    original.add_note(str(cleanup_error))
                else:
                    raise cleanup_error from original
            if cancelled:
                if isinstance(original, asyncio.CancelledError):
                    raise original
                raise asyncio.CancelledError from original
            if isinstance(original, TimeoutError):
                raise DependencyProcessTimeout("Dependency command exceeded its timeout.") from original
            raise
        finally:
            # Retrieve any protocol exception from the aggregate future too.
            if not completion.done():
                completion.cancel()
            await asyncio.gather(completion, return_exceptions=True)
    finally:
        if transport is not None:
            transport.close()
        if read_fd >= 0:
            os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def _send_control(fd: int, value: dict) -> None:
    os.write(fd, json.dumps(value, separators=(",", ":")).encode() + b"\n")


def _reap_tree(command_pid: int) -> bool:
    if _owned_group(command_pid, os.getpid()):
        try:
            os.killpg(command_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + SUPERVISOR_CLEANUP_SECONDS
    children = Path(f"/proc/self/task/{os.getpid()}/children")
    while time.monotonic() < deadline:
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            return True
        # A descendant may call setsid(). Subreaping makes it our child once
        # its parent exits; kill and reap only these actual adopted children.
        for child in children.read_text().split():
            try:
                os.kill(int(child), signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(0.01)
    return False


def _supervise(control_fd: int, command: list[str]) -> int:
    stopping = False

    def request_stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER, this disposable process only.
        return 125
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    child = None
    cleaned = False
    try:
        child = subprocess.Popen(command, process_group=0, close_fds=True)
        _send_control(control_fd, {"command_pid": child.pid})
        while child.poll() is None and not stopping:
            time.sleep(0.01)
        result = child.returncode if child.returncode is not None else 128 + signal.SIGTERM
        cleaned = _reap_tree(child.pid)
        _send_control(control_fd, {"cleaned": cleaned})
        if not cleaned:
            return 125
        if result < 0:
            signal.signal(-result, signal.SIG_DFL)
            os.kill(os.getpid(), -result)
        return result
    finally:
        if child is not None and not cleaned:
            _reap_tree(child.pid)
        os.close(control_fd)


if __name__ == "__main__":
    if sys.platform != "linux" or len(sys.argv) < 4 or sys.argv[1] != "--supervise":
        raise SystemExit(125)
    try:
        raise SystemExit(_supervise(int(sys.argv[2]), sys.argv[3:]))
    except (OSError, ValueError):
        raise SystemExit(125)
