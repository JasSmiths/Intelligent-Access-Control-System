"""Accepted results and canonical late arrival hydration in isolated PostgreSQL."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import event as sql_event, func, select, text
from test_recovery_boundaries import isolated_resources as isolated_resources

from app.db.session import AsyncSessionLocal, engine
from app.models import (AccessCompletionWork, AccessEvent, AuditLog, LprIngestEvent,
    MovementSagaRecord, VehicleInformationJob, VisitorPass, VisitorPassReservationRecord)
from app.models.enums import AccessDecision, AccessDirection, MovementSagaState, VisitorPassStatus, VisitorPassType
from app.services.access import completion_results, completion_store, visitor_link
from app.services.access.completion_contracts import ExternalCompletionOrigin, VisitorCompletionTarget
from app.services.access.completion_results import (InformationClaim, accept_information_in_session,
    accept_visual_in_session, assert_information_before_commit, release_visitor_link_in_session,
    reserve_information_in_session, prepare_visual_in_session)
from app.services.access.completion_store import AccessCompletionStore, reserve_external_completion_in_session
from app.services.access.pipeline_contracts import PipelineLeaseLost
from app.services.vehicle_information_jobs import VehicleInformationWorker
from app.services.visitor_passes import get_visitor_pass_service

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def resources(isolated_resources):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE access_events,visitor_passes CASCADE"))
        await session.commit()


async def context(*, offset=-240, baseline=None, consumed=False, target=True, pass_type=VisitorPassType.ONE_TIME):
    async with AsyncSessionLocal() as session:
        now = await completion_store.database_now(session)
        origin = now + timedelta(seconds=offset)
        visitor = VisitorPass(id=uuid.uuid4(), visitor_name="Synthetic retained arrival", pass_type=pass_type,
            status=VisitorPassStatus.ACTIVE, expected_time=origin, valid_from=origin-timedelta(seconds=1),
            valid_until=origin+timedelta(seconds=60), vehicle_colour=baseline,
            number_plate="AB12CDE" if pass_type==VisitorPassType.DURATION else None)
        session.add(visitor); await session.flush()
        event = AccessEvent(id=uuid.uuid4(), registration_number="AB12CDE", direction=AccessDirection.ENTRY,
            decision=AccessDecision.GRANTED, confidence=1, source="synthetic", occurred_at=origin,
            raw_payload={"visitor_pass":{"id":str(visitor.id),"mode":"arrival"}})
        session.add(event); await session.flush()
        saga = MovementSagaRecord(id=uuid.uuid4(),idempotency_key=str(uuid.uuid4()),source="synthetic",
            access_event_id=event.id,registration_number=event.registration_number,occurred_at=origin,
            direction=event.direction,decision=event.decision,state=MovementSagaState.RECONCILIATION_REQUIRED,
            admission_status="pending")
        session.add(saga);await session.flush()
        event.raw_payload={**event.raw_payload,"movement_saga":{"id":str(saga.id)}}
        # Persist an original reservation as recovery would see it, with its
        # original expired dispatch budget. No new hardware authority is granted.
        session.add(VisitorPassReservationRecord(visitor_pass_id=visitor.id,access_event_id=event.id,
            intent_id=uuid.uuid5(event.id,"automatic-gate-open"),pass_type=pass_type.value,
            normalized_plate=event.registration_number,state="reserved",dispatch_deadline=origin+timedelta(seconds=60)))
        await session.flush()
        await reserve_external_completion_in_session(session,event_id=event.id,
            origin=ExternalCompletionOrigin(uuid.uuid4(),origin),
            visitor_target=VisitorCompletionTarget(visitor.id,visitor.vehicle_make,visitor.vehicle_colour) if target else None)
        # No hidden media workers; this owner fixture exercises only visual/link.
        await session.execute(text("UPDATE access_completion_work SET state='skipped' WHERE stage IN ('snapshot','zone_shadow')"))
        await reserve_information_in_session(session,event,origin_at=origin)
        await session.commit()
        ids=(event.id,visitor.id,saga.id,origin)
    if consumed:
        await admission(ids)
    return ids


async def admission(ids):
    event_id,_,saga_id,_=ids
    async with AsyncSessionLocal() as session:
        saga=await session.get(MovementSagaRecord,saga_id,with_for_update=True)
        event=await session.get(AccessEvent,event_id,with_for_update=True)
        saga.admission_status="verified";saga.state=MovementSagaState.COMPLETED
        await get_visitor_pass_service().settle_admission_in_session(session,event=event,
            gate_command_id=None,admission_status="verified",verification_evidence={"state":"open"},entry_delivery="accepted")
        await release_visitor_link_in_session(session,event)
        await session.commit()


async def visual(ids, colour="Blue"):
    claim=(await AccessCompletionStore().claim(1))[0]
    assert claim.stage=="visual"
    async with AsyncSessionLocal() as session:
        assert await prepare_visual_in_session(session,claim)>0
    async with AsyncSessionLocal() as session:
        reference=await accept_visual_in_session(session,claim,{"observed_vehicle_color":colour,"retained_display":"value"})
        await session.commit()
    return reference


async def information(ids, *, make=None, colour="Green", status="queued"):
    event_id,_,_,origin=ids
    async with AsyncSessionLocal() as session:
        event=await session.get(AccessEvent,event_id,with_for_update=True)
        job=await session.get(VehicleInformationJob,event_id,with_for_update=True)
        token=uuid.uuid4();now=await completion_results.database_now(session)
        job.status,job.lease_token,job.lease_until="processing",token,now+timedelta(seconds=120)
        claim=InformationClaim(event_id,token,event.registration_number,None,origin,job.deadline)
        old_until=job.lease_until
        reference=await accept_information_in_session(session,event,job,claim,make=make,colour=colour)
        job.status=status;job.lease_token,job.lease_until=None,None
        await assert_information_before_commit(session,event,job,claim,lease_until=old_until)
        await session.commit()
        return reference


async def link():
    claims=await AccessCompletionStore().claim(1)
    assert len(claims)==1 and claims[0].stage=="visitor_link"
    async with AsyncSessionLocal() as session:
        outcome=await visitor_link.apply_visitor_link_in_session(session,claims[0])
        await session.commit()
    return outcome


async def rows(ids):
    async with AsyncSessionLocal() as session:
        visitor=await session.get(VisitorPass,ids[1])
        work=await session.scalar(select(AccessCompletionWork).where(AccessCompletionWork.event_id==ids[0],AccessCompletionWork.stage=="visitor_link"))
        audits=await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action=="visitor_pass.arrival_enriched",AuditLog.target_id==str(ids[1])))
        event=await session.get(AccessEvent,ids[0])
        return visitor,work,audits,event


@pytest.mark.parametrize("kind",["visual","information"])
async def test_accepted_at_four_minutes_waits_for_late_used_arrival_and_restarts_without_provider(kind,monkeypatch):
    from app.services import vehicle_information_jobs
    def forbidden():raise AssertionError("late database linker must not obtain a provider")
    monkeypatch.setattr(vehicle_information_jobs,"get_vehicle_information_service",forbidden)
    ids=await context()
    if kind=="visual":await visual(ids)
    else:
        await visual(ids,colour=None)
        await information(ids)
    before=(await rows(ids))[1]
    assert before.state=="waiting" and before.result["requested"] and not before.result["applied"]
    original_clock=completion_store.database_now
    async def later(session):return await original_clock(session)+timedelta(seconds=120)
    monkeypatch.setattr(completion_store,"database_now",later)
    await admission(ids)
    assert await link()=="arrival_enriched"
    visitor,work,audits,_=await rows(ids)
    assert visitor.status==VisitorPassStatus.USED and visitor.valid_until<await later_clock()
    assert visitor.vehicle_colour==("Blue" if kind=="visual" else "Green") and audits==1
    assert work.origin_at==ids[3] and work.deadline==ids[3]+timedelta(seconds=300)
    assert await AccessCompletionStore().claim(2)==[]


async def later_clock():
    async with AsyncSessionLocal() as session:return await completion_store.database_now(session)


async def test_partial_final_and_visual_precedence_use_one_trailing_link_and_retained_receipt():
    ids=await context(consumed=True)
    await visual(ids);await link()
    first=await information(ids,make="First",colour=None)
    await link()
    assert (await rows(ids))[0].vehicle_colour=="Blue"
    second=await information(ids,make="Final",colour="Green",status="failed")
    assert second.revision==first.revision+1 and second.accepted_at>=first.accepted_at
    await link()
    visitor,work,audits,_=await rows(ids)
    assert (visitor.vehicle_make,visitor.vehicle_colour)==("Final","Green") and audits==3
    assert work.result["applied"]==work.result["requested"]
    async with AsyncSessionLocal() as session:
        job=await session.get(VehicleInformationJob,ids[0]);job.status="expired";await session.commit()
    assert (await rows(ids))[0].vehicle_colour=="Green"


@pytest.mark.parametrize("replacement",[None,"User Red"])
async def test_owned_field_clear_or_replacement_is_protected_and_cannot_reacquire_baseline(replacement):
    ids=await context(consumed=True)
    await visual(ids);await link()
    async with AsyncSessionLocal() as session:
        visitor=await session.get(VisitorPass,ids[1],with_for_update=True);visitor.vehicle_colour=replacement;await session.commit()
    await information(ids,colour="Green");await link()
    assert (await rows(ids))[0].vehicle_colour==replacement
    assert (await rows(ids))[1].result["fields"]["colour"]=={"state":"protected"}
    async with AsyncSessionLocal() as session:
        visitor=await session.get(VisitorPass,ids[1]);visitor.vehicle_colour=None;await session.commit()
    await information(ids,colour="Yellow");await link()
    assert (await rows(ids))[0].vehicle_colour is None


async def test_distinct_baseline_change_before_first_result_is_preserved():
    ids=await context(consumed=True,baseline="Original")
    async with AsyncSessionLocal() as session:
        visitor=await session.get(VisitorPass,ids[1]);visitor.vehicle_colour="User";await session.commit()
    await visual(ids);await link()
    visitor,work,audits,_=await rows(ids)
    assert visitor.vehicle_colour=="User" and audits==0 and work.result["fields"]["colour"]["state"]=="protected"


@pytest.mark.parametrize("corruption",["baseline","saga_missing","saga_wrong","saga_ambiguous","arrival","plate","intent","reservation","visual","information"])
async def test_malformed_current_authority_or_unaccepted_result_holds_without_mutation(corruption):
    ids=await context(consumed=True,target=corruption!="baseline")
    await visual(ids)
    if corruption=="information":await information(ids)
    async with AsyncSessionLocal() as session:
        event=await session.get(AccessEvent,ids[0]);visitor=await session.get(VisitorPass,ids[1])
        if corruption=="saga_missing":event.raw_payload={**event.raw_payload,"movement_saga":{}}
        elif corruption=="saga_wrong":event.raw_payload={**event.raw_payload,"movement_saga":{"id":str(uuid.uuid4())}}
        elif corruption=="saga_ambiguous":session.add(MovementSagaRecord(idempotency_key=str(uuid.uuid4()),source="synthetic",access_event_id=event.id,occurred_at=event.occurred_at))
        elif corruption=="arrival":visitor.arrival_event_id=None
        elif corruption=="plate":visitor.number_plate="OTHER"
        elif corruption in {"intent","reservation"}:
            reservation=await session.scalar(select(VisitorPassReservationRecord).where(VisitorPassReservationRecord.access_event_id==event.id))
            if corruption=="intent":reservation.intent_id=uuid.uuid4()
            else:reservation.state="held"
        elif corruption=="visual":
            work=await session.scalar(select(AccessCompletionWork).where(AccessCompletionWork.event_id==event.id,AccessCompletionWork.stage=="visual"));work.state="skipped"
        elif corruption=="information":
            job=await session.get(VehicleInformationJob,event.id);job.accepted_result_revision=0
        await session.commit()
    assert await link() in {"canonical_saga_unavailable","canonical_arrival_unavailable","accepted_visual_unavailable","accepted_information_unavailable","frozen_field_baseline_unavailable"}
    visitor,work,audits,_=await rows(ids)
    assert visitor.vehicle_colour is None and audits==0 and work.state=="held"


async def test_audit_failure_rolls_back_fields_applied_receipt_and_publication(monkeypatch):
    ids=await context(consumed=True);await visual(ids)
    before=(await rows(ids))[3].resource_revision
    service=get_visitor_pass_service()
    async def fail(*args,**kwargs):raise RuntimeError("required audit unavailable")
    monkeypatch.setattr(service,"_audit_change",fail)
    claim=(await AccessCompletionStore().claim(1))[0]
    async with AsyncSessionLocal() as session:
        with pytest.raises(RuntimeError,match="required audit"):await visitor_link.apply_visitor_link_in_session(session,claim)
        await session.rollback()
    visitor,work,audits,event=await rows(ids)
    assert visitor.vehicle_colour is None and work.result["applied"]=={} and audits==0
    assert event.resource_revision==before and work.state=="leased"


async def test_duplicate_wake_and_restarted_same_reference_do_not_duplicate_mutation_or_audit():
    ids=await context(consumed=True);await visual(ids);await link()
    async with AsyncSessionLocal() as session:
        event=await session.get(AccessEvent,ids[0],with_for_update=True)
        await release_visitor_link_in_session(session,event);await session.commit()
    assert not await AccessCompletionStore().claim(1)
    assert (await rows(ids))[2]==1


@pytest.mark.parametrize("changed",["event","deadline","lease"])
async def test_visual_preparation_and_final_acceptance_reject_lost_authority(changed):
    ids=await context(consumed=True)
    claim=(await AccessCompletionStore().claim(1))[0]
    async with AsyncSessionLocal() as session:
        assert await prepare_visual_in_session(session,claim)>0
    async with AsyncSessionLocal() as session:
        if changed=="event":
            event=await session.get(AccessEvent,ids[0]);event.registration_number="OTHER"
        else:
            work=await session.get(AccessCompletionWork,claim.work_id)
            if changed=="deadline":work.deadline=await completion_store.database_now(session)-timedelta(seconds=1)
            else:work.lease_until=await completion_store.database_now(session)-timedelta(seconds=1)
        await session.commit()
    async with AsyncSessionLocal() as session:
        with pytest.raises(PipelineLeaseLost):await prepare_visual_in_session(session,claim)
    async with AsyncSessionLocal() as session:
        with pytest.raises(PipelineLeaseLost):await accept_visual_in_session(session,claim,{"observed_vehicle_color":"Blue"})
    assert (await rows(ids))[1].result["requested"]=={}


async def test_unrelated_public_revision_does_not_invalidate_visual_result():
    ids=await context(consumed=True);claim=(await AccessCompletionStore().claim(1))[0]
    async with AsyncSessionLocal() as session:
        await prepare_visual_in_session(session,claim)
        event=await session.get(AccessEvent,ids[0]);event.resource_revision+=3;await session.commit()
    async with AsyncSessionLocal() as session:
        await accept_visual_in_session(session,claim,{"observed_vehicle_color":"Blue"});await session.commit()
    await link();assert (await rows(ids))[0].vehicle_colour=="Blue"


async def test_blocked_saga_keeps_event_available_for_latest_information_and_link_uses_latest():
    ids=await context(consumed=True);await visual(ids);await information(ids,colour="First")
    claim=(await AccessCompletionStore().claim(1))[0]
    blocker=AsyncSessionLocal();await blocker.get(MovementSagaRecord,ids[2],with_for_update=True)
    started=asyncio.Event()
    async def run():
        async with AsyncSessionLocal() as session:
            started.set();outcome=await visitor_link.apply_visitor_link_in_session(session,claim);await session.commit();return outcome
    task=asyncio.create_task(run());await started.wait();await asyncio.sleep(.05)
    await asyncio.wait_for(information(ids,colour="Final"),2)
    await blocker.rollback();await blocker.close()
    assert await asyncio.wait_for(task,2)=="arrival_enriched"
    visitor,work,audits,_=await rows(ids)
    assert visitor.vehicle_colour=="Final" and audits==1 and work.result["applied"]["information"]["revision"]==2


async def test_blocked_saga_past_lease_cannot_mutate_or_audit():
    ids=await context(consumed=True);await visual(ids);claim=(await AccessCompletionStore().claim(1))[0]
    async with AsyncSessionLocal() as session:
        work=await session.get(AccessCompletionWork,claim.work_id);work.lease_until=await completion_store.database_now(session)+timedelta(seconds=.1);await session.commit()
    blocker=AsyncSessionLocal();await blocker.get(MovementSagaRecord,ids[2],with_for_update=True)
    async def run():
        async with AsyncSessionLocal() as session:await visitor_link.apply_visitor_link_in_session(session,claim)
    task=asyncio.create_task(run());await asyncio.sleep(.2);await blocker.rollback();await blocker.close()
    with pytest.raises(PipelineLeaseLost):await asyncio.wait_for(task,2)
    assert (await rows(ids))[0].vehicle_colour is None and (await rows(ids))[2]==0


async def test_cancelled_link_rolls_back_actual_child_before_release():
    ids=await context(consumed=True);await visual(ids);claim=(await AccessCompletionStore().claim(1))[0]
    blocker=AsyncSessionLocal();await blocker.get(VisitorPass,ids[1],with_for_update=True)
    async def run():
        async with AsyncSessionLocal() as session:await visitor_link.apply_visitor_link_in_session(session,claim)
    task=asyncio.create_task(run());await asyncio.sleep(.05);task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    await blocker.rollback();await blocker.close()
    assert (await rows(ids))[0].vehicle_colour is None and (await rows(ids))[2]==0


async def test_actual_supported_plate_update_invalidates_old_arrival_link():
    ids=await context(consumed=True,pass_type=VisitorPassType.DURATION);await visual(ids)
    async with AsyncSessionLocal() as session:
        visitor=await session.get(VisitorPass,ids[1]);visitor.valid_until=await completion_store.database_now(session)+timedelta(minutes=10)
        await session.commit()
    async with AsyncSessionLocal() as session:
        visitor=await session.get(VisitorPass,ids[1])
        await get_visitor_pass_service().update_visitor_plate(session,visitor,new_plate="OTHER123",vehicle_make="User",vehicle_colour="User",actor="Synthetic")
        await session.commit()
    assert await link()=="canonical_arrival_unavailable"
    assert (await rows(ids))[0].vehicle_colour=="User"


@pytest.mark.parametrize("mode",["missing","expired","multiple"])
async def test_information_reservation_retains_original_origin_without_renewal(mode):
    ids=await context(offset=-400 if mode=="expired" else -240)
    async with AsyncSessionLocal() as session:
        await session.execute(text("DELETE FROM vehicle_information_jobs"))
        if mode!="missing":
            for delta in (0,20):
                session.add(LprIngestEvent(idempotency_key=str(uuid.uuid4()),source="synthetic",registration_number="AB12CDE",captured_at=ids[3],received_at=ids[3]+timedelta(seconds=delta),
                    status="succeeded",access_event_id=ids[0],movement_saga_id=ids[2],normalized_payload={"webhook_trace":{"received_at":"2099-01-01T00:00:00+00:00"}}))
        await session.commit();event=await session.get(AccessEvent,ids[0])
    async with AsyncSessionLocal() as session:
        current = await session.get(AccessEvent, event.id, with_for_update=True)
        origin = None if mode == "missing" else ids[3]
        await reserve_information_in_session(session, current, origin_at=origin)
        await reserve_information_in_session(session, current, origin_at=origin)
        await session.commit()
    async with AsyncSessionLocal() as session:
        job=await session.get(VehicleInformationJob,ids[0])
        assert job.status=={"missing":"held","expired":"expired","multiple":"queued"}[mode]
        assert job.origin_at==(None if mode=="missing" else ids[3])
        if mode!="missing":assert job.deadline==ids[3]+timedelta(seconds=300)
        assert job.accepted_result_revision==0 and job.accepted_result is None


async def test_information_prepare_checks_plate_before_any_provider_input():
    ids=await context();worker=VehicleInformationWorker();claim=(await worker._claim())[0]
    async with AsyncSessionLocal() as session:
        event=await session.get(AccessEvent,ids[0]);event.registration_number="OTHER";await session.commit()
    with pytest.raises(PipelineLeaseLost,match="registration"):await worker._prepare(*claim)


async def test_latest_partial_final_before_claim_is_applied_once_with_immutable_origin_revision():
    ids=await context(consumed=True);await visual(ids)
    await information(ids,colour="Partial");await information(ids,colour="Final")
    assert await link()=="arrival_enriched"
    visitor,work,audits,_=await rows(ids)
    assert visitor.vehicle_colour=="Final" and audits==1 and work.expected_revision==1
    assert work.result["applied"]["information"]["revision"]==2


async def test_result_blocked_behind_link_event_lock_releases_one_trailing_latest_link(monkeypatch):
    ids=await context(consumed=True);await visual(ids)
    claim=(await AccessCompletionStore().claim(1))[0]
    service=get_visitor_pass_service();original=service.enrich_arrival
    entered,release=asyncio.Event(),asyncio.Event()
    async def blocked(*args,**kwargs):
        entered.set();await release.wait();return await original(*args,**kwargs)
    monkeypatch.setattr(service,"enrich_arrival",blocked)
    async def first_link():
        async with AsyncSessionLocal() as session:
            outcome=await visitor_link.apply_visitor_link_in_session(session,claim);await session.commit();return outcome
    first=asyncio.create_task(first_link());await entered.wait()
    new_result=asyncio.create_task(information(ids,colour="Final"));await asyncio.sleep(.05)
    assert not new_result.done()
    release.set();assert await first=="arrival_enriched";await new_result
    assert (await rows(ids))[1].state=="queued"
    assert await link()=="arrival_enriched"
    visitor,work,audits,_=await rows(ids)
    assert visitor.vehicle_colour=="Final" and audits==2 and work.expected_revision==1
    assert work.result["requested"]==work.result["applied"]
    assert await AccessCompletionStore().claim(2)==[]


@pytest.mark.parametrize("kind",["visual","information"])
async def test_acceptance_blocked_by_publication_past_original_deadline_rolls_back_receipt(kind):
    from app.services.access.publication import reserve_initial_publication_in_session
    ids=await context(offset=-298,consumed=True)
    async with AsyncSessionLocal() as session:
        await reserve_initial_publication_in_session(session,ids[0]);await session.commit()
    blocker=AsyncSessionLocal()
    await blocker.scalar(select(AccessCompletionWork).where(AccessCompletionWork.event_id==ids[0],AccessCompletionWork.stage=="publication").with_for_update())
    task=asyncio.create_task(visual(ids) if kind=="visual" else information(ids))
    await asyncio.sleep(2.2);await blocker.rollback();await blocker.close()
    with pytest.raises(PipelineLeaseLost):await asyncio.wait_for(task,3)
    visitor,work,audits,event=await rows(ids)
    assert visitor.vehicle_colour is None and audits==0 and work.result["requested"]=={} and event.resource_revision==1
    async with AsyncSessionLocal() as session:
        job=await session.get(VehicleInformationJob,ids[0])
        assert job.accepted_result_revision==0 and job.accepted_result_at is None and job.accepted_result is None
        assert "vehicle_visual_detection" not in event.raw_payload


async def test_current_identity_preparation_projects_columns_without_event_evidence_or_vehicle_photo():
    from app.models import Vehicle
    ids=await context()
    async with AsyncSessionLocal() as session:
        vehicle=Vehicle(id=uuid.uuid4(),registration_number="AB12CDE",vehicle_photo_data_url="data:"+"x"*900000)
        session.add(vehicle);await session.flush()
        event=await session.get(AccessEvent,ids[0]);event.vehicle_id=vehicle.id
        event.raw_payload={**event.raw_payload,"unused":"x"*900000}
        job=await session.get(VehicleInformationJob,ids[0]);job.vehicle_id=vehicle.id
        await session.commit()
    worker=VehicleInformationWorker();claim=(await worker._claim())[0];statements=[]
    def capture(conn,cursor,statement,parameters,context,executemany):statements.append(statement)
    sql_event.listen(engine.sync_engine,"before_cursor_execute",capture)
    try:
        await worker._prepare(*claim)
        visual_claim=(await AccessCompletionStore().claim(1))[0]
        async with AsyncSessionLocal() as session:await prepare_visual_in_session(session,visual_claim)
    finally:sql_event.remove(engine.sync_engine,"before_cursor_execute",capture)
    assert all("vehicle_photo_data_url" not in statement and "access_events.raw_payload" not in statement for statement in statements)



@pytest.mark.parametrize("state", [[], {}])
async def test_unhashable_retained_verification_state_holds_instead_of_requeue_exception(state):
    ids=await context(consumed=True);await visual(ids)
    async with AsyncSessionLocal() as session:
        reservation=await session.scalar(select(VisitorPassReservationRecord).where(VisitorPassReservationRecord.access_event_id==ids[0]))
        reservation.verification_evidence={"state":state};await session.commit()
    assert await link()=="canonical_arrival_unavailable"
    visitor,work,audits,_=await rows(ids)
    assert visitor.vehicle_colour is None and audits==0 and work.state=="held"


@pytest.mark.parametrize("target", ["not-a-uuid", "19FEF22E-1E39-47B0-A90A-E4BE790D965F", None, ["forged"]])
async def test_malformed_saved_visitor_identity_holds_without_sql_conversion_or_lost_accepted_visual(target):
    ids=await context(consumed=True)
    async with AsyncSessionLocal() as session:
        work=await session.scalar(select(AccessCompletionWork).where(AccessCompletionWork.event_id==ids[0],AccessCompletionWork.stage=="visitor_link"))
        work.result={**work.result,"visitor_id":target};await session.commit()
    await visual(ids)
    visitor,work,audits,_=await rows(ids)
    assert work.state=="held" and work.outcome=="canonical_target_unavailable"
    assert visitor.vehicle_colour is None and audits==0
    async with AsyncSessionLocal() as session:
        visual_work=await session.scalar(select(AccessCompletionWork).where(AccessCompletionWork.event_id==ids[0],AccessCompletionWork.stage=="visual"))
        assert visual_work.state=="succeeded" and visual_work.result["projection"]["colour"]=="Blue"


@pytest.mark.parametrize("shape", ["result_list", "requested_list", "requested_unknown", "applied_list"])
async def test_malformed_saved_result_readiness_is_durably_explained_without_losing_source(shape):
    ids=await context(consumed=True)
    async with AsyncSessionLocal() as session:
        work=await session.scalar(select(AccessCompletionWork).where(AccessCompletionWork.event_id==ids[0],AccessCompletionWork.stage=="visitor_link"))
        value=deepcopy(work.result)
        if shape=="result_list":value=[1]
        elif shape=="requested_list":value["requested"]=[1]
        elif shape=="requested_unknown":value["requested"]={"retired":{}}
        else:value["applied"]=[1]
        work.result=value;await session.commit()
    await visual(ids)
    visitor,work,audits,_=await rows(ids)
    assert work.state=="held" and work.outcome in {"link_checkpoint_unavailable","link_references_unavailable"}
    assert work.result==value and visitor.vehicle_colour is None and audits==0
    async with AsyncSessionLocal() as session:
        visual_work=await session.scalar(select(AccessCompletionWork).where(AccessCompletionWork.event_id==ids[0],AccessCompletionWork.stage=="visual"))
        assert visual_work.state=="succeeded" and visual_work.result["accepted"]["revision"]==1


@pytest.mark.parametrize('commit', [False, True])
async def test_actual_finalizer_releases_preaccepted_visitor_result_atomically(monkeypatch, commit):
    """Canonical admission joins retained-link readiness; no new provider attempt."""
    from unittest.mock import AsyncMock

    from test_movement_admission import evidence

    from app.services.access_device_commands import AccessDeviceCommandJournal
    from app.services.durable_work import DurableWorkListener, WorkReadiness, WorkTopic, WorkWake
    from app.services.movement.admission import finalize_in_session

    ids = await context(consumed=False)
    await visual(ids, 'Blue')
    assert (await rows(ids))[1].state == 'waiting'
    parent_id = await evidence(ids[0], ids[2], verified=True)
    no_attempt = AsyncMock(side_effect=AssertionError('finalization must never attempt provider I/O'))
    monkeypatch.setattr(AccessDeviceCommandJournal, 'begin_attempt', no_attempt)
    wake = WorkWake()
    listener = DurableWorkListener(wakes={WorkTopic.COMPLETION: wake})
    await listener.start()
    try:
        assert listener.diagnostics()['connected']
        generation = wake.generation
        before_origin = (await rows(ids))[1].origin_at
        async with AsyncSessionLocal() as session:
            result = await finalize_in_session(session, saga_id=ids[2], gate_command_id=parent_id)
            assert result.admission_status == 'verified'
            if commit:
                await session.commit()
            else:
                await session.rollback()
        if not commit:
            await asyncio.sleep(.05)
            visitor, work, audits, _ = await rows(ids)
            assert visitor.arrival_event_id is None and work.state == 'waiting' and audits == 0
            assert wake.generation == generation
            return
        await asyncio.wait_for(wake.wait(generation, WorkReadiness(), fallback_seconds=2), 2)
        assert wake.generation != generation
        visitor, work, _, _ = await rows(ids)
        assert visitor.arrival_event_id == ids[0] and work.state == 'queued'
        assert work.origin_at == before_origin
        assert await link() == 'arrival_enriched'
        visitor, work, audits, _ = await rows(ids)
        assert visitor.vehicle_colour == 'Blue' and audits == 1
        applied = deepcopy(work.result['applied'])
        generation = wake.generation
        async with AsyncSessionLocal() as session:
            await finalize_in_session(session, saga_id=ids[2], gate_command_id=parent_id)
            await session.commit()
        await asyncio.sleep(.05)
        assert wake.generation == generation
        assert not await AccessCompletionStore().claim(1)
        visitor, work, audits, _ = await rows(ids)
        assert work.result['applied'] == applied and audits == 1
        assert visitor.arrival_event_id == ids[0]
        no_attempt.assert_not_awaited()
    finally:
        await listener.stop()
