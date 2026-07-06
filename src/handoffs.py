"""Validated transfer of active ownership between agents."""

from dataclasses import asdict, dataclass, field
from uuid import uuid4

from .protocol import AgentMessage, MessageBus, MessageType, utc_now

MAX_HANDOFF_DEPTH = 3
MAX_HANDOFFS = 5


@dataclass(frozen=True)
class HandoffRequest:
    source: str
    destination: str
    task: str
    context_summary: str
    reason: str
    trace_id: str
    return_policy: str = "return_to_coordinator"
    handoff_id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> dict:
        return asdict(self)


class HandoffManager:
    def __init__(self, routes: dict[str, tuple[str, ...]], bus: MessageBus) -> None:
        self.routes = routes
        self.bus = bus
        self.count = 0
        self.events: list[dict] = []

    def transfer(self, request: HandoffRequest, depth: int = 1) -> AgentMessage:
        if depth > MAX_HANDOFF_DEPTH:
            raise ValueError("Maximum handoff depth reached.")
        if self.count >= MAX_HANDOFFS:
            raise ValueError("Maximum handoff count reached.")
        if request.destination not in self.routes.get(request.source, ()):
            raise ValueError(
                f"Handoff route is not allowed: {request.source} -> {request.destination}"
            )
        if request.return_policy not in {"return_to_coordinator", "remain_active"}:
            raise ValueError("Unknown handoff return policy.")
        self.count += 1
        self.events.append(
            {
                "type": "handoff_started",
                "handoff_id": request.handoff_id,
                "source": request.source,
                "destination": request.destination,
                "depth": depth,
                "timestamp": utc_now(),
            }
        )
        message = AgentMessage(
            sender=request.source,
            recipient=request.destination,
            type=MessageType.HANDOFF_REQUEST,
            payload=request.to_dict(),
            trace_id=request.trace_id,
            correlation_id=request.handoff_id,
        )
        reply = self.bus.send(message)
        if reply is None or reply.type not in {
            MessageType.HANDOFF_ACCEPT,
            MessageType.HANDOFF_REJECT,
        }:
            raise ValueError("Destination must explicitly accept or reject a handoff.")
        self.events.append(
            {
                "type": "handoff_completed",
                "handoff_id": request.handoff_id,
                "status": "accepted"
                if reply.type == MessageType.HANDOFF_ACCEPT
                else "rejected",
                "timestamp": utc_now(),
            }
        )
        return reply
