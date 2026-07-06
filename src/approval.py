"""Human-in-the-loop approval records and decisions."""

from dataclasses import asdict, dataclass, field
from enum import Enum
from uuid import uuid4

from .protocol import utc_now


class ApprovalStatus(str, Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    EDITED = "edited"
    EXPIRED = "expired"


@dataclass
class ApprovalRequest:
    workflow_id: str
    node_id: str
    action: str
    reason: str
    input_preview: dict
    approval_id: str = field(default_factory=lambda: str(uuid4()))
    status: ApprovalStatus = ApprovalStatus.REQUESTED
    decision_note: str = ""
    edited_input: dict | None = None
    created_at: str = field(default_factory=utc_now)
    resolved_at: str | None = None

    def resolve(
        self,
        decision: str,
        note: str = "",
        edited_input: dict | None = None,
    ) -> None:
        try:
            status = ApprovalStatus(decision)
        except ValueError as error:
            raise ValueError("Decision must be approved, rejected, edited, or expired.") from error
        if status == ApprovalStatus.REQUESTED:
            raise ValueError("requested is not a resolution.")
        if self.status != ApprovalStatus.REQUESTED:
            raise ValueError("Approval has already been resolved.")
        if status == ApprovalStatus.EDITED and edited_input is None:
            raise ValueError("An edited decision requires edited_input.")
        self.status = status
        self.decision_note = note
        self.edited_input = edited_input
        self.resolved_at = utc_now()

    def to_dict(self) -> dict:
        value = asdict(self)
        value["status"] = self.status.value
        return value
