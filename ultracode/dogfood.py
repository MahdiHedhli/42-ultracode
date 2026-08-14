"""A bounded multi-iteration self-dogfood scenario for 42 Ultracode."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .controller import Controller
from .protocol import JsonObject, JsonValue, ResultStatus, redact_secrets


@dataclass(frozen=True)
class DogfoodEvidence:
    run_id: str
    final_state: str
    max_iterations: int
    iterations: int
    events_produced: int
    state_transitions: tuple[str, ...]
    commands: tuple[str, ...]
    tests: tuple[str, ...]
    restart_reconstructed: bool
    manual_prompt_copy_count: int
    manual_interventions_required: tuple[str, ...]
    final_result: JsonObject | None

    def to_dict(self) -> JsonObject:
        return {
            "run_id": self.run_id,
            "final_state": self.final_state,
            "max_iterations": self.max_iterations,
            "iterations": self.iterations,
            "events_produced": self.events_produced,
            "state_transitions": list(self.state_transitions),
            "commands": list(self.commands),
            "tests": list(self.tests),
            "restart_reconstructed": self.restart_reconstructed,
            "manual_prompt_copy_count": self.manual_prompt_copy_count,
            "manual_interventions_required": list(self.manual_interventions_required),
            "final_result": self.final_result,
        }


def _instruction(goal: str, done_when: str) -> dict[str, object]:
    return {
        "goal": goal,
        "context": [
            "This is the 42 Ultracode repository.",
            "The controller state must be reconstructable from append-only events.",
        ],
        "constraints": [
            "Do not modify controller policy.",
            "Capture evidence rather than asserting success.",
        ],
        "done_when": done_when,
        "required_tests": ["targeted replay tests", "full automated suite"],
        "discipline_skills": ["ultracode-worker", "speckit-analyze"],
    }


def _result(
    *,
    summary: str,
    evidence: Sequence[JsonValue],
    tests: Sequence[JsonValue],
    commands: Sequence[JsonValue],
    next_action: str,
) -> dict[str, object]:
    return {
        "status": ResultStatus.COMPLETED.value,
        "summary": summary,
        "evidence": list(evidence),
        "changed_files": [],
        "tests": list(tests),
        "commands": list(commands),
        "commit": None,
        "blockers": [],
        "questions": [],
        "remaining_uncertainty": [],
        "recommended_next_action": next_action,
    }


def run_dogfood(
    database: str | Path,
    *,
    evidence_path: str | Path | None = None,
    project_root: str | Path | None = None,
    test_command: Sequence[str] | None = None,
) -> DogfoodEvidence:
    """Exercise two real controller iterations and persist sanitized evidence.

    The harness deliberately simulates planner/worker tool callers. It validates
    the local transport core without falsely claiming autonomous ChatGPT re-entry.
    It does run the repository's automated test command as worker evidence.
    """

    root = Path(project_root) if project_root is not None else Path.cwd()
    controller = Controller(database, max_iteration_ceiling=20)
    run = controller.create_run(
        "Dogfood 42 Ultracode: inspect replay correctness, validate recovery, and review the suite.",
        max_iterations=4,
        idempotency_key="dogfood:create",
    )
    run = controller.submit_instruction(
        run.run_id,
        _instruction(
            "Inspect event replay and controller restart semantics for correctness gaps.",
            "Claim one instruction, submit evidence-rich findings, and preserve a replayable state.",
        ),
        idempotency_key="dogfood:instruction:1",
    )
    claim_one = controller.claim_turn(run.run_id, worker_id="dogfood-worker", idempotency_key="dogfood:claim:1")
    controller.submit_result(
        run.run_id,
        _result(
            summary="Replayed the initial handoff and confirmed the controller can reload its state.",
            evidence=["Initial instruction was claimed once and its state was persisted."],
            tests=["restart snapshot comparison"],
            commands=["controller history + replay"],
            next_action="Run the full validation suite and review its evidence.",
        ),
        worker_id=claim_one.worker_id,
        lease_token=claim_one.lease_token,
        idempotency_key="dogfood:result:1",
    )
    after_restart = Controller(database, max_iteration_ceiling=20).get_run(run.run_id)
    restart_reconstructed = after_restart.state.value == "CODEX_COMPLETE" and after_restart.iteration == 1

    run = controller.submit_instruction(
        run.run_id,
        _instruction(
            "Run the full validation suite and review whether evidence supports completion.",
            "The complete test command exits successfully and results are persisted as evidence.",
        ),
        idempotency_key="dogfood:instruction:2",
    )
    claim_two = controller.claim_turn(run.run_id, worker_id="dogfood-worker", idempotency_key="dogfood:claim:2")
    command = list(test_command) if test_command is not None else [sys.executable, "-m", "pytest"]
    if not command:
        raise ValueError("test_command must not be empty")
    command_text = " ".join(command)
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    test_summary = (completed.stdout + completed.stderr)[-2000:]
    if completed.returncode != 0:
        controller.report_blocker(
            run.run_id,
            worker_id=claim_two.worker_id,
            lease_token=claim_two.lease_token,
            reason=f"dogfood test command failed with exit {completed.returncode}: {test_summary}",
            idempotency_key="dogfood:blocker:2",
        )
        raise RuntimeError("dogfood validation command failed")
    run = controller.submit_result(
        run.run_id,
        _result(
            summary="The full automated validation suite completed successfully.",
            evidence=[f"pytest exited with code {completed.returncode}", test_summary],
            tests=[command_text],
            commands=[command_text],
            next_action="Review evidence and complete the bounded dogfood run.",
        ),
        worker_id=claim_two.worker_id,
        lease_token=claim_two.lease_token,
        idempotency_key="dogfood:result:2",
    )
    final_run = controller.complete_run(
        run.run_id,
        "Two bounded planner/worker iterations supplied restart and test evidence.",
        idempotency_key="dogfood:complete",
    )
    history = controller.history(run.run_id)
    evidence = DogfoodEvidence(
        run_id=run.run_id,
        final_state=final_run.state.value,
        max_iterations=final_run.max_iterations,
        iterations=final_run.iteration,
        events_produced=len(history),
        state_transitions=tuple(event.event_type.value for event in history),
        commands=(command_text,),
        tests=(command_text,),
        restart_reconstructed=restart_reconstructed,
        manual_prompt_copy_count=0,
        manual_interventions_required=(
            "No prompt copying inside the local harness.",
            "ChatGPT planner invocation remains explicit at Level C.",
        ),
        final_result=None if final_run.last_result is None else final_run.last_result.to_dict(),
    )
    if evidence_path is not None:
        destination = Path(evidence_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        safe = redact_secrets(evidence.to_dict())
        destination.write_text(json.dumps(safe, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return evidence
