from __future__ import annotations

import io
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

import ultracode.route_discovery as discovery
import ultracode.supervised_delivery as delivery


def _discovery_transcript(*, thread: dict[str, object] | None = None) -> bytes:
    base = json.loads(delivery._fake_transcript().splitlines()[1])["result"]["data"][0]
    selected = dict(base if thread is None else thread)
    return (
        b"\n".join(
            (
                delivery._canonical(
                    {
                        "id": 1,
                        "jsonrpc": "2.0",
                        "result": {
                            "codexHome": "/synthetic/codex-home",
                            "platformFamily": "unix",
                            "platformOs": "macos",
                            "userAgent": "codex-cli synthetic",
                        },
                    }
                ),
                delivery._canonical({"id": 2, "jsonrpc": "2.0", "result": {"data": [selected]}}),
                delivery._canonical({"id": 3, "jsonrpc": "2.0", "result": {"data": []}}),
            )
        )
        + b"\n"
    )


def _session(transcript: bytes | None = None) -> tuple[delivery._JsonlSession, io.BytesIO]:
    writer = io.BytesIO()
    session = delivery._JsonlSession(
        io.BytesIO(transcript or _discovery_transcript()),
        writer,
        protocol_profile=delivery.DISCOVERY_PROTOCOL_PROFILE,
    )
    return session, writer


def test_discovery_profile_is_directionally_closed() -> None:
    assert delivery.DISCOVERY_PROTOCOL_PROFILE.request_methods == {"initialize", "thread/list"}
    assert delivery.DISCOVERY_PROTOCOL_PROFILE.client_notifications == {"initialized"}
    assert delivery.DISCOVERY_PROTOCOL_PROFILE.server_notifications == {"thread/status/changed"}
    for method in ("thread/read", "thread/resume", "turn/start", "arbitrary"):
        session, writer = _session()
        with pytest.raises(delivery.DeliveryError, match="outside"):
            session._request(method, {})
        assert writer.getvalue() == b""
        assert session.methods == []


def test_discovery_listing_and_direction_census() -> None:
    session, writer = _session()
    session.initialize()
    listing = session.list_threads()
    assert [entry.thread_id for entry in listing.entries] == ["synthetic-thread"]
    assert listing.rejected_entries == 0
    assert session.methods == ["initialize", "thread/list", "thread/list"]
    assert session.notifications == ["initialized"]
    assert session.inbound_responses == 3
    assert session.inbound_notifications == {}
    requests = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert [item.get("method") for item in requests] == ["initialize", "initialized", "thread/list", "thread/list"]
    for item in requests[2:]:
        assert set(item["params"]) == {
            "archived",
            "cursor",
            "limit",
            "sortDirection",
            "sortKey",
            "useStateDbOnly",
        }


def test_twenty_deterministic_discovery_reconstructions() -> None:
    observations: list[tuple[object, ...]] = []
    for _iteration in range(20):
        session, writer = _session()
        session.initialize()
        listing = session.list_threads()
        observations.append(
            (
                listing,
                tuple(session.methods),
                tuple(session.notifications),
                session.inbound_responses,
                tuple(sorted(session.inbound_notifications.items())),
                writer.getvalue(),
            )
        )
    assert len(set(observations)) == 1


def test_discovery_entry_composes_with_target_bound_delivery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "_configuration_root", lambda: tmp_path / "config")
    monkeypatch.setattr(discovery, "_data_root", lambda: tmp_path / "data")
    discovery_session, _writer = _session()
    discovery_session.initialize()
    listing = discovery_session.list_threads()
    assert len(listing.entries) == 1
    files = discovery._publish_route(listing.entries[0], "f017-d8-pilot-sequence38.json")
    route = delivery._resolve_alias(files.registry, "F017_D8_PILOT_TARGET")
    app_writer = io.BytesIO()
    delivery_session = delivery._JsonlSession(io.BytesIO(delivery._fake_transcript()), app_writer)
    delivery_session.prepare(route)
    assert delivery_session.methods == [
        "initialize",
        "thread/list",
        "thread/list",
        "thread/list",
        "thread/read",
        "thread/resume",
        "thread/read",
    ]
    assert b'"method":"turn/start"' not in app_writer.getvalue()


