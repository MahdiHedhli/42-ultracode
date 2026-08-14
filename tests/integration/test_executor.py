"""Integration coverage for the subscription-backed Codex CLI adapter.

The tests use a tiny local stand-in for the CLI. They verify adapter behavior
without implying that a fake executable proves subscription authentication; the
real subscription probe is recorded separately in the validation evidence.
"""

from __future__ import annotations

import shlex
import stat
import sys
from pathlib import Path

import pytest

from ultracode.controller import Controller
from ultracode.executor import _RESULT_SCHEMA, CodexCliExecutor, ExecutorError, execute_one
from ultracode.protocol import Instruction, RunState


def _instruction() -> Instruction:
    return Instruction.from_dict(
        {
            "instruction_id": "executor-instruction",
            "goal": "Validate the local executor adapter.",
            "context": ["The test uses a local Codex CLI stand-in."],
            "constraints": ["Do not write outside the configured workspace."],
            "done_when": "A structured result is returned.",
            "relevant_files": ["ultracode/executor.py"],
        }
    )


def _write_fake_codex(path: Path, *, fail_execution: bool = False) -> None:
    """Create a deterministic CLI stand-in in a disposable test directory."""

    payload = """{
  \"status\": \"completed\",
  \"summary\": \"The fake Codex turn completed.\",
  \"evidence\": [{\"kind\": \"fake-cli\", \"outcome\": \"passed\"}],
  \"changed_files\": [\"ultracode/executor.py\"],
  \"tests\": [\"fake validation\"],
  \"commands\": [\"fake-codex exec\"],
  \"commit\": null,
  \"blockers\": [],
  \"questions\": [],
  \"remaining_uncertainty\": [],
  \"recommended_next_action\": \"Review the structured result.\"
}"""
    execution = """
if '--output-last-message' not in args:
    raise SystemExit('missing output path')
output = pathlib.Path(args[args.index('--output-last-message') + 1])
print('sk-' + '0123456789abcdefghijklmnopqrstuv')
output.write_text(PAYLOAD, encoding='utf-8')
raise SystemExit(0)
"""
    if fail_execution:
        execution = """
print('fake execution failure', file=sys.stderr)
raise SystemExit(23)
"""
    implementation = path.with_suffix(".py")
    script = f"""\
import pathlib
import sys

PAYLOAD = {payload!r}
args = sys.argv[1:]
if args == ['login', 'status']:
    print('Logged in using ChatGPT')
    raise SystemExit(0)
if not args or args[0] != 'exec':
    raise SystemExit('unexpected arguments: ' + repr(args))
{execution}
"""
    implementation.write_text(script, encoding="utf-8")
    path.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(implementation))} "$@"\n',
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_executor_preflight_and_structured_execution_are_subscription_shaped(tmp_path: Path) -> None:
    fake_codex = tmp_path / "fake-codex"
    _write_fake_codex(fake_codex)
    executor = CodexCliExecutor(workspace=tmp_path, codex_command=str(fake_codex), model="gpt-5.5")

    preflight = executor.preflight()
    outcome = executor.execute(_instruction())

    assert preflight.available is True
    assert preflight.authenticated is True
    assert preflight.detail == "Logged in using ChatGPT"
    assert outcome.result.summary == "The fake Codex turn completed."
    assert outcome.result.changed_files == ("ultracode/executor.py",)
    assert "--ignore-user-config" in outcome.command
    assert "--model" in outcome.command
    assert "gpt-5.5" in outcome.command
    assert "sk-" not in outcome.stdout
    assert "[REDACTED_OPENAI_KEY]" in outcome.stdout
    assert _RESULT_SCHEMA["properties"]["evidence"]["items"] == {"type": "string"}


def test_execute_one_submits_a_claimed_result_through_the_controller(tmp_path: Path) -> None:
    fake_codex = tmp_path / "fake-codex"
    _write_fake_codex(fake_codex)
    controller = Controller(tmp_path / "controller.db")
    run = controller.create_run("Exercise the executor handoff.", idempotency_key="create")
    controller.submit_instruction(run.run_id, _instruction(), idempotency_key="instruction")
    executor = CodexCliExecutor(workspace=tmp_path, codex_command=str(fake_codex))

    result = execute_one(
        controller,
        run_id=run.run_id,
        worker_id="fake-worker",
        executor=executor,
        claim_idempotency_key="claim",
        result_idempotency_key="result",
    )

    snapshot = Controller(tmp_path / "controller.db").get_run(run.run_id)
    assert result.status.value == "completed"
    assert snapshot.state is RunState.CODEX_COMPLETE
    assert snapshot.last_result == result


def test_executor_failure_becomes_a_visible_human_escalation(tmp_path: Path) -> None:
    fake_codex = tmp_path / "fake-codex"
    _write_fake_codex(fake_codex, fail_execution=True)
    controller = Controller(tmp_path / "controller.db")
    run = controller.create_run("Exercise a failed executor turn.", idempotency_key="create")
    controller.submit_instruction(run.run_id, _instruction(), idempotency_key="instruction")
    executor = CodexCliExecutor(workspace=tmp_path, codex_command=str(fake_codex))

    with pytest.raises(ExecutorError, match=r"failed \(23\)"):
        execute_one(
            controller,
            run_id=run.run_id,
            worker_id="fake-worker",
            executor=executor,
            claim_idempotency_key="claim",
            result_idempotency_key="result",
        )

    snapshot = controller.get_run(run.run_id)
    assert snapshot.state is RunState.HUMAN_REQUIRED
    assert snapshot.human_reason is not None
    assert "fake execution failure" in snapshot.human_reason
