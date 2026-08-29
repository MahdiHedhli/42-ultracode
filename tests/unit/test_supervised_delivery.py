from __future__ import annotations

import io
import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import threading
import time
from argparse import Namespace
from pathlib import Path

import pytest

import ultracode.supervised_delivery as delivery
from ultracode import cli
from ultracode.supervised_delivery import DeliveryError, DeliveryOutcome, DeliveryPreview


def _route(root: Path) -> Path:
    path = root / "routes.json"
    path.write_text(
        '{"aliases":{"SYNTHETIC_TARGET":{"cwd":"/synthetic/workspace",'
        '"source_kind":"appServer","thread_id":"synthetic-thread"}},"version":2}',
        encoding="ascii",
    )
    path.chmod(0o600)
    return path


def _confirmed(preview: DeliveryPreview) -> object:
    rendered = io.StringIO()

    class Input(io.StringIO):
        def isatty(self) -> bool:
            return True

        def readline(self, _size: int = -1) -> str:
            challenge = rendered.getvalue().rsplit("confirmation-challenge: ", 1)
            assert len(challenge) == 2
            return challenge[1].splitlines()[0] + "\n"

    class Output(io.StringIO):
        def isatty(self) -> bool:
            return True

    rendered = Output()
    return delivery.confirm_preview(preview, input_stream=Input(), output_stream=rendered)


def _run(root: Path, transcript: bytes | None = None) -> tuple[DeliveryOutcome, delivery._JsonlSession, bytes]:
    preview = DeliveryPreview("SYNTHETIC_TARGET", b"synthetic supervised delivery")
    writer = io.BytesIO()
    outcome, session = delivery._perform(
        preview=preview,
        capability=_confirmed(preview),
        route_registry=_route(root),
        journal_path=root / "journal.jsonl",
        streams=(io.BytesIO(transcript or delivery._fake_transcript()), writer),
        attempt_id="0" * 32,
    )
    assert session is not None
    return outcome, session, writer.getvalue()


def test_protocol_profile_and_fixed_process_boundary() -> None:
    assert delivery.APP_SERVER_ARGV == ("app-server", "--listen", "stdio://")
    assert delivery.PROTOCOL_PROFILE.request_methods == {
        "initialize",
        "thread/list",
        "thread/read",
        "thread/resume",
        "turn/start",
    }
    assert delivery.PROTOCOL_PROFILE.client_notifications == {"initialized"}
    assert delivery.PROTOCOL_PROFILE.server_notifications == {
        "thread/status/changed",
        "turn/started",
        "turn/completed",
        "error",
    }
    assert "experimental" not in " ".join(delivery.APP_SERVER_ARGV)


