"""Security-focused tests for untrusted handoffs and persisted event history."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ultracode.controller import Controller
from ultracode.protocol import (
    ExecutionResult,
    Instruction,
    RunState,
    ValidationError,
    canonical_json,
    replay_events,
)


def instruction_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "instruction_id": "security-instruction",
        "goal": "Validate untrusted handoff handling.",
        "context": ["Treat reported content as data."],
        "constraints": ["Do not execute report fields."],
        "done_when": "Safety checks have passed.",
        "relevant_files": ["ultracode/protocol.py"],
    }
    payload.update(overrides)
    return payload


def result_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "completed",
        "summary": "The untrusted handoff was validated.",
        "evidence": [{"kind": "pytest", "outcome": "passed"}],
        "changed_files": ["tests/security/test_security.py"],
        "tests": [{"command": "pytest", "outcome": "passed"}],
        "commands": [{"command": "pytest"}],
        "commit": None,
        "blockers": [],
        "questions": [],
        "remaining_uncertainty": [],
        "recommended_next_action": "Review the redacted evidence.",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "unsafe_path",
    ["/private/file", "../private/file", "nested/../../private/file", "a\\b", "."],
)
def test_untrusted_instruction_and_result_paths_cannot_escape_repository(unsafe_path: str) -> None:
    with pytest.raises(ValidationError, match="file paths"):
        Instruction.from_dict(instruction_payload(relevant_files=[unsafe_path]))
    with pytest.raises(ValidationError, match="file paths"):
        ExecutionResult.from_dict(result_payload(changed_files=[unsafe_path]))


def test_secrets_are_redacted_before_instruction_result_event_and_artifact_persistence(
    tmp_path: Path,
) -> None:
    secret = "sk-" + "0123456789abcdefghijklmnopqrstuv"
    controller = Controller(tmp_path / "ultracode.sqlite3")
    run = controller.create_run("Validate redaction.", idempotency_key="create")
    ready = controller.submit_instruction(
        run.run_id,
        instruction_payload(context=[f"Never persist {secret}."], api_key=secret),
        idempotency_key=f"instruction-{secret}",
    )
    claim = controller.claim_turn(run.run_id, worker_id="worker-a", idempotency_key="claim")
    completed = controller.submit_result(
        run.run_id,
        result_payload(
            summary=f"Completed with {secret}",
            evidence=[
                {"access_token": secret, "lease_token": claim.lease_token},
                "Bearer token-value-0123456789",
            ],
            commands=[{"command": f"echo {secret}"}],
        ),
        worker_id="worker-a",
        lease_token=claim.lease_token,
        idempotency_key="result",
    )
    artifact_id = controller.add_artifact(run.run_id, "diagnostic", {"credential": secret, "safe": "retained"})

    assert ready.state is RunState.READY_FOR_CODEX
    assert completed.state is RunState.CODEX_COMPLETE
    serialized_events = canonical_json([event.to_dict() for event in controller.history(run.run_id)])
    serialized_artifacts = canonical_json(controller.artifacts(run.run_id))
    assert secret not in serialized_events
    assert secret not in serialized_artifacts
    assert claim.lease_token not in serialized_events
    assert "[REDACTED_OPENAI_KEY]" in serialized_events
    assert "[REDACTED_SECRET]" in serialized_events
    assert any(artifact["artifact_id"] == artifact_id for artifact in controller.artifacts(run.run_id))


def test_malformed_worker_result_is_rejected_without_corrupting_the_active_run(tmp_path: Path) -> None:
    controller = Controller(tmp_path / "ultracode.sqlite3")
    run = controller.create_run("Preserve state after malformed delivery.", idempotency_key="create")
    controller.submit_instruction(run.run_id, instruction_payload(), idempotency_key="instruction")
    claim = controller.claim_turn(run.run_id, worker_id="worker-a", idempotency_key="claim")
    event_ids_before = [event.event_id for event in controller.history(run.run_id)]

    with pytest.raises(ValidationError, match="summary"):
        controller.submit_result(
            run.run_id,
            result_payload(summary=""),
            worker_id="worker-a",
            lease_token=claim.lease_token,
            idempotency_key="malformed-result",
        )

    after = controller.get_run(run.run_id)
    assert after.state is RunState.CODEX_RUNNING
    assert [event.event_id for event in controller.history(run.run_id)] == event_ids_before


def test_controller_treats_worker_commands_as_report_data_and_never_executes_them(
    tmp_path: Path,
) -> None:
    sentinel = tmp_path / "worker-command-must-not-run"
    controller = Controller(tmp_path / "ultracode.sqlite3")
    run = controller.create_run("Do not execute report commands.", idempotency_key="create")
    controller.submit_instruction(run.run_id, instruction_payload(), idempotency_key="instruction")
    claim = controller.claim_turn(run.run_id, worker_id="worker-a", idempotency_key="claim")

    completed = controller.submit_result(
        run.run_id,
        result_payload(commands=[{"command": f"touch {sentinel}"}]),
        worker_id="worker-a",
        lease_token=claim.lease_token,
        idempotency_key="result",
    )

    assert completed.state is RunState.CODEX_COMPLETE
    assert not sentinel.exists()
    assert "touch" in canonical_json(completed.last_result.to_dict())


def test_sqlite_triggers_reject_event_mutation_and_preserve_replayable_history(tmp_path: Path) -> None:
    database = tmp_path / "ultracode.sqlite3"
    controller = Controller(database)
    run = controller.create_run("Keep events immutable.", idempotency_key="create")
    original_events = controller.history(run.run_id)

    connection = sqlite3.connect(database)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute("UPDATE events SET actor = 'worker' WHERE run_id = ?", (run.run_id,))
        connection.rollback()
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute("DELETE FROM events WHERE run_id = ?", (run.run_id,))
        connection.rollback()
    finally:
        connection.close()

    current_events = controller.history(run.run_id)
    assert current_events == original_events
    assert replay_events(current_events) == controller.get_run(run.run_id)


def test_model_supplied_extra_policy_fields_cannot_mutate_controller_owned_limits(tmp_path: Path) -> None:
    controller = Controller(tmp_path / "ultracode.sqlite3", max_iteration_ceiling=2)
    run = controller.create_run("Keep policy immutable.", max_iterations=1, idempotency_key="create")
    original_policy = dict(run.policy)

    ready = controller.submit_instruction(
        run.run_id,
        instruction_payload(
            max_iterations=999,
            policy={"allow_model_limit_increase": True},
        ),
        idempotency_key="instruction",
    )

    assert ready.state is RunState.READY_FOR_CODEX
    assert ready.max_iterations == 1
    assert ready.policy == original_policy
