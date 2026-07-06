"""Bounded DAG scheduler for agent, tool, reasoning, HITL, and handoff nodes."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4

from .approval import ApprovalRequest, ApprovalStatus
from .protocol import utc_now
from .workflow import NodeResult, NodeStatus, NodeType, Workflow, validate_workflow

MAX_CONCURRENT_NODES = 3


@dataclass
class WorkflowRun:
    workflow: Workflow
    results: dict[str, NodeResult] = field(default_factory=dict)
    approvals: dict[str, ApprovalRequest] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    run_id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "workflow": self.workflow.to_dict(),
            "results": {key: value.to_dict() for key, value in self.results.items()},
            "approvals": {key: value.to_dict() for key, value in self.approvals.items()},
            "events": self.events,
        }


class WorkflowRuntime:
    """Execute ready DAG nodes while dependencies and human gates permit."""

    def __init__(
        self,
        agent_handlers: dict[str, Callable],
        tool_handlers: dict[str, Callable],
        reasoning_handler: Callable | None = None,
        tree_handler: Callable | None = None,
        handoff_handler: Callable | None = None,
    ) -> None:
        self.agent_handlers = agent_handlers
        self.tool_handlers = tool_handlers
        self.reasoning_handler = reasoning_handler
        self.tree_handler = tree_handler
        self.handoff_handler = handoff_handler

    def start(self, workflow: Workflow) -> WorkflowRun:
        validate_workflow(
            workflow,
            agents=set(self.agent_handlers),
            tools=set(self.tool_handlers),
        )
        run = WorkflowRun(workflow=workflow)
        run.events.append(self._event(run, "workflow_created"))
        return self.continue_run(run)

    def continue_run(self, run: WorkflowRun) -> WorkflowRun:
        while True:
            self._block_failed_descendants(run)
            ready = [
                node
                for node in run.workflow.nodes
                if node.node_id not in run.results
                and all(
                    run.results.get(dependency)
                    and run.results[dependency].status == NodeStatus.COMPLETED
                    for dependency in node.depends_on
                )
            ]
            if not ready:
                break
            executable = []
            for node in ready:
                if node.type == NodeType.HUMAN_APPROVAL:
                    self._request_approval(run, node)
                else:
                    executable.append(node)
            if executable:
                with ThreadPoolExecutor(
                    max_workers=min(MAX_CONCURRENT_NODES, len(executable))
                ) as executor:
                    future_to_node = {
                        executor.submit(self._execute_node, run, node): node
                        for node in executable
                    }
                    for future in as_completed(future_to_node):
                        node = future_to_node[future]
                        try:
                            result = future.result()
                        except Exception as error:
                            result = NodeResult(
                                status=NodeStatus.FAILED,
                                error=str(error),
                            )
                        run.results[node.node_id] = result
                        run.events.append(
                            self._event(
                                run,
                                "node_completed",
                                node_id=node.node_id,
                                status=result.status.value,
                                error=result.error,
                            )
                        )
            if any(
                result.status == NodeStatus.WAITING_FOR_HUMAN
                for result in run.results.values()
            ):
                run.workflow.status = "waiting_for_human"
                break
        unfinished = [
            node for node in run.workflow.nodes if node.node_id not in run.results
        ]
        if not unfinished:
            run.workflow.status = (
                "failed"
                if any(
                    result.status in {NodeStatus.FAILED, NodeStatus.BLOCKED}
                    for result in run.results.values()
                )
                else "completed"
            )
        return run

    def resolve_approval(
        self,
        run: WorkflowRun,
        approval_id: str,
        decision: str,
        note: str = "",
        edited_input: dict | None = None,
    ) -> WorkflowRun:
        approval = run.approvals.get(approval_id)
        if approval is None:
            raise ValueError("Unknown approval ID.")
        approval.resolve(decision, note, edited_input)
        if approval.status in {ApprovalStatus.APPROVED, ApprovalStatus.EDITED}:
            run.results[approval.node_id] = NodeResult(
                status=NodeStatus.COMPLETED,
                output=approval.edited_input or approval.input_preview,
            )
        else:
            run.results[approval.node_id] = NodeResult(
                status=NodeStatus.FAILED,
                error=f"Human decision: {approval.status.value}. {note}".strip(),
            )
        run.events.append(
            self._event(
                run,
                "approval_resolved",
                node_id=approval.node_id,
                approval_id=approval_id,
                status=approval.status.value,
            )
        )
        run.workflow.status = "running"
        return self.continue_run(run)

    def _execute_node(self, run: WorkflowRun, node) -> NodeResult:
        run.events.append(self._event(run, "node_started", node_id=node.node_id))
        inputs = self._inputs(run, node)
        if node.type == NodeType.AGENT:
            output = self.agent_handlers[node.config["agent"]](
                str(node.config.get("task", run.workflow.goal)), inputs
            )
        elif node.type == NodeType.TOOL:
            arguments = dict(node.config.get("arguments", {}))
            arguments.update(node.config.get("inject", {}))
            output = self.tool_handlers[node.config["tool"]](**arguments)
        elif node.type == NodeType.REASONING_CHAIN:
            if self.reasoning_handler is None:
                raise ValueError("No reasoning-chain handler is configured.")
            output = self.reasoning_handler(node.config, inputs)
        elif node.type == NodeType.TREE_SEARCH:
            if self.tree_handler is None:
                raise ValueError("No tree-search handler is configured.")
            output = self.tree_handler(node.config, inputs)
        elif node.type == NodeType.HANDOFF:
            if self.handoff_handler is None:
                raise ValueError("No handoff handler is configured.")
            output = self.handoff_handler(node.config, inputs)
        elif node.type == NodeType.JOIN:
            output = {
                "instruction": node.config.get("instruction", "Combine dependency results."),
                "inputs": inputs,
            }
        else:
            raise ValueError(f"Unsupported executable node type: {node.type.value}")
        status = (
            NodeStatus.FAILED
            if isinstance(output, dict) and output.get("ok") is False
            else NodeStatus.COMPLETED
        )
        return NodeResult(status=status, output=output)

    def _inputs(self, run: WorkflowRun, node) -> dict:
        return {
            dependency: run.results[dependency].output
            for dependency in node.depends_on
        }

    def _request_approval(self, run: WorkflowRun, node) -> None:
        if node.node_id in run.results:
            return
        request = ApprovalRequest(
            workflow_id=run.workflow.workflow_id,
            node_id=node.node_id,
            action=str(node.config.get("action", "Continue workflow")),
            reason=str(node.config.get("reason", "Human review required")),
            input_preview=self._inputs(run, node),
        )
        run.approvals[request.approval_id] = request
        run.results[node.node_id] = NodeResult(
            status=NodeStatus.WAITING_FOR_HUMAN,
            output={"approval_id": request.approval_id},
        )
        run.events.append(
            self._event(
                run,
                "approval_requested",
                node_id=node.node_id,
                approval_id=request.approval_id,
            )
        )

    def _block_failed_descendants(self, run: WorkflowRun) -> None:
        changed = True
        while changed:
            changed = False
            for node in run.workflow.nodes:
                if node.node_id in run.results:
                    continue
                failed = [
                    dependency
                    for dependency in node.depends_on
                    if dependency in run.results
                    and run.results[dependency].status
                    in {NodeStatus.FAILED, NodeStatus.BLOCKED}
                ]
                if failed:
                    run.results[node.node_id] = NodeResult(
                        status=NodeStatus.BLOCKED,
                        error=f"Blocked by failed dependencies: {failed}",
                    )
                    changed = True

    @staticmethod
    def _event(run: WorkflowRun, event_type: str, **fields) -> dict:
        return {
            "event_id": str(uuid4()),
            "run_id": run.run_id,
            "workflow_id": run.workflow.workflow_id,
            "type": event_type,
            "timestamp": utc_now(),
            **fields,
        }