def _selection(
    listing: delivery.ThreadListing,
    answer: bytes,
    monkeypatch: pytest.MonkeyPatch,
    *,
    timeout: bool = False,
) -> tuple[delivery.ThreadListingEntry | None, str]:
    read_fd, write_fd = os.pipe()
    try:
        if answer:
            os.write(write_fd, answer)
        os.close(write_fd)
        write_fd = -1
        with os.fdopen(read_fd, "r", encoding="utf-8") as reader:
            read_fd = -1
            rendered = io.StringIO()
            monkeypatch.setattr(discovery, "_revalidate_tty", lambda *_args: None)
            if timeout:
                monkeypatch.setattr(discovery.select, "select", lambda *_args: ([], [], []))
            selected = discovery._present_and_select(
                listing,
                reader=reader,
                writer=rendered,
                tty_fd=reader.fileno(),
                identity=object(),  # type: ignore[arg-type]
            )
            return selected, rendered.getvalue()
    finally:
        if read_fd >= 0:
            os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def test_foreground_selection_and_abort_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    session, _writer = _session()
    session.initialize()
    listing = session.list_threads()

    selected, rendered = _selection(listing, b"1\n", monkeypatch)
    assert selected == listing.entries[0]
    assert "Select the intended Codex task:" in rendered
    assert "synthetic-th" in rendered

    for answer in (b"q\n", b"", b"bad\n0\n2\n"):
        selected, _rendered = _selection(listing, answer, monkeypatch)
        assert selected is None

    selected, _rendered = _selection(listing, b"", monkeypatch, timeout=True)
    assert selected is None

    selected, rendered = _selection(replace(listing, entries=()), b"", monkeypatch)
    assert selected is None
    assert "No eligible Codex tasks" in rendered


def test_valid_unrelated_notification_is_ignored_and_counted() -> None:
    lines = _discovery_transcript().splitlines()
    lines.insert(
        1,
        delivery._canonical(
            {
                "jsonrpc": "2.0",
                "method": "thread/status/changed",
                "params": {"status": {"type": "idle"}, "threadId": "unrelated-thread"},
            }
        ),
    )
    session, _writer = _session(b"\n".join(lines) + b"\n")
    session.initialize()
    assert len(session.list_threads().entries) == 1
    assert session.inbound_notifications == {"thread/status/changed": 1}


def test_server_request_fails_closed() -> None:
    lines = _discovery_transcript().splitlines()
    lines.insert(
        1,
        delivery._canonical(
            {"id": 99, "jsonrpc": "2.0", "method": "thread/status/changed", "params": {}},
        ),
    )
    session, _writer = _session(b"\n".join(lines) + b"\n")
    session.initialize()
    with pytest.raises(delivery.DeliveryError, match="server requests"):
        session.list_threads()
    assert session.inbound_server_requests == 1


def test_malformed_active_entry_is_excluded_but_archived_without_identity_fails() -> None:
    active = json.loads(delivery._fake_transcript().splitlines()[1])["result"]["data"][0]
    active["preview"] = 7
    session, _writer = _session(_discovery_transcript(thread=active))
    session.initialize()
    listing = session.list_threads()
    assert listing.entries == ()
    assert listing.rejected_entries == 1

    lines = _discovery_transcript().splitlines()
    archived = json.loads(lines[2])
    archived["result"]["data"] = [{"preview": "missing identity"}]
    lines[2] = delivery._canonical(archived)
    session, _writer = _session(b"\n".join(lines) + b"\n")
    session.initialize()
    with pytest.raises(delivery.DeliveryError, match="safe identity"):
        session.list_threads()


def test_active_archive_overlap_and_duplicate_fail_closed() -> None:
    lines = _discovery_transcript().splitlines()
    active = json.loads(lines[1])["result"]["data"][0]
    archived = json.loads(lines[2])
    archived["result"]["data"] = [active]
    lines[2] = delivery._canonical(archived)
    session, _writer = _session(b"\n".join(lines) + b"\n")
    session.initialize()
    with pytest.raises(delivery.DeliveryError, match="inconsistent"):
        session.list_threads()


def test_ephemeral_presence_tracks_verified_schema() -> None:
    thread = json.loads(delivery._fake_transcript().splitlines()[1])["result"]["data"][0]
    without = dict(thread)
    del without["ephemeral"]
    entry = delivery._JsonlSession._thread_entry(without, ephemeral_field_required=False)
    assert entry.thread_id == "synthetic-thread"
    with pytest.raises(delivery.DeliveryError):
        delivery._JsonlSession._thread_entry(without, ephemeral_field_required=True)
    with pytest.raises(delivery.DeliveryError):
        delivery._JsonlSession._thread_entry(thread, ephemeral_field_required=False)


@pytest.mark.parametrize(
    "text",
    [
        "x\x00y",
        "x\x1by",
        "x\x7fy",
        "x\x85y",
        "x\u200by",
        "x\u200cy",
        "x\u200dy",
        "x\u202ay",
        "x\u202ey",
        "x\u2066y",
        "x\u2069y",
        "x\ufeffy",
        "x\ny",
        "x\ry",
        "x\ty",
        "a\u0301\u0301\u0301\u0301b",
    ],
)
def test_terminal_rendering_escapes_controls(text: str) -> None:
    rendered = discovery._terminal_text(text)
    assert "\x00" not in rendered
    assert "\x1b" not in rendered
    assert "\n" not in rendered
    assert "\r" not in rendered
    assert "\t" not in rendered
    assert not any(
        char in rendered for char in ("\u200b", "\u200c", "\u200d", "\u202a", "\u202e", "\u2066", "\u2069", "\ufeff")
    )


