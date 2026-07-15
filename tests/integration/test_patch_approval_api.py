"""HTTP 202/decision lifecycle and safe response tests for P4."""

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from repopilot.api.app import create_app
from repopilot.infrastructure.config import AppSettings
from repopilot.testing.contracts import TestOutcome
from tests.fake_runner import ScriptedPytestRunner, make_test_result
from tests.scripted_model import ScriptedToolCallingModel


def _call(path: str, content: str, call_id: str = "patch-call") -> dict[str, object]:
    return {
        "name": "propose_patch",
        "args": {"path": path, "new_content": content, "rationale": "review this change"},
        "id": call_id,
        "type": "tool_call",
    }


def _app(tmp_path: Path, responses: list[AIMessage], *, pass_count: int = 1):
    settings = AppSettings(
        workspace_path=tmp_path,
        data_directory=tmp_path.parent / f"{tmp_path.name}-runtime",
        model_api_key=None,
        _env_file=None,
    )
    model = ScriptedToolCallingModel(responses=responses)
    runner = ScriptedPytestRunner(
        [make_test_result(TestOutcome.PASSED, exit_code=0) for _ in range(pass_count)]
    )
    return create_app(settings, model_override=model, runner_override=runner), model


def test_patch_start_returns_202_safe_payload_and_approve_resumes(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(b"old\n")
    proposed_marker = "new-private-content\n"
    app, model = _app(
        tmp_path,
        [
            AIMessage(content="", tool_calls=[_call("a.py", proposed_marker)]),
            AIMessage(content="done"),
        ],
    )

    with TestClient(app) as client:
        pending = client.post("/agent/run", json={"goal": "change", "max_steps": 3})
        assert target.read_bytes() == b"old\n"
        payload = pending.json()
        decision = client.post(
            f"/agent/runs/{payload['run_id']}/decision",
            json={"proposal_id": payload["approval"]["proposal_id"], "decision": "approve"},
        )
        health = client.get("/health")

    assert pending.status_code == 202
    assert payload["status"] == "awaiting_approval"
    assert payload["approval"]["relative_path"] == "a.py"
    assert proposed_marker.strip() in payload["approval"]["unified_diff"]
    assert "proposed_content" not in pending.text
    assert "checkpoint" not in pending.text
    assert payload["approval"]["post_apply_verification"] == {
        "runner": "pytest",
        "target": "tests",
        "automatic": True,
    }
    assert str(tmp_path) not in pending.text
    assert decision.status_code == 200
    assert decision.json()["status"] == "repaired"
    assert proposed_marker not in decision.text
    assert target.read_bytes() == proposed_marker.encode()
    assert health.status_code == 200
    assert len(model.received_messages) == 1


def test_reject_endpoint_keeps_file_and_duplicate_decision_is_conflict(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(b"old\n")
    app, _model = _app(
        tmp_path,
        [AIMessage(content="", tool_calls=[_call("a.py", "new\n")]), AIMessage(content="kept")],
    )

    with TestClient(app) as client:
        pending = client.post("/agent/run", json={"goal": "change"}).json()
        body = {"proposal_id": pending["approval"]["proposal_id"], "decision": "reject"}
        rejected = client.post(f"/agent/runs/{pending['run_id']}/decision", json=body)
        duplicate = client.post(f"/agent/runs/{pending['run_id']}/decision", json=body)

    assert rejected.status_code == 200
    assert target.read_bytes() == b"old\n"
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "run_already_completed"


def test_decision_endpoint_returns_stable_lookup_and_mismatch_errors(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_bytes(b"old\n")
    app, _model = _app(
        tmp_path,
        [AIMessage(content="", tool_calls=[_call("a.py", "new\n")])],
    )

    with TestClient(app) as client:
        pending = client.post("/agent/run", json={"goal": "change"}).json()
        not_found = client.post(
            f"/agent/runs/{uuid4()}/decision",
            json={"proposal_id": pending["approval"]["proposal_id"], "decision": "approve"},
        )
        mismatch = client.post(
            f"/agent/runs/{pending['run_id']}/decision",
            json={"proposal_id": str(uuid4()), "decision": "approve"},
        )

    assert not_found.status_code == 404
    assert not_found.json()["detail"]["code"] == "run_not_found"
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "proposal_mismatch"


def test_decision_schema_rejects_edit_thread_id_and_new_content(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_bytes(b"old\n")
    app, _model = _app(
        tmp_path,
        [AIMessage(content="", tool_calls=[_call("a.py", "new\n")])],
    )
    with TestClient(app) as client:
        pending = client.post("/agent/run", json={"goal": "change"}).json()
        endpoint = f"/agent/runs/{pending['run_id']}/decision"
        base = {"proposal_id": pending["approval"]["proposal_id"], "decision": "approve"}
        invalid_decision = client.post(endpoint, json={**base, "decision": "edit"})
        with_content = client.post(endpoint, json={**base, "new_content": "forged"})
        with_thread = client.post(endpoint, json={**base, "thread_id": "other"})

    assert invalid_decision.status_code == 422
    assert with_content.status_code == 422
    assert with_thread.status_code == 422
    assert (tmp_path / "a.py").read_bytes() == b"old\n"


def test_two_pending_api_runs_do_not_cross_apply(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_bytes(b"a-old\n")
    (tmp_path / "b.py").write_bytes(b"b-old\n")
    app, _model = _app(
        tmp_path,
        [
            AIMessage(content="", tool_calls=[_call("a.py", "a-new\n", "a")]),
            AIMessage(content="", tool_calls=[_call("b.py", "b-new\n", "b")]),
            AIMessage(content="a done"),
        ],
    )
    with TestClient(app) as client:
        run_a = client.post("/agent/run", json={"goal": "a"}).json()
        run_b = client.post("/agent/run", json={"goal": "b"}).json()
        crossed = client.post(
            f"/agent/runs/{run_a['run_id']}/decision",
            json={"proposal_id": run_b["approval"]["proposal_id"], "decision": "approve"},
        )
        approved_a = client.post(
            f"/agent/runs/{run_a['run_id']}/decision",
            json={"proposal_id": run_a["approval"]["proposal_id"], "decision": "approve"},
        )

    assert crossed.status_code == 409
    assert approved_a.status_code == 200
    assert (tmp_path / "a.py").read_bytes() == b"a-new\n"
    assert (tmp_path / "b.py").read_bytes() == b"b-old\n"
