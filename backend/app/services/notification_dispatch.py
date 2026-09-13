"""Notification lifecycle and recovery; provider operations stay in NotificationService."""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from app.modules.notifications.base import NotificationDeliveryError
from app.services.notification_runs import (
    ClaimLost,
    NotificationRunStore,
    destination_outcome_truth,
    safe_destination_outcomes,
)

logger = get_logger(__name__)
POLL_SECONDS = 2
ACTION_TIMEOUT_SECONDS = 120


class NotificationDispatcher:
    def __init__(self, service, store: NotificationRunStore):
        self.service = service
        self.store = store
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._worker(), name="notification-dispatch")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def wake(self) -> None:
        self._wake.set()

    async def _worker(self) -> None:
        while True:
            self._wake.clear()
            try:
                # Bound each cycle and yield between runs; no unbounded local task fanout.
                for _ in range(20):
                    if not await self.run_once():
                        break
            except Exception:
                logger.exception("notification_dispatch_tick_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=POLL_SECONDS)

    async def run_once(self, run_id=None, *, claimed=None, ephemeral_config=None) -> bool:
        row = claimed if claimed is not None else await self.store.claim(run_id)
        if row is None:
            return False
        token = row.claim_token
        try:
            plan = row.delivery_plan
            if plan is None:
                plan = await self.service.prepare_delivery_plan(row)
                await self.store.save_plan(row.id, token, plan)
            for index, item in enumerate(plan):
                if item["state"] != "pending":
                    continue
                # Bind each closure to this action and its mutable in-memory
                # authorization snapshot. No transport configuration is stored.
                attempt = {"config": await self.service.delivery_config()}
                action = item["action"]
                refresh_authorization = None
                authorize: Callable[..., Awaitable[Any]]
                if row.context.get("confirmed_delivery") is not None:
                    async def authorize_confirmed(
                        session,
                        payload,
                        identity,
                        *,
                        _attempt=attempt,
                        _action=action,
                        _item=item,
                    ):
                        _attempt["config"], denial = await self.service.authorize_confirmed_attempt(
                            session,
                            payload,
                            identity,
                            plan=plan,
                            ephemeral_config=ephemeral_config,
                            action=_action,
                            item=_item,
                        )
                        return denial

                    authorize = authorize_confirmed
                else:
                    config_authorizer = getattr(self.service, "authorize_attempt_with_config", None)
                    owner = getattr(self.service, "authorize_attempt", None)
                    if config_authorizer is not None:
                        async def authorize_configured(
                            session,
                            payload,
                            identity,
                            *,
                            _attempt=attempt,
                            _authorizer=config_authorizer,
                            _action=action,
                            _item=item,
                        ):
                            _attempt["config"], policy = await _authorizer(
                                session,
                                payload,
                                identity,
                                action=_action,
                                item=_item,
                            )
                            return policy

                        authorize = authorize_configured

                        async def refresh_authorization(
                            session,
                            payload,
                            identity,
                            *,
                            _attempt=attempt,
                            _authorizer=config_authorizer,
                            _action=action,
                            _item=item,
                        ):
                            _attempt["config"], policy = await _authorizer(
                                session,
                                payload,
                                identity,
                                action=_action,
                                item=_item,
                                final=True,
                            )
                            return policy
                    elif owner is not None:
                        async def authorize_owned(
                            session,
                            payload,
                            identity,
                            *,
                            _owner=owner,
                            _action=action,
                            _item=item,
                        ):
                            return await _owner(session, payload, identity, action=_action, item=_item)

                        authorize = authorize_owned
                    else:
                        async def authorize_missing(_session, _payload, _identity):
                            return "notification_origin_validator_missing"

                        authorize = authorize_missing
                started = await self.store.begin_action(
                    row.id,
                    token,
                    index,
                    authorize_origin=authorize,
                    refresh_authorization=refresh_authorization,
                )
                if started is not None and not getattr(started, "attempted", True):
                    # A saved workflow can change after plan preparation. Its
                    # action is durably skipped, while other rule actions stay
                    # eligible for this same claimed run.
                    continue
                config = attempt["config"]
                try:
                    async with asyncio.timeout(ACTION_TIMEOUT_SECONDS):
                        if ephemeral_config is None:
                            outcome = await self.service.deliver_planned_action(item, row, config)
                        else:
                            outcome = await self.service.deliver_planned_action(
                                item, row, config, ephemeral_config=ephemeral_config,
                            )
                except NotificationDeliveryError as exc:
                    checkpoint = _checkpoint_from_delivery_error(exc)
                    await self.store.finish_action(
                        row.id,
                        token,
                        index,
                        checkpoint,
                        prepare_output=getattr(self.service, "prepare_delivery_output", None),
                    )
                    try:
                        await self.service.publish_planned_failure(
                            item,
                            row,
                            reason=checkpoint["reason"],
                            requires_review=bool(checkpoint.get("review_required")),
                            metadata=_checkpoint_metadata(checkpoint),
                        )
                    except Exception:
                        logger.exception("notification_failure_enrichment_failed")
                    if checkpoint.get("review_required"):
                        return True
                    continue
                except Exception:  # noqa: BLE001 - an unclassified provider error is always ambiguous.
                    checkpoint = {"state": "unknown", "reason": "provider_outcome_unknown", "review_required": True}
                    await self.store.finish_action(
                        row.id,
                        token,
                        index,
                        checkpoint,
                        prepare_output=getattr(self.service, "prepare_delivery_output", None),
                    )
                    try:
                        await self.service.publish_planned_failure(item, row)
                    except Exception:
                        logger.exception("notification_failure_enrichment_failed")
                    return True
                checkpoint = _checkpoint_from_outcome(outcome)
                if outcome.metadata.get("provider_message_id"):
                    checkpoint["provider_message_id"] = str(outcome.metadata["provider_message_id"])[:255]
                await self.store.finish_action(
                    row.id,
                    token,
                    index,
                    checkpoint,
                    prepare_output=getattr(self.service, "prepare_delivery_output", None),
                )
                if checkpoint.get("review_required"):
                    try:
                        await self.service.publish_planned_failure(
                            item,
                            row,
                            reason=checkpoint["reason"],
                            requires_review=True,
                            metadata=_checkpoint_metadata(checkpoint),
                        )
                    except Exception:
                        logger.exception("notification_failure_enrichment_failed")
                    return True
                # Durable checkpoint comes first. Realtime/last-fired are independent enrichment.
                try:
                    await self.service.publish_planned_outcome(item, row, outcome)
                except Exception:
                    logger.exception("notification_dispatch_enrichment_failed")
            completed = await self.store.finish(row.id, token)
            try:
                await self.service.publish_plan_completion(completed)
            except Exception:
                logger.exception("notification_completion_enrichment_failed")
        except ClaimLost:
            pass
        except BaseException:
            # Cancellation before send requeues; cancellation during send requires review.
            # If this write fails, the lease expiry follows the same conservative rule.
            try:
                await self.store.interrupt(row.id, token)
            except Exception:
                logger.exception("notification_dispatch_interruption_checkpoint_failed")
            raise
        return True


def _checkpoint_from_outcome(outcome) -> dict[str, Any]:
    metadata = outcome.metadata if isinstance(getattr(outcome, "metadata", None), dict) else {}
    raw_outcomes = metadata.get("destination_outcomes")
    receipt_accepted, receipt_failures, receipt_uncertain = destination_outcome_truth(raw_outcomes)
    # Projection is solely for the durable, user-visible receipt. Recovery
    # truth is computed from every validated provider certainty first, so an
    # unsafe label or the 101st receipt cannot suppress required review.
    outcomes = safe_destination_outcomes(raw_outcomes)
    accepted_any = receipt_accepted or bool(metadata.get("accepted_any", outcome.delivered))
    failure_count = max(_failure_count(metadata.get("failure_count")), receipt_failures)
    delivery_uncertain = bool(metadata.get("delivery_uncertain")) or receipt_uncertain
    review_required = bool(metadata.get("review_required")) or delivery_uncertain
    if outcome.skipped:
        state = "skipped"
    elif outcome.delivered or accepted_any:
        state = "accepted"
    elif review_required:
        state = "unknown"
    elif failure_count:
        state = "failed"
    else:
        state = "unknown"
        review_required = True
    checkpoint: dict[str, Any] = {
        "state": state,
        "reason": outcome.reason or (
            "provider_outcome_unknown" if review_required else "provider_rejected" if failure_count else ""
        ),
        "partial_failure": bool(metadata.get("partial_failure")) or failure_count > 0,
        "failure_count": failure_count,
        "accepted_any": accepted_any,
        "review_required": review_required,
        "delivery_uncertain": delivery_uncertain,
    }
    if outcomes:
        checkpoint["destination_outcomes"] = outcomes
    return checkpoint


def _checkpoint_from_delivery_error(error: NotificationDeliveryError) -> dict[str, Any]:
    raw_outcomes = getattr(error, "destination_outcomes", None)
    receipt_accepted, receipt_failures, receipt_uncertain = destination_outcome_truth(raw_outcomes)
    outcomes = safe_destination_outcomes(raw_outcomes)
    delivery = str(getattr(error, "delivery", "unknown"))
    accepted_any = receipt_accepted or delivery == "accepted"
    delivery_uncertain = delivery == "unknown" or receipt_uncertain
    review_required = delivery_uncertain
    failure_count = max(
        _failure_count(getattr(error, "failure_count", 0)),
        receipt_failures,
        0 if accepted_any and not review_required else 1,
    )
    if accepted_any:
        state = "accepted"
    elif review_required:
        state = "unknown"
    else:
        state = "failed"
    checkpoint: dict[str, Any] = {
        "state": state,
        "reason": (
            "provider_outcome_unknown"
            if review_required
            else "provider_rejected"
            if delivery == "rejected"
            else "provider_not_sent"
        ),
        "partial_failure": failure_count > 0,
        "failure_count": failure_count,
        "accepted_any": accepted_any,
        "review_required": review_required,
        "delivery_uncertain": delivery_uncertain,
    }
    if outcomes:
        checkpoint["destination_outcomes"] = outcomes
    return checkpoint


def _failure_count(value: Any) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def _checkpoint_metadata(checkpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        key: checkpoint[key]
        for key in (
            "accepted_any",
            "destination_outcomes",
            "failure_count",
            "partial_failure",
            "review_required",
            "delivery_uncertain",
        )
        if key in checkpoint
    }
