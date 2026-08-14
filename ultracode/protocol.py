"""Typed protocol, event reduction, and input safety for 42 Ultracode.

The controller deliberately stores events rather than mutable model-authored state.
This module contains no database or subprocess code, which keeps validation and
deterministic replay independently testable.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import PurePosixPath
from uuid import uuid4

JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]


class ProtocolError(ValueError):
    """Base class for invalid handoffs or event histories."""


class ValidationError(ProtocolError):
    """Raised when an external payload violates the typed handoff contract."""


class IllegalTransitionError(ProtocolError):
    """Raised when an event attempts a state transition the controller forbids."""


class ReplayIntegrityError(ProtocolError):
    """Raised when event ordering or hash-chain integrity is invalid."""


class RunState(StrEnum):
    NEW = "NEW"
    PLANNING = "PLANNING"
    READY_FOR_CODEX = "READY_FOR_CODEX"
    CODEX_RUNNING = "CODEX_RUNNING"
    CODEX_COMPLETE = "CODEX_COMPLETE"
    REVIEWING = "REVIEWING"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    FAILED = "FAILED"
    COMPLETE = "COMPLETE"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


TERMINAL_STATES = frozenset({RunState.HUMAN_REQUIRED, RunState.FAILED, RunState.COMPLETE, RunState.STOPPED})


class Actor(StrEnum):
    PLANNER = "planner"
    WORKER = "worker"
    CONTROL = "control"
    HUMAN = "human"
    SYSTEM = "system"


class EventType(StrEnum):
    RUN_CREATED = "RUN_CREATED"
    PLANNING_STARTED = "PLANNING_STARTED"
    INSTRUCTION_SUBMITTED = "INSTRUCTION_SUBMITTED"
    TURN_CLAIMED = "TURN_CLAIMED"
    RESULT_SUBMITTED = "RESULT_SUBMITTED"
    REVIEW_STARTED = "REVIEW_STARTED"
    HUMAN_REQUESTED = "HUMAN_REQUESTED"
    PAUSED = "PAUSED"
    RESUMED = "RESUMED"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    PROGRESS_REPORTED = "PROGRESS_REPORTED"


class ResultStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"


_SENSITIVE_KEY = re.compile(r"(?:api[_-]?key|access[_-]?token|lease[_-]?token|secret|password|credential)", re.I)
_SECRET_REPLACERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]{12,}"), r"\1[REDACTED_TOKEN]"),
    (
        re.compile(r"(?i)((?:openai|codex|chatgpt)[_-]?(?:api[_-]?key|token)\s*[=:]\s*)[^\s'\";]+"),
        r"\1[REDACTED_SECRET]",
    ),
)


def utc_now() -> str:
    """Return a stable, timezone-aware timestamp format for persisted events."""

    return datetime.now(UTC).isoformat(timespec="microseconds")


def canonical_json(value: JsonValue) -> str:
    """Serialize a JSON-compatible value deterministically for hashes and requests."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _as_json(value: object) -> JsonValue:
    """Make a JSON-safe deep copy and reject objects that cannot be persisted."""

    try:
        encoded = json.dumps(value, ensure_ascii=False)
        decoded: JsonValue = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValidationError("payload must contain only JSON-compatible values") from exc
    return decoded


def redact_secrets(value: JsonValue) -> JsonValue:
    """Conservatively redact recognizable credential material before persistence.

    This is defense in depth, not a claim of exhaustive secret detection. A caller
    should avoid sending credentials to the controller in the first place.
    """

    if isinstance(value, str):
        redacted = value
        for pattern, replacement in _SECRET_REPLACERS:
            redacted = pattern.sub(replacement, redacted)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, dict):
        safe: dict[str, JsonValue] = {}
        for key, item in value.items():
            if _SENSITIVE_KEY.search(key):
                safe[key] = "[REDACTED_SECRET]"
            else:
                safe[key] = redact_secrets(item)
        return safe
    return value


