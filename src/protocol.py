"""Small, local agent-to-agent message protocol.

The protocol is transport-independent: today messages are delivered in memory,
but the same dictionaries can later travel over HTTP or a queue.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from uuid import uuid4

MAX_MESSAGE_BYTES = 16 * 1024


class MessageType(str, Enum):
    TASK_REQUEST = "task_request"
    TASK_RESULT = "task_result"
    HANDOFF_REQUEST = "handoff_request"
    HANDOFF_ACCEPT = "handoff_accept"
    HANDOFF_REJECT = "handoff_reject"
    CONTEXT_UPDATE = "context_update"
    CANCEL = "cancel"
    ERROR = "error"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AgentMessage:
    sender: str
    recipient: str
    type: MessageType
    payload: dict
    trace_id: str
    correlation_id: str = field(default_factory=lambda: str(uuid4()))
    message_id: str = field(default_factory=lambda: str(uuid4()))
    reply_to: str | None = None
    timestamp: str = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        value = asdict(self)
        value["type"] = self.type.value
        return value


class MessageBus:
    """Validate routes and synchronously deliver messages to registered agents."""

    def __init__(self) -> None:
        self._handlers: dict[str, callable] = {}
        self.events: list[dict] = []

    def register(self, agent_name: str, handler: callable) -> None:
        if not agent_name or agent_name in self._handlers:
            raise ValueError(f"Invalid or duplicate agent registration: {agent_name}")
        self._handlers[agent_name] = handler

    def send(self, message: AgentMessage) -> AgentMessage | None:
        encoded = json.dumps(message.to_dict(), ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_MESSAGE_BYTES:
            raise ValueError(f"Message exceeds {MAX_MESSAGE_BYTES} bytes.")
        handler = self._handlers.get(message.recipient)
        if handler is None:
            raise ValueError(f"Unknown message recipient: {message.recipient}")
        self.events.append({"type": "message_sent", **message.to_dict()})
        reply = handler(message)
        self.events.append(
            {
                "type": "message_received",
                "message_id": message.message_id,
                "sender": message.sender,
                "recipient": message.recipient,
                "timestamp": utc_now(),
            }
        )
        if reply is not None and reply.reply_to != message.message_id:
            raise ValueError("A reply must reference the original message_id.")
        return reply
