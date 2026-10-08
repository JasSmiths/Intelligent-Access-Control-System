"""Actual export owner admission, request lifetime and truthful commit receipts."""
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError

from test_process_lifecycle import owned_resources as owned_resources

from app import main
from app.api.v1 import reports as api
from app.models import ReportExport
from app.services import report_exports as exports
from app.services import report_renderer as renderer
from app.services.report_contracts import (
    ReportAuthority, ReportAuthorityError, ReportBusyError, ReportOutcomeUnknown,
    ReportRequest, ReportStoppedError, ReportTimeoutError, ReportWork,
)


class Session:
    def __init__(self, harness):
        self.harness = harness
        self.row = None
        self.closed = False
        self.committed = False
        self.close_error = False
    async def __aenter__(self):
        self.harness.open.add(self)
        return self
    async def __aexit__(self, *_args):
        await self.close()
    async def connection(self, **_kwargs):
        if self.harness.connection_hook:
            await self.harness.connection_hook(self)
    async def execute(self, _statement):
        return None
    def add(self, row):
        self.row = row
    async def flush(self):
        if self.harness.flush_hook:
            await self.harness.flush_hook(self)
    async def commit(self):
        if self.harness.commit_hook:
            await self.harness.commit_hook(self)
        self.committed = True
        self.harness.durable[self.row.id] = self.row
    async def rollback(self):
        self.row = None
    async def close(self):
        self.closed = True
        self.harness.open.discard(self)
        if self.close_error:
            raise ConnectionError("synthetic close failure")
    async def get(self, model, identity):
        assert model is ReportExport
        self.harness.lookups.append(identity)
        if self.harness.lookup_hook:
            return await self.harness.lookup_hook(self, identity)
        return self.harness.durable.get(identity)


class Harness:
    def __init__(self, monkeypatch, tmp_path):
        self.open = set()
        self.sessions = []
        self.durable = {}
        self.lookups = []
        self.rendered = []
        self.audits = []
        self.commit_hook = self.flush_hook = self.connection_hook = self.lookup_hook = None
        self.actor = SimpleNamespace(id=uuid.uuid4(), username="synthetic", display_name="Synthetic", role="Admin")
        self.authority = ReportAuthority(self.actor.id, "synthetic-token-never-production")
        self.request = ReportRequest(uuid.uuid4(), None, datetime(2026, 5, 1, 9, tzinfo=UTC),
                                     datetime(2026, 5, 1, 10, tzinfo=UTC), False, True, True)
        self.path = tmp_path / "owned.pdf"
        self.numbers = iter(["123456", "234567", "345678"])
        @asynccontextmanager
        async def source(session, work):
            work.check()
            yield SimpleNamespace(site_timezone="Europe/London")
        async def number(_session):
            return next(self.numbers)
        async def snapshot(_session, **kwargs):
            assert kwargs["include_profile_source"] is True
            assert kwargs["verify_snapshot_availability"] is False
            assert kwargs["period_start"].utcoffset().total_seconds() == 0
            return {"report_id": kwargs["report_number"], "subject_type": "person",
                    "subject": {"id": str(self.request.person_id)}, "events": [], "person": {}}
        async def render(value, path, *, work):
            assert not self.open and all(s.closed for s in self.sessions)
            work.check()
            self.rendered.append(value["report_id"])
            path.write_bytes(b"%PDF-synthetic")
            return path.stat().st_size
        async def authenticate(_session, token):
            assert token == self.authority.token
            return self.actor
        async def audit(_session, **kwargs):
            self.audits.append(kwargs)
        monkeypatch.setattr(exports, "report_read_transaction", source)
        monkeypatch.setattr(exports, "generate_report_number", number)
        monkeypatch.setattr(exports, "build_movement_report_snapshot", snapshot)
        monkeypatch.setattr(exports, "render_person_movement_report_pdf", render)
        monkeypatch.setattr(exports, "authenticate_token", authenticate)
        monkeypatch.setattr(exports, "actor_from_user", lambda actor: actor.username)
        monkeypatch.setattr(exports, "write_audit_log", audit)
        monkeypatch.setattr(exports, "_resolve_report_path", lambda *_args, **_kwargs: self.path)
        self.owner = exports.ReportExportPipeline(self.factory)
    def factory(self):
        session = Session(self)
        self.sessions.append(session)
        return session