def test_twenty_deterministic_fake_peer_reconstructions(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("production process creation reached")

    monkeypatch.setattr(delivery.subprocess, "Popen", forbidden)
    results = []
    for index in range(20):
        result = delivery._qualify_fake_peer(tmp_path / f"run-{index:02d}")
        assert result["outcome"] == "DELIVERED"
        assert result["request_methods"] == [
            "initialize",
            "thread/list",
            "thread/list",
            "thread/list",
            "thread/read",
            "thread/resume",
            "thread/read",
            "turn/start",
        ]
        assert result["client_notifications"] == ["initialized"]
        assert set(result["real_operation_counters"].values()) == {0}  # type: ignore[union-attr]
        assert result["static_exclusions"] == ["automatic_loops", "browser_operations", "mcp_operations"]
        results.append((result["transcript_sha256"], result["journal_sha256"]))
    assert len(set(results)) == 1


def test_turn_start_has_no_override_fields_and_is_exactly_once(tmp_path: Path) -> None:
    outcome, session, written = _run(tmp_path)
    assert outcome is DeliveryOutcome.DELIVERED
    requests = [json.loads(line) for line in written.splitlines()]
    start = next(item for item in requests if item.get("method") == "turn/start")
    assert set(start["params"]) == {"input", "threadId"}
    assert start["params"]["input"] == [{"text": "synthetic supervised delivery", "type": "text"}]
    assert session.methods.count("turn/start") == 1


def test_confirmation_uses_tty_exact_preview_and_one_use() -> None:
    master_fd, slave_fd = pty.openpty()
    reader = os.fdopen(os.dup(slave_fd), "r", encoding="utf-8", buffering=1)
    writer = os.fdopen(os.dup(slave_fd), "w", encoding="utf-8", buffering=1)
    preview = DeliveryPreview("SYNTHETIC_TARGET", b"exact payload")
    captured: dict[str, object] = {}

    def confirm() -> None:
        captured["capability"] = delivery.confirm_preview(
            preview, input_stream=reader, output_stream=writer, ttl_seconds=10
        )

    thread = threading.Thread(target=confirm)
    thread.start()
    deadline = time.monotonic() + 5
    output = ""
    while "re-enter challenge exactly:" not in output and time.monotonic() < deadline:
        ready, _, _ = select.select([master_fd], [], [], max(0, deadline - time.monotonic()))
        if ready:
            output += os.read(master_fd, 8192).decode("utf-8")
    challenge = re.search(r"confirmation-challenge: ([0-9A-F]{16})", output)
    assert challenge is not None
    os.write(master_fd, (challenge.group(1) + "\n").encode("ascii"))
    thread.join(timeout=5)
    assert not thread.is_alive()
    capability = captured["capability"]
    delivery._consume_capability(capability, preview)  # type: ignore[operator]
    with pytest.raises(DeliveryError, match="already consumed"):
        delivery._consume_capability(capability, preview)  # type: ignore[operator]
    reader.close()
    writer.close()
    os.close(master_fd)
    os.close(slave_fd)


def test_confirmation_rejects_non_tty_before_route_resolution(tmp_path: Path) -> None:
    preview = DeliveryPreview("SYNTHETIC_TARGET", b"payload")
    with pytest.raises(DeliveryError, match="interactive TTY"):
        delivery.confirm_preview(preview, input_stream=io.StringIO("x\n"), output_stream=io.StringIO())
    assert not (tmp_path / "routes.json").exists()


def test_foreground_entry_rejects_non_tty_before_process_launch(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    message = tmp_path / "message.txt"
    message.write_bytes(b"payload")
    message.chmod(0o600)
    route = _route(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("process launch reached")

    monkeypatch.setattr(delivery.subprocess, "Popen", forbidden)
    with pytest.raises(DeliveryError, match="interactive TTY"):
        delivery.deliver_foreground(
            message_path=message,
            target_alias="SYNTHETIC_TARGET",
            route_registry=route,
            journal_path=tmp_path / "journal.jsonl",
            input_stream=io.StringIO(),
            output_stream=io.StringIO(),
        )


def test_executable_authority_is_absolute_owner_controlled_and_drift_detected(tmp_path: Path) -> None:
    executable = tmp_path / "codex"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    authority = delivery._seal_executable(executable)
    assert delivery._validate_executable(authority).path == executable
    executable.write_bytes(b"#!/bin/sh\nexit 1\n")
    with pytest.raises(DeliveryError, match="drifted"):
        delivery._validate_executable(authority)
    with pytest.raises(DeliveryError, match="explicit absolute"):
        delivery._seal_executable(Path("codex"))


def test_minimal_environment_excludes_inherited_and_injection_values() -> None:
    environment = delivery._minimal_environment(
        {
            "HOME": "/safe/home",
            "USER": "safe",
            "PATH": "/attacker",
            "OPENAI_API_KEY": "secret",
            "DYLD_INSERT_LIBRARIES": "/bad",
            "PYTHONPATH": "/bad",
            "ARBITRARY": "bad",
        }
    )
    assert environment == {"HOME": "/safe/home", "USER": "safe", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}


@pytest.mark.parametrize(
    "user_agent",
    [
        "codex-cli 0.146.0 suffix",
        "prefix codex-cli 0.146.0",
        "alternate-product 0.146.0",
        "codex-cli 0.146.00",
        "codex-cli 0.146.0 codex-cli 0.146.0",
    ],
)
def test_noncanonical_versions_fail_closed(tmp_path: Path, user_agent: str) -> None:
    lines = delivery._fake_transcript().splitlines()
    first = json.loads(lines[0])
    first["result"]["userAgent"] = user_agent
    lines[0] = delivery._canonical(first)
    outcome, _session, _written = _run(tmp_path, b"\n".join(lines) + b"\n")
    assert outcome is DeliveryOutcome.FAILED_BEFORE_WRITE


def test_schema_and_lifecycle_mutations_fail_before_write(tmp_path: Path) -> None:
    mutations = []
    for mutation in ("archived_membership", "active", "cwd", "source", "duplicate"):
        lines = delivery._fake_transcript().splitlines()
        line_index = 3 if mutation == "archived_membership" else 1
        response = json.loads(lines[line_index])
        if mutation == "archived_membership":
            response["result"]["data"] = [json.loads(lines[1])["result"]["data"][0]]
        elif mutation == "active":
            response["result"]["data"][0]["status"] = {"activeFlags": [], "type": "active"}
        elif mutation == "cwd":
            response["result"]["data"][0]["cwd"] = "/wrong"
        elif mutation == "source":
            response["result"]["data"][0]["source"] = "cli"
        else:
            response["result"]["data"].append(response["result"]["data"][0])
        lines[line_index] = delivery._canonical(response)
        mutations.append(b"\n".join(lines) + b"\n")
    for index, transcript in enumerate(mutations):
        root = tmp_path / str(index)
        root.mkdir()
        outcome, _session, _written = _run(root, transcript)
        assert outcome is DeliveryOutcome.FAILED_BEFORE_WRITE


def test_target_absent_from_active_listing_fails_before_write(tmp_path: Path) -> None:
    lines = delivery._fake_transcript().splitlines()
    active = json.loads(lines[1])
    active["result"]["data"] = []
    active["result"]["nextCursor"] = None
    del lines[2]
    lines[1] = delivery._canonical(active)
    outcome, _session, _written = _run(tmp_path, b"\n".join(lines) + b"\n")
    assert outcome is DeliveryOutcome.FAILED_BEFORE_WRITE


def test_silent_and_partial_pipe_reads_obey_deadline(tmp_path: Path) -> None:
    preview = DeliveryPreview("SYNTHETIC_TARGET", b"payload")
    for index, partial in enumerate((b"", b'{"jsonrpc":"2.0"')):
        root = tmp_path / f"deadline-{index}"
        root.mkdir()
        read_fd, write_fd = os.pipe()
        if partial:
            os.write(write_fd, partial)
        reader = os.fdopen(read_fd, "rb", buffering=0)
        try:
            outcome, _session = delivery._perform(
                preview=preview,
                capability=_confirmed(preview),
                route_registry=_route(root),
                journal_path=root / "journal.jsonl",
                streams=(reader, io.BytesIO()),
                deadline=delivery._Deadline.start(session_seconds=0.1, operation_seconds=0.05),
            )
            assert outcome is DeliveryOutcome.FAILED_BEFORE_WRITE
        finally:
            os.close(write_fd)
            reader.close()


def test_cli_outcomes_have_distinct_dispositions(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    args = Namespace(message="m", target_alias="T", route_registry="r", journal="j")
    codes = {}
    for outcome in DeliveryOutcome:
        monkeypatch.setattr(cli, "deliver_foreground", lambda _outcome=outcome, **_kwargs: _outcome)
        codes[outcome] = cli._supervised_delivery_command(args)
    assert codes == {
        DeliveryOutcome.DELIVERED: 0,
        DeliveryOutcome.FAILED_BEFORE_WRITE: 20,
        DeliveryOutcome.UNCERTAIN: 21,
    }


def test_thread_list_pagination_parameters_are_exact(tmp_path: Path) -> None:
    outcome, _session, written = _run(tmp_path)
    assert outcome is DeliveryOutcome.DELIVERED
    requests = [json.loads(line) for line in written.splitlines()]
    listed = [item for item in requests if item.get("method") == "thread/list"]
    assert [item["params"]["archived"] for item in listed] == [False, False, True]
    assert [item["params"]["cursor"] for item in listed] == [None, "page-2", None]
    expected_common = {
        "cwd": "/synthetic/workspace",
        "limit": 100,
        "sortDirection": "desc",
        "sortKey": "created_at",
        "sourceKinds": ["appServer"],
        "useStateDbOnly": True,
    }
    for item in listed:
        assert set(item["params"]) == {"archived", "cursor", *expected_common}
        assert {key: item["params"][key] for key in expected_common} == expected_common


def test_cursor_cycle_and_transient_sensitive_non_target_fail_privately(tmp_path: Path) -> None:
    lines = delivery._fake_transcript().splitlines()
    second_page = json.loads(lines[2])
    second_page["result"]["nextCursor"] = "page-2"
    second_page["result"]["data"][0]["preview"] = "sk-" + "A" * 32
    lines[2] = delivery._canonical(second_page)
    outcome, _session, written = _run(tmp_path, b"\n".join(lines) + b"\n")
    assert outcome is DeliveryOutcome.FAILED_BEFORE_WRITE
    assert b"sk-" not in written


@pytest.mark.parametrize("backwards", ["", "x" * 1025, 7, False])
def test_malformed_backwards_cursor_fails_closed(tmp_path: Path, backwards: object) -> None:
    lines = delivery._fake_transcript().splitlines()
    page = json.loads(lines[1])
    page["result"]["backwardsCursor"] = backwards
    lines[1] = delivery._canonical(page)
    outcome, _session, _written = _run(tmp_path, b"\n".join(lines) + b"\n")
    assert outcome is DeliveryOutcome.FAILED_BEFORE_WRITE


def test_empty_continuation_and_cross_archive_identity_fail_closed(tmp_path: Path) -> None:
    for mutation in ("empty", "cross-query"):
        root = tmp_path / mutation
        root.mkdir()
        lines = delivery._fake_transcript().splitlines()
        if mutation == "empty":
            page = json.loads(lines[2])
            page["result"] = {"data": [], "nextCursor": "page-3"}
            lines[2] = delivery._canonical(page)
        else:
            active_id = json.loads(lines[2])["result"]["data"][0]["id"]
            archived = json.loads(lines[3])
            archived["result"]["data"][0]["id"] = active_id
            lines[3] = delivery._canonical(archived)
        outcome, _session, _written = _run(root, b"\n".join(lines) + b"\n")
        assert outcome is DeliveryOutcome.FAILED_BEFORE_WRITE


@pytest.mark.parametrize(("field", "value"), [("cwd", "/other"), ("source", "cli")])
def test_non_target_filter_drift_fails_closed(tmp_path: Path, field: str, value: str) -> None:
    lines = delivery._fake_transcript().splitlines()
    page = json.loads(lines[2])
    page["result"]["data"][0][field] = value
    lines[2] = delivery._canonical(page)
    outcome, _session, _written = _run(tmp_path, b"\n".join(lines) + b"\n")
    assert outcome is DeliveryOutcome.FAILED_BEFORE_WRITE


@pytest.mark.parametrize("cwd", ["relative", "/a/../b", "/a/./b", "/a//b", "/a/", "/a\x00b"])
def test_route_cwd_must_be_normalized_absolute(tmp_path: Path, cwd: str) -> None:
    route = tmp_path / "route.json"
    route.write_text(
        json.dumps(
            {
                "aliases": {
                    "SYNTHETIC_TARGET": {
                        "cwd": cwd,
                        "source_kind": "appServer",
                        "thread_id": "synthetic-thread",
                    }
                },
                "version": 2,
            }
        ),
        encoding="ascii",
    )
    route.chmod(0o600)
    with pytest.raises(DeliveryError, match="cwd"):
        delivery._resolve_alias(route, "SYNTHETIC_TARGET")


def test_ambiguous_unknown_source_kind_is_rejected(tmp_path: Path) -> None:
    route = _route(tmp_path)
    data = json.loads(route.read_text(encoding="ascii"))
    data["aliases"]["SYNTHETIC_TARGET"]["source_kind"] = "unknown"
    route.write_text(json.dumps(data), encoding="ascii")
    with pytest.raises(DeliveryError, match="source kind"):
        delivery._resolve_alias(route, "SYNTHETIC_TARGET")


class _FakeStream:
    def __init__(self, *, fail_close: bool = False) -> None:
        self.fail_close = fail_close
        self.closed = False

    def close(self) -> None:
        self.closed = True
        if self.fail_close:
            raise OSError("synthetic close failure")


class _FakeProcess:
    pid = 4242

    def __init__(
        self,
        waits: list[str],
        *,
        fail_stream: bool = False,
        parent_alive: bool = True,
        poll_error: bool = False,
        wait_error: bool = False,
    ) -> None:
        self.stdin = _FakeStream(fail_close=fail_stream)
        self.stdout = _FakeStream()
        self.stderr = _FakeStream()
        self.waits = waits
        self.alive = parent_alive
        self.poll_error = poll_error
        self.wait_error = wait_error

    def poll(self) -> int | None:
        if self.poll_error:
            raise OSError("synthetic poll failure")
        return None if self.alive else 0

    def wait(self, timeout: float) -> int:
        if self.wait_error:
            raise OSError("synthetic wait failure")
        assert 0 <= timeout <= delivery._CLEANUP_SECONDS
        action = self.waits.pop(0) if self.waits else "success"
        if action == "timeout":
            raise subprocess.TimeoutExpired("fake", timeout)
        self.alive = False
        return 0

    def terminate(self) -> None:
        self.alive = False

    def kill(self) -> None:
        self.alive = False


class _FakeStderr:
    def __init__(self, issue: str | None = None, *, alive: bool = False) -> None:
        self.issue = issue
        self.alive = alive
        self.joined = False

    def close(self, timeout: float) -> str | None:
        assert timeout >= 0
        self.joined = True
        return self.issue


def _cleanup_fake(
    monkeypatch,
    *,
    waits: list[str],
    term_stops: bool,
    kill_stops: bool,
    fail_stream: bool = False,
    helper_issue: str | None = None,
    helper_alive: bool = False,
    parent_alive: bool = True,
    group_alive: bool = True,
    probe_failures: int = 0,
    signal_failures: int = 0,
    poll_error: bool = False,
    wait_error: bool = False,
    owned_group: bool = True,
) -> tuple[delivery._CodexProcess, _FakeProcess, list[signal.Signals], _FakeStderr]:
    fake = _FakeProcess(
        waits,
        fail_stream=fail_stream,
        parent_alive=parent_alive,
        poll_error=poll_error,
        wait_error=wait_error,
    )
    helper = _FakeStderr(helper_issue, alive=helper_alive)
    signals: list[signal.Signals] = []
    group = [group_alive]
    probes = [probe_failures]
    failed_signals = [signal_failures]

    def killpg(_pid: int, sig: signal.Signals) -> None:
        if sig == 0:
            if probes[0]:
                probes[0] -= 1
                raise OSError("synthetic probe failure")
            if not group[0]:
                raise ProcessLookupError
            return
        signals.append(sig)
        if failed_signals[0]:
            failed_signals[0] -= 1
            raise OSError("synthetic signal failure")
        if (sig is signal.SIGTERM and term_stops) or (sig is signal.SIGKILL and kill_stops):
            group[0] = False

    monkeypatch.setattr(delivery.os, "killpg", killpg)
    expired = delivery._Deadline(time.monotonic() - 10, 0.01)
    owner = delivery._CodexProcess(object(), expired)
    owner._process = fake  # type: ignore[assignment]
    owner._process_group_id = fake.pid if owned_group else None
    owner._stderr = helper  # type: ignore[assignment]
    owner.__exit__(None, None, None)
    return owner, fake, signals, helper


def test_cleanup_term_success_after_shared_deadline_expiry(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    owner, fake, signals, helper = _cleanup_fake(monkeypatch, waits=["success"], term_stops=True, kill_stops=True)
    assert signals == [signal.SIGTERM]
    assert owner.cleanup_issue is None
    assert not fake.alive and helper.joined
    assert owner.cleanup_actions == [
        "close_stream",
        "close_stream",
        "term_group",
        "term_wait",
        "reap_child",
        "join_stderr",
        "close_stderr",
    ]


def test_cleanup_term_refusal_escalates_to_kill(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    owner, fake, signals, helper = _cleanup_fake(monkeypatch, waits=["success"], term_stops=False, kill_stops=True)
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert owner.cleanup_issue is None
    assert not fake.alive and helper.joined


@pytest.mark.parametrize(
    ("kwargs", "issue"),
    [
        ({"waits": ["success"], "term_stops": True, "kill_stops": True, "fail_stream": True}, "stream_close_failed"),
        (
            {"waits": ["timeout"], "term_stops": False, "kill_stops": False},
            "owned_process_alive+process_group_still_alive+process_reap_timeout",
        ),
        (
            {
                "waits": ["success"],
                "term_stops": True,
                "kill_stops": True,
                "helper_issue": "stderr_helper_alive",
                "helper_alive": True,
            },
            "stderr_helper_alive",
        ),
    ],
)
def test_cleanup_failures_are_categorical_and_not_swallowed(monkeypatch, kwargs: dict[str, object], issue: str) -> None:  # type: ignore[no-untyped-def]
    owner, _fake, _signals, helper = _cleanup_fake(monkeypatch, **kwargs)  # type: ignore[arg-type]
    assert owner.cleanup_issue == issue
    assert helper.joined


def test_cleanup_detects_owned_helper_thread_census_mismatch(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    census = iter((0, 1))
    monkeypatch.setattr(delivery, "_stderr_thread_census", lambda: next(census))
    owner, _fake, _signals, helper = _cleanup_fake(monkeypatch, waits=["success"], term_stops=True, kill_stops=True)
    assert owner.cleanup_issue == "thread_census_mismatch"
    assert helper.joined


def test_cleanup_parent_exit_does_not_hide_live_descendant(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    owner, fake, signals, helper = _cleanup_fake(
        monkeypatch,
        waits=["success"],
        term_stops=True,
        kill_stops=True,
        parent_alive=False,
        group_alive=True,
    )
    assert signals == [signal.SIGTERM]
    assert owner.cleanup_issue is None
    assert not fake.alive and helper.joined


def test_cleanup_descendant_term_refusal_requires_group_kill(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    owner, _fake, signals, helper = _cleanup_fake(
        monkeypatch,
        waits=["success"],
        term_stops=False,
        kill_stops=True,
        parent_alive=False,
        group_alive=True,
    )
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert owner.cleanup_issue is None
    assert helper.joined


def test_cleanup_probe_failure_is_resolved_only_by_exact_group_absence(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    owner, _fake, signals, helper = _cleanup_fake(
        monkeypatch,
        waits=["success"],
        term_stops=True,
        kill_stops=True,
        probe_failures=1,
    )
    assert signals == [signal.SIGTERM]
    assert owner.cleanup_issue is None
    assert helper.joined


def test_cleanup_unresolved_probe_and_signal_failures_remain_categorical(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(delivery, "_CLEANUP_SECONDS", 0.05)
    monkeypatch.setattr(delivery, "_CLEANUP_TAIL_SECONDS", 0.01)
    owner, _fake, signals, helper = _cleanup_fake(
        monkeypatch,
        waits=[],
        term_stops=False,
        kill_stops=False,
        probe_failures=100,
        signal_failures=2,
    )
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert owner.cleanup_issue is not None
    assert "process_group_probe_failed" in owner.cleanup_issue
    assert "process_group_signal_failed" in owner.cleanup_issue
    assert "process_group_still_alive" in owner.cleanup_issue
    assert helper.joined


def test_cleanup_poll_and_wait_errors_are_categorical(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    owner, _fake, signals, helper = _cleanup_fake(
        monkeypatch,
        waits=[],
        term_stops=True,
        kill_stops=True,
        poll_error=True,
        wait_error=True,
    )
    assert signals == [signal.SIGTERM]
    assert owner.cleanup_issue == "process_poll_failed+process_wait_failed"
    assert helper.joined


def test_cleanup_missing_group_identity_uses_direct_child_fallback(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    owner, fake, signals, helper = _cleanup_fake(
        monkeypatch,
        waits=["success"],
        term_stops=False,
        kill_stops=False,
        owned_group=False,
    )
    assert signals == []
    assert not fake.alive and helper.joined
    assert owner.cleanup_issue == "process_group_identity_missing"
    assert "terminate_unverified_child" in owner.cleanup_actions


def _real_owned_cleanup(
    command: list[str], *, wait_for_ready: bool = False, wait_for_unreaped_exit: bool = False
) -> tuple[delivery._CodexProcess, subprocess.Popen[bytes], float]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        start_new_session=True,
    )
    if wait_for_ready:
        assert process.stdout is not None
        readable, _, _ = select.select([process.stdout], [], [], 2)
        assert readable and process.stdout.readline() == b"ready\n"
    if wait_for_unreaped_exit:
        deadline = time.monotonic() + 2
        while True:
            try:
                os.killpg(process.pid, 0)
            except PermissionError:
                break
            except ProcessLookupError:
                pytest.fail("Darwin removed the child group before Popen reaped it")
            if time.monotonic() >= deadline:
                pytest.fail("child did not reach the unreaped Darwin zombie state")
            time.sleep(0.01)
    owner = delivery._CodexProcess(object(), delivery._Deadline(time.monotonic() - 1, 0.01))
    owner._process = process
    owner._process_group_id = process.pid
    assert process.stderr is not None
    owner._stderr = delivery._StderrScanner(
        process.stderr, delivery._Deadline.start(session_seconds=5, operation_seconds=1)
    )
    owner._stderr.start()
    started = time.monotonic()
    owner.__exit__(None, None, None)
    return owner, process, time.monotonic() - started


def test_real_darwin_child_is_reaped_before_group_absence() -> None:
    baseline = delivery._stderr_thread_census()
    owner, process, elapsed = _real_owned_cleanup(["/bin/sh", "-c", "sleep 30"])
    assert owner.cleanup_issue is None
    assert process.returncode == -signal.SIGTERM
    assert elapsed < delivery._CLEANUP_SECONDS
    assert delivery._stderr_thread_census() == baseline
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin reports EPERM for a zombie-only process group")
def test_real_darwin_already_exited_unreaped_child_resolves_probe_and_signal_ambiguity() -> None:
    baseline = delivery._stderr_thread_census()
    owner, process, elapsed = _real_owned_cleanup(["/bin/sh", "-c", "exit 0"], wait_for_unreaped_exit=True)
    assert owner.cleanup_issue is None
    assert process.returncode == 0
    assert owner.cleanup_actions[2:4] == ["term_group", "term_wait"]
    assert elapsed < delivery._CLEANUP_SECONDS
    assert delivery._stderr_thread_census() == baseline
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)


def test_real_darwin_descendant_survives_term_then_is_killed_and_reaped() -> None:
    program = (
        "import os,signal,time\n"
        "read_fd,write_fd=os.pipe()\n"
        "child=os.fork()\n"
        "if child == 0:\n"
        " os.close(read_fd)\n"
        " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        " os.write(write_fd,b'1')\n"
        " os.close(write_fd)\n"
        " time.sleep(30)\n"
        "else:\n"
        " os.close(write_fd)\n"
        " os.read(read_fd,1)\n"
        " os.close(read_fd)\n"
        " print('ready',flush=True)\n"
        " time.sleep(30)\n"
    )
    baseline = delivery._stderr_thread_census()
    owner, process, elapsed = _real_owned_cleanup(["/usr/bin/python3", "-c", program], wait_for_ready=True)
    assert owner.cleanup_issue is None
    assert process.returncode == -signal.SIGTERM
    assert "kill_group" in owner.cleanup_actions
    assert elapsed < delivery._CLEANUP_SECONDS
    assert delivery._stderr_thread_census() == baseline
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)


def _sleeping_executable(tmp_path: Path) -> Path:
    executable = tmp_path / "synthetic-codex"
    executable.write_text("#!/bin/sh\nexec /bin/sleep 30\n", encoding="ascii")
    executable.chmod(0o700)
    return executable


def test_post_spawn_deadline_error_runs_bounded_cleanup(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    processes: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def capture(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = real_popen(*args, **kwargs)  # type: ignore[arg-type]
        processes.append(process)
        return process

    monkeypatch.setattr(delivery.subprocess, "Popen", capture)
    authority = delivery._seal_executable(_sleeping_executable(tmp_path))
    owner = delivery._CodexProcess(authority, delivery._Deadline(time.monotonic() - 1, 0.01))
    with pytest.raises(DeliveryError, match="process spawn exceeded the session deadline"):
        owner.__enter__()
    assert len(processes) == 1 and processes[0].returncode == -signal.SIGTERM
    assert owner.cleanup_issue is None
    assert "term_group" in owner.cleanup_actions and "reap_child" in owner.cleanup_actions


def test_post_spawn_stderr_thread_start_error_runs_bounded_cleanup(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    processes: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def capture(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = real_popen(*args, **kwargs)  # type: ignore[arg-type]
        processes.append(process)
        return process

    monkeypatch.setattr(delivery.subprocess, "Popen", capture)
    monkeypatch.setattr(
        delivery._StderrScanner, "start", lambda _self: (_ for _ in ()).throw(RuntimeError("synthetic"))
    )
    authority = delivery._seal_executable(_sleeping_executable(tmp_path))
    owner = delivery._CodexProcess(authority, delivery._Deadline.start(session_seconds=5, operation_seconds=1))
    with pytest.raises(RuntimeError, match="synthetic"):
        owner.__enter__()
    assert len(processes) == 1 and processes[0].returncode == -signal.SIGTERM
    assert owner.cleanup_issue == "stderr_helper_close_failed"
    assert "term_group" in owner.cleanup_actions and "reap_child" in owner.cleanup_actions


def test_cleanup_failure_downgrades_apparent_delivery_to_terminal_uncertain(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    writer = io.BytesIO()

    class FailingCleanupContext:
        cleanup_issue = "owned_process_alive"

        def __enter__(self) -> tuple[io.BytesIO, io.BytesIO, object]:
            return io.BytesIO(delivery._fake_transcript()), writer, lambda: None

        def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
            return None

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("production Popen reached")

    monkeypatch.setattr(delivery.subprocess, "Popen", forbidden)
    monkeypatch.setattr(delivery, "_CodexProcess", lambda _authority, _deadline: FailingCleanupContext())
    preview = DeliveryPreview("SYNTHETIC_TARGET", b"synthetic supervised delivery")
    journal = tmp_path / "journal.jsonl"
    outcome, _session = delivery._perform(
        preview=preview,
        capability=_confirmed(preview),
        route_registry=_route(tmp_path),
        journal_path=journal,
        executable_authority=object(),
        attempt_id="0" * 32,
    )
    assert outcome is DeliveryOutcome.UNCERTAIN
    assert [json.loads(line)["event"] for line in journal.read_bytes().splitlines()] == [
        "ATTEMPT_STARTED",
        "UNCERTAIN",
    ]


def test_execution_result_preflight_uses_production_vocabulary() -> None:
    base = {
        "status": "completed",
        "summary": "D8_SUPERVISED_DELIVERY_STABLE_ELIGIBILITY_RESULT: PASS_PENDING_PLANNER_REVIEW",
        "evidence": [],
        "changed_files": [],
        "tests": [],
        "commands": [],
        "commit": None,
        "blockers": [],
        "questions": [],
        "remaining_uncertainty": [],
        "recommended_next_action": "planner review",
    }
    assert delivery.preflight_qualification_result(base).status.value == "completed"
    invalid = dict(base)
    invalid["status"] = "PASS_PENDING_PLANNER_REVIEW"
    with pytest.raises(DeliveryError, match="controller result envelope"):
        delivery.preflight_qualification_result(invalid)


def test_pinned_sequence28_schema_digest_literals_are_change_detector_pins() -> None:
    expected = {
        "v2/ThreadListParams.json": "b227bb78acf9b91060d03c56d3f2072cdd9f1bd08290c11e8869f1a663b16da2",
        "v2/ThreadListResponse.json": "d12dce8505f06cb53404bdac3cbfffbb64f8808ff48556f7d09996f2198e0719",
        "v2/ThreadReadResponse.json": "96017b5053c54ccddd8f8a1d8a07fb850e88bd761ad9e17fc9cb0b82a6870fe8",
        "v2/ThreadResumeResponse.json": "32fc20f4853f89bcee82dba6065751e0b08c104cf6a5c51f9c1aa658d1ce9154",
        "v2/TurnStartResponse.json": "1203962cc16ebf6e1474935a979e07bb054afb9b47060cafb5f4674e56a589d2",
        "v2/ThreadStatusChangedNotification.json": ("26f3c60c1b73f7fa2d31c74429cdc36f8746c76c33e3d314b3fb61d3661f05f6"),
    }
    assert {key: delivery.PROTOCOL_PROFILE.schema_sha256[key] for key in expected} == expected


@pytest.mark.parametrize("name", sorted(delivery.PROTOCOL_PROFILE.schema_sha256))
def test_schema_hash_verifier_rejects_each_wrong_digest(name: str) -> None:
    actual = dict(delivery.PROTOCOL_PROFILE.schema_sha256)
    actual[name] = "0" * 64
    with pytest.raises(DeliveryError, match="schema bundle"):
        delivery._verify_schema_hashes(actual)


def test_production_identity_rejects_executable_hash_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    authority = object()
    changed = delivery._ExecutableRecord(
        path=delivery._CODEX_EXECUTABLE,
        device=1,
        inode=2,
        size=3,
        mode=0o100755,
        uid=501,
        mtime_ns=4,
        sha256="0" * 64,
    )
    monkeypatch.setattr(delivery, "_validate_executable", lambda selected: changed if selected is authority else None)
    with pytest.raises(DeliveryError, match="executable hash"):
        delivery._require_production_identity(authority)


def test_fixed_identity_probe_is_allowlisted_owned_and_reaped(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    class Process:
        pid = 123
        returncode = 0

        def communicate(self, *, timeout: int) -> tuple[bytes, bytes]:
            assert timeout == 10
            return b"codex-cli 0.149.0-alpha.4.3\n", b""

    def popen(argv: tuple[str, ...], **kwargs: object) -> Process:
        observed["argv"] = argv
        observed.update(kwargs)
        return Process()

    monkeypatch.setattr(delivery.subprocess, "Popen", popen)
    stdout, stderr = delivery._run_fixed_identity_probe((str(delivery._CODEX_EXECUTABLE), "--version"))
    assert stdout == b"codex-cli 0.149.0-alpha.4.3\n"
    assert stderr == b""
    assert observed["close_fds"] is True
    assert observed["start_new_session"] is True
    with pytest.raises(DeliveryError, match="allowlist"):
        delivery._run_fixed_identity_probe(("/usr/bin/true",))


def test_fixed_identity_probe_timeout_kills_group_and_reaps(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    class Process:
        pid = 456
        returncode = None
        attempts = 0

        def communicate(self, *, timeout: int) -> tuple[bytes, bytes]:
            self.attempts += 1
            calls.append(("communicate", timeout))
            if self.attempts == 1:
                raise subprocess.TimeoutExpired("probe", timeout)
            self.returncode = -signal.SIGKILL
            return b"", b""

    process = Process()
    monkeypatch.setattr(delivery.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(delivery.os, "killpg", lambda pid, sig: calls.append(("killpg", pid, sig)))
    with pytest.raises(DeliveryError, match="did not complete"):
        delivery._run_fixed_identity_probe((str(delivery._CODEX_EXECUTABLE), "--version"))
    assert calls == [
        ("communicate", 10),
        ("killpg", 456, signal.SIGKILL),
        ("communicate", 3),
    ]


def test_fixed_identity_probe_group_signal_failure_still_reaps_direct_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class Process:
        pid = 789
        returncode = None
        attempts = 0

        def communicate(self, *, timeout: int) -> tuple[bytes, bytes]:
            self.attempts += 1
            calls.append(("communicate", timeout))
            if self.attempts == 1:
                raise subprocess.TimeoutExpired("probe", timeout)
            self.returncode = -signal.SIGKILL
            return b"", b""

        def kill(self) -> None:
            calls.append(("kill", self.pid))

    process = Process()
    monkeypatch.setattr(delivery.subprocess, "Popen", lambda *_args, **_kwargs: process)

    def denied(pid: int, sig: signal.Signals) -> None:
        calls.append(("killpg", pid, sig))
        raise PermissionError("synthetic group signal denial")

    monkeypatch.setattr(delivery.os, "killpg", denied)
    with pytest.raises(DeliveryError, match="direct child reaped"):
        delivery._run_fixed_identity_probe((str(delivery._CODEX_EXECUTABLE), "--version"))
    assert calls == [
        ("communicate", 10),
        ("killpg", 789, signal.SIGKILL),
        ("kill", 789),
        ("communicate", 3),
    ]


def test_production_identity_rejects_old_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    authority = object()
    record = delivery._ExecutableRecord(
        path=delivery._CODEX_EXECUTABLE,
        device=1,
        inode=2,
        size=3,
        mode=0o100755,
        uid=501,
        mtime_ns=4,
        sha256=delivery._EXPECTED_CODEX_SHA256,
    )
    monkeypatch.setattr(delivery, "_validate_executable", lambda selected: record if selected is authority else None)
    monkeypatch.setattr(
        delivery,
        "_inspect_desktop_identity",
        lambda: delivery._DesktopIdentity(
            cli_version="codex-cli 0.146.0",
            authority=delivery._EXPECTED_CODEX_AUTHORITY,
            team_id=delivery._EXPECTED_CODEX_TEAM_ID,
            app_version=delivery._EXPECTED_CODEX_APP_VERSION,
            app_build=delivery._EXPECTED_CODEX_APP_BUILD,
        ),
    )
    with pytest.raises(DeliveryError, match="Desktop identity"):
        delivery._require_production_identity(authority)


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("authority", "Developer ID Application: Untrusted (2DC432GLL2)"),
        ("team_id", "UNTRUSTED1"),
        ("app_version", "0.0.0"),
        ("app_build", "0"),
    ],
)
def test_production_identity_rejects_signer_or_app_drift(
    monkeypatch: pytest.MonkeyPatch, field: str, wrong: str
) -> None:
    authority = object()
    record = delivery._ExecutableRecord(
        path=delivery._CODEX_EXECUTABLE,
        device=1,
        inode=2,
        size=3,
        mode=0o100755,
        uid=501,
        mtime_ns=4,
        sha256=delivery._EXPECTED_CODEX_SHA256,
    )
    monkeypatch.setattr(delivery, "_validate_executable", lambda selected: record if selected is authority else None)
    values = {
        "cli_version": delivery._EXPECTED_USER_AGENT,
        "authority": delivery._EXPECTED_CODEX_AUTHORITY,
        "team_id": delivery._EXPECTED_CODEX_TEAM_ID,
        "app_version": delivery._EXPECTED_CODEX_APP_VERSION,
        "app_build": delivery._EXPECTED_CODEX_APP_BUILD,
    }
    values[field] = wrong
    monkeypatch.setattr(delivery, "_inspect_desktop_identity", lambda: delivery._DesktopIdentity(**values))
    with pytest.raises(DeliveryError, match="Desktop identity"):
        delivery._require_production_identity(authority)


def test_production_identity_failure_precedes_human_challenge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    message = tmp_path / "message.txt"
    message.write_bytes(b"synthetic supervised delivery")
    message.chmod(0o600)

    def rejected() -> object:
        raise DeliveryError("production identity rejected")

    def challenge_forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("human challenge reached before production identity validation")

    monkeypatch.setattr(delivery, "_require_interactive_tty", lambda *_args: None)
    monkeypatch.setattr(delivery, "_seal_production_executable", rejected)
    monkeypatch.setattr(delivery, "confirm_preview", challenge_forbidden)
    with pytest.raises(DeliveryError, match="production identity rejected"):
        delivery.deliver_foreground(
            message_path=message,
            target_alias="SYNTHETIC_TARGET",
            route_registry=tmp_path / "routes.json",
            journal_path=tmp_path / "journal.jsonl",
            input_stream=io.StringIO(),
            output_stream=io.StringIO(),
        )


def test_thread_accepts_current_project_and_section_shape() -> None:
    route = delivery._RouteAuthority("synthetic-thread", "appServer", "/synthetic/workspace")
    thread = json.loads(delivery._fake_transcript().splitlines()[1])["result"]["data"][0]
    thread["section"] = {
        "id": "section-1",
        "name": "Main",
        "appearance": {"color": None, "icon": "circle"},
    }
    thread["sectionEnteredAt"] = None
    assert delivery._JsonlSession._thread(thread, route) == "idle"


def test_thread_accepts_nullable_section_and_appearance() -> None:
    route = delivery._RouteAuthority("synthetic-thread", "appServer", "/synthetic/workspace")
    thread = json.loads(delivery._fake_transcript().splitlines()[1])["result"]["data"][0]
    assert thread["section"] is None
    assert delivery._JsonlSession._thread(thread, route) == "idle"
    thread["section"] = {"id": "section-1", "name": "Main", "appearance": None}
    assert delivery._JsonlSession._thread(thread, route) == "idle"


@pytest.mark.parametrize("field", ["projectId", "section"])
def test_thread_rejects_invalid_current_consumed_fields(field: str) -> None:
    route = delivery._RouteAuthority("synthetic-thread", "appServer", "/synthetic/workspace")
    thread = json.loads(delivery._fake_transcript().splitlines()[1])["result"]["data"][0]
    thread[field] = 3 if field == "projectId" else {"id": "section-1", "name": 3}
    with pytest.raises(DeliveryError):
        delivery._JsonlSession._thread(thread, route)


@pytest.mark.parametrize(
    "mutation",
    ["project_removed", "project_renamed", "status_widened", "section_ambiguous"],
)
def test_thread_rejects_consumed_field_removal_rename_widening_or_ambiguity(mutation: str) -> None:
    route = delivery._RouteAuthority("synthetic-thread", "appServer", "/synthetic/workspace")
    thread = json.loads(delivery._fake_transcript().splitlines()[1])["result"]["data"][0]
    if mutation == "project_removed":
        del thread["projectId"]
    elif mutation == "project_renamed":
        thread["project_id"] = thread.pop("projectId")
    elif mutation == "status_widened":
        thread["status"] = {"type": "future"}
    else:
        thread["section"] = {"id": "section-1", "name": "Main", "unexpected": True}
    with pytest.raises(DeliveryError):
        delivery._JsonlSession._thread(thread, route)


@pytest.mark.parametrize("will_retry", [False, True])
def test_error_notification_exact_shape_always_fails_without_retry(will_retry: bool) -> None:
    values = {
        "error": {"message": "synthetic"},
        "threadId": "thread-1",
        "turnId": "turn-1",
        "willRetry": will_retry,
    }
    with pytest.raises(DeliveryError, match="app-server reported a turn error"):
        delivery._JsonlSession._reject_error_notification(values, "thread-1", "turn-1")


@pytest.mark.parametrize(
    "mutation",
    ["missing", "extra", "renamed", "widened", "wrong_thread", "wrong_turn"],
)
def test_error_notification_rejects_shape_or_identity_drift(mutation: str) -> None:
    values: dict[str, object] = {
        "error": {"message": "synthetic"},
        "threadId": "thread-1",
        "turnId": "turn-1",
        "willRetry": False,
    }
    if mutation == "missing":
        del values["error"]
    elif mutation == "extra":
        values["extra"] = True
    elif mutation == "renamed":
        values["turn_id"] = values.pop("turnId")
    elif mutation == "widened":
        values["willRetry"] = "false"
    elif mutation == "wrong_thread":
        values["threadId"] = "thread-2"
    else:
        values["turnId"] = "turn-2"
    with pytest.raises(DeliveryError, match="selected schema"):
        delivery._JsonlSession._reject_error_notification(values, "thread-1", "turn-1")


@pytest.mark.parametrize(
    ("insert_at", "expected"),
    [
        (6, DeliveryOutcome.FAILED_BEFORE_WRITE),
        (7, DeliveryOutcome.DELIVERED),
        (8, DeliveryOutcome.DELIVERED),
    ],
)
def test_status_change_race_fails_on_the_correct_side_of_attempt(
    tmp_path: Path, insert_at: int, expected: DeliveryOutcome
) -> None:
    lines = delivery._fake_transcript().splitlines()
    notification = delivery._canonical(
        {
            "jsonrpc": "2.0",
            "method": "thread/status/changed",
            "params": {"status": {"activeFlags": [], "type": "active"}, "threadId": "synthetic-thread"},
        }
    )
    lines.insert(insert_at, notification)
    outcome, _session, _written = _run(tmp_path, b"\n".join(lines) + b"\n")
    assert outcome is expected


@pytest.mark.parametrize(
    "mutation", ["system_error", "wrong_target", "malformed", "unhashable_type", "unhashable_flag"]
)
def test_post_write_ambiguous_status_notifications_are_terminal_uncertain(tmp_path: Path, mutation: str) -> None:
    lines = delivery._fake_transcript().splitlines()
    params: dict[str, object] = {"status": {"activeFlags": [], "type": "active"}, "threadId": "synthetic-thread"}
    if mutation == "system_error":
        params["status"] = {"type": "systemError"}
    elif mutation == "wrong_target":
        params["threadId"] = "other-thread"
    elif mutation == "unhashable_type":
        params["status"] = {"type": []}
    elif mutation == "unhashable_flag":
        params["status"] = {"activeFlags": [[]], "type": "active"}
    else:
        params["status"] = {"type": "active", "unexpected": True}
    lines.insert(7, delivery._canonical({"jsonrpc": "2.0", "method": "thread/status/changed", "params": params}))
    outcome, _session, _written = _run(tmp_path, b"\n".join(lines) + b"\n")
    assert outcome is DeliveryOutcome.UNCERTAIN


@pytest.mark.parametrize(
    "status",
    [
        {"type": []},
        {"type": {}},
        {"activeFlags": [[]], "type": "active"},
        {"activeFlags": [{}], "type": "active"},
    ],
)
def test_thread_status_unhashable_type_drift_is_categorical(status: object) -> None:
    with pytest.raises(delivery.DeliveryError, match="thread status violates the stable schema"):
        delivery._JsonlSession._status_value(status)


def test_preview_escapes_terminal_control_sequences() -> None:
    rendered = DeliveryPreview("SYNTHETIC_TARGET", b"safe\x1b[2J\r\n").render()
    assert "\x1b" not in rendered
    assert "\\u001b[2J\\r\\n" in rendered


@pytest.mark.parametrize(
    "mutator",
    [
        lambda lines: lines.__setitem__(0, b'{"id":99,"jsonrpc":"2.0","result":{}}'),
        lambda lines: lines.__setitem__(1, b'{"jsonrpc":"2.0","method":"approval","params":{}}'),
        lambda lines: lines.__setitem__(-1, b'{"jsonrpc":"2.0","method":"unknown","params":{}}'),
        lambda lines: lines.__setitem__(-1, lines[-1][:-1]),
    ],
)
def test_protocol_mutations_fail_closed(tmp_path: Path, mutator) -> None:  # type: ignore[no-untyped-def]
    lines = delivery._fake_transcript().splitlines()
    mutator(lines)
    transcript = b"\n".join(lines) + b"\n"
    outcome, _session, _written = _run(tmp_path, transcript)
    assert outcome in {DeliveryOutcome.FAILED_BEFORE_WRITE, DeliveryOutcome.UNCERTAIN}


def test_crash_after_attempt_start_recovers_as_terminal_uncertain(tmp_path: Path) -> None:
    preview = DeliveryPreview("SYNTHETIC_TARGET", b"payload")
    journal_path = tmp_path / "journal.jsonl"
    with delivery._Journal(journal_path) as journal:
        journal.append(
            event="ATTEMPT_STARTED",
            attempt_id="a" * 32,
            preview=preview,
            thread_id="synthetic-thread",
        )
    with delivery._Journal(journal_path) as journal:
        assert (
            journal.terminal_for(payload_sha256=preview.payload_sha256, target_alias=preview.target_alias)
            is DeliveryOutcome.UNCERTAIN
        )

    writer = io.BytesIO()
    with pytest.raises(DeliveryError, match="terminal: UNCERTAIN"):
        delivery._perform(
            preview=preview,
            capability=_confirmed(preview),
            route_registry=_route(tmp_path),
            journal_path=journal_path,
            streams=(io.BytesIO(delivery._fake_transcript()), writer),
        )
    assert writer.getvalue() == b""


def test_pre_write_failure_allows_only_fresh_confirmed_retry(tmp_path: Path) -> None:
    preview = DeliveryPreview("SYNTHETIC_TARGET", b"synthetic supervised delivery")
    route = _route(tmp_path)
    journal = tmp_path / "journal.jsonl"
    outcome, _session = delivery._perform(
        preview=preview,
        capability=_confirmed(preview),
        route_registry=route,
        journal_path=journal,
        streams=(io.BytesIO(b'{"id":99,"jsonrpc":"2.0","result":{}}\n'), io.BytesIO()),
    )
    assert outcome is DeliveryOutcome.FAILED_BEFORE_WRITE
    outcome, _session = delivery._perform(
        preview=preview,
        capability=_confirmed(preview),
        route_registry=route,
        journal_path=journal,
        streams=(io.BytesIO(delivery._fake_transcript()), io.BytesIO()),
    )
    assert outcome is DeliveryOutcome.DELIVERED


def test_attempt_start_is_durable_before_turn_start_write(tmp_path: Path) -> None:
    preview = DeliveryPreview("SYNTHETIC_TARGET", b"synthetic supervised delivery")
    journal_path = tmp_path / "journal.jsonl"

    class InspectingWriter(io.BytesIO):
        def write(self, value: bytes) -> int:
            message = json.loads(value)
            if message.get("method") == "turn/start":
                records = [json.loads(line) for line in journal_path.read_bytes().splitlines()]
                assert [record["event"] for record in records] == ["ATTEMPT_STARTED"]
            return super().write(value)

    outcome, _session = delivery._perform(
        preview=preview,
        capability=_confirmed(preview),
        route_registry=_route(tmp_path),
        journal_path=journal_path,
        streams=(io.BytesIO(delivery._fake_transcript()), InspectingWriter()),
    )
    assert outcome is DeliveryOutcome.DELIVERED


def test_partial_or_corrupt_journal_fails_closed(tmp_path: Path) -> None:
    partial = tmp_path / "partial.jsonl"
    partial.write_bytes(b'{"event":"ATTEMPT_STARTED"}')
    partial.chmod(0o600)
    with pytest.raises(DeliveryError, match="partial"), delivery._Journal(partial):
        pass
    corrupt = tmp_path / "corrupt.jsonl"
    corrupt.write_bytes(b'{"attempt_id":"x"}\n')
    corrupt.chmod(0o600)
    with pytest.raises(DeliveryError), delivery._Journal(corrupt):
        pass

    valid_root = tmp_path / "valid"
    valid_root.mkdir()
    _run(valid_root)
    valid = valid_root / "journal.jsonl"
    records = [json.loads(line) for line in valid.read_bytes().splitlines()]
    records[0]["record_sha256"] = "0" * 64
    invalid_hash = tmp_path / "invalid-hash.jsonl"
    invalid_hash.write_bytes(b"".join(delivery._canonical(record) + b"\n" for record in records))
    invalid_hash.chmod(0o600)
    with pytest.raises(DeliveryError, match="hash chain"), delivery._Journal(invalid_hash):
        pass


def test_alias_registry_rejects_symlink_and_unsafe_mode(tmp_path: Path) -> None:
    route = _route(tmp_path)
    route.chmod(0o666)
    with pytest.raises(DeliveryError, match="ownership or mode"):
        delivery._resolve_alias(route, "SYNTHETIC_TARGET")
    route.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(route)
    with pytest.raises(DeliveryError, match="cannot be opened safely"):
        delivery._resolve_alias(link, "SYNTHETIC_TARGET")


def test_fake_qualification_never_follows_or_overwrites_route_symlink(tmp_path: Path) -> None:
    root = tmp_path / "qualification"
    root.mkdir(mode=0o700)
    victim = tmp_path / "victim.txt"
    victim.write_text("preserve", encoding="ascii")
    (root / "routes.json").symlink_to(victim)
    with pytest.raises(DeliveryError, match="new owned file"):
        delivery._qualify_fake_peer(root)
    assert victim.read_text(encoding="ascii") == "preserve"