def validate_relative_path(path: object) -> str:
    """Return a safe repository-relative POSIX path or raise a validation error."""

    if not isinstance(path, str) or not path.strip():
        raise ValidationError("file paths must be non-empty strings")
    if "\\" in path:
        raise ValidationError("file paths must use POSIX separators")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts or str(candidate) in {".", ""}:
        raise ValidationError("file paths must be relative and cannot traverse parents")
    return str(candidate)


def validate_relative_paths(paths: object) -> tuple[str, ...]:
    """Validate a sequence of repository-relative paths."""

    if not isinstance(paths, Sequence) or isinstance(paths, (str, bytes)):
        raise ValidationError("file paths must be a list of strings")
    return tuple(validate_relative_path(path) for path in paths)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _string_list(value: object, field_name: str, *, required: bool = False) -> tuple[str, ...]:
    if value is None and not required:
        return ()
    if isinstance(value, str):
        items: Sequence[object] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items = value
    else:
        raise ValidationError(f"{field_name} must be a string or list of strings")
    cleaned = tuple(_required_text(item, field_name) for item in items)
    if required and not cleaned:
        raise ValidationError(f"{field_name} must not be empty")
    return cleaned


def _json_list(value: object, field_name: str) -> tuple[JsonValue, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValidationError(f"{field_name} must be a list")
    result: list[JsonValue] = []
    for item in value:
        result.append(redact_secrets(_as_json(item)))
    return tuple(result)


@dataclass(frozen=True)
class Instruction:
    instruction_id: str
    goal: str
    context: tuple[str, ...]
    constraints: tuple[str, ...]
    done_when: str
    relevant_files: tuple[str, ...] = ()
    required_tests: tuple[str, ...] = ()
    prohibited_changes: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    discipline_skills: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Instruction:
        safe = redact_secrets(_as_json(dict(data)))
        if not isinstance(safe, dict):  # pragma: no cover - _as_json preserves mappings
            raise ValidationError("instruction must be an object")
        instruction_id = safe.get("instruction_id") or str(uuid4())
        return cls(
            instruction_id=_required_text(instruction_id, "instruction_id"),
            goal=_required_text(safe.get("goal"), "goal"),
            context=_string_list(safe.get("context"), "context", required=True),
            constraints=_string_list(safe.get("constraints"), "constraints", required=True),
            done_when=_required_text(safe.get("done_when"), "done_when"),
            relevant_files=validate_relative_paths(safe.get("relevant_files", [])),
            required_tests=_string_list(safe.get("required_tests"), "required_tests"),
            prohibited_changes=_string_list(safe.get("prohibited_changes"), "prohibited_changes"),
            evidence_requirements=_string_list(safe.get("evidence_requirements"), "evidence_requirements"),
            discipline_skills=_string_list(safe.get("discipline_skills"), "discipline_skills"),
        )

    def to_dict(self) -> JsonObject:
        return {
            "instruction_id": self.instruction_id,
            "goal": self.goal,
            "context": list(self.context),
            "constraints": list(self.constraints),
            "done_when": self.done_when,
            "relevant_files": list(self.relevant_files),
            "required_tests": list(self.required_tests),
            "prohibited_changes": list(self.prohibited_changes),
            "evidence_requirements": list(self.evidence_requirements),
            "discipline_skills": list(self.discipline_skills),
        }


@dataclass(frozen=True)
class ExecutionResult:
    status: ResultStatus
    summary: str
    evidence: tuple[JsonValue, ...]
    changed_files: tuple[str, ...]
    tests: tuple[JsonValue, ...]
    commands: tuple[JsonValue, ...]
    commit: str | None
    blockers: tuple[str, ...]
    questions: tuple[str, ...]
    remaining_uncertainty: tuple[str, ...]
    recommended_next_action: str

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ExecutionResult:
        safe = redact_secrets(_as_json(dict(data)))
        if not isinstance(safe, dict):  # pragma: no cover - _as_json preserves mappings
            raise ValidationError("execution result must be an object")
        try:
            status = ResultStatus(_required_text(safe.get("status"), "status"))
        except ValueError as exc:
            valid = ", ".join(status.value for status in ResultStatus)
            raise ValidationError(f"status must be one of: {valid}") from exc
        raw_commit = safe.get("commit")
        if raw_commit is not None and not isinstance(raw_commit, str):
            raise ValidationError("commit must be a string or null")
        return cls(
            status=status,
            summary=_required_text(safe.get("summary"), "summary"),
            evidence=_json_list(safe.get("evidence"), "evidence"),
            changed_files=validate_relative_paths(safe.get("changed_files", [])),
            tests=_json_list(safe.get("tests"), "tests"),
            commands=_json_list(safe.get("commands"), "commands"),
            commit=raw_commit,
            blockers=_string_list(safe.get("blockers"), "blockers"),
            questions=_string_list(safe.get("questions"), "questions"),
            remaining_uncertainty=_string_list(safe.get("remaining_uncertainty"), "remaining_uncertainty"),
            recommended_next_action=_required_text(safe.get("recommended_next_action"), "recommended_next_action"),
        )

    def to_dict(self) -> JsonObject:
        return {
            "status": self.status.value,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "changed_files": list(self.changed_files),
            "tests": list(self.tests),
            "commands": list(self.commands),
            "commit": self.commit,
            "blockers": list(self.blockers),
            "questions": list(self.questions),
            "remaining_uncertainty": list(self.remaining_uncertainty),
            "recommended_next_action": self.recommended_next_action,
        }


@dataclass(frozen=True)
class Event:
    event_id: str
    run_id: str
    sequence: int
    event_type: EventType
    actor: Actor
    payload: JsonObject
    idempotency_key: str | None
    previous_hash: str
    event_hash: str
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        sequence: int,
        event_type: EventType | str,
        actor: Actor | str,
        payload: Mapping[str, object],
        previous_hash: str = "",
        idempotency_key: str | None = None,
        event_id: str | None = None,
        created_at: str | None = None,
    ) -> Event:
        if sequence < 1:
            raise ValidationError("event sequence must start at 1")
        try:
            typed_event = EventType(event_type)
            typed_actor = Actor(actor)
        except ValueError as exc:
            raise ValidationError("event type and actor must be recognized") from exc
        safe_payload = redact_secrets(_as_json(dict(payload)))
        if not isinstance(safe_payload, dict):  # pragma: no cover - mappings stay mappings
            raise ValidationError("event payload must be an object")
        safe_idempotency_key: str | None = None
        if idempotency_key is not None:
            safe_idempotency = redact_secrets(_required_text(idempotency_key, "idempotency_key"))
            if not isinstance(safe_idempotency, str):  # pragma: no cover - string redaction stays text
                raise ValidationError("idempotency_key must be text")
            safe_idempotency_key = safe_idempotency
        event = cls(
            event_id=event_id or str(uuid4()),
            run_id=_required_text(run_id, "run_id"),
            sequence=sequence,
            event_type=typed_event,
            actor=typed_actor,
            payload=safe_payload,
            idempotency_key=safe_idempotency_key,
            previous_hash=previous_hash,
            event_hash="",
            created_at=created_at or utc_now(),
        )
        return cls(**{**event.__dict__, "event_hash": hash_event(event)})

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Event:
        try:
            payload = data["payload"]
            if not isinstance(payload, Mapping):
                raise ValidationError("event payload must be an object")
            raw_sequence = data["sequence"]
            if isinstance(raw_sequence, bool) or not isinstance(raw_sequence, (int, str)):
                raise ValidationError("event sequence must be an integer")
            # Persisted events are already redacted by Event.create. Do not run
            # new redaction rules while loading them: changing a recognizer must
            # never change historical bytes before hash-chain verification.
            safe_payload = _as_json(dict(payload))
            if not isinstance(safe_payload, dict):  # pragma: no cover - mappings stay mappings
                raise ValidationError("event payload must be an object")
            event = cls(
                event_id=_required_text(data["event_id"], "event_id"),
                run_id=_required_text(data["run_id"], "run_id"),
                sequence=int(raw_sequence),
                event_type=EventType(str(data["event_type"])),
                actor=Actor(str(data["actor"])),
                payload=safe_payload,
                idempotency_key=(None if data.get("idempotency_key") is None else str(data["idempotency_key"])),
                previous_hash=str(data.get("previous_hash", "")),
                event_hash=_required_text(data["event_hash"], "event_hash"),
                created_at=_required_text(data["created_at"], "created_at"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ProtocolError):
                raise
            raise ValidationError("event payload is malformed") from exc
        return event

    def to_dict(self) -> JsonObject:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "event_type": self.event_type.value,
            "actor": self.actor.value,
            "payload": self.payload,
            "idempotency_key": self.idempotency_key,
            "previous_hash": self.previous_hash,
            "event_hash": self.event_hash,
            "created_at": self.created_at,
        }

    def to_public_dict(self) -> JsonObject:
        """Serialize an event for a history viewer with defense-in-depth redaction.

        If an older pre-release database contains a newly recognized secret, the
        returned payload may no longer hash to ``event_hash``. The canonical event
        remains available to trusted controller replay; public MCP/CLI history is
        deliberately a safe display projection rather than a replacement log.
        """

        result = self.to_dict()
        safe_payload = redact_secrets(self.payload)
        safe_idempotency = None if self.idempotency_key is None else redact_secrets(self.idempotency_key)
        if safe_payload != self.payload:
            result["payload"] = safe_payload
            result["payload_redacted"] = True
        if safe_idempotency != self.idempotency_key:
            result["idempotency_key"] = safe_idempotency
            result["idempotency_key_redacted"] = True
        return result