def test_route_publication_is_owner_only_and_resolver_compatible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config"
    data = tmp_path / "data"
    monkeypatch.setattr(discovery, "_configuration_root", lambda: config)
    monkeypatch.setattr(discovery, "_data_root", lambda: data)
    entry = delivery.ThreadListingEntry("synthetic-thread", "appServer", "/synthetic/workspace", "idle", None, "x")
    files = discovery._publish_route(entry, "f017-d8-pilot-sequence38.json")
    discovery._revalidate_route(files)
    route = delivery._resolve_alias(files.registry, "F017_D8_PILOT_TARGET")
    assert (route.thread_id, route.source_kind, route.cwd) == (
        "synthetic-thread",
        "appServer",
        "/synthetic/workspace",
    )
    assert stat_mode(files.root) == 0o700
    assert stat_mode(files.registry) == stat_mode(files.locator) == 0o600
    assert not files.journal.exists()
    assert not files.launch_marker.exists()
    with pytest.raises(FileExistsError):
        discovery._publish_route(entry, "f017-d8-pilot-sequence38.json")


def stat_mode(path: Path) -> int:
    return os.stat(path, follow_symlinks=False).st_mode & 0o777


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "unknown"),
        ("source", 7),
        ("cwd", "relative"),
        ("cwd", "/a/../b"),
        ("cwd", "/a//b"),
        ("cwd", "/a/"),
        ("ephemeral", True),
        ("ephemeral", "false"),
        ("status", {"type": "future"}),
        ("status", {"type": []}),
        ("id", ""),
        ("id", "bad/id"),
        ("preview", 1),
        ("createdAt", "1"),
        ("updatedAt", False),
        ("turns", {}),
        ("projectId", 3),
        ("historyMode", "future"),
        ("section", []),
        ("sectionEnteredAt", "1"),
        ("recencyAt", "1"),
        ("name", 3),
        ("modelProvider", []),
        ("sessionId", {}),
        ("cliVersion", None),
        ("threadSource", 4),
        ("gitInfo", []),
        ("agentRole", 9),
        ("agentNickname", 9),
        ("forkedFromId", 9),
        ("parentThreadId", 9),
        ("path", 9),
    ],
)
def test_listing_field_mutations_are_rejected(field: str, value: object) -> None:
    thread = json.loads(delivery._fake_transcript().splitlines()[1])["result"]["data"][0]
    thread[field] = value
    with pytest.raises(delivery.DeliveryError):
        delivery._JsonlSession._thread_entry(thread, ephemeral_field_required=True)


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_method",
        "missing_jsonrpc",
        "wrong_jsonrpc",
        "missing_params",
        "extra_field",
        "params_not_object",
        "missing_status",
        "missing_thread",
        "extra_param",
        "thread_not_string",
        "thread_empty",
        "thread_oversized",
        "status_not_object",
        "status_unknown",
    ],
)
def test_notification_mutations_fail_closed(mutation: str) -> None:
    message: dict[str, object] = {
        "jsonrpc": "2.0",
        "method": "thread/status/changed",
        "params": {"status": {"type": "idle"}, "threadId": "other"},
    }
    params = message["params"]
    assert isinstance(params, dict)
    if mutation == "unknown_method":
        message["method"] = "unknown"
    elif mutation == "missing_jsonrpc":
        del message["jsonrpc"]
    elif mutation == "wrong_jsonrpc":
        message["jsonrpc"] = "1.0"
    elif mutation == "missing_params":
        del message["params"]
    elif mutation == "extra_field":
        message["extra"] = True
    elif mutation == "params_not_object":
        message["params"] = []
    elif mutation == "missing_status":
        del params["status"]
    elif mutation == "missing_thread":
        del params["threadId"]
    elif mutation == "extra_param":
        params["extra"] = True
    elif mutation == "thread_not_string":
        params["threadId"] = 7
    elif mutation == "thread_empty":
        params["threadId"] = ""
    elif mutation == "thread_oversized":
        params["threadId"] = "x" * 257
    elif mutation == "status_not_object":
        params["status"] = []
    else:
        params["status"] = {"type": "future"}
    session, _writer = _session()
    with pytest.raises(delivery.DeliveryError):
        session._accept_interleaved_notification(message, allow_unrelated_status=True)


def test_sanitized_report_is_closed_and_contains_no_task_data(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    discovery._write_report(
        report,
        outcome=discovery.PilotOutcome.TOOLING_FAILURE_BEFORE_LIST,
        phase="DISCOVERY",
        list_shown=False,
        eligible_count=0,
        session=None,
        rejected_entries=0,
        post_guard="NOT_REACHED",
        failure_class="DISCOVERY_TOOLING",
    )
    data = json.loads(report.read_text())
    assert set(data) == {
        "eligible_count_bucket",
        "failure_class",
        "list_shown",
        "method_counts",
        "notification_count",
        "per_entry_rejection_count",
        "phase_reached",
        "post_terminal_route_digest_guard",
        "schema",
        "status",
    }
    assert stat_mode(report) == 0o600
    assert "synthetic-thread" not in report.read_text()


def test_pilot_exit_codes_are_unique() -> None:
    assert len(set(discovery.PILOT_EXIT_CODES.values())) == len(discovery.PILOT_EXIT_CODES)
    assert discovery.PILOT_EXIT_CODES[discovery.PilotOutcome.DELIVERED] == 0