@pytest.fixture
def harness(monkeypatch, tmp_path):
    return Harness(monkeypatch, tmp_path)


async def test_detached_source_fresh_authority_audit_and_precommit_receipt(harness):
    await harness.owner.start()
    receipt = await harness.owner.export(harness.request, harness.authority)
    assert receipt["report_id"] == "123456"
    assert receipt["pdf_bytes"] == len(b"%PDF-synthetic")
    assert len(harness.durable) == 1
    row = next(iter(harness.durable.values()))
    assert row.created_by_user_id == harness.actor.id
    assert row.pdf_path == f"reports/{row.id}.pdf"
    assert harness.audits[0]["metadata"]["subject_id"] == str(harness.request.person_id)
    assert all(s.closed for s in harness.sessions)
    assert harness.path.exists() and harness.owner._active is None


async def test_failed_artifact_setup_releases_admitted_slot(harness, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("synthetic path setup failure")
    monkeypatch.setattr(exports, "_resolve_report_path", fail)
    await harness.owner.start()
    with pytest.raises(OSError):
        await harness.owner.export(harness.request, harness.authority)
    assert harness.owner._active is None and harness.owner._work is None
    monkeypatch.setattr(exports, "_resolve_report_path", lambda *_args, **_kwargs: harness.path)
    assert (await harness.owner.export(harness.request, harness.authority))["report_id"] == "123456"


@pytest.mark.parametrize("stop", [False, True])
async def test_cancel_or_service_stop_retains_capacity_until_actual_thread_drains(harness, monkeypatch, stop):
    entered, release = Event(), Event()
    def blocked(snapshot, work):
        entered.set()
        assert release.wait(5)
        work.check()
        return snapshot
    monkeypatch.setattr(renderer, "report_for_pdf", blocked)
    monkeypatch.setattr(exports, "render_person_movement_report_pdf", renderer.render_person_movement_report_pdf)
    monkeypatch.setattr(renderer, "async_playwright", lambda: pytest.fail("stopped thread started browser acquisition"))
    await harness.owner.start()
    request_task = asyncio.create_task(harness.owner.export(harness.request, harness.authority))
    await asyncio.to_thread(entered.wait, 5)
    if stop:
        stop_task = asyncio.create_task(harness.owner.stop())
        await asyncio.sleep(0)
    else:
        request_task.cancel()
        stop_task = None
        await asyncio.sleep(0)
    assert harness.owner._active is not None
    with pytest.raises(ReportBusyError):
        await harness.owner.export(harness.request, harness.authority)
    assert not request_task.done()
    if stop_task:
        assert not stop_task.done()
    release.set()
    with pytest.raises(ReportStoppedError if stop else asyncio.CancelledError):
        await request_task
    if stop_task:
        await stop_task
    assert not harness.durable and not harness.path.exists() and harness.owner._active is None


@pytest.mark.parametrize("stage", ["driver", "browser", "page"])
async def test_stop_during_browser_acquisition_retrieves_and_closes_result(harness, monkeypatch, stage):
    entered, release = asyncio.Event(), asyncio.Event()
    closed = []
    async def block(name, result):
        if stage == name:
            entered.set()
            await release.wait()
        return result
    class Browser:
        async def new_page(self, **_kwargs):
            return await block("page", object())
        async def close(self):
            closed.append("browser")
    class Driver:
        def __init__(self):
            self.chromium = self
        async def launch(self, **kwargs):
            assert kwargs["timeout"] > 0
            return await block("browser", Browser())
        async def stop(self):
            closed.append("driver")
    class Starter:
        async def start(self):
            return await block("driver", Driver())
    monkeypatch.setattr(renderer, "async_playwright", Starter)
    monkeypatch.setattr(renderer, "report_for_pdf", lambda snapshot, work: snapshot)
    monkeypatch.setattr(renderer, "_TEMPLATES", SimpleNamespace(get_template=lambda _: SimpleNamespace(render=lambda **_: "<p>synthetic</p>")))
    monkeypatch.setattr(exports, "render_person_movement_report_pdf", renderer.render_person_movement_report_pdf)
    await harness.owner.start()
    request_task = asyncio.create_task(harness.owner.export(harness.request, harness.authority))
    await entered.wait()
    stop_task = asyncio.create_task(harness.owner.stop())
    await asyncio.sleep(0)
    assert not stop_task.done() and harness.owner._active is not None
    release.set()
    with pytest.raises(ReportStoppedError):
        await request_task
    await stop_task
    assert closed == (["driver"] if stage == "driver" else ["browser", "driver"])
    assert not harness.durable and harness.owner._active is None


async def test_final_authority_rejection_never_persists_or_keeps_artifact(harness):
    await harness.owner.start()
    harness.actor = None
    with pytest.raises(ReportAuthorityError):
        await harness.owner.export(harness.request, harness.authority)
    assert not harness.durable and not harness.path.exists()


async def test_commit_accepted_during_stop_remains_receipt_despite_close_failure(harness):
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked(session):
        entered.set()
        await release.wait()
        session.close_error = True
    harness.commit_hook = blocked
    await harness.owner.start()
    request_task = asyncio.create_task(harness.owner.export(harness.request, harness.authority))
    await entered.wait()
    stop_task = asyncio.create_task(harness.owner.stop())
    await asyncio.sleep(0)
    assert not stop_task.done()
    release.set()
    receipt = await request_task
    await stop_task
    assert receipt["report_id"] == "123456" and harness.path.exists()
    assert len(harness.durable) == 1 and len(harness.rendered) == 1


@pytest.mark.parametrize("known", [False, True])
async def test_uncertain_commit_and_cleanup_fault_never_deletes_artifact_or_retries_create(harness, known):
    async def uncertain(session):
        if known:
            harness.durable[session.row.id] = session.row
        session.close_error = True
        raise ConnectionError("synthetic uncertain commit")
    async def lookup(session, identity):
        session.close_error = True
        return harness.durable.get(identity)
    harness.commit_hook, harness.lookup_hook = uncertain, lookup
    await harness.owner.start()
    if known:
        receipt = await harness.owner.export(harness.request, harness.authority)
        assert receipt["report_id"] == "123456" and len(harness.lookups) == 1
    else:
        with pytest.raises(ReportOutcomeUnknown, match="123456"):
            await harness.owner.export(harness.request, harness.authority)
        assert len(harness.lookups) == 3
    assert harness.path.exists() and len(harness.rendered) == 1
    assert len(harness.audits) == 1 and harness.owner._active is None


async def test_number_collision_rerenders_with_fresh_number_and_same_uuid(harness):
    class Collision(Exception):
        constraint_name = "ux_report_exports_report_number"
    ids = []
    async def collide_once(session):
        ids.append(session.row.id)
        if len(ids) == 1:
            raise IntegrityError("synthetic", {}, Collision())
    harness.flush_hook = collide_once
    await harness.owner.start()
    receipt = await harness.owner.export(harness.request, harness.authority)
    assert receipt["report_id"] == "234567" and receipt["report"]["report_id"] == "234567"
    assert harness.rendered == ["123456", "234567"] and len(set(ids)) == 1
    assert len(harness.durable) == 1 and harness.path.exists()


async def test_whole_deadline_drains_thread_before_releasing_capacity(harness, monkeypatch):
    entered, release = Event(), Event()
    def blocked(snapshot, work):
        entered.set()
        assert release.wait(5)
        work.check()
    monkeypatch.setattr(exports, "REPORT_PIPELINE_SECONDS", 0.02)
    monkeypatch.setattr(renderer, "report_for_pdf", blocked)
    monkeypatch.setattr(exports, "render_person_movement_report_pdf", renderer.render_person_movement_report_pdf)
    await harness.owner.start()
    task = asyncio.create_task(harness.owner.export(harness.request, harness.authority))
    await asyncio.to_thread(entered.wait, 5)
    await asyncio.sleep(0.04)
    assert not task.done() and harness.owner._active is not None
    release.set()
    with pytest.raises(ReportTimeoutError):
        await task
    assert harness.owner._active is None and not harness.durable


async def test_export_api_uses_short_real_auth_dependency_and_no_yield_session(harness, monkeypatch):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1/reports")
    monkeypatch.setattr(api, "AsyncSessionLocal", harness.factory)
    monkeypatch.setattr(api, "extract_http_token", lambda request: asyncio.sleep(0, result=harness.authority.token))
    async def auth(session, token):
        assert token == harness.authority.token
        return harness.actor
    monkeypatch.setattr(api, "authenticate_token", auth)
    async def owned(request, authority, **_kwargs):
        assert all(session.closed for session in harness.sessions)
        assert authority == harness.authority and request.person_id == harness.request.person_id
        return {"report_id": "123456"}
    monkeypatch.setattr(api.report_exports, "export", owned)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/reports/person-movements/export", json={
            "person_id": str(harness.request.person_id), "period_start": "2026-05-01T09:00:00Z", "period_end": "2026-05-01T10:00:00Z",
        })
    assert response.status_code == 200 and len(harness.sessions) == 1


