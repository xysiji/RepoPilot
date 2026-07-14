"""Dynamic interrupt payload and resume behavior of the Approval Node."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from repopilot.agent.nodes import ApprovalNode
from repopilot.agent.state import AgentState, create_initial_state
from repopilot.patching.proposal import PatchProposalBuilder
from repopilot.tools.policy import WorkspaceGuard


def _approval_graph():
    builder = StateGraph(AgentState)
    builder.add_node("approval", ApprovalNode())
    builder.add_edge(START, "approval")
    builder.add_edge("approval", END)
    return builder.compile(checkpointer=InMemorySaver())


def _state(tmp_path: Path, run_id: str | None = None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "a.py"
    target.write_bytes(b"old\n")
    proposal = PatchProposalBuilder(WorkspaceGuard(tmp_path)).build(
        tool_call_id="call-1",
        path="a.py",
        new_content="new\n",
        rationale="change",
    )
    state = create_initial_state("goal", 3, run_id or str(uuid4()))
    state["status"] = "awaiting_approval"
    state["pending_approval"] = proposal.model_dump(mode="json")
    return target, proposal, state


def test_approval_node_interrupt_payload_is_complete_safe_and_repeatable(tmp_path: Path) -> None:
    target, proposal, state = _state(tmp_path)
    graph = _approval_graph()
    config = {"configurable": {"thread_id": state["run_id"]}}

    interrupted = asyncio.run(graph.ainvoke(state, config))
    payload = interrupted["__interrupt__"][0].value

    json.dumps(payload)
    assert payload["proposal_id"] == str(proposal.proposal_id)
    assert payload["unified_diff"] == proposal.unified_diff
    assert "proposed_content" not in payload
    assert payload["post_apply_verification"] == {
        "runner": "pytest",
        "target": "tests",
        "automatic": True,
    }
    assert str(tmp_path) not in json.dumps(payload)
    assert target.read_bytes() == b"old\n"

    resumed = asyncio.run(
        graph.ainvoke(
            Command(resume={"proposal_id": str(proposal.proposal_id), "decision": "approve"}),
            config,
        )
    )
    assert resumed["approval_decision"]["decision"] == "approve"
    assert resumed["pending_approval"]["proposal_id"] == str(proposal.proposal_id)
    assert target.read_bytes() == b"old\n"


def test_approval_node_accepts_reject_and_defensively_rejects_invalid_resume(
    tmp_path: Path,
) -> None:
    _target, proposal, state = _state(tmp_path)
    graph = _approval_graph()
    config = {"configurable": {"thread_id": state["run_id"]}}
    asyncio.run(graph.ainvoke(state, config))

    rejected = asyncio.run(
        graph.ainvoke(
            Command(resume={"proposal_id": str(proposal.proposal_id), "decision": "reject"}),
            config,
        )
    )

    assert rejected["approval_decision"]["decision"] == "reject"
    assert rejected["approval_decision"]["valid"] is True

    _target2, proposal2, state2 = _state(tmp_path / "other")
    graph2 = _approval_graph()
    config2 = {"configurable": {"thread_id": state2["run_id"]}}
    asyncio.run(graph2.ainvoke(state2, config2))
    invalid = asyncio.run(graph2.ainvoke(Command(resume={"decision": "approve"}), config2))
    assert invalid["approval_decision"]["valid"] is False
    assert invalid["approval_decision"]["error_code"] == "invalid_approval_decision"


def test_approval_node_rejects_mismatched_proposal_id(tmp_path: Path) -> None:
    _target, _proposal, state = _state(tmp_path)
    graph = _approval_graph()
    config = {"configurable": {"thread_id": state["run_id"]}}
    asyncio.run(graph.ainvoke(state, config))

    resumed = asyncio.run(
        graph.ainvoke(
            Command(resume={"proposal_id": str(uuid4()), "decision": "approve"}),
            config,
        )
    )

    assert resumed["approval_decision"]["valid"] is False
    assert resumed["approval_decision"]["error_code"] == "proposal_mismatch"
