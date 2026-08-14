"""End-to-end local dogfood and fixed-ceiling replay coverage."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ultracode.controller import Controller, PolicyError
from ultracode.dogfood import run_dogfood
from ultracode.protocol import RunState, replay_events


def _instruction(index: int) -> dict[str, object]:
    return {
        "instruction_id": f"iteration-{index}",
        "goal": f"Complete bounded iteration {index}.",
        "context": ["This test validates controller-owned iteration limits."],
        "constraints": ["Do not change the configured limit."],
        "done_when": "A structured result is persisted.",
    }


def _result(index: int) -> dict[str, object]:
    return {
        "status": "completed",
        "summary": f"Iteration {index} completed.",
        "evidence": [{"iteration": index, "outcome": "passed"}],
        "changed_files": [],
        "tests": ["pytest"],
        "commands": ["pytest"],
        "commit": None,
        "blockers": [],
        "questions": [],
        "remaining_uncertainty": [],
        "recommended_next_action": "Review the evidence.",
    }


def test_scripted_dogfood_records_two_iterations_restart_and_sanitized_evidence(tmp_path: Path) -> None:
    database = tmp_path / "dogfood.db"
    evidence_path = tmp_path / "dogfood-evidence.json"
    command = [sys.executable, "-c", "print('dogfood validation passed')"]

    evidence = run_dogfood(
        database,
        evidence_path=evidence_path,
        project_root=tmp_path,
        test_command=command,
    )

    persisted = json.loads(evidence_path.read_text(encoding="utf-8"))
    snapshot = Controller(database).get_run(evidence.run_id)
    assert evidence.final_state == RunState.COMPLETE.value
    assert evidence.max_iterations == 4
    assert evidence.iterations == 2
    assert evidence.restart_reconstructed is True
    assert evidence.manual_prompt_copy_count == 0
    assert evidence.final_result is not None
    assert evidence.final_result["summary"] == "The full automated validation suite completed successfully."
    assert persisted == evidence.to_dict()
    assert persisted["manual_prompt_copy_count"] == 0
    assert snapshot.state is RunState.COMPLETE
    assert replay_events(Controller(database).history(evidence.run_id)) == snapshot


def test_fixed_limit_allows_ten_sequential_iterations_then_rejects_the_eleventh(tmp_path: Path) -> None:
    controller = Controller(tmp_path / "ten-iterations.db", max_iteration_ceiling=10)
    run = controller.create_run("Exercise exactly ten bounded turns.", max_iterations=10, idempotency_key="create")

    for index in range(1, 11):
        ready = controller.submit_instruction(
            run.run_id,
            _instruction(index),
            idempotency_key=f"instruction-{index}",
        )
        assert ready.iteration == index
        claim = controller.claim_turn(run.run_id, worker_id="ten-turn-worker", idempotency_key=f"claim-{index}")
        completed = controller.submit_result(
            run.run_id,
            _result(index),
            worker_id=claim.worker_id,
            lease_token=claim.lease_token,
            idempotency_key=f"result-{index}",
        )
        assert completed.state is RunState.CODEX_COMPLETE

    rejected = controller.submit_instruction(
        run.run_id,
        _instruction(11),
        idempotency_key="instruction-11",
    )
    assert rejected.state is RunState.FAILED
    assert rejected.iteration == 10
    assert rejected.failure_reason == "iteration limit reached"
    assert replay_events(controller.history(run.run_id)) == rejected

    with pytest.raises(PolicyError, match="ceiling 10"):
        Controller(tmp_path / "separate.db", max_iteration_ceiling=10).create_run(
            "A model may not raise the controller ceiling.",
            max_iterations=11,
        )