def hash_event(event: Event) -> str:
    """Compute the deterministic hash for an event excluding its own hash."""

    body: JsonObject = {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "sequence": event.sequence,
        "event_type": event.event_type.value,
        "actor": event.actor.value,
        "payload": event.payload,
        "idempotency_key": event.idempotency_key,
        "previous_hash": event.previous_hash,
        "created_at": event.created_at,
    }
    return sha256(canonical_json(body).encode("utf-8")).hexdigest()


def verify_event_chain(events: Sequence[Event]) -> None:
    """Verify per-run ordering and the tamper-evident hash chain."""

    expected_sequence = 1
    previous_hash = ""
    run_id: str | None = None
    for event in events:
        if run_id is None:
            run_id = event.run_id
        elif event.run_id != run_id:
            raise ReplayIntegrityError("a replay stream may contain only one run")
        if event.sequence != expected_sequence:
            raise ReplayIntegrityError("event sequence is not contiguous")
        if event.previous_hash != previous_hash:
            raise ReplayIntegrityError("event previous hash does not match")
        if event.event_hash != hash_event(event):
            raise ReplayIntegrityError("event hash does not match event content")
        expected_sequence += 1
        previous_hash = event.event_hash


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    objective: str
    state: RunState
    iteration: int
    max_iterations: int
    policy: JsonObject
    current_instruction: Instruction | None = None
    last_result: ExecutionResult | None = None
    paused_from: RunState | None = None
    human_reason: str | None = None
    failure_reason: str | None = None
    stop_reason: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_dict(self) -> JsonObject:
        return {
            "run_id": self.run_id,
            "objective": self.objective,
            "state": self.state.value,
            "iteration": self.iteration,
            "max_iterations": self.max_iterations,
            "policy": self.policy,
            "current_instruction": (None if self.current_instruction is None else self.current_instruction.to_dict()),
            "last_result": None if self.last_result is None else self.last_result.to_dict(),
            "paused_from": None if self.paused_from is None else self.paused_from.value,
            "human_reason": self.human_reason,
            "failure_reason": self.failure_reason,
            "stop_reason": self.stop_reason,
        }


