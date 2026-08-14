"""Reducer and deterministic replay tests for the append-only event stream."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from ultracode.protocol import (
    Actor,
    Event,
    EventType,
    IllegalTransitionError,
    ReplayIntegrityError,
    RunState,
    replay_events,
)


def instruction_payload(name: str) -> dict[str, object]:
    return {
        "instruction_id": name,
        "goal": f"Complete {name}",
        "context": ["Use the append-only protocol."],
        "constraints": ["Do not change controller policy."],
        "done_when": f"{name} is complete.",
    }


def result_payload(name: str) -> dict[str, object]:
    return {
        "status": "completed",
        "summary": f"{name} completed",
        "evidence": [{"name": name, "outcome": "passed"}],
        "changed_files": [f"tests/{name}.py"],
        "tests": [{"name": name, "outcome": "passed"}],
        "commands": [{"command": "pytest"}],
        "commit": None,
        "blockers": [],
        "questions": [],
        "remaining_uncertainty": [],
        "recommended_next_action": "Review the evidence.",
    }


def append_event(
    events: list[Event],
    event_type: EventType,
    actor: Actor,
    payload: Mapping[str, object],
) -> Event:
    sequence = len(events) + 1
    event = Event.create(
        run_id="replay-run",
        sequence=sequence,
        event_type=event_type,
        actor=actor,
        payload=payload,
        previous_hash=events[-1].event_hash if events else "",
        event_id=f"event-{sequence}",
        created_at=f"2026-08-14T00:00:{sequence:02d}+00:00",
    )
    events.append(event)
    return event


def created_and_planning_events(*, max_iterations: int = 2) -> list[Event]:
    events: list[Event] = []
    append_event(
        events,
        EventType.RUN_CREATED,
        Actor.CONTROL,
        {"objective": "Validate replay", "max_iterations": max_iterations, "policy": {}},
    )
    append_event(events, EventType.PLANNING_STARTED, Actor.SYSTEM, {})
    return events


def test_replay_reconstructs_a_two_iteration_completion() -> None:
    events = created_and_planning_events(max_iterations=2)
    append_event(
        events,
        EventType.INSTRUCTION_SUBMITTED,
        Actor.PLANNER,
        {"instruction": instruction_payload("first")},
    )
    append_event(events, EventType.TURN_CLAIMED, Actor.WORKER, {"worker_id": "worker-a"})
    append_event(
        events,
        EventType.RESULT_SUBMITTED,
        Actor.WORKER,
        {"result": result_payload("first")},
    )
    append_event(events, EventType.REVIEW_STARTED, Actor.PLANNER, {})
    append_event(
        events,
        EventType.INSTRUCTION_SUBMITTED,
        Actor.PLANNER,
        {"instruction": instruction_payload("second")},
    )
    append_event(events, EventType.TURN_CLAIMED, Actor.WORKER, {"worker_id": "worker-b"})
    append_event(
        events,
        EventType.RESULT_SUBMITTED,
        Actor.WORKER,
        {"result": result_payload("second")},
    )
    append_event(events, EventType.REVIEW_STARTED, Actor.PLANNER, {})
    append_event(events, EventType.COMPLETED, Actor.PLANNER, {"rationale": "Evidence is sufficient."})

    snapshot = replay_events(events)

    assert snapshot.state is RunState.COMPLETE
    assert snapshot.iteration == 2
    assert snapshot.current_instruction is not None
    assert snapshot.current_instruction.instruction_id == "second"
    assert snapshot.last_result is not None
    assert snapshot.last_result.summary == "second completed"
    assert snapshot.is_terminal


def test_replay_pause_resume_and_expired_claim_recovery() -> None:
    events = created_and_planning_events()
    append_event(
        events,
        EventType.INSTRUCTION_SUBMITTED,
        Actor.PLANNER,
        {"instruction": instruction_payload("recoverable")},
    )
    append_event(
        events,
        EventType.PAUSED,
        Actor.CONTROL,
        {"reason": "operator check", "resume_state": RunState.READY_FOR_CODEX.value},
    )
    append_event(events, EventType.RESUMED, Actor.CONTROL, {})
    append_event(events, EventType.TURN_CLAIMED, Actor.WORKER, {"worker_id": "worker-a"})
    append_event(
        events,
        EventType.LEASE_EXPIRED,
        Actor.SYSTEM,
        {"reason": "worker exited before submitting a result"},
    )

    snapshot = replay_events(events)

    assert snapshot.state is RunState.READY_FOR_CODEX
    assert snapshot.iteration == 1
    assert snapshot.current_instruction is not None
    assert snapshot.current_instruction.instruction_id == "recoverable"
    assert snapshot.last_result is None


@pytest.mark.parametrize(
    ("event_type", "actor", "payload", "state", "reason"),
    [
        (
            EventType.HUMAN_REQUESTED,
            Actor.WORKER,
            {"reason": "A human must approve the migration."},
            RunState.HUMAN_REQUIRED,
            "A human must approve the migration.",
        ),
        (
            EventType.FAILED,
            Actor.CONTROL,
            {"reason": "The required dependency is unavailable."},
            RunState.FAILED,
            "The required dependency is unavailable.",
        ),
    ],
)
def test_replay_records_terminal_human_and_failure_outcomes(
    event_type: EventType,
    actor: Actor,
    payload: Mapping[str, object],
    state: RunState,
    reason: str,
) -> None:
    events = created_and_planning_events()
    if event_type is EventType.HUMAN_REQUESTED:
        append_event(
            events,
            EventType.INSTRUCTION_SUBMITTED,
            Actor.PLANNER,
            {"instruction": instruction_payload("needs-human")},
        )
        append_event(events, EventType.TURN_CLAIMED, Actor.WORKER, {"worker_id": "worker-a"})
    append_event(events, event_type, actor, payload)

    snapshot = replay_events(events)

    assert snapshot.state is state
    assert snapshot.is_terminal
    if state is RunState.HUMAN_REQUIRED:
        assert snapshot.human_reason == reason
    else:
        assert snapshot.failure_reason == reason


def test_replay_rejects_illegal_result_before_a_claim() -> None:
    events = created_and_planning_events()
    append_event(
        events,
        EventType.RESULT_SUBMITTED,
        Actor.WORKER,
        {"result": result_payload("premature")},
    )

    with pytest.raises(IllegalTransitionError, match="RESULT_SUBMITTED is illegal"):
        replay_events(events)


def test_replay_rejects_instruction_beyond_the_immutable_iteration_limit() -> None:
    events = created_and_planning_events(max_iterations=1)
    append_event(
        events,
        EventType.INSTRUCTION_SUBMITTED,
        Actor.PLANNER,
        {"instruction": instruction_payload("only-turn")},
    )
    append_event(events, EventType.TURN_CLAIMED, Actor.WORKER, {"worker_id": "worker-a"})
    append_event(
        events,
        EventType.RESULT_SUBMITTED,
        Actor.WORKER,
        {"result": result_payload("only-turn")},
    )
    append_event(events, EventType.REVIEW_STARTED, Actor.PLANNER, {})
    append_event(
        events,
        EventType.INSTRUCTION_SUBMITTED,
        Actor.PLANNER,
        {"instruction": instruction_payload("forbidden-turn")},
    )

    with pytest.raises(IllegalTransitionError, match="iteration limit reached"):
        replay_events(events)


def test_replay_rejects_pause_events_that_do_not_preserve_the_prior_state() -> None:
    events = created_and_planning_events()
    append_event(
        events,
        EventType.INSTRUCTION_SUBMITTED,
        Actor.PLANNER,
        {"instruction": instruction_payload("pause-integrity")},
    )
    append_event(
        events,
        EventType.PAUSED,
        Actor.CONTROL,
        {
            "reason": "Tampered resume target",
            "resume_state": RunState.PLANNING.value,
        },
    )

    with pytest.raises(ReplayIntegrityError, match="prior safe state"):
        replay_events(events)