@pytest.mark.parametrize("committing", [False, True])
async def test_actual_asgi_disconnect_through_body_middleware_stops_future_work_but_keeps_commit(harness, monkeypatch, committing):
    import json
    from starlette.middleware.base import BaseHTTPMiddleware
    entered, release = asyncio.Event(), asyncio.Event()
    received_disconnect, stopped = asyncio.Event(), asyncio.Event()
    original_stop = ReportWork.request_stop
    def observed_stop(work):
        original_stop(work)
        stopped.set()
    monkeypatch.setattr(ReportWork, "request_stop", observed_stop)
    middleware_seen = []
    async def blocked_render(snapshot, path, *, work):
        entered.set()
        await release.wait()
        work.check()
        pytest.fail("disconnected request started artifact write")
    async def blocked_commit(session):
        entered.set()
        await release.wait()
    if committing:
        harness.commit_hook = blocked_commit
    else:
        monkeypatch.setattr(exports, "render_person_movement_report_pdf", blocked_render)
    await harness.owner.start()
    monkeypatch.setattr(api, "report_exports", harness.owner)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1/reports")
    app.dependency_overrides[api.report_authority] = lambda: harness.authority
    async def middleware(request, call_next):
        middleware_seen.append(request.url.path)
        return await call_next(request)
    app.add_middleware(BaseHTTPMiddleware, dispatch=middleware)
    messages = asyncio.Queue()
    await messages.put({"type": "http.request", "body": json.dumps({
        "person_id": str(harness.request.person_id), "period_start": "2026-05-01T09:00:00Z",
        "period_end": "2026-05-01T10:00:00Z",
    }).encode(), "more_body": False})
    async def receive():
        message = await messages.get()
        if message["type"] == "http.disconnect":
            received_disconnect.set()
        return message
    sent = []
    async def send(message):
        sent.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/api/v1/reports/person-movements/export", "raw_path": b"/api/v1/reports/person-movements/export",
             "query_string": b"", "headers": [(b"content-type", b"application/json")], "client": ("127.0.0.1", 1), "server": ("test", 80)}
    task = asyncio.create_task(app(scope, receive, send))
    await entered.wait()
    await messages.put({"type": "http.disconnect"})
    await received_disconnect.wait()
    # Let the receive port set the existing work flag; no task cancellation is
    # sent to the endpoint, matching Uvicorn's actual disconnect behavior.
    await asyncio.wait_for(stopped.wait(), 5)
    assert harness.owner._work._stop.is_set() and harness.owner._active is not None
    release.set()
    await asyncio.wait_for(task, 5)
    assert middleware_seen == [scope["path"]]
    assert len(harness.durable) == int(committing)
    assert harness.path.exists() is committing
    assert harness.owner._active is None
    assert next(message["status"] for message in sent if message["type"] == "http.response.start") == (200 if committing else 503)


