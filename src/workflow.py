"""Typed directed-acyclic workflow definitions and validation."""

from dataclasses import asdict, dataclass, field
from enum import Enum
from uuid import uuid4

MAX_WORKFLOW_NODES = 20
MAX_WORKFLOW_DEPTH = 8


class NodeType(str, Enum):
    AGENT = "agent"
    TOOL = "tool"
    REASONING_CHAIN = "reasoning_chain"
    TREE_SEARCH = "tree_search"
    HUMAN_APPROVAL = "human_approval"
    HANDOFF = "handoff"
    JOIN = "join"


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_HUMAN = "waiting_for_human"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class WorkflowNode:
    node_id: str
    type: NodeType
    config: dict
    depends_on: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: dict) -> "WorkflowNode":
        return cls(
            node_id=str(value["node_id"]),
            type=NodeType(value["type"]),
            config=dict(value.get("config", {})),
            depends_on=tuple(value.get("depends_on", ())),
        )


@dataclass
class NodeResult:
    status: NodeStatus
    output: object = None
    evidence: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass
class Workflow:
    goal: str
    nodes: tuple[WorkflowNode, ...]
    workflow_id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "pending"

    @classmethod
    def from_dict(cls, value: dict) -> "Workflow":
        return cls(
            goal=str(value["goal"]),
            nodes=tuple(WorkflowNode.from_dict(node) for node in value["nodes"]),
            workflow_id=str(value.get("workflow_id") or uuid4()),
        )

    def to_dict(self) -> dict:
        return {
            "workflow_id": self.workflow_id,
            "goal": self.goal,
            "status": self.status,
            "nodes": [
                {
                    "node_id": node.node_id,
                    "type": node.type.value,
                    "config": node.config,
                    "depends_on": list(node.depends_on),
                }
                for node in self.nodes
            ],
        }


def validate_workflow(workflow: Workflow, agents: set[str], tools: set[str]) -> None:
    if not workflow.goal.strip():
        raise ValueError("Workflow goal is required.")
    if not workflow.nodes or len(workflow.nodes) > MAX_WORKFLOW_NODES:
        raise ValueError(f"A workflow needs 1 to {MAX_WORKFLOW_NODES} nodes.")
    by_id = {node.node_id: node for node in workflow.nodes}
    if len(by_id) != len(workflow.nodes) or any(not value for value in by_id):
        raise ValueError("Node IDs must be non-empty and unique.")
    for node in workflow.nodes:
        missing = set(node.depends_on) - set(by_id)
        if missing:
            raise ValueError(f"{node.node_id} has missing dependencies: {sorted(missing)}")
        if node.node_id in node.depends_on:
            raise ValueError(f"{node.node_id} cannot depend on itself.")
        if node.type == NodeType.AGENT and node.config.get("agent") not in agents:
            raise ValueError(f"Unknown agent in {node.node_id}.")
        if node.type == NodeType.TOOL and node.config.get("tool") not in tools:
            raise ValueError(f"Unknown tool in {node.node_id}.")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str, depth: int) -> None:
        if depth > MAX_WORKFLOW_DEPTH:
            raise ValueError(f"Workflow depth exceeds {MAX_WORKFLOW_DEPTH}.")
        if node_id in visiting:
            raise ValueError("Workflow contains a cycle.")
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in by_id[node_id].depends_on:
            visit(dependency, depth + 1)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in by_id:
        visit(node_id, 1)
