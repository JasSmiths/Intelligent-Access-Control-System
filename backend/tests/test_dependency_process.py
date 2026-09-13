"""Inert subprocess contracts. Run in the isolated Linux test container only."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time

import pytest

from app.services import dependency_process as owner

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(sys.platform != "linux", reason="Linux supervisor contract")]


async def test_success_captures_stdout_stderr_and_uses_requested_cwd_without_shell(tmp_path):
    argument = "; touch must-not-exist"
    result = await owner.run_dependency_process(
        [sys.executable, "-u", "-c",
         "import os,sys; print(os.getcwd()); print(sys.argv[1]); print('stderr',file=sys.stderr)", argument],
        tmp_path, 5,
    )
    assert result.returncode == 0
    assert str(tmp_path).encode() in result.stdout
    assert argument.encode() in result.stdout and b"stderr" in result.stdout
    assert not (tmp_path / "must-not-exist").exists()


async def test_nonzero_exit_preserves_output_and_returncode(tmp_path):
    result = await owner.run_dependency_process(
        [sys.executable, "-c", "print('synthetic failure'); raise SystemExit(7)"], tmp_path, 5,
    )
    assert result == owner.Completed(7, b"synthetic failure\n")


async def test_signal_exit_preserves_negative_returncode(tmp_path):
    result = await owner.run_dependency_process(
        [sys.executable, "-c", "import os,signal; os.kill(os.getpid(),signal.SIGTERM)"], tmp_path, 5,
    )
    assert result.returncode == -signal.SIGTERM


IDENTITY = """
def identity(path):
    path.write_text(json.dumps({'pid': os.getpid(), 'start': Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]}))
"""


def tree_command(tmp_path, *, exit_parent=False, detached=False):
    child = "\n".join([
        "import json,os,signal,sys,time", "from pathlib import Path", IDENTITY,
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
        "identity(Path(sys.argv[1]))", "time.sleep(120)",
    ])
    script = "\n".join([
        "import json,os,subprocess,sys,time", "from pathlib import Path", IDENTITY,
        f"child = subprocess.Popen([sys.executable, '-c', {child!r}, 'child.json'], start_new_session={detached!r})",
        "identity(Path('parent.json'))",
        "while not Path('child.json').exists(): time.sleep(0.01)",
        "Path('ready').write_text('ready')",
        "os._exit(0)" if exit_parent else "time.sleep(120)",
    ])
    return [sys.executable, "-c", script]


async def ready(tmp_path):
    async with asyncio.timeout(5):
        while not (tmp_path / "ready").exists():
            await asyncio.sleep(0.01)


def assert_tree_reaped(tmp_path):
    for name in ("parent.json", "child.json"):
        identity = json.loads((tmp_path / name).read_text())
        # A zombie is a failure too: kill(pid,0) alone cannot prove reaping.
        assert not Path(f"/proc/{identity['pid']}").exists(), f"Owned {name} process still exists"


async def finish_test_task(task, tmp_path):
    """On a failed assertion, never abandon this test's task or inert children."""
    if not task.done():
        task.cancel()
    try:
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 8)
    finally:
        for name in ("parent.json", "child.json"):
            path = tmp_path / name
            if not path.exists():
                continue
            identity = json.loads(path.read_text())
            process = Path(f"/proc/{identity['pid']}/stat")
            try:
                fields = process.read_text().rsplit(")", 1)[1].split()
                if fields[19] == identity["start"]:
                    os.kill(identity["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
            except FileNotFoundError:
                pass


@pytest.mark.parametrize("detached", [False, True])
async def test_timeout_kills_and_reaps_term_ignoring_descendants(tmp_path, detached):
    started = time.monotonic()
    task = asyncio.create_task(owner.run_dependency_process(tree_command(tmp_path, detached=detached), tmp_path, 1.5))
    try:
        await ready(tmp_path)
        with pytest.raises(owner.DependencyProcessTimeout):
            await task
        assert time.monotonic() - started < 1.5 + owner.CLEANUP_SECONDS + 2
        assert_tree_reaped(tmp_path)
    finally:
        await finish_test_task(task, tmp_path)


@pytest.mark.parametrize("detached", [False, True])
async def test_cancellation_is_preserved_after_reaping_even_when_cancelled_again(tmp_path, detached):
    task = asyncio.create_task(owner.run_dependency_process(tree_command(tmp_path, detached=detached), tmp_path, 60))
    repeated = None
    try:
        await ready(tmp_path)
        started = time.monotonic()
        task.cancel()
        repeated = asyncio.get_running_loop().call_later(0.001, task.cancel)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - started < owner.CLEANUP_SECONDS + 2
        assert_tree_reaped(tmp_path)
    finally:
        if repeated:
            repeated.cancel()
        await finish_test_task(task, tmp_path)


@pytest.mark.parametrize("detached", [False, True])
async def test_exited_parent_cannot_leave_its_child_or_zombie_behind(tmp_path, detached):
    task = asyncio.create_task(owner.run_dependency_process(
        tree_command(tmp_path, exit_parent=True, detached=detached), tmp_path, 5,
    ))
    try:
        result = await task
        assert result.returncode == 0
        assert_tree_reaped(tmp_path)
    finally:
        await finish_test_task(task, tmp_path)


async def test_missing_executable_refuses_success_without_echoing_command(tmp_path):
    command = "/synthetic-private-command-does-not-exist"
    with pytest.raises(owner.DependencyProcessError) as error:
        await owner.run_dependency_process([command], tmp_path, 5)
    assert command not in str(error.value)


async def test_unsupported_platform_refuses_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(owner.sys, "platform", "unsupported")
    with pytest.raises(owner.DependencyProcessError, match="Linux"):
        await owner.run_dependency_process([sys.executable, "-c", "raise AssertionError"], tmp_path, 5)


@pytest.mark.parametrize("raw", [b"not-json\n", b"[]\n", b'{"command_pid":true}\n',
                                   b'{"command_pid":1}\n', b'{"command_pid":-1}\n'])
async def test_invalid_control_records_grant_no_group_authority(raw):
    reader = asyncio.StreamReader(limit=owner.PROTOCOL_LIMIT)
    reader.feed_data(raw)
    reader.feed_eof()
    state = {}
    with pytest.raises(owner.DependencyProcessError):
        await owner._read_control(reader, os.getpid() + 1, state)
    assert state == {}
    assert owner._owned_group(os.getpgrp(), os.getpid() + 1) is False
