"""Explicit transport acknowledgement; it is not browser or business completion."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class RealtimePublishReceipt:
    transport: Literal["redis_stream", "in_process"]
    accepted: bool
    stream_id: str | None = None
    reason: Literal["bus_closed", "redis_unavailable", "redis_rejected"] | None = None

    def __post_init__(self) -> None:
        if self.accepted and self.reason is not None:
            raise ValueError("Accepted transport has no rejection reason")
        if self.accepted and self.transport == "redis_stream" and not self.stream_id:
            raise ValueError("Redis acceptance requires the XADD stream identity")
        if self.stream_id is not None and (not self.accepted or self.transport != "redis_stream"):
            raise ValueError("Only accepted Redis publication has a stream identity")


class PublicationTransport(Protocol):
    async def publish(self, event_type: str, payload: dict[str, Any]) -> RealtimePublishReceipt: ...
