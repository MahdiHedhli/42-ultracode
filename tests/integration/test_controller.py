"""Transactional controller integration tests backed by real SQLite files."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ultracode.controller import (
    ConflictError,
    Controller,
    ControllerError,
    DuplicateDeliveryError,
    LeaseError,
    PolicyError,
)
from ultracode.protocol import EventType, IllegalTransitionError, RunSnapshot, RunState, replay_events


def instruction_payload(label: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "instruction_id": f"instruction-{label}",
        "goal": f"Complete {label}",
        "context": ["Use the local controller state."],
        "constraints": ["Do not change workflow policy."],
        "done_when": f"{label} is complete and validated.",
        "relevant_files": ["ultracode/controller.py"],
        "required_tests": ["pytest"],
        "prohibited_changes": [".specify/memory/constitution.md"],
        "evidence_requirements": ["A passing test result."],
        "discipline_skills": ["ultracode-worker"],
    }
    payload.update(overrides)
    return payload


def result_payload(label: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "completed",
        "summary": f"{label} completed successfully.",
        "evidence": [{"kind": "test", "label": label, "outcome": "passed"}],
        "changed_files": [f"tests/{label}.py"],
        "tests": [{"command": "pytest", "outcome": "passed"}],
        "commands": [{"command": "pytest"}],
        "commit": None,
        "blockers": [],
        "questions": [],
        "remaining_uncertainty": [],
        "recommended_next_action": "Review the reported evidence.",
    }
    payload.update(overrides)
    return payload


def ready_run(controller: Controller, *, max_iterations: int = 3) -> tuple[str, RunSnapshot]:
    run = controller.create_run(
        "Exercise the persistent controller.",
        max_iterations=max_iterations,
        idempotency_key="create-run",
    )
    ready = controller.submit_instruction(
        run.run_id,
        instruction_payload("first"),
        idempotency_key="instruction-first",
    )
    return run.run_id, ready


def test_two_iteration_progression_is_replayable_after_a_controller_restart(tmp_path: Path) -> None:
    database = tmp_path / "ultracode.sqlite3"
    controller = Controller(database, max_iteration_ceiling=3)
    run = controller.create_run("Complete a two-turn dogfood loop.", max_iterations=2, idempotency_key="create")

    first_ready = controller.submit_instruction(
        run.run_id, instruction_payload("first"), idempotency_key="instruction-1"
    )
    assert first_ready.state is RunState.READY_FOR_CODEX
    assert first_ready.iteration == 1
    first_claim = controller.claim_turn(run.run_id, worker_id="worker-a", idempotency_key="claim-1")
    assert first_claim.instruction.instruction_id == "instruction-first"
    first_result = controller.submit_result(
        run.run_id,
        result_payload("first"),
        worker_id="worker-a",
        lease_token=first_claim.lease_token,
        idempotency_key="result-1",
    )
    assert first_result.state is RunState.CODEX_COMPLETE

    second_ready = controller.submit_instruction(
        run.run_id, instruction_payload("second"), idempotency_key="instruction-2"
    )
    assert second_ready.state is RunState.READY_FOR_CODEX
    assert second_ready.iteration == 2
    second_claim = controller.claim_turn(run.run_id, worker_id="worker-b", idempotency_key="claim-2")
    second_result = controller.submit_result(
        run.run_id,
        result_payload("second"),
        worker_id="worker-b",
        lease_token=second_claim.lease_token,
        idempotency_key="result-2",
    )
    assert second_result.state is RunState.CODEX_COMPLETE

    completed = controller.complete_run(
        run.run_id, "Two evidenced iterations meet the objective.", idempotency_key="complete"
    )
    events = controller.history(run.run_id)

    assert completed.state is RunState.COMPLETE
    assert completed.iteration == 2
    assert completed.last_result is not None
    assert completed.last_result.summary == "second completed successfully."
    assert [event.event_type for event in events] == [
        EventType.RUN_CREATED,
        EventType.PLANNING_STARTED,
        EventType.INSTRUCTION_SUBMITTED,
        EventType.TURN_CLAIMED,
        EventType.RESULT_SUBMITTED,
        EventType.REVIEW_STARTED,
        EventType.INSTRUCTION_SUBMITTED,
        EventType.TURN_CLAIMED,
        EventType.RESULT_SUBMITTED,
        EventType.REVIEW_STARTED,
        EventType.COMPLETED,
    ]
    assert replay_events(events) == completed

    restarted = Controller(database, max_iteration_ceiling=3)
    assert restarted.get_run(run.run_id) == completed
    assert restarted.read_result(run.run_id) == completed.last_result


def test_pause_resume_revokes_stale_lease_and_preserves_pending_instruction(tmp_path: Path) -> None:
    controller = Controller(tmp_path / "ultracode.sqlite3")
    run_id, ready = ready_run(controller)
    assert ready.state is RunState.READY_FOR_CODEX
    original_claim = controller.claim_turn(run_id, worker_id="worker-a", idempotency_key="claim-before-pause")

    paused = controller.pause_run(run_id, "Operator is reviewing scope.", idempotency_key="pause")
    assert paused.state is RunState.PAUSED
    assert paused.paused_from is RunState.READY_FOR_CODEX
    assert paused.current_instruction == original_claim.instruction
    with pytest.raises(ConflictError, match="no claimable instruction"):
        controller.claim_turn(run_id, worker_id="worker-b", idempotency_key="claim-paused")

    resumed = controller.resume_run(run_id, idempotency_key="resume")
    assert resumed.state is RunState.READY_FOR_CODEX
    with pytest.raises((LeaseError, IllegalTransitionError)):
        controller.submit_result(
            run_id,
            result_payload("stale"),
            worker_id="worker-a",
            lease_token=original_claim.lease_token,
            idempotency_key="stale-result",
        )

    replacement_claim = controller.claim_turn(run_id, worker_id="worker-b", idempotency_key="claim-after-resume")
    assert replacement_claim.lease_token != original_claim.lease_token
    assert replacement_claim.instruction == original_claim.instruction


def test_stop_failure_and_human_escalation_are_terminal(tmp_path: Path) -> None:
    controller = Controller(tmp_path / "ultracode.sqlite3")

    stopped_run = controller.create_run("Stop this run.", idempotency_key="create-stopped")
    stopped = controller.stop_run(stopped_run.run_id, "Operator stopped it.", idempotency_key="stop")
    assert stopped.state is RunState.STOPPED
    stopped_events = controller.history(stopped_run.run_id)
    with pytest.raises((ControllerError, IllegalTransitionError)):
        controller.resume_run(stopped_run.run_id, idempotency_key="resume-stopped")
    assert controller.get_run(stopped_run.run_id).state is RunState.STOPPED
    assert controller.history(stopped_run.run_id) == stopped_events

    failed_run = controller.create_run("Fail this run.", idempotency_key="create-failed")
    failed = controller.fail_run(failed_run.run_id, "Required local dependency failed.", idempotency_key="fail")
    assert failed.state is RunState.FAILED
    assert failed.failure_reason == "Required local dependency failed."
    with pytest.raises(IllegalTransitionError, match="cannot submit"):
        controller.submit_instruction(
            failed_run.run_id, instruction_payload("failed"), idempotency_key="instruction-failed"
        )

    human_run_id, _ = ready_run(controller)
    claim = controller.claim_turn(human_run_id, worker_id="worker-a", idempotency_key="claim-human")
    human_required = controller.request_human(
        human_run_id, "A human must approve the destructive step.", idempotency_key="human"
    )
    assert human_required.state is RunState.HUMAN_REQUIRED
    assert human_required.human_reason == "A human must approve the destructive step."
    with pytest.raises(LeaseError, match="no active worker lease"):
        controller.report_progress(
            human_run_id,
            worker_id="worker-a",
            lease_token=claim.lease_token,
            message="This stale worker must not continue.",
            idempotency_key="stale-progress",
        )


def test_duplicate_delivery_is_idempotent_but_reused_keys_with_new_requests_fail(tmp_path: Path) -> None:
    controller = Controller(tmp_path / "ultracode.sqlite3")
    run = controller.create_run("Exercise retry semantics.", idempotency_key="create")
    retry_run = controller.create_run("Exercise retry semantics.", idempotency_key="create")
    assert retry_run.run_id == run.run_id
    with pytest.raises(DuplicateDeliveryError, match="reused"):
        controller.create_run("A different objective.", idempotency_key="create")

    instruction = instruction_payload("retry")
    ready = controller.submit_instruction(run.run_id, instruction, idempotency_key="instruction")
    history_length = len(controller.history(run.run_id))
    assert controller.submit_instruction(run.run_id, instruction, idempotency_key="instruction") == ready
    assert len(controller.history(run.run_id)) == history_length
    with pytest.raises(DuplicateDeliveryError, match="reused"):
        controller.submit_instruction(run.run_id, instruction_payload("different"), idempotency_key="instruction")

    claim = controller.claim_turn(run.run_id, worker_id="worker-a", idempotency_key="claim")
    assert controller.claim_turn(run.run_id, worker_id="worker-a", idempotency_key="claim") == claim
    with pytest.raises(DuplicateDeliveryError, match="reused"):
        controller.claim_turn(run.run_id, worker_id="worker-b", idempotency_key="claim")

    result = result_payload("retry")
    submitted = controller.submit_result(
        run.run_id,
        result,
        worker_id="worker-a",
        lease_token=claim.lease_token,
        idempotency_key="result",
    )
    history_length = len(controller.history(run.run_id))
    assert (
        controller.submit_result(
            run.run_id,
            result,
            worker_id="worker-a",
            lease_token=claim.lease_token,
            idempotency_key="result",
        )
        == submitted
    )
    assert len(controller.history(run.run_id)) == history_length
    with pytest.raises(DuplicateDeliveryError, match="reused"):
        controller.submit_result(
            run.run_id,
            result_payload("retry", summary="A different retried result."),
            worker_id="worker-a",
            lease_token=claim.lease_token,
            idempotency_key="result",
        )


def test_instruction_ids_are_unique_within_a_run_without_mutating_review_state(tmp_path: Path) -> None:
    controller = Controller(tmp_path / "ultracode.sqlite3")
    run = controller.create_run("Preserve unique instruction identities.", idempotency_key="create")
    first = instruction_payload("first", instruction_id="stable-instruction")
    controller.submit_instruction(run.run_id, first, idempotency_key="instruction-1")
    claim = controller.claim_turn(run.run_id, worker_id="worker-a", idempotency_key="claim-1")
    completed = controller.submit_result(
        run.run_id,
        result_payload("first"),
        worker_id=claim.worker_id,
        lease_token=claim.lease_token,
        idempotency_key="result-1",
    )
    events_before = controller.history(run.run_id)

    with pytest.raises(ConflictError, match="instruction_id"):
        controller.submit_instruction(
            run.run_id,
            instruction_payload("second", instruction_id="stable-instruction"),
            idempotency_key="instruction-2",
        )

    assert controller.get_run(run.run_id) == completed
    assert controller.history(run.run_id) == events_before


def test_simultaneous_claims_produce_exactly_one_worker_owner(tmp_path: Path) -> None:
    controller = Controller(tmp_path / "ultracode.sqlite3")
    run_id, _ = ready_run(controller)
    barrier = threading.Barrier(2)

    def attempt_claim(worker_id: str) -> object:
        barrier.wait()
        try:
            return controller.claim_turn(run_id, worker_id=worker_id, idempotency_key=f"claim-{worker_id}")
        except Exception as error:  # returned so assertion exposes an unexpected error type
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt_claim, ("worker-a", "worker-b")))

    claims = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
    errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    assert len(claims) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], ConflictError)
    assert controller.get_run(run_id).state is RunState.CODEX_RUNNING


def test_restart_recovers_an_expired_lease_without_losing_history(tmp_path: Path) -> None:
    database = tmp_path / "ultracode.sqlite3"
    controller = Controller(database, default_lease_seconds=30)
    run_id, _ = ready_run(controller)
    original_claim = controller.claim_turn(run_id, worker_id="worker-a", idempotency_key="claim", lease_seconds=30)

    restarted = Controller(database, default_lease_seconds=30)
    assert restarted.get_run(run_id).state is RunState.CODEX_RUNNING
    assert restarted.recover_expired_leases() == []

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE leases SET expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", run_id),
        )

    assert restarted.recover_expired_leases() == [run_id]
    recovered = restarted.get_run(run_id)
    assert recovered.state is RunState.READY_FOR_CODEX
    assert recovered.current_instruction == original_claim.instruction
    assert restarted.history(run_id)[-1].event_type is EventType.LEASE_EXPIRED
    replacement = restarted.claim_turn(run_id, worker_id="worker-b", idempotency_key="replacement-claim")
    assert replacement.lease_token != original_claim.lease_token


def test_iteration_limit_is_controller_owned_and_exhaustion_fails_the_run(tmp_path: Path) -> None:
    controller = Controller(tmp_path / "ultracode.sqlite3", max_iteration_ceiling=2)
    with pytest.raises(PolicyError, match="controller ceiling"):
        controller.create_run("Too many iterations.", max_iterations=3, idempotency_key="over-ceiling")

    run = controller.create_run("One bounded turn.", max_iterations=1, idempotency_key="create")
    controller.submit_instruction(
        run.run_id,
        instruction_payload("only"),
        idempotency_key="instruction-only",
    )
    claim = controller.claim_turn(run.run_id, worker_id="worker-a", idempotency_key="claim-only")
    controller.submit_result(
        run.run_id,
        result_payload("only"),
        worker_id="worker-a",
        lease_token=claim.lease_token,
        idempotency_key="result-only",
    )

    exhausted = controller.submit_instruction(
        run.run_id,
        instruction_payload("forbidden"),
        idempotency_key="instruction-forbidden",
    )
    assert exhausted.state is RunState.FAILED
    assert exhausted.iteration == 1
    assert exhausted.max_iterations == 1
    assert exhausted.failure_reason == "iteration limit reached"
    assert exhausted.policy["allow_model_limit_increase"] is False
    with pytest.raises(IllegalTransitionError, match="terminal"):
        controller.stop_run(run.run_id, "Cannot restart a terminal run.", idempotency_key="stop")
