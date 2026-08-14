"""Small role-scoped JSON-RPC/MCP server over line-delimited stdio.

The server is deliberately a transport adapter: it translates validated tool
calls into controller operations and never stores independent workflow state.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from typing import Final, TextIO

from ..controller import Controller, ControllerError
from ..protocol import JsonObject, JsonValue, ProtocolError, ValidationError, canonical_json, redact_secrets

MCP_PROTOCOL_VERSION: Final = "2025-03-26"
SERVER_NAME: Final = "42-ultracode"
SERVER_VERSION: Final = "0.1.0"

_VALID_ROLES: Final = frozenset({"planner", "worker", "control"})
_ROLE_TOOLS: Final[dict[str, tuple[str, ...]]] = {
    "planner": (
        "ultracode_create_run",
        "ultracode_read_run",
        "ultracode_submit_instruction",
        "ultracode_read_result",
        "ultracode_complete_run",
        "ultracode_request_human",
    ),
    "worker": (
        "ultracode_claim_instruction",
        "ultracode_submit_result",
        "ultracode_report_progress",
        "ultracode_report_blocker",
    ),
    "control": (
        "ultracode_pause",
        "ultracode_resume",
        "ultracode_stop",
        "ultracode_status",
        "ultracode_history",
    ),
}

_INSTRUCTION_REQUIRED: Final = ("goal", "context", "constraints", "done_when")
_INSTRUCTION_OPTIONAL: Final = (
    "instruction_id",
    "relevant_files",
    "required_tests",
    "prohibited_changes",
    "evidence_requirements",
    "discipline_skills",
    "selected_discipline_skills",
)
_RESULT_REQUIRED: Final = ("status", "summary", "recommended_next_action")
_RESULT_OPTIONAL: Final = (
    "evidence",
    "changed_files",
    "tests",
    "commands",
    "commit",
    "blockers",
    "questions",
    "remaining_uncertainty",
)


class RpcFault(Exception):
    """A JSON-RPC error that can safely be returned to a client."""

    def __init__(self, code: int, message: str, data: JsonObject | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _field(field_type: str, description: str, **extra: JsonValue) -> JsonObject:
    return {"type": field_type, "description": description, **extra}


def _array_field(description: str, *, items: JsonObject | None = None) -> JsonObject:
    return {
        "type": "array",
        "description": description,
        "items": {} if items is None else items,
    }


def _object_schema(properties: Mapping[str, JsonObject], required: Sequence[str] = ()) -> JsonObject:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _tool(name: str, description: str, input_schema: JsonObject) -> JsonObject:
    return {"name": name, "description": description, "inputSchema": input_schema}


_RUN_ID = _field("string", "Controller-generated run identifier.")
_IDEMPOTENCY = _field("string", "Stable delivery key for safe retries.")
_OBJECTIVE = _field("string", "Human-approved bounded engineering objective.")
_MAX_ITERATIONS = _field("integer", "Controller-bounded maximum planner iterations.", minimum=1)
_WORKER_ID = _field("string", "Stable local worker identifier.")
_LEASE_TOKEN = _field("string", "Bearer token returned by a successful turn claim.")
_REASON = _field("string", "Concise reason recorded in immutable run history.")
_RATIONALE = _field("string", "Evidence-based completion rationale.")
_MESSAGE = _field("string", "Progress message recorded as evidence.")

_INSTRUCTION_FIELDS: Final[dict[str, JsonObject]] = {
    "goal": _field("string", "Bounded executor objective."),
    "context": _array_field("Relevant bounded background.", items=_field("string", "Context item.")),
    "constraints": _array_field("Non-negotiable work constraints.", items=_field("string", "Constraint.")),
    "done_when": _field("string", "Observable completion condition."),
    "instruction_id": _field("string", "Optional caller-generated instruction identifier."),
    "relevant_files": _array_field(
        "Repository-relative files relevant to this turn.", items=_field("string", "Relative path.")
    ),
    "required_tests": _array_field("Tests required for this turn.", items=_field("string", "Test.")),
    "prohibited_changes": _array_field("Changes prohibited for this turn.", items=_field("string", "Prohibition.")),
    "evidence_requirements": _array_field(
        "Evidence the worker must collect.", items=_field("string", "Evidence requirement.")
    ),
    "discipline_skills": _array_field("Selected repository discipline Skills.", items=_field("string", "Skill name.")),
    "selected_discipline_skills": _array_field(
        "Alias for discipline_skills retained for role-Skill wording.",
        items=_field("string", "Skill name."),
    ),
}

_RESULT_FIELDS: Final[dict[str, JsonObject]] = {
    "status": _field("string", "Execution outcome.", enum=["completed", "partial", "blocked", "failed"]),
    "summary": _field("string", "Concise execution outcome."),
    "evidence": _array_field("Structured observed evidence."),
    "changed_files": _array_field(
        "Repository-relative files claimed changed.", items=_field("string", "Relative path.")
    ),
    "tests": _array_field("Structured test evidence."),
    "commands": _array_field("Commands reported as evidence; never executed by the server."),
    "commit": {"type": ["string", "null"], "description": "Optional revision reference."},
    "blockers": _array_field("Current blockers.", items=_field("string", "Blocker.")),
    "questions": _array_field("Questions requiring a response.", items=_field("string", "Question.")),
    "remaining_uncertainty": _array_field("Known uncertainty.", items=_field("string", "Uncertainty.")),
    "recommended_next_action": _field("string", "Bounded recommended next step."),
}

_TOOL_DEFINITIONS: Final[dict[str, JsonObject]] = {
    "ultracode_create_run": _tool(
        "ultracode_create_run",
        "Create a controller-owned bounded run.",
        _object_schema(
            {
                "objective": _OBJECTIVE,
                "max_iterations": _MAX_ITERATIONS,
                "idempotency_key": _IDEMPOTENCY,
            },
            ("objective", "idempotency_key"),
        ),
    ),
    "ultracode_read_run": _tool(
        "ultracode_read_run",
        "Read a run reconstructed from immutable events.",
        _object_schema({"run_id": _RUN_ID}, ("run_id",)),
    ),
    "ultracode_submit_instruction": _tool(
        "ultracode_submit_instruction",
        "Submit one bounded planner instruction for the current run.",
        _object_schema(
            {"run_id": _RUN_ID, "idempotency_key": _IDEMPOTENCY, **_INSTRUCTION_FIELDS},
            ("run_id", "idempotency_key", *_INSTRUCTION_REQUIRED),
        ),
    ),
    "ultracode_read_result": _tool(
        "ultracode_read_result",
        "Read the latest structured executor result.",
        _object_schema({"run_id": _RUN_ID}, ("run_id",)),
    ),
    "ultracode_complete_run": _tool(
        "ultracode_complete_run",
        "Complete a reviewed run only when controller-visible evidence exists.",
        _object_schema(
            {"run_id": _RUN_ID, "rationale": _RATIONALE, "idempotency_key": _IDEMPOTENCY},
            ("run_id", "rationale", "idempotency_key"),
        ),
    ),
    "ultracode_request_human": _tool(
        "ultracode_request_human",
        "Escalate an active execution or review to a human.",
        _object_schema(
            {"run_id": _RUN_ID, "reason": _REASON, "idempotency_key": _IDEMPOTENCY},
            ("run_id", "reason", "idempotency_key"),
        ),
    ),
    "ultracode_claim_instruction": _tool(
        "ultracode_claim_instruction",
        "Claim the single current executor turn and receive its lease token.",
        _object_schema(
            {"run_id": _RUN_ID, "worker_id": _WORKER_ID, "idempotency_key": _IDEMPOTENCY},
            ("run_id", "worker_id", "idempotency_key"),
        ),
    ),
    "ultracode_submit_result": _tool(
        "ultracode_submit_result",
        "Submit an evidence-rich result using an active worker lease.",
        _object_schema(
            {
                "run_id": _RUN_ID,
                "worker_id": _WORKER_ID,
                "lease_token": _LEASE_TOKEN,
                "idempotency_key": _IDEMPOTENCY,
                **_RESULT_FIELDS,
            },
            ("run_id", "worker_id", "lease_token", "idempotency_key", *_RESULT_REQUIRED),
        ),
    ),
    "ultracode_report_progress": _tool(
        "ultracode_report_progress",
        "Append a worker progress event while its lease is active.",
        _object_schema(
            {
                "run_id": _RUN_ID,
                "worker_id": _WORKER_ID,
                "lease_token": _LEASE_TOKEN,
                "message": _MESSAGE,
                "idempotency_key": _IDEMPOTENCY,
            },
            ("run_id", "worker_id", "lease_token", "message", "idempotency_key"),
        ),
    ),
    "ultracode_report_blocker": _tool(
        "ultracode_report_blocker",
        "Escalate a blocked active worker turn to a human.",
        _object_schema(
            {
                "run_id": _RUN_ID,
                "worker_id": _WORKER_ID,
                "lease_token": _LEASE_TOKEN,
                "reason": _REASON,
                "idempotency_key": _IDEMPOTENCY,
            },
            ("run_id", "worker_id", "lease_token", "reason", "idempotency_key"),
        ),
    ),
    "ultracode_pause": _tool(
        "ultracode_pause",
        "Pause a non-terminal run and remove any active worker lease.",
        _object_schema(
            {"run_id": _RUN_ID, "reason": _REASON, "idempotency_key": _IDEMPOTENCY},
            ("run_id", "reason", "idempotency_key"),
        ),
    ),
    "ultracode_resume": _tool(
        "ultracode_resume",
        "Resume a paused run to its controller-recorded safe state.",
        _object_schema(
            {"run_id": _RUN_ID, "idempotency_key": _IDEMPOTENCY},
            ("run_id", "idempotency_key"),
        ),
    ),
    "ultracode_stop": _tool(
        "ultracode_stop",
        "Stop a non-terminal run permanently.",
        _object_schema(
            {"run_id": _RUN_ID, "reason": _REASON, "idempotency_key": _IDEMPOTENCY},
            ("run_id", "reason", "idempotency_key"),
        ),
    ),
    "ultracode_status": _tool(
        "ultracode_status",
        "Read controller-replayed state for a run.",
        _object_schema({"run_id": _RUN_ID}, ("run_id",)),
    ),
    "ultracode_history": _tool(
        "ultracode_history",
        "Read the ordered, redacted immutable event history for a run.",
        _object_schema({"run_id": _RUN_ID}, ("run_id",)),
    ),
}


def _error_response(request_id: JsonValue, fault: RpcFault) -> JsonObject:
    error: JsonObject = {"code": fault.code, "message": fault.message}
    if fault.data is not None:
        error["data"] = fault.data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _result_response(request_id: JsonValue, result: JsonObject) -> JsonObject:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _invalid_params(message: str) -> RpcFault:
    return RpcFault(-32602, message)


def _string_argument(arguments: Mapping[str, object], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise _invalid_params(f"{name} must be a non-empty string")
    return value.strip()


def _validate_fields(
    arguments: Mapping[str, object], *, required: Sequence[str], allowed: Sequence[str]
) -> dict[str, object]:
    missing = [field for field in required if field not in arguments]
    if missing:
        raise _invalid_params(f"missing required argument(s): {', '.join(missing)}")
    unexpected = sorted(set(arguments).difference(allowed))
    if unexpected:
        raise _invalid_params(f"unexpected argument(s): {', '.join(unexpected)}")
    return dict(arguments)


def _instruction_arguments(arguments: Mapping[str, object]) -> tuple[str, str, dict[str, object]]:
    allowed = ("run_id", "idempotency_key", *_INSTRUCTION_REQUIRED, *_INSTRUCTION_OPTIONAL)
    validated = _validate_fields(
        arguments,
        required=("run_id", "idempotency_key", *_INSTRUCTION_REQUIRED),
        allowed=allowed,
    )
    if "discipline_skills" in validated and "selected_discipline_skills" in validated:
        raise _invalid_params("use either discipline_skills or selected_discipline_skills, not both")
    if "selected_discipline_skills" in validated:
        validated["discipline_skills"] = validated.pop("selected_discipline_skills")
    run_id = _string_argument(validated, "run_id")
    idempotency_key = _string_argument(validated, "idempotency_key")
    instruction = {key: value for key, value in validated.items() if key not in {"run_id", "idempotency_key"}}
    return run_id, idempotency_key, instruction


def _result_arguments(
    arguments: Mapping[str, object],
) -> tuple[str, str, str, str, dict[str, object]]:
    allowed = (
        "run_id",
        "worker_id",
        "lease_token",
        "idempotency_key",
        *_RESULT_REQUIRED,
        *_RESULT_OPTIONAL,
    )
    validated = _validate_fields(
        arguments,
        required=(
            "run_id",
            "worker_id",
            "lease_token",
            "idempotency_key",
            *_RESULT_REQUIRED,
        ),
        allowed=allowed,
    )
    run_id = _string_argument(validated, "run_id")
    worker_id = _string_argument(validated, "worker_id")
    lease_token = _string_argument(validated, "lease_token")
    idempotency_key = _string_argument(validated, "idempotency_key")
    result = {
        key: value
        for key, value in validated.items()
        if key not in {"run_id", "worker_id", "lease_token", "idempotency_key"}
    }
    return run_id, worker_id, lease_token, idempotency_key, result


def _tool_result(content: JsonObject) -> JsonObject:
    text_content: JsonObject = {"type": "text", "text": canonical_json(content)}
    return {"content": [text_content], "structuredContent": content}


def _snapshot_content(snapshot: JsonObject) -> JsonObject:
    return {"run": snapshot}


class MCPServer:
    """Translate one role's local MCP calls into a shared ``Controller``."""

    def __init__(self, controller: Controller, role: str) -> None:
        if role not in _VALID_ROLES:
            valid = ", ".join(sorted(_VALID_ROLES))
            raise ValueError(f"role must be one of: {valid}")
        self.controller = controller
        self.role = role

    @property
    def tool_names(self) -> tuple[str, ...]:
        """Names advertised by this role-specific process, in contract order."""

        return _ROLE_TOOLS[self.role]

    def handle(self, message: object) -> JsonObject | None:
        """Handle one parsed JSON-RPC message.

        A notification produces no response, as JSON-RPC requires. Parse errors
        are handled by ``serve`` because there is no parsed request identifier.
        """

        request_id: JsonValue = None
        notification = False
        try:
            if not isinstance(message, Mapping):
                raise RpcFault(-32600, "Invalid Request")
            notification = "id" not in message
            if not notification:
                candidate_id = message["id"]
                if (
                    isinstance(candidate_id, bool) or not isinstance(candidate_id, (str, int, float))
                ) and candidate_id is not None:
                    raise RpcFault(-32600, "Invalid Request")
                request_id = candidate_id
            if message.get("jsonrpc") != "2.0":
                raise RpcFault(-32600, "Invalid Request")
            method = message.get("method")
            if not isinstance(method, str) or not method:
                raise RpcFault(-32600, "Invalid Request")
            result = self._dispatch(method, message.get("params"))
        except RpcFault as fault:
            return None if notification else _error_response(request_id, fault)
        except ValidationError as exc:
            detail = redact_secrets(str(exc))
            return (
                None
                if notification
                else _error_response(
                    request_id,
                    RpcFault(-32602, str(detail), {"kind": type(exc).__name__}),
                )
            )
        except (ControllerError, ProtocolError) as exc:
            detail = redact_secrets(str(exc))
            return (
                None
                if notification
                else _error_response(
                    request_id,
                    RpcFault(-32000, str(detail), {"kind": type(exc).__name__}),
                )
            )
        except Exception:
            return None if notification else _error_response(request_id, RpcFault(-32603, "Internal error"))
        return None if notification else _result_response(request_id, result)

    def _dispatch(self, method: str, raw_params: object) -> JsonObject:
        if method == "notifications/initialized":
            return {}
        if method == "initialize":
            return self._initialize(raw_params)
        if method == "tools/list":
            if raw_params not in (None, {}):
                raise _invalid_params("tools/list does not accept parameters")
            tools: list[JsonValue] = []
            for name in self.tool_names:
                tools.append(_TOOL_DEFINITIONS[name])
            return {"tools": tools}
        if method != "tools/call":
            raise RpcFault(-32601, "Method not found")
        return self._call_tool(raw_params)

    @staticmethod
    def _initialize(raw_params: object) -> JsonObject:
        if not isinstance(raw_params, Mapping):
            raise _invalid_params("initialize requires an object params value")
        protocol_version = raw_params.get("protocolVersion")
        if not isinstance(protocol_version, str) or not protocol_version:
            raise _invalid_params("initialize requires protocolVersion")
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _call_tool(self, raw_params: object) -> JsonObject:
        if not isinstance(raw_params, Mapping):
            raise _invalid_params("tools/call requires an object params value")
        name = raw_params.get("name")
        if not isinstance(name, str) or not name:
            raise _invalid_params("tools/call requires a non-empty tool name")
        arguments = raw_params.get("arguments")
        if not isinstance(arguments, Mapping):
            raise _invalid_params("tools/call requires an object arguments value")
        if name not in _TOOL_DEFINITIONS:
            raise RpcFault(-32601, "Tool not found")
        if name not in self.tool_names:
            raise RpcFault(
                -32001,
                "Tool is not available for this role",
                {"role": self.role, "tool": name},
            )
        return _tool_result(self._invoke_tool(name, arguments))

    def _invoke_tool(self, name: str, arguments: Mapping[str, object]) -> JsonObject:
        """Call one allowed controller operation after schema validation."""

        if name == "ultracode_create_run":
            validated = _validate_fields(
                arguments,
                required=("objective", "idempotency_key"),
                allowed=("objective", "max_iterations", "idempotency_key"),
            )
            objective = _string_argument(validated, "objective")
            max_iterations = validated.get("max_iterations", 10)
            if not isinstance(max_iterations, int) or isinstance(max_iterations, bool):
                raise _invalid_params("max_iterations must be an integer")
            idempotency_key = _string_argument(validated, "idempotency_key")
            snapshot = self.controller.create_run(
                objective,
                max_iterations=max_iterations,
                idempotency_key=idempotency_key,
            )
            return _snapshot_content(snapshot.to_dict())

        if name == "ultracode_read_run":
            validated = _validate_fields(arguments, required=("run_id",), allowed=("run_id",))
            snapshot = self.controller.get_run(_string_argument(validated, "run_id"))
            return _snapshot_content(snapshot.to_dict())

        if name == "ultracode_submit_instruction":
            run_id, idempotency_key, instruction = _instruction_arguments(arguments)
            snapshot = self.controller.submit_instruction(
                run_id,
                instruction,
                idempotency_key=idempotency_key,
            )
            instruction_id = (
                None if snapshot.current_instruction is None else snapshot.current_instruction.instruction_id
            )
            return {"run": snapshot.to_dict(), "instruction_id": instruction_id}

        if name == "ultracode_read_result":
            validated = _validate_fields(arguments, required=("run_id",), allowed=("run_id",))
            run_id = _string_argument(validated, "run_id")
            result = self.controller.read_result(run_id)
            return {"run_id": run_id, "result": None if result is None else result.to_dict()}

        if name == "ultracode_complete_run":
            validated = _validate_fields(
                arguments,
                required=("run_id", "rationale", "idempotency_key"),
                allowed=("run_id", "rationale", "idempotency_key"),
            )
            snapshot = self.controller.complete_run(
                _string_argument(validated, "run_id"),
                _string_argument(validated, "rationale"),
                idempotency_key=_string_argument(validated, "idempotency_key"),
            )
            return _snapshot_content(snapshot.to_dict())

        if name == "ultracode_request_human":
            validated = _validate_fields(
                arguments,
                required=("run_id", "reason", "idempotency_key"),
                allowed=("run_id", "reason", "idempotency_key"),
            )
            snapshot = self.controller.request_human(
                _string_argument(validated, "run_id"),
                _string_argument(validated, "reason"),
                idempotency_key=_string_argument(validated, "idempotency_key"),
            )
            return _snapshot_content(snapshot.to_dict())

        if name == "ultracode_claim_instruction":
            validated = _validate_fields(
                arguments,
                required=("run_id", "worker_id", "idempotency_key"),
                allowed=("run_id", "worker_id", "idempotency_key"),
            )
            claim = self.controller.claim_turn(
                _string_argument(validated, "run_id"),
                worker_id=_string_argument(validated, "worker_id"),
                idempotency_key=_string_argument(validated, "idempotency_key"),
            )
            return claim.to_dict()

        if name == "ultracode_submit_result":
            run_id, worker_id, lease_token, idempotency_key, execution_result = _result_arguments(arguments)
            snapshot = self.controller.submit_result(
                run_id,
                execution_result,
                worker_id=worker_id,
                lease_token=lease_token,
                idempotency_key=idempotency_key,
            )
            return _snapshot_content(snapshot.to_dict())

        if name == "ultracode_report_progress":
            validated = _validate_fields(
                arguments,
                required=("run_id", "worker_id", "lease_token", "message", "idempotency_key"),
                allowed=("run_id", "worker_id", "lease_token", "message", "idempotency_key"),
            )
            snapshot = self.controller.report_progress(
                _string_argument(validated, "run_id"),
                worker_id=_string_argument(validated, "worker_id"),
                lease_token=_string_argument(validated, "lease_token"),
                message=_string_argument(validated, "message"),
                idempotency_key=_string_argument(validated, "idempotency_key"),
            )
            return _snapshot_content(snapshot.to_dict())

        if name == "ultracode_report_blocker":
            validated = _validate_fields(
                arguments,
                required=("run_id", "worker_id", "lease_token", "reason", "idempotency_key"),
                allowed=("run_id", "worker_id", "lease_token", "reason", "idempotency_key"),
            )
            snapshot = self.controller.report_blocker(
                _string_argument(validated, "run_id"),
                worker_id=_string_argument(validated, "worker_id"),
                lease_token=_string_argument(validated, "lease_token"),
                reason=_string_argument(validated, "reason"),
                idempotency_key=_string_argument(validated, "idempotency_key"),
            )
            return _snapshot_content(snapshot.to_dict())

        if name == "ultracode_pause":
            validated = _validate_fields(
                arguments,
                required=("run_id", "reason", "idempotency_key"),
                allowed=("run_id", "reason", "idempotency_key"),
            )
            snapshot = self.controller.pause_run(
                _string_argument(validated, "run_id"),
                _string_argument(validated, "reason"),
                idempotency_key=_string_argument(validated, "idempotency_key"),
            )
            return _snapshot_content(snapshot.to_dict())

        if name == "ultracode_resume":
            validated = _validate_fields(
                arguments,
                required=("run_id", "idempotency_key"),
                allowed=("run_id", "idempotency_key"),
            )
            snapshot = self.controller.resume_run(
                _string_argument(validated, "run_id"),
                idempotency_key=_string_argument(validated, "idempotency_key"),
            )
            return _snapshot_content(snapshot.to_dict())

        if name == "ultracode_stop":
            validated = _validate_fields(
                arguments,
                required=("run_id", "reason", "idempotency_key"),
                allowed=("run_id", "reason", "idempotency_key"),
            )
            snapshot = self.controller.stop_run(
                _string_argument(validated, "run_id"),
                _string_argument(validated, "reason"),
                idempotency_key=_string_argument(validated, "idempotency_key"),
            )
            return _snapshot_content(snapshot.to_dict())

        if name == "ultracode_status":
            validated = _validate_fields(arguments, required=("run_id",), allowed=("run_id",))
            snapshot = self.controller.get_run(_string_argument(validated, "run_id"))
            return _snapshot_content(snapshot.to_dict())

        if name == "ultracode_history":
            validated = _validate_fields(arguments, required=("run_id",), allowed=("run_id",))
            events = self.controller.history(_string_argument(validated, "run_id"))
            return {"events": [event.to_public_dict() for event in events]}

        raise RpcFault(-32601, "Tool not found")


def serve(
    controller: Controller,
    role: str,
    *,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
) -> None:
    """Serve JSON-RPC messages from ``input_stream`` until EOF."""

    server = MCPServer(controller, role)
    for line in input_stream:
        if not line.strip():
            continue
        response: JsonObject | None
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = _error_response(None, RpcFault(-32700, "Parse error"))
        else:
            response = server.handle(message)
        if response is not None:
            output_stream.write(canonical_json(response))
            output_stream.write("\n")
            output_stream.flush()


def run_stdio_server(*, database: str, role: str) -> int:
    """Run the public stdio server entry point used by the Ultracode CLI."""

    serve(Controller(database), role)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run a role-scoped stdio MCP process."""

    parser = argparse.ArgumentParser(description="42 Ultracode role-scoped stdio MCP server")
    parser.add_argument("--role", choices=sorted(_VALID_ROLES), required=True)
    parser.add_argument("--database", required=True, help="Shared local SQLite database path")
    arguments = parser.parse_args(argv)
    return run_stdio_server(database=arguments.database, role=arguments.role)


if __name__ == "__main__":  # pragma: no cover - subprocess entrypoint
    raise SystemExit(main())
