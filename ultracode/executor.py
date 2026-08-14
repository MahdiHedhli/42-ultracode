"""Subscription-backed Codex CLI worker adapter.

This adapter is deliberately optional. It translates one claimed typed instruction
into a Codex CLI turn and translates the model's final structured response back
into an Ultracode result. It never mutates lifecycle state directly.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .controller import Controller, TurnClaim
from .protocol import ExecutionResult, Instruction, ResultStatus, ValidationError, redact_secrets


class ExecutorError(RuntimeError):
    """Raised when the Codex CLI cannot produce a valid worker result."""


@dataclass(frozen=True)
class ExecutorPreflight:
    available: bool
    authenticated: bool
    detail: str


@dataclass(frozen=True)
class ExecutorOutcome:
    result: ExecutionResult
    stdout: str
    stderr: str
    command: tuple[str, ...]


_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "status",
        "summary",
        "evidence",
        "changed_files",
        "tests",
        "commands",
        "commit",
        "blockers",
        "questions",
        "remaining_uncertainty",
        "recommended_next_action",
    ],
    "properties": {
        "status": {"type": "string", "enum": [item.value for item in ResultStatus]},
        "summary": {"type": "string"},
        # Codex's response-format validator requires every array item schema to
        # declare a type. Keep the executor adapter's evidence portable as text;
        # the controller protocol still accepts richer JSON evidence from MCP.
        "evidence": {"type": "array", "items": {"type": "string"}},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "tests": {"type": "array", "items": {"type": "string"}},
        "commands": {"type": "array", "items": {"type": "string"}},
        "commit": {"type": ["string", "null"]},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}},
        "remaining_uncertainty": {"type": "array", "items": {"type": "string"}},
        "recommended_next_action": {"type": "string"},
    },
}


class CodexCliExecutor:
    """Run one structured Codex turn under the existing CLI login.

    `gpt-5.5` is the known compatible local default from the feasibility spike.
    It is configurable rather than an API fallback; a failure here remains visible
    to the worker/control path.
    """

    def __init__(
        self,
        *,
        workspace: str | Path,
        model: str = "gpt-5.5",
        sandbox: str = "read-only",
        codex_command: str = "codex",
        timeout_seconds: int = 900,
        ignore_user_config: bool = True,
    ) -> None:
        if sandbox not in {"read-only", "workspace-write"}:
            raise ValueError("sandbox must be read-only or workspace-write")
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        self.workspace = Path(workspace).resolve()
        self.model = model
        self.sandbox = sandbox
        self.codex_command = codex_command
        self.timeout_seconds = timeout_seconds
        self.ignore_user_config = ignore_user_config

    def preflight(self) -> ExecutorPreflight:
        """Check CLI availability and its existing subscription login without secrets."""

        try:
            completed = subprocess.run(
                [self.codex_command, "login", "status"],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ExecutorPreflight(False, False, f"Codex CLI unavailable: {exc}")
        detail = str(redact_secrets(completed.stdout.strip() or completed.stderr.strip()))
        authenticated = completed.returncode == 0 and "ChatGPT" in detail
        return ExecutorPreflight(True, authenticated, detail)

    @staticmethod
    def _prompt(instruction: Instruction) -> str:
        fields = instruction.to_dict()
        return (
            "You are the 42 Ultracode worker. Execute only the bounded instruction below "
            "inside the configured Codex sandbox. Do not silently redefine the objective, "
            "do not modify workflow limits or policy, and do not include secrets. Validate "
            "your work. Your final response MUST match the supplied JSON schema exactly.\n\n"
            f"Instruction:\n{json.dumps(fields, indent=2, ensure_ascii=False)}"
        )

    def execute(self, instruction: Instruction) -> ExecutorOutcome:
        """Run Codex and parse the schema-constrained final message."""

        if not self.workspace.is_dir():
            raise ExecutorError(f"workspace does not exist: {self.workspace}")
        with tempfile.TemporaryDirectory(prefix="ultracode-codex-") as temporary:
            temporary_path = Path(temporary)
            schema_path = temporary_path / "result-schema.json"
            output_path = temporary_path / "result.json"
            schema_path.write_text(json.dumps(_RESULT_SCHEMA), encoding="utf-8")
            command = [
                self.codex_command,
                "exec",
                "--ephemeral",
                "--json",
                "--model",
                self.model,
                "--sandbox",
                self.sandbox,
                "--cd",
                str(self.workspace),
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
            ]
            if self.ignore_user_config:
                command.append("--ignore-user-config")
            command.append(self._prompt(instruction))
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise ExecutorError(f"Codex execution timed out after {self.timeout_seconds}s") from exc
            except OSError as exc:
                raise ExecutorError(f"could not start Codex CLI: {exc}") from exc
            stdout = str(redact_secrets(completed.stdout))
            stderr = str(redact_secrets(completed.stderr))
            if completed.returncode != 0:
                diagnostics = "\n".join(
                    part
                    for part in (
                        f"stdout: {stdout[-1000:]}" if stdout.strip() else "",
                        f"stderr: {stderr[-1000:]}" if stderr.strip() else "",
                    )
                    if part
                )
                raise ExecutorError(f"Codex execution failed ({completed.returncode}): {diagnostics}")
            if not output_path.is_file():
                raise ExecutorError("Codex execution did not write a final structured result")
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
                if not isinstance(payload, Mapping):
                    raise TypeError("final result is not an object")
                result = ExecutionResult.from_dict(payload)
            except (OSError, TypeError, ValueError, ValidationError) as exc:
                raise ExecutorError(f"Codex final result did not satisfy worker schema: {exc}") from exc
            return ExecutorOutcome(result=result, stdout=stdout, stderr=stderr, command=tuple(command[:-1]))


def execute_claim(
    controller: Controller,
    claim: TurnClaim,
    executor: CodexCliExecutor,
    *,
    result_idempotency_key: str,
) -> ExecutionResult:
    """Execute a claimed turn and submit its result through the controller."""

    outcome = executor.execute(claim.instruction)
    controller.submit_result(
        claim.run_id,
        outcome.result,
        worker_id=claim.worker_id,
        lease_token=claim.lease_token,
        idempotency_key=result_idempotency_key,
    )
    return outcome.result


def execute_one(
    controller: Controller,
    *,
    run_id: str,
    worker_id: str,
    executor: CodexCliExecutor,
    claim_idempotency_key: str,
    result_idempotency_key: str,
) -> ExecutionResult:
    """Claim and execute one available instruction.

    Failures intentionally become a structured human escalation rather than a
    hidden retry loop. The controller stays authoritative even when Codex fails.
    """

    claim = controller.claim_turn(run_id, worker_id=worker_id, idempotency_key=claim_idempotency_key)
    try:
        return execute_claim(
            controller,
            claim,
            executor,
            result_idempotency_key=result_idempotency_key,
        )
    except ExecutorError as exc:
        controller.report_blocker(
            run_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
            reason=str(redact_secrets(str(exc))),
            idempotency_key=f"{result_idempotency_key}:blocker",
        )
        raise
