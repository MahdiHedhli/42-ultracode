"""Subprocess coverage for the role-scoped local MCP transport."""

from __future__ import annotations

import json
import select
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_VERSION = "2025-03-26"


class McpClient:
    """A deliberately small line-delimited JSON-RPC test client."""

    def __init__(self, role: str, database: Path) -> None:
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ultracode.mcp.server",
                "--role",
                role,
                "--database",
                str(database),
            ],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._next_id = 1

    def request(self, method: str, params: object | None = None) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        payload: dict[str, object] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)
        response = self._read()
        assert response["id"] == request_id
        return response

    def notify(self, method: str, params: object | None = None) -> None:
        payload: dict[str, object] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)

    def send_raw(self, line: str) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def close(self) -> None:
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        assert self.process.returncode == 0, self._stderr()

    def _write(self, payload: dict[str, object]) -> None:
        self.send_raw(json.dumps(payload))

    def _read(self) -> dict[str, Any]:
        assert self.process.stdout is not None
        ready, _, _ = select.select([self.process.stdout], [], [], 5)
        assert ready, self._stderr()
        line = self.process.stdout.readline()
        assert line, self._stderr()
        loaded = json.loads(line)
        assert isinstance(loaded, dict)
        return loaded

    def _stderr(self) -> str:
        if self.process.poll() is None:
            return "MCP subprocess did not produce a response"
        assert self.process.stderr is not None
        return self.process.stderr.read()


def _initialize(client: McpClient) -> None:
    response = client.request(
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    )
    assert response["result"]["serverInfo"]["name"] == "42-ultracode"
    client.notify("notifications/initialized")


def _tool_call(client: McpClient, name: str, arguments: dict[str, object]) -> dict[str, Any]:
    response = client.request("tools/call", {"name": name, "arguments": arguments})
    assert "error" not in response, response
    result = response["result"]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    return result["structuredContent"]


def _tool_names(client: McpClient) -> set[str]:
    response = client.request("tools/list")
    assert "error" not in response, response
    return {tool["name"] for tool in response["result"]["tools"]}


