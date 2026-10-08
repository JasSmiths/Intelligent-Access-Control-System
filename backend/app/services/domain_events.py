from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from app.services.event_bus import event_bus
from app.services.realtime_contracts import PublicationTransport


class DomainEvent(Protocol):
    event_type: ClassVar[str]

    def payload(self) -> dict[str, Any]: ...


async def publish_domain_event(
    event: DomainEvent, *, bus: PublicationTransport | None = None
) -> None:
    target_bus = bus or event_bus
    await target_bus.publish(event.event_type, event.payload())


@dataclass(frozen=True)
class VisitorPassStatusChanged:
    visitor_pass: dict[str, Any]

    event_type: ClassVar[str] = "visitor_pass.status_changed"

    def payload(self) -> dict[str, Any]:
        return {"visitor_pass": self.visitor_pass}


async def publish_visitor_pass_status_changed(
    visitor_pass: dict[str, Any],
    *,
    bus: PublicationTransport | None = None,
) -> None:
    await publish_domain_event(VisitorPassStatusChanged(visitor_pass), bus=bus)
