"""Focused unit coverage for typed Ultracode handoffs and event envelopes."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ultracode.protocol import (
    Actor,
    Event,
    EventType,
    ExecutionResult,
    Instruction,
    ReplayIntegrityError,
    ResultStatus,
    RunState,
    ValidationError,
    canonical_json,
    hash_event,
    replay_events,
)


def instruction_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "instruction_id": "instruction-1",
        "goal": "Add focused tests",
        "context": ["The repository uses local SQLite state."],
        "constraints": ["Do not change policy."],
        "done_when": "The tests pass.",
        "relevant_files": ["ultracode/protocol.py"],
        "required_tests": ["pytest tests/unit/test_protocol.py"],
        "prohibited_changes": ["policy.json"],
        "evidence_requirements": ["Report pytest output."],
        "discipline_skills": ["ultracode-worker"],
    }
    payload.update(overrides)
    return payload


def result_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": ResultStatus.COMPLETED.value,
        "summary": "Focused protocol validation completed.",
        "evidence": [{"kind": "test", "outcome": "passed"}],
        "changed_files": ["tests/unit/test_protocol.py"],
        "tests": [{"command": "pytest", "outcome": "passed"}],
        "commands": [{"command": "pytest tests/unit/test_protocol.py"}],
        "commit": "abc1234",
        "blockers": [],
        "questions": [],
        "remaining_uncertainty": [],
        "recommended_next_action": "Review the evidence.",
    }
    payload.update(overrides)
    return payload


def test_instruction_preserves_typed_optional_handoff_fields() -> None:
    instruction = Instruction.from_dict(instruction_payload())

    assert instruction.goal == "Add focused tests"
    assert instruction.context == ("The repository uses local SQLite state.",)
    assert instruction.constraints == ("Do not change policy.",)
    assert instruction.relevant_files == ("ultracode/protocol.py",)
    assert instruction.required_tests == ("pytest tests/unit/test_protocol.py",)
    assert instruction.discipline_skills == ("ultracode-worker",)
    assert instruction.to_dict()["done_when"] == "The tests pass."


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/tmp/outside.py",
        "../outside.py",
        "nested/../../outside.py",
        "ultracode\\protocol.py",
        ".",
        "",
    ],
)
def test_instruction_rejects_unsafe_repository_paths(unsafe_path: str) -> None:
    with pytest.raises(ValidationError, match="file paths"):
        Instruction.from_dict(instruction_payload(relevant_files=[unsafe_path]))


@pytest.mark.parametrize(
    "field,value",
    [
        ("goal", ""),
        ("context", []),
        ("constraints", None),
        ("done_when", " "),
    ],
)
def test_instruction_rejects_missing_or_empty_required_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Instruction.from_dict(instruction_payload(**{field: value}))


def test_execution_result_is_typed_and_redacts_secrets_before_use() -> None:
    secret = "sk-" + "0123456789abcdefghijklmnopqrstuv"
    result = ExecutionResult.from_dict(
        result_payload(
            summary=f"Validation used {secret}",
            evidence=[{"api_key": secret}, "Bearer token-value-0123456789"],
            commands=[{"command": f"echo {secret}"}],
        )
    )

    serialized = canonical_json(result.to_dict())
    assert result.status is ResultStatus.COMPLETED
    assert secret not in serialized
    assert "[REDACTED_OPENAI_KEY]" in serialized
    assert "[REDACTED_SECRET]" in serialized
    assert "[REDACTED_TOKEN]" in serialized


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "not-a-status"),
        ("summary", ""),
        ("changed_files", ["/workspace/absolute.py"]),
        ("changed_files", ["../outside.py"]),
        ("tests", "pytest"),
        ("commit", 42),
        ("recommended_next_action", ""),
    ],
)
def test_execution_result_rejects_malformed_handoffs(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ExecutionResult.from_dict(result_payload(**{field: value}))


def test_event_hash_chain_is_deterministic_and_detects_mutated_content() -> None:
    created = Event.create(
        run_id="run-1",
        sequence=1,
        event_type=EventType.RUN_CREATED,
        actor=Actor.CONTROL,
        payload={"objective": "Validate protocol", "max_iterations": 2, "policy": {}},
        event_id="event-1",
        created_at="2026-08-14T00:00:00+00:00",
    )
    planning = Event.create(
        run_id="run-1",
        sequence=2,
        event_type=EventType.PLANNING_STARTED,
        actor=Actor.SYSTEM,
        payload={},
        previous_hash=created.event_hash,
        event_id="event-2",
        created_at="2026-08-14T00:00:01+00:00",
    )

    assert replay_events([created, planning]).state is RunState.PLANNING

    tampered = replace(planning, payload={"unexpected": "mutation"})
    with pytest.raises(ReplayIntegrityError, match="hash"):
        replay_events([created, tampered])


def test_loading_legacy_events_preserves_hashes_while_public_history_redacts_newly_recognized_tokens() -> None:
    created = Event.create(
        run_id="legacy-run",
        sequence=1,
        event_type=EventType.RUN_CREATED,
        actor=Actor.CONTROL,
        payload={"objective": "Validate legacy replay", "max_iterations": 1, "policy": {}},
        event_id="legacy-event",
        created_at="2026-08-14T00:00:00+00:00",
    )
    legacy = replace(
        created,
        payload={**created.payload, "lease_token": "legacy-bearer-token"},
    )
    legacy = replace(legacy, event_hash=hash_event(legacy))

    loaded = Event.from_dict(legacy.to_dict())

    assert replay_events([loaded]).state is RunState.NEW
    assert loaded.payload["lease_token"] == "legacy-bearer-token"
    public = loaded.to_public_dict()
    assert public["payload"]["lease_token"] == "[REDACTED_SECRET]"
    assert public["payload_redacted"] is True
