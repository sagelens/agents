"""Inspectable reasoning summaries and bounded tree search.

These records are application-level decision artifacts. They are not private
model chain-of-thought and prompts should never ask a model to reveal that.
"""

from dataclasses import asdict, dataclass, field
from uuid import uuid4


@dataclass
class ReasoningStep:
    summary: str
    decision: str
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def reasoning_chain(problem: str, step_builder: callable, max_steps: int = 4) -> dict:
    if not 1 <= max_steps <= 8:
        raise ValueError("max_steps must be between 1 and 8.")
    steps: list[dict] = []
    state = {"problem": problem, "steps": steps}
    for index in range(max_steps):
        candidate = step_builder(problem, list(steps), index)
        if not isinstance(candidate, dict):
            raise ValueError("Reasoning step builder must return a dictionary.")
        step = ReasoningStep(
            summary=str(candidate.get("summary", ""))[:2_000],
            decision=str(candidate.get("decision", ""))[:2_000],
            evidence=[str(item)[:1_000] for item in candidate.get("evidence", [])[:5]],
            confidence=max(0.0, min(1.0, float(candidate.get("confidence", 0.0)))),
        )
        steps.append(step.to_dict())
        if candidate.get("final"):
            state["answer"] = str(candidate.get("answer", step.decision))[:8_000]
            break
    state.setdefault("answer", steps[-1]["decision"] if steps else "")
    return state


@dataclass
class Thought:
    proposal: str
    parent_id: str | None
    depth: int
    thought_id: str = field(default_factory=lambda: str(uuid4()))
    score: float = 0.0
    critique: str = ""
    selected: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def tree_of_thought(
    problem: str,
    generate: callable,
    evaluate: callable,
    branches: int = 3,
    levels: int = 2,
) -> dict:
    if not 2 <= branches <= 3:
        raise ValueError("branches must be between 2 and 3.")
    if not 1 <= levels <= 2:
        raise ValueError("levels must be between 1 and 2.")
    frontier = [Thought(proposal=problem, parent_id=None, depth=0)]
    all_thoughts: list[Thought] = []
    for depth in range(1, levels + 1):
        candidates: list[Thought] = []
        for parent in frontier:
            proposals = generate(problem, parent.to_dict(), branches)
            for proposal in list(proposals)[:branches]:
                thought = Thought(
                    proposal=str(proposal)[:2_000],
                    parent_id=parent.thought_id,
                    depth=depth,
                )
                assessment = evaluate(problem, thought.to_dict())
                thought.score = max(0.0, min(1.0, float(assessment.get("score", 0))))
                thought.critique = str(assessment.get("critique", ""))[:2_000]
                candidates.append(thought)
                all_thoughts.append(thought)
        frontier = sorted(candidates, key=lambda item: item.score, reverse=True)[:branches]
    if not frontier:
        return {"problem": problem, "thoughts": [], "answer": "", "score": 0.0}
    winner = frontier[0]
    winner.selected = True
    return {
        "problem": problem,
        "thoughts": [thought.to_dict() for thought in all_thoughts],
        "answer": winner.proposal,
        "score": winner.score,
    }