def _state_required(snapshot: RunSnapshot, allowed: set[RunState], event: Event) -> None:
    if snapshot.state not in allowed:
        legal = ", ".join(state.value for state in sorted(allowed, key=str))
        raise IllegalTransitionError(
            f"{event.event_type.value} is illegal from {snapshot.state.value}; expected {legal}"
        )


def _payload_instruction(payload: JsonObject) -> Instruction:
    raw = payload.get("instruction", payload)
    if not isinstance(raw, Mapping):
        raise ReplayIntegrityError("instruction event has no instruction object")
    return Instruction.from_dict(raw)


def _payload_result(payload: JsonObject) -> ExecutionResult:
    raw = payload.get("result", payload)
    if not isinstance(raw, Mapping):
        raise ReplayIntegrityError("result event has no result object")
    return ExecutionResult.from_dict(raw)


def apply_event(snapshot: RunSnapshot | None, event: Event) -> RunSnapshot:
    """Apply one validated event to a snapshot without side effects."""

    if snapshot is None:
        if event.event_type is not EventType.RUN_CREATED:
            raise IllegalTransitionError("the first event for a run must be RUN_CREATED")
        objective = _required_text(event.payload.get("objective"), "objective")
        raw_max_iterations = event.payload.get("max_iterations")
        if isinstance(raw_max_iterations, bool) or not isinstance(raw_max_iterations, (int, str)):
            raise ReplayIntegrityError("run creation event has invalid max_iterations")
        try:
            max_iterations = int(raw_max_iterations)
        except ValueError as exc:
            raise ReplayIntegrityError("run creation event has invalid max_iterations") from exc
        if max_iterations < 1:
            raise ReplayIntegrityError("max_iterations must be at least one")
        policy_raw = event.payload.get("policy", {})
        if not isinstance(policy_raw, Mapping):
            raise ReplayIntegrityError("run policy must be an object")
        return RunSnapshot(
            run_id=event.run_id,
            objective=objective,
            state=RunState.NEW,
            iteration=0,
            max_iterations=max_iterations,
            policy=dict(policy_raw),
        )

    if snapshot.run_id != event.run_id:
        raise ReplayIntegrityError("event belongs to a different run")

    if event.event_type is EventType.PLANNING_STARTED:
        _state_required(snapshot, {RunState.NEW}, event)
        return _replace(snapshot, state=RunState.PLANNING)
    if event.event_type is EventType.INSTRUCTION_SUBMITTED:
        _state_required(snapshot, {RunState.PLANNING, RunState.REVIEWING}, event)
        if snapshot.iteration >= snapshot.max_iterations:
            raise IllegalTransitionError("iteration limit reached")
        return _replace(
            snapshot,
            state=RunState.READY_FOR_CODEX,
            iteration=snapshot.iteration + 1,
            current_instruction=_payload_instruction(event.payload),
            last_result=None,
            human_reason=None,
            failure_reason=None,
        )
    if event.event_type is EventType.TURN_CLAIMED:
        _state_required(snapshot, {RunState.READY_FOR_CODEX}, event)
        return _replace(snapshot, state=RunState.CODEX_RUNNING)
    if event.event_type is EventType.RESULT_SUBMITTED:
        _state_required(snapshot, {RunState.CODEX_RUNNING}, event)
        return _replace(snapshot, state=RunState.CODEX_COMPLETE, last_result=_payload_result(event.payload))
    if event.event_type is EventType.REVIEW_STARTED:
        _state_required(snapshot, {RunState.CODEX_COMPLETE}, event)
        return _replace(snapshot, state=RunState.REVIEWING)
    if event.event_type is EventType.HUMAN_REQUESTED:
        _state_required(snapshot, {RunState.CODEX_RUNNING, RunState.CODEX_COMPLETE, RunState.REVIEWING}, event)
        return _replace(
            snapshot,
            state=RunState.HUMAN_REQUIRED,
            human_reason=_required_text(event.payload.get("reason"), "reason"),
        )
    if event.event_type is EventType.PAUSED:
        if snapshot.is_terminal or snapshot.state is RunState.PAUSED:
            raise IllegalTransitionError("only active runs can be paused")
        raw_resume = event.payload.get("resume_state")
        try:
            resume_state = RunState(_required_text(raw_resume, "resume_state"))
        except ValueError as exc:
            raise ReplayIntegrityError("pause event has invalid resume state") from exc
        if resume_state in TERMINAL_STATES or resume_state is RunState.PAUSED:
            raise ReplayIntegrityError("pause event has unsafe resume state")
        expected_resume = RunState.READY_FOR_CODEX if snapshot.state is RunState.CODEX_RUNNING else snapshot.state
        if resume_state is not expected_resume:
            raise ReplayIntegrityError("pause event does not preserve the prior safe state")
        return _replace(snapshot, state=RunState.PAUSED, paused_from=resume_state)
    if event.event_type is EventType.RESUMED:
        _state_required(snapshot, {RunState.PAUSED}, event)
        if snapshot.paused_from is None:
            raise ReplayIntegrityError("paused run lacks resume state")
        return _replace(snapshot, state=snapshot.paused_from, paused_from=None)
    if event.event_type is EventType.LEASE_EXPIRED:
        _state_required(snapshot, {RunState.CODEX_RUNNING}, event)
        return _replace(snapshot, state=RunState.READY_FOR_CODEX)
    if event.event_type is EventType.PROGRESS_REPORTED:
        _state_required(snapshot, {RunState.CODEX_RUNNING}, event)
        return snapshot
    if event.event_type is EventType.COMPLETED:
        _state_required(snapshot, {RunState.REVIEWING}, event)
        return _replace(snapshot, state=RunState.COMPLETE)
    if event.event_type is EventType.FAILED:
        if snapshot.is_terminal:
            raise IllegalTransitionError("terminal runs cannot fail again")
        return _replace(
            snapshot,
            state=RunState.FAILED,
            failure_reason=_required_text(event.payload.get("reason"), "reason"),
        )
    if event.event_type is EventType.STOPPED:
        if snapshot.is_terminal:
            raise IllegalTransitionError("terminal runs cannot be stopped")
        return _replace(
            snapshot,
            state=RunState.STOPPED,
            stop_reason=_required_text(event.payload.get("reason"), "reason"),
        )
    raise ReplayIntegrityError(f"unhandled event type {event.event_type.value}")


def _replace(snapshot: RunSnapshot, **changes: object) -> RunSnapshot:
    values: dict[str, object] = {
        "run_id": snapshot.run_id,
        "objective": snapshot.objective,
        "state": snapshot.state,
        "iteration": snapshot.iteration,
        "max_iterations": snapshot.max_iterations,
        "policy": snapshot.policy,
        "current_instruction": snapshot.current_instruction,
        "last_result": snapshot.last_result,
        "paused_from": snapshot.paused_from,
        "human_reason": snapshot.human_reason,
        "failure_reason": snapshot.failure_reason,
        "stop_reason": snapshot.stop_reason,
    }
    values.update(changes)
    return RunSnapshot(**values)  # type: ignore[arg-type]


def replay_events(events: Sequence[Event]) -> RunSnapshot:
    """Rebuild the complete current run state from immutable ordered events."""

    if not events:
        raise ReplayIntegrityError("cannot replay an empty event stream")
    verify_event_chain(events)
    snapshot: RunSnapshot | None = None
    for event in events:
        snapshot = apply_event(snapshot, event)
    if snapshot is None:  # pragma: no cover - guarded by non-empty event stream
        raise ReplayIntegrityError("event stream did not produce a run")
    return snapshot