def test_role_scoped_mcp_round_trip_across_subprocesses(tmp_path: Path) -> None:
    """Planner, worker, and control processes exchange state through one DB."""

    database = tmp_path / "ultracode.db"
    planner = McpClient("planner", database)
    worker: McpClient | None = None
    control: McpClient | None = None
    try:
        _initialize(planner)
        assert _tool_names(planner) == {
            "ultracode_create_run",
            "ultracode_read_run",
            "ultracode_submit_instruction",
            "ultracode_read_result",
            "ultracode_complete_run",
            "ultracode_request_human",
        }

        created = _tool_call(
            planner,
            "ultracode_create_run",
            {
                "objective": "Exercise role-scoped MCP handoff",
                "max_iterations": 2,
                "idempotency_key": "create",
            },
        )
        run_id = created["run"]["run_id"]
        assert created["run"]["state"] == "PLANNING"

        control = McpClient("control", database)
        _initialize(control)
        assert _tool_names(control) == {
            "ultracode_pause",
            "ultracode_resume",
            "ultracode_stop",
            "ultracode_status",
            "ultracode_history",
        }
        paused = _tool_call(
            control,
            "ultracode_pause",
            {"run_id": run_id, "reason": "test control", "idempotency_key": "pause"},
        )
        assert paused["run"]["state"] == "PAUSED"
        resumed = _tool_call(
            control,
            "ultracode_resume",
            {"run_id": run_id, "idempotency_key": "resume"},
        )
        assert resumed["run"]["state"] == "PLANNING"

        submitted = _tool_call(
            planner,
            "ultracode_submit_instruction",
            {
                "run_id": run_id,
                "goal": "Validate the MCP handoff",
                "context": ["This is an integration test."],
                "constraints": ["Do not execute reported commands."],
                "done_when": "A structured result is persisted.",
                "selected_discipline_skills": ["ultracode-worker"],
                "idempotency_key": "instruction",
            },
        )
        assert submitted["run"]["state"] == "READY_FOR_CODEX"
        assert submitted["instruction_id"]

        worker = McpClient("worker", database)
        _initialize(worker)
        assert _tool_names(worker) == {
            "ultracode_claim_instruction",
            "ultracode_submit_result",
            "ultracode_report_progress",
            "ultracode_report_blocker",
        }

        role_error = worker.request(
            "tools/call",
            {"name": "ultracode_read_run", "arguments": {"run_id": run_id}},
        )
        assert role_error["error"]["code"] == -32001
        assert role_error["error"]["data"]["role"] == "worker"
        unknown_tool = planner.request(
            "tools/call",
            {"name": "ultracode_nope", "arguments": {}},
        )
        assert unknown_tool["error"]["code"] == -32601
        malformed_args = planner.request(
            "tools/call",
            {"name": "ultracode_submit_instruction", "arguments": {"run_id": run_id}},
        )
        assert malformed_args["error"]["code"] == -32602
        invalid_path = planner.request(
            "tools/call",
            {
                "name": "ultracode_submit_instruction",
                "arguments": {
                    "run_id": run_id,
                    "goal": "Reject a path outside the workspace",
                    "context": ["This is an MCP validation test."],
                    "constraints": ["Do not accept traversal."],
                    "done_when": "The input is rejected with an actionable error.",
                    "relevant_files": ["../outside.py"],
                    "idempotency_key": "invalid-path",
                },
            },
        )
        assert invalid_path["error"]["code"] == -32602
        assert "file paths" in invalid_path["error"]["message"]
        planner.send_raw("{")
        assert planner._read()["error"]["code"] == -32700

        claim = _tool_call(
            worker,
            "ultracode_claim_instruction",
            {"run_id": run_id, "worker_id": "pytest-worker", "idempotency_key": "claim"},
        )
        assert claim["instruction"]["goal"] == "Validate the MCP handoff"
        lease_token = claim["lease_token"]
        progress = _tool_call(
            worker,
            "ultracode_report_progress",
            {
                "run_id": run_id,
                "worker_id": "pytest-worker",
                "lease_token": lease_token,
                "message": "validation started",
                "idempotency_key": "progress",
            },
        )
        assert progress["run"]["state"] == "CODEX_RUNNING"
        complete = _tool_call(
            worker,
            "ultracode_submit_result",
            {
                "run_id": run_id,
                "worker_id": "pytest-worker",
                "lease_token": lease_token,
                "status": "completed",
                "summary": "The local MCP exchange passed.",
                "evidence": [{"kind": "integration", "passed": True}],
                "changed_files": ["ultracode/mcp/server.py"],
                "tests": ["pytest tests/integration/test_mcp.py"],
                "commands": ["pytest tests/integration/test_mcp.py"],
                "blockers": [],
                "questions": [],
                "remaining_uncertainty": [],
                "recommended_next_action": "Review the evidence and complete the run.",
                "idempotency_key": "result",
            },
        )
        assert complete["run"]["state"] == "CODEX_COMPLETE"

        result = _tool_call(planner, "ultracode_read_result", {"run_id": run_id})
        assert result["result"]["evidence"] == [{"kind": "integration", "passed": True}]
        completed = _tool_call(
            planner,
            "ultracode_complete_run",
            {
                "run_id": run_id,
                "rationale": "The result contains integration evidence.",
                "idempotency_key": "complete",
            },
        )
        assert completed["run"]["state"] == "COMPLETE"
        status = _tool_call(control, "ultracode_status", {"run_id": run_id})
        assert status["run"]["state"] == "COMPLETE"
        history = _tool_call(control, "ultracode_history", {"run_id": run_id})
        assert [event["event_type"] for event in history["events"]].count("PROGRESS_REPORTED") == 1
    finally:
        if worker is not None:
            worker.close()
        if control is not None:
            control.close()
        planner.close()