async def test_busy_export_creates_no_disconnect_waiter_and_success_drains_one(harness):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def commit(session):
        entered.set()
        await release.wait()
    async def disconnected():
        calls.append("admitted")
        await asyncio.Future()
    async def forbidden():
        pytest.fail("rejected busy export allocated a receive waiter")
    harness.commit_hook = commit
    await harness.owner.start()
    task = asyncio.create_task(harness.owner.export(harness.request, harness.authority, disconnected=disconnected))
    await entered.wait()
    with pytest.raises(ReportBusyError):
        await harness.owner.export(harness.request, harness.authority, disconnected=forbidden)
    release.set()
    await task
    assert calls == ["admitted"]
    assert not [task for task in asyncio.all_tasks() if task.get_name() == "report-disconnect"]


@pytest.mark.parametrize("stage", ["thread", "driver", "commit"])
async def test_full_lifespan_cleanup_waits_actual_report_work_even_after_stop_timeout(harness, owned_resources, monkeypatch, stage):
    events, _controls = owned_resources
    entered, release = asyncio.Event(), asyncio.Event()
    thread_entered, thread_release = Event(), Event()
    cleanup = []
    if stage == "thread":
        def blocked(snapshot, work):
            thread_entered.set()
            assert thread_release.wait(5)
            cleanup.append("thread")
            work.check()
        monkeypatch.setattr(renderer, "report_for_pdf", blocked)
        monkeypatch.setattr(exports, "render_person_movement_report_pdf", renderer.render_person_movement_report_pdf)
    elif stage == "driver":
        class Driver:
            async def stop(self):
                cleanup.append("driver")
        class Starter:
            async def start(self):
                entered.set()
                await release.wait()
                return Driver()
        monkeypatch.setattr(renderer, "async_playwright", Starter)
        monkeypatch.setattr(renderer, "report_for_pdf", lambda snapshot, work: snapshot)
        monkeypatch.setattr(renderer, "_TEMPLATES", SimpleNamespace(get_template=lambda _: SimpleNamespace(render=lambda **_: "synthetic")))
        monkeypatch.setattr(exports, "render_person_movement_report_pdf", renderer.render_person_movement_report_pdf)
    else:
        async def commit(session):
            entered.set()
            await release.wait()
            cleanup.append("commit")
        harness.commit_hook = commit
    monkeypatch.setattr(main, "report_exports", harness.owner)
    real_wait_for = asyncio.wait_for
    async def short_stop(awaitable, *, timeout):
        return await real_wait_for(awaitable, timeout=.02)
    monkeypatch.setattr(main.asyncio, "wait_for", short_stop)
    lifespan = main.lifespan(FastAPI())
    await lifespan.__aenter__()
    request_task = asyncio.create_task(harness.owner.export(harness.request, harness.authority))
    if stage == "thread":
        assert await asyncio.to_thread(thread_entered.wait, 5)
    else:
        await entered.wait()
    shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
    await asyncio.sleep(.04)  # Exceed injected outer service-stop timeout.
    assert not shutdown.done() and harness.owner._active is not None
    assert ("stop", "database") not in events
    thread_release.set()
    release.set()
    if stage == "commit":
        assert (await request_task)["report_id"] == "123456"
    else:
        with pytest.raises(ReportStoppedError):
            await request_task
    await shutdown
    assert cleanup == [stage]
    assert events[-1] == ("stop", "database")
    assert harness.owner._active is None and all(session.closed for session in harness.sessions)
