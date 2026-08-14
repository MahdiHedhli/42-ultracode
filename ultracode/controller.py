"""Transactional SQLite controller for 42 Ultracode runs."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import uuid4

from .protocol import (
    TERMINAL_STATES,
    Actor,
    Event,
    EventType,
    ExecutionResult,
    IllegalTransitionError,
    Instruction,
    JsonObject,
    JsonValue,
    ProtocolError,
    ResultStatus,
    RunSnapshot,
    RunState,
    ValidationError,
    canonical_json,
    redact_secrets,
    replay_events,
    utc_now,
)


class ControllerError(RuntimeError):
    """Base class for controller-level failures."""


class RunNotFoundError(ControllerError):
    """Raised when a requested run ID does not exist."""


class ConflictError(ControllerError):
    """Raised when a valid request conflicts with current turn ownership."""


class DuplicateDeliveryError(ControllerError):
    """Raised when an idempotency key is reused for a different request."""


class PolicyError(ControllerError):
    """Raised when an operation exceeds controller-owned policy."""


class LeaseError(ControllerError):
    """Raised when a worker does not hold the current lease."""


@dataclass(frozen=True)
class TurnClaim:
    """A worker's temporary right to submit one result."""

    run_id: str
    worker_id: str
    instruction: Instruction
    lease_token: str
    expires_at: str

    def to_dict(self) -> JsonObject:
        return {
            "run_id": self.run_id,
            "worker_id": self.worker_id,
            "instruction": self.instruction.to_dict(),
            "lease_token": self.lease_token,
            "expires_at": self.expires_at,
        }


_CREATE_SCOPE = "__create_run__"


