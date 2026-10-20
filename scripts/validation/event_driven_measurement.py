"""Actual old/new worker-loop cost probe; invoked only by the isolated suite.

PYTHONPATH selects the frozen old backend or candidate backend. No old discovery
SQL is reconstructed here, and process startup/leadership is recorded separately.
"""
from test_recovery_boundaries import DATABASE_NAME  # fail-closed before app imports

import argparse
import asyncio
from collections import Counter
from contextlib import ExitStack
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import time
import tracemalloc
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import asyncpg

from sqlalchemy import event, text
from app.db.session import AsyncSessionLocal, engine
from app.services import access_events, automations
from app.services.access.completion_store import AccessCompletionStore
from app.services.access.completion_worker import AccessCompletionOwner, CompletionStageOwners
from app.services.access.publication import AccessPublicationStore
from app.services.access.publication_worker import AccessPublicationLane
from app.services.ha_presence_attempts import HaPresenceAttemptStore
from app.services.ha_presence_worker import HaPresenceWorker
from app.services.leaderboard_dirty import LeaderboardDirtyStore
from app.services.leaderboard_worker import LeaderboardWorker
from app.services.movement_reconciliation import MovementReconciliationService
from app.services.notifications import NotificationService
from app.services.vehicle_information_jobs import VehicleInformationWorker
from app.services.visitor_passes import VisitorPassService


class Errors(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []
    def emit(self, record):
        # Keep only closed local message identifiers, never formatted provider data.
        self.messages.append({"logger": record.name, "message": str(record.msg)[:160]})


async def durable_outputs():
    result = {}
    async with AsyncSessionLocal() as session:
        for table in ("automation_runs", "notification_runs", "access_completion_work", "vehicle_information_jobs"):
            result[table] = list((await session.execute(text(
                "SELECT count(*),md5(string_agg(md5(to_jsonb(t)::text),'' ORDER BY to_jsonb(t)::text)) FROM " + table + " t"
            ))).one())
    return result


async def main(args):
    root = Path(access_events.__file__).resolve().parents[2]
    source = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(root.rglob("*.py"))}
    startup, queries = [], []
    recording = startup
    def capture(connection, cursor, statement, parameters, context, many):
        task = asyncio.current_task()
        recording.append({"task": task.get_name() if task else "unknown", "sql": statement})
    errors = Errors()
    listener = None
    stops, tasks = [], []
    async def no_effect(*_args, **_kwargs):
        raise AssertionError("idle_fixture_attempted_effect")
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    def check(address):
        assert isinstance(address, tuple) and address[:2] == ("127.0.0.1", 5432), "non-isolated socket"
    def connect(sock, address):
        check(address)
        return original_connect(sock, address)
    def connect_ex(sock, address):
        check(address)
        return original_connect_ex(sock, address)
    with ExitStack() as stack:
        stack.enter_context(patch.object(socket.socket, "connect", connect))
        stack.enter_context(patch.object(socket.socket, "connect_ex", connect_ex))
        stack.enter_context(patch.object(access_events, "get_runtime_config", AsyncMock(return_value=SimpleNamespace(lpr_debounce_quiet_seconds=0, lpr_debounce_max_seconds=0))))
        stack.enter_context(patch.object(access_events, "is_maintenance_mode_active", AsyncMock(return_value=False)))
        async with AsyncSessionLocal() as session:
            assert await session.scalar(text("SELECT current_database()")) == DATABASE_NAME
            assert await session.scalar(text("SELECT count(*) FROM system_settings")) == 0
        initial_outputs = await durable_outputs()
        logging.getLogger().addHandler(errors)
        event.listen(engine.sync_engine, "before_cursor_execute", capture)
        try:
            if args.phase == "candidate":
                from app.services.durable_work import DurableWorkListener
                async def listener_connection():
                    connection = await asyncpg.connect(os.environ["IACS_DATABASE_URL"].replace("+asyncpg", ""), timeout=5, statement_cache_size=0)
                    connection.add_query_logger(lambda record: recording.append({"task":"durable-work-listener","sql":record.query}))
                    return connection
                listener = DurableWorkListener(listener_connection)
                await listener.start()
                assert listener.diagnostics()["connected"]
            lpr = access_events.AccessEventService()
            lpr._stop_event.clear()
            task = asyncio.create_task(lpr._process_queue(), name="access-event-worker")
            tasks.append(task)
            publication = AccessPublicationLane(AccessPublicationStore(), SimpleNamespace(publish=no_effect))
            completion = AccessCompletionOwner(AccessCompletionStore(), CompletionStageOwners(no_effect, no_effect, no_effect, no_effect), publication)
            notification, automation = NotificationService(), automations.AutomationService()
            for owner in (completion, HaPresenceWorker(HaPresenceAttemptStore()),
                          LeaderboardWorker(LeaderboardDirtyStore(), no_effect),
                          VehicleInformationWorker(), VisitorPassService(), MovementReconciliationService()):
                await owner.start()
                stops.append(owner.stop)
            notification.dispatcher.start()
            automation.dispatcher.start()
            stops.extend([notification.dispatcher.stop, automation.dispatcher.stop])
            tasks.append(asyncio.create_task(automation._run_scheduler(), name="automation-scheduler"))
            async with asyncio.timeout(8):
                while not lpr._coordinator_owned:
                    await asyncio.sleep(.01)
            await asyncio.sleep(.1)
            recording = queries
            started = time.monotonic()
            tracemalloc.start()
            try:
                await asyncio.sleep(args.seconds)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            elapsed = time.monotonic() - started
            recording = []  # Shutdown is explicitly outside the steady-state window.
            assert all(not task.done() for task in tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for stop in reversed(stops):
                await stop()
            if listener is not None:
                await listener.stop()
            event.remove(engine.sync_engine, "before_cursor_execute", capture)
            logging.getLogger().removeHandler(errors)
            await engine.dispose()
        final_outputs = await durable_outputs()
        await engine.dispose()
        assert initial_outputs == final_outputs
    report = {"phase":args.phase,"window_seconds":elapsed,"python_peak_bytes":peak,
        "startup_queries":startup,"queries":queries,"counts_by_task":dict(Counter(item["task"] for item in queries)),
        "source_root":str(root),"source_hashes":source,"errors":errors.messages,
        "durable_outputs":final_outputs,
        "limits":"Actual synthetic worker loops; Python allocations include query-capture instrumentation and retained SQL records; they exclude PostgreSQL memory and process RSS. Timings are not production guarantees."}
    Path(args.output).write_text(json.dumps(report, indent=2))
    assert not errors.messages, errors.messages


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--seconds", type=float, default=125)
    parser.add_argument("--output", required=True)
    asyncio.run(main(parser.parse_args()))
