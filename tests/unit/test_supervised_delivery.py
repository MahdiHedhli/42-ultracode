from __future__ import annotations

import io
import json
import os
import pty
import re
import select
import threading
import time
from pathlib import Path

import pytest

import ultracode.supervised_delivery as delivery
from ultracode.supervised_delivery import DeliveryError, DeliveryOutcome, DeliveryPreview


def _route(root: Path) -> Path:
    path = root / "routes.json"
    path.write_text('{"aliases":{"SYNTHETIC_TARGET":"synthetic-thread"},"version":1}', encoding="ascii")
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
    assert delivery.APP_SERVER_ARGV == ("codex", "app-server", "--listen", "stdio://")
    assert delivery.PROTOCOL_PROFILE.request_methods == {
        "initialize",
        "thread/read",
        "thread/resume",
        "turn/start",
    }
    assert delivery.PROTOCOL_PROFILE.client_notifications == {"initialized"}
    assert delivery.PROTOCOL_PROFILE.server_notifications == {"turn/started", "turn/completed", "error"}
    assert "experimental" not in " ".join(delivery.APP_SERVER_ARGV)


def test_twenty_deterministic_fake_peer_reconstructions(tmp_path: Path) -> None:
    results = []
    for index in range(20):
        result = delivery._qualify_fake_peer(tmp_path / f"run-{index:02d}")
        assert result["outcome"] == "DELIVERED"
        assert result["request_methods"] == ["initialize", "thread/read", "thread/resume", "turn/start"]
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