class Controller:
    """The only component permitted to mutate authoritative workflow state.

    Each public mutating method opens one SQLite immediate transaction, appends
    events, updates the derived run cache, and saves its delivery key. A crash
    before commit leaves no partial transition; a crash after commit is safe to
    retry with the same idempotency key.
    """

    def __init__(
        self,
        database: str | Path,
        *,
        max_iteration_ceiling: int = 20,
        default_lease_seconds: int = 300,
    ) -> None:
        if max_iteration_ceiling < 1:
            raise ValueError("max_iteration_ceiling must be at least one")
        if default_lease_seconds < 1:
            raise ValueError("default_lease_seconds must be at least one")
        self.database = str(database)
        self.max_iteration_ceiling = max_iteration_ceiling
        self.default_lease_seconds = default_lease_seconds
        if self.database != ":memory:" and not self.database.startswith("file:"):
            Path(self.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
            uri=self.database.startswith("file:"),
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    state TEXT NOT NULL,
                    iteration INTEGER NOT NULL CHECK (iteration >= 0),
                    max_iterations INTEGER NOT NULL CHECK (max_iterations >= 1),
                    policy_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK (version >= 1)
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    idempotency_key TEXT,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (run_id, sequence)
                );

                CREATE TABLE IF NOT EXISTS leases (
                    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                    worker_id TEXT NOT NULL,
                    lease_token TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    kind TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operations (
                    scope TEXT NOT NULL,
                    operation_key TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (scope, operation_key)
                );

                CREATE TRIGGER IF NOT EXISTS events_are_immutable_update
                BEFORE UPDATE ON events
                BEGIN
                    SELECT RAISE(ABORT, 'events are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS events_are_immutable_delete
                BEFORE DELETE ON events
                BEGIN
                    SELECT RAISE(ABORT, 'events are append-only');
                END;
                """
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _operation_key(key: str | None) -> str:
        if key is None:
            return f"generated:{uuid4()}"
        if not isinstance(key, str) or not key.strip():
            raise ValidationError("idempotency_key must be a non-empty string")
        return key.strip()

    @staticmethod
    def _request_hash(payload: Mapping[str, object]) -> str:
        safe = redact_secrets(cast(JsonValue, json.loads(json.dumps(dict(payload)))))
        return sha256(canonical_json(safe).encode("utf-8")).hexdigest()

    def _existing_operation(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str,
        key: str,
        operation: str,
        request_hash: str,
    ) -> JsonObject | None:
        row = connection.execute(
            "SELECT operation, request_hash, response_json FROM operations WHERE scope = ? AND operation_key = ?",
            (scope, key),
        ).fetchone()
        if row is None:
            return None
        if row["operation"] != operation or row["request_hash"] != request_hash:
            raise DuplicateDeliveryError("idempotency key was reused for a different request")
        parsed = json.loads(str(row["response_json"]))
        if not isinstance(parsed, dict):  # pragma: no cover - controller writes JSON objects
            raise ControllerError("stored idempotency response is malformed")
        return cast(JsonObject, parsed)

    @staticmethod
    def _store_operation(
        connection: sqlite3.Connection,
        *,
        scope: str,
        key: str,
        operation: str,
        request_hash: str,
        response: Mapping[str, object],
    ) -> None:
        connection.execute(
            "INSERT INTO operations (scope, operation_key, operation, request_hash, response_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (scope, key, operation, request_hash, canonical_json(cast(JsonValue, dict(response))), utc_now()),
        )

    @staticmethod
    def _assert_run_exists(connection: sqlite3.Connection, run_id: str) -> None:
        row = connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RunNotFoundError(f"run not found: {run_id}")

    def _events(self, connection: sqlite3.Connection, run_id: str) -> list[Event]:
        rows = connection.execute(
            "SELECT event_id, run_id, sequence, event_type, actor, payload_json, "
            "idempotency_key, previous_hash, event_hash, created_at "
            "FROM events WHERE run_id = ? ORDER BY sequence ASC",
            (run_id,),
        ).fetchall()
        events: list[Event] = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            events.append(
                Event.from_dict(
                    {
                        "event_id": row["event_id"],
                        "run_id": row["run_id"],
                        "sequence": row["sequence"],
                        "event_type": row["event_type"],
                        "actor": row["actor"],
                        "payload": payload,
                        "idempotency_key": row["idempotency_key"],
                        "previous_hash": row["previous_hash"],
                        "event_hash": row["event_hash"],
                        "created_at": row["created_at"],
                    }
                )
            )
        return events

    def _snapshot(self, connection: sqlite3.Connection, run_id: str) -> RunSnapshot:
        self._assert_run_exists(connection, run_id)
        try:
            return replay_events(self._events(connection, run_id))
        except ProtocolError as exc:
            raise ControllerError(f"run {run_id} has invalid persisted history: {exc}") from exc

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        event_type: EventType,
        actor: Actor,
        payload: Mapping[str, object],
        idempotency_key: str | None,
    ) -> Event:
        previous_events = self._events(connection, run_id)
        event = Event.create(
            run_id=run_id,
            sequence=len(previous_events) + 1,
            event_type=event_type,
            actor=actor,
            payload=payload,
            previous_hash=previous_events[-1].event_hash if previous_events else "",
            idempotency_key=idempotency_key,
        )
        connection.execute(
            "INSERT INTO events (event_id, run_id, sequence, event_type, actor, payload_json, "
            "idempotency_key, previous_hash, event_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                event.run_id,
                event.sequence,
                event.event_type.value,
                event.actor.value,
                canonical_json(event.payload),
                event.idempotency_key,
                event.previous_hash,
                event.event_hash,
                event.created_at,
            ),
        )
        return event

    @staticmethod
    def _sync_snapshot(connection: sqlite3.Connection, snapshot: RunSnapshot) -> None:
        connection.execute(
            "UPDATE runs SET state = ?, iteration = ?, updated_at = ?, version = version + 1 WHERE run_id = ?",
            (snapshot.state.value, snapshot.iteration, utc_now(), snapshot.run_id),
        )

    def _append_and_sync(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        event_type: EventType,
        actor: Actor,
        payload: Mapping[str, object],
        idempotency_key: str | None,
    ) -> RunSnapshot:
        self._append_event(
            connection,
            run_id=run_id,
            event_type=event_type,
            actor=actor,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        snapshot = self._snapshot(connection, run_id)
        self._sync_snapshot(connection, snapshot)
        return snapshot

    def create_run(
        self,
        objective: str,
        *,
        max_iterations: int = 10,
        idempotency_key: str | None = None,
    ) -> RunSnapshot:
        if not isinstance(objective, str) or not objective.strip():
            raise ValidationError("objective must be a non-empty string")
        safe_objective = redact_secrets(objective.strip())
        if not isinstance(safe_objective, str):  # pragma: no cover - strings remain strings
            raise ValidationError("objective must be text")
        if not isinstance(max_iterations, int) or isinstance(max_iterations, bool):
            raise ValidationError("max_iterations must be an integer")
        if not 1 <= max_iterations <= self.max_iteration_ceiling:
            raise PolicyError(f"max_iterations must be between 1 and controller ceiling {self.max_iteration_ceiling}")
        operation_key = self._operation_key(idempotency_key)
        request: dict[str, object] = {"objective": safe_objective, "max_iterations": max_iterations}
        request_hash = self._request_hash(request)
        with self._transaction() as connection:
            duplicate = self._existing_operation(
                connection,
                scope=_CREATE_SCOPE,
                key=operation_key,
                operation="create_run",
                request_hash=request_hash,
            )
            if duplicate is not None:
                return self._snapshot(connection, str(duplicate["run_id"]))
            run_id = str(uuid4())
            policy: JsonObject = {
                "policy_version": "v0.1",
                "max_iteration_ceiling": self.max_iteration_ceiling,
                "allow_model_policy_mutation": False,
                "allow_model_limit_increase": False,
            }
            created_at = utc_now()
            connection.execute(
                "INSERT INTO runs (run_id, objective, state, iteration, max_iterations, policy_json, "
                "created_at, updated_at, version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    safe_objective,
                    RunState.NEW.value,
                    0,
                    max_iterations,
                    canonical_json(policy),
                    created_at,
                    created_at,
                    1,
                ),
            )
            self._append_event(
                connection,
                run_id=run_id,
                event_type=EventType.RUN_CREATED,
                actor=Actor.CONTROL,
                payload={"objective": safe_objective, "max_iterations": max_iterations, "policy": policy},
                idempotency_key=f"{operation_key}:created",
            )
            snapshot = self._append_and_sync(
                connection,
                run_id=run_id,
                event_type=EventType.PLANNING_STARTED,
                actor=Actor.SYSTEM,
                payload={},
                idempotency_key=f"{operation_key}:planning",
            )
            self._store_operation(
                connection,
                scope=_CREATE_SCOPE,
                key=operation_key,
                operation="create_run",
                request_hash=request_hash,
                response={"run_id": run_id},
            )
            return snapshot

    def get_run(self, run_id: str) -> RunSnapshot:
        with self._connect() as connection:
            return self._snapshot(connection, run_id)

    def history(self, run_id: str) -> list[Event]:
        with self._connect() as connection:
            self._assert_run_exists(connection, run_id)
            events = self._events(connection, run_id)
            replay_events(events)
            return events

    def read_result(self, run_id: str) -> ExecutionResult | None:
        return self.get_run(run_id).last_result

    @staticmethod
    def _as_instruction(value: Instruction | Mapping[str, object]) -> Instruction:
        return value if isinstance(value, Instruction) else Instruction.from_dict(value)

    @staticmethod
    def _as_result(value: ExecutionResult | Mapping[str, object]) -> ExecutionResult:
        return value if isinstance(value, ExecutionResult) else ExecutionResult.from_dict(value)

    def _instruction_id_exists(self, connection: sqlite3.Connection, run_id: str, instruction_id: str) -> bool:
        """Use authoritative events to keep handoff IDs unique within a run."""

        for event in self._events(connection, run_id):
            if event.event_type is not EventType.INSTRUCTION_SUBMITTED:
                continue
            raw_instruction = event.payload.get("instruction", event.payload)
            if isinstance(raw_instruction, Mapping) and raw_instruction.get("instruction_id") == instruction_id:
                return True
        return False

    def submit_instruction(
        self,
        run_id: str,
        instruction: Instruction | Mapping[str, object],
        *,
        idempotency_key: str | None = None,
    ) -> RunSnapshot:
        typed_instruction = self._as_instruction(instruction)
        operation_key = self._operation_key(idempotency_key)
        request_hash = self._request_hash({"instruction": typed_instruction.to_dict()})
        with self._transaction() as connection:
            self._assert_run_exists(connection, run_id)
            duplicate = self._existing_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="submit_instruction",
                request_hash=request_hash,
            )
            if duplicate is not None:
                return self._snapshot(connection, run_id)
            snapshot = self._snapshot(connection, run_id)
            if self._instruction_id_exists(connection, run_id, typed_instruction.instruction_id):
                raise ConflictError("instruction_id has already been used for this run")
            if snapshot.state is RunState.CODEX_COMPLETE:
                snapshot = self._append_and_sync(
                    connection,
                    run_id=run_id,
                    event_type=EventType.REVIEW_STARTED,
                    actor=Actor.PLANNER,
                    payload={},
                    idempotency_key=f"{operation_key}:review",
                )
            if snapshot.state not in {RunState.PLANNING, RunState.REVIEWING}:
                raise IllegalTransitionError(f"cannot submit an instruction while run is {snapshot.state.value}")
            if snapshot.iteration >= snapshot.max_iterations:
                snapshot = self._append_and_sync(
                    connection,
                    run_id=run_id,
                    event_type=EventType.FAILED,
                    actor=Actor.SYSTEM,
                    payload={"reason": "iteration limit reached"},
                    idempotency_key=f"{operation_key}:limit",
                )
                self._store_operation(
                    connection,
                    scope=run_id,
                    key=operation_key,
                    operation="submit_instruction",
                    request_hash=request_hash,
                    response={"state": snapshot.state.value},
                )
                return snapshot
            snapshot = self._append_and_sync(
                connection,
                run_id=run_id,
                event_type=EventType.INSTRUCTION_SUBMITTED,
                actor=Actor.PLANNER,
                payload={"instruction": typed_instruction.to_dict()},
                idempotency_key=operation_key,
            )
            self._store_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="submit_instruction",
                request_hash=request_hash,
                response={"state": snapshot.state.value},
            )
            return snapshot

    @staticmethod
    def _parse_expiry(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ControllerError("stored lease has invalid timestamp") from exc
        if parsed.tzinfo is None:
            raise ControllerError("stored lease lacks timezone")
        return parsed

    def _recover_expired_lease(self, connection: sqlite3.Connection, run_id: str, snapshot: RunSnapshot) -> RunSnapshot:
        row = connection.execute("SELECT worker_id, expires_at FROM leases WHERE run_id = ?", (run_id,)).fetchone()
        if snapshot.state is not RunState.CODEX_RUNNING:
            return snapshot
        if row is None:
            raise ControllerError("CODEX_RUNNING run has no active lease")
        if self._parse_expiry(str(row["expires_at"])) > datetime.now(UTC):
            return snapshot
        connection.execute("DELETE FROM leases WHERE run_id = ?", (run_id,))
        return self._append_and_sync(
            connection,
            run_id=run_id,
            event_type=EventType.LEASE_EXPIRED,
            actor=Actor.SYSTEM,
            payload={"reason": "worker lease expired"},
            idempotency_key=f"lease-expired:{uuid4()}",
        )

    def recover_expired_leases(self) -> list[str]:
        """Recover every expired worker claim after a controller restart."""

        recovered: list[str] = []
        with self._transaction() as connection:
            rows = connection.execute("SELECT run_id FROM leases ORDER BY run_id").fetchall()
            for row in rows:
                run_id = str(row["run_id"])
                before = self._snapshot(connection, run_id)
                after = self._recover_expired_lease(connection, run_id, before)
                if before.state is RunState.CODEX_RUNNING and after.state is RunState.READY_FOR_CODEX:
                    recovered.append(run_id)
        return recovered

    def claim_turn(
        self,
        run_id: str,
        *,
        worker_id: str,
        idempotency_key: str | None = None,
        lease_seconds: int | None = None,
    ) -> TurnClaim:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValidationError("worker_id must be a non-empty string")
        lease_duration = self.default_lease_seconds if lease_seconds is None else lease_seconds
        if not isinstance(lease_duration, int) or lease_duration < 1:
            raise ValidationError("lease_seconds must be a positive integer")
        operation_key = self._operation_key(idempotency_key)
        request_hash = self._request_hash({"worker_id": worker_id.strip(), "lease_seconds": lease_duration})
        with self._transaction() as connection:
            self._assert_run_exists(connection, run_id)
            duplicate = self._existing_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="claim_turn",
                request_hash=request_hash,
            )
            if duplicate is not None:
                raw_instruction = duplicate.get("instruction")
                if not isinstance(raw_instruction, Mapping):
                    raise ControllerError("stored turn claim lacks an instruction")
                return TurnClaim(
                    run_id=run_id,
                    worker_id=str(duplicate["worker_id"]),
                    instruction=Instruction.from_dict(raw_instruction),
                    lease_token=str(duplicate["lease_token"]),
                    expires_at=str(duplicate["expires_at"]),
                )
            snapshot = self._recover_expired_lease(connection, run_id, self._snapshot(connection, run_id))
            if snapshot.state is not RunState.READY_FOR_CODEX or snapshot.current_instruction is None:
                raise ConflictError(f"no claimable instruction while run is {snapshot.state.value}")
            existing = connection.execute("SELECT 1 FROM leases WHERE run_id = ?", (run_id,)).fetchone()
            if existing is not None:
                raise ConflictError("another worker owns the current turn")
            token = str(uuid4())
            expires_at = (datetime.now(UTC) + timedelta(seconds=lease_duration)).isoformat(timespec="microseconds")
            connection.execute(
                "INSERT INTO leases (run_id, worker_id, lease_token, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, worker_id.strip(), token, expires_at, utc_now()),
            )
            self._append_and_sync(
                connection,
                run_id=run_id,
                event_type=EventType.TURN_CLAIMED,
                actor=Actor.WORKER,
                # The lease token is a live bearer credential, not event history.
                # It stays in the private leases/idempotency tables needed to
                # complete this active turn and is never exposed by history().
                payload={"worker_id": worker_id.strip(), "expires_at": expires_at},
                idempotency_key=operation_key,
            )
            claim = TurnClaim(
                run_id=run_id,
                worker_id=worker_id.strip(),
                instruction=snapshot.current_instruction,
                lease_token=token,
                expires_at=expires_at,
            )
            self._store_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="claim_turn",
                request_hash=request_hash,
                response=claim.to_dict(),
            )
            return claim

    @staticmethod
    def _assert_lease(connection: sqlite3.Connection, run_id: str, worker_id: str, lease_token: str) -> None:
        row = connection.execute(
            "SELECT worker_id, lease_token, expires_at FROM leases WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise LeaseError("no active worker lease for this run")
        if row["worker_id"] != worker_id or row["lease_token"] != lease_token:
            raise LeaseError("worker does not own the current turn")
        if Controller._parse_expiry(str(row["expires_at"])) <= datetime.now(UTC):
            raise LeaseError("worker lease has expired")

    def submit_result(
        self,
        run_id: str,
        result: ExecutionResult | Mapping[str, object],
        *,
        worker_id: str,
        lease_token: str,
        idempotency_key: str | None = None,
    ) -> RunSnapshot:
        typed_result = self._as_result(result)
        operation_key = self._operation_key(idempotency_key)
        request_hash = self._request_hash(
            {
                "worker_id": worker_id,
                "lease_token": lease_token,
                "result": typed_result.to_dict(),
            }
        )
        with self._transaction() as connection:
            self._assert_run_exists(connection, run_id)
            duplicate = self._existing_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="submit_result",
                request_hash=request_hash,
            )
            if duplicate is not None:
                return self._snapshot(connection, run_id)
            snapshot = self._snapshot(connection, run_id)
            if snapshot.state is not RunState.CODEX_RUNNING:
                raise IllegalTransitionError("results require a CODEX_RUNNING run")
            self._assert_lease(connection, run_id, worker_id, lease_token)
            connection.execute("DELETE FROM leases WHERE run_id = ?", (run_id,))
            snapshot = self._append_and_sync(
                connection,
                run_id=run_id,
                event_type=EventType.RESULT_SUBMITTED,
                actor=Actor.WORKER,
                payload={"result": typed_result.to_dict()},
                idempotency_key=operation_key,
            )
            self._store_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="submit_result",
                request_hash=request_hash,
                response={"state": snapshot.state.value},
            )
            return snapshot

    def report_progress(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_token: str,
        message: str,
        idempotency_key: str | None = None,
    ) -> RunSnapshot:
        if not isinstance(message, str) or not message.strip():
            raise ValidationError("message must be a non-empty string")
        operation_key = self._operation_key(idempotency_key)
        request_hash = self._request_hash(
            {"worker_id": worker_id, "lease_token": lease_token, "message": message.strip()}
        )
        with self._transaction() as connection:
            self._assert_run_exists(connection, run_id)
            duplicate = self._existing_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="report_progress",
                request_hash=request_hash,
            )
            if duplicate is not None:
                return self._snapshot(connection, run_id)
            self._assert_lease(connection, run_id, worker_id, lease_token)
            snapshot = self._append_and_sync(
                connection,
                run_id=run_id,
                event_type=EventType.PROGRESS_REPORTED,
                actor=Actor.WORKER,
                payload={"message": message.strip()},
                idempotency_key=operation_key,
            )
            self._store_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="report_progress",
                request_hash=request_hash,
                response={"state": snapshot.state.value},
            )
            return snapshot

    def report_blocker(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_token: str,
        reason: str,
        idempotency_key: str | None = None,
    ) -> RunSnapshot:
        return self._worker_human_request(
            run_id,
            worker_id=worker_id,
            lease_token=lease_token,
            reason=reason,
            idempotency_key=idempotency_key,
            operation="report_blocker",
        )

    def _worker_human_request(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_token: str,
        reason: str,
        idempotency_key: str | None,
        operation: str,
    ) -> RunSnapshot:
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError("reason must be a non-empty string")
        operation_key = self._operation_key(idempotency_key)
        request_hash = self._request_hash(
            {"worker_id": worker_id, "lease_token": lease_token, "reason": reason.strip()}
        )
        with self._transaction() as connection:
            self._assert_run_exists(connection, run_id)
            duplicate = self._existing_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation=operation,
                request_hash=request_hash,
            )
            if duplicate is not None:
                return self._snapshot(connection, run_id)
            self._assert_lease(connection, run_id, worker_id, lease_token)
            connection.execute("DELETE FROM leases WHERE run_id = ?", (run_id,))
            snapshot = self._append_and_sync(
                connection,
                run_id=run_id,
                event_type=EventType.HUMAN_REQUESTED,
                actor=Actor.WORKER,
                payload={"reason": reason.strip()},
                idempotency_key=operation_key,
            )
            self._store_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation=operation,
                request_hash=request_hash,
                response={"state": snapshot.state.value},
            )
            return snapshot

    def request_human(self, run_id: str, reason: str, *, idempotency_key: str | None = None) -> RunSnapshot:
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError("reason must be a non-empty string")
        operation_key = self._operation_key(idempotency_key)
        request_hash = self._request_hash({"reason": reason.strip()})
        with self._transaction() as connection:
            self._assert_run_exists(connection, run_id)
            duplicate = self._existing_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="request_human",
                request_hash=request_hash,
            )
            if duplicate is not None:
                return self._snapshot(connection, run_id)
            snapshot = self._snapshot(connection, run_id)
            if snapshot.state is RunState.CODEX_COMPLETE:
                snapshot = self._append_and_sync(
                    connection,
                    run_id=run_id,
                    event_type=EventType.REVIEW_STARTED,
                    actor=Actor.PLANNER,
                    payload={},
                    idempotency_key=f"{operation_key}:review",
                )
            if snapshot.state not in {RunState.CODEX_RUNNING, RunState.REVIEWING}:
                raise IllegalTransitionError("human escalation requires active execution or review")
            if snapshot.state is RunState.CODEX_RUNNING:
                connection.execute("DELETE FROM leases WHERE run_id = ?", (run_id,))
            snapshot = self._append_and_sync(
                connection,
                run_id=run_id,
                event_type=EventType.HUMAN_REQUESTED,
                actor=Actor.PLANNER,
                payload={"reason": reason.strip()},
                idempotency_key=operation_key,
            )
            self._store_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="request_human",
                request_hash=request_hash,
                response={"state": snapshot.state.value},
            )
            return snapshot

    def pause_run(self, run_id: str, reason: str, *, idempotency_key: str | None = None) -> RunSnapshot:
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError("reason must be a non-empty string")
        operation_key = self._operation_key(idempotency_key)
        request_hash = self._request_hash({"reason": reason.strip()})
        with self._transaction() as connection:
            self._assert_run_exists(connection, run_id)
            duplicate = self._existing_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="pause_run",
                request_hash=request_hash,
            )
            if duplicate is not None:
                return self._snapshot(connection, run_id)
            snapshot = self._snapshot(connection, run_id)
            if snapshot.state in TERMINAL_STATES or snapshot.state is RunState.PAUSED:
                raise IllegalTransitionError("only active runs can be paused")
            resume_state = snapshot.state
            if snapshot.state is RunState.CODEX_RUNNING:
                connection.execute("DELETE FROM leases WHERE run_id = ?", (run_id,))
                resume_state = RunState.READY_FOR_CODEX
            snapshot = self._append_and_sync(
                connection,
                run_id=run_id,
                event_type=EventType.PAUSED,
                actor=Actor.CONTROL,
                payload={"reason": reason.strip(), "resume_state": resume_state.value},
                idempotency_key=operation_key,
            )
            self._store_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="pause_run",
                request_hash=request_hash,
                response={"state": snapshot.state.value},
            )
            return snapshot

    def resume_run(self, run_id: str, *, idempotency_key: str | None = None) -> RunSnapshot:
        operation_key = self._operation_key(idempotency_key)
        request_hash = self._request_hash({})
        with self._transaction() as connection:
            self._assert_run_exists(connection, run_id)
            duplicate = self._existing_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="resume_run",
                request_hash=request_hash,
            )
            if duplicate is not None:
                return self._snapshot(connection, run_id)
            current = self._snapshot(connection, run_id)
            if current.state is not RunState.PAUSED:
                raise IllegalTransitionError("only paused runs can be resumed")
            snapshot = self._append_and_sync(
                connection,
                run_id=run_id,
                event_type=EventType.RESUMED,
                actor=Actor.CONTROL,
                payload={},
                idempotency_key=operation_key,
            )
            self._store_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="resume_run",
                request_hash=request_hash,
                response={"state": snapshot.state.value},
            )
            return snapshot

    def stop_run(self, run_id: str, reason: str, *, idempotency_key: str | None = None) -> RunSnapshot:
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError("reason must be a non-empty string")
        operation_key = self._operation_key(idempotency_key)
        request_hash = self._request_hash({"reason": reason.strip()})
        with self._transaction() as connection:
            self._assert_run_exists(connection, run_id)
            duplicate = self._existing_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="stop_run",
                request_hash=request_hash,
            )
            if duplicate is not None:
                return self._snapshot(connection, run_id)
            snapshot = self._snapshot(connection, run_id)
            if snapshot.state in TERMINAL_STATES:
                raise IllegalTransitionError("terminal runs cannot be stopped")
            connection.execute("DELETE FROM leases WHERE run_id = ?", (run_id,))
            snapshot = self._append_and_sync(
                connection,
                run_id=run_id,
                event_type=EventType.STOPPED,
                actor=Actor.CONTROL,
                payload={"reason": reason.strip()},
                idempotency_key=operation_key,
            )
            self._store_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="stop_run",
                request_hash=request_hash,
                response={"state": snapshot.state.value},
            )
            return snapshot

    def fail_run(self, run_id: str, reason: str, *, idempotency_key: str | None = None) -> RunSnapshot:
        """Allow trusted control to terminate a run after a non-recoverable failure."""

        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError("reason must be a non-empty string")
        operation_key = self._operation_key(idempotency_key)
        request_hash = self._request_hash({"reason": reason.strip()})
        with self._transaction() as connection:
            self._assert_run_exists(connection, run_id)
            duplicate = self._existing_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="fail_run",
                request_hash=request_hash,
            )
            if duplicate is not None:
                return self._snapshot(connection, run_id)
            connection.execute("DELETE FROM leases WHERE run_id = ?", (run_id,))
            snapshot = self._append_and_sync(
                connection,
                run_id=run_id,
                event_type=EventType.FAILED,
                actor=Actor.CONTROL,
                payload={"reason": reason.strip()},
                idempotency_key=operation_key,
            )
            self._store_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="fail_run",
                request_hash=request_hash,
                response={"state": snapshot.state.value},
            )
            return snapshot

    def complete_run(self, run_id: str, rationale: str, *, idempotency_key: str | None = None) -> RunSnapshot:
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValidationError("rationale must be a non-empty string")
        operation_key = self._operation_key(idempotency_key)
        request_hash = self._request_hash({"rationale": rationale.strip()})
        with self._transaction() as connection:
            self._assert_run_exists(connection, run_id)
            duplicate = self._existing_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="complete_run",
                request_hash=request_hash,
            )
            if duplicate is not None:
                return self._snapshot(connection, run_id)
            snapshot = self._snapshot(connection, run_id)
            if snapshot.state is RunState.CODEX_COMPLETE:
                snapshot = self._append_and_sync(
                    connection,
                    run_id=run_id,
                    event_type=EventType.REVIEW_STARTED,
                    actor=Actor.PLANNER,
                    payload={},
                    idempotency_key=f"{operation_key}:review",
                )
            if snapshot.state is not RunState.REVIEWING:
                raise IllegalTransitionError("completion requires a reviewed executor result")
            if snapshot.last_result is None or not snapshot.last_result.evidence:
                raise PolicyError("completion requires at least one executor evidence item")
            snapshot = self._append_and_sync(
                connection,
                run_id=run_id,
                event_type=EventType.COMPLETED,
                actor=Actor.PLANNER,
                payload={"rationale": rationale.strip()},
                idempotency_key=operation_key,
            )
            self._store_operation(
                connection,
                scope=run_id,
                key=operation_key,
                operation="complete_run",
                request_hash=request_hash,
                response={"state": snapshot.state.value},
            )
            return snapshot

    def add_artifact(self, run_id: str, kind: str, content: Mapping[str, object]) -> str:
        """Persist an optional redacted diagnostic artifact without mutating state."""

        if not isinstance(kind, str) or not kind.strip():
            raise ValidationError("artifact kind must be a non-empty string")
        artifact_id = str(uuid4())
        safe = redact_secrets(cast(JsonValue, json.loads(json.dumps(dict(content)))))
        with self._transaction() as connection:
            self._assert_run_exists(connection, run_id)
            connection.execute(
                "INSERT INTO artifacts (artifact_id, run_id, kind, content_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (artifact_id, run_id, kind.strip(), canonical_json(safe), utc_now()),
            )
        return artifact_id

    def artifacts(self, run_id: str) -> list[JsonObject]:
        with self._connect() as connection:
            self._assert_run_exists(connection, run_id)
            rows = connection.execute(
                "SELECT artifact_id, kind, content_json, created_at "
                "FROM artifacts WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
            return [
                {
                    "artifact_id": str(row["artifact_id"]),
                    "kind": str(row["kind"]),
                    "content": cast(JsonValue, json.loads(str(row["content_json"]))),
                    "created_at": str(row["created_at"]),
                }
                for row in rows
            ]


def result_template(
    *,
    status: ResultStatus = ResultStatus.COMPLETED,
    summary: str = "",
    evidence: Sequence[JsonValue] = (),
    changed_files: Sequence[str] = (),
    tests: Sequence[JsonValue] = (),
    commands: Sequence[JsonValue] = (),
    commit: str | None = None,
    blockers: Sequence[str] = (),
    questions: Sequence[str] = (),
    remaining_uncertainty: Sequence[str] = (),
    recommended_next_action: str = "",
) -> ExecutionResult:
    """Create a typed result from explicit fields for adapters and the dogfood harness."""

    return ExecutionResult.from_dict(
        {
            "status": status.value,
            "summary": summary,
            "evidence": list(evidence),
            "changed_files": list(changed_files),
            "tests": list(tests),
            "commands": list(commands),
            "commit": commit,
            "blockers": list(blockers),
            "questions": list(questions),
            "remaining_uncertainty": list(remaining_uncertainty),
            "recommended_next_action": recommended_next_action,
        }
    )
