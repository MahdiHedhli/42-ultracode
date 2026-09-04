from __future__ import annotations

import ast
import inspect
from pathlib import Path

import ultracode.route_discovery as discovery
import ultracode.supervised_delivery as subject


def test_only_fixed_subprocess_launch_exists() -> None:
    source = Path(inspect.getfile(subject)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    launches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and node.func.attr == "Popen"
    ]
    assert len(launches) == 2
    launch = next(item for item in launches if isinstance(item.args[0], ast.Tuple))
    assert isinstance(launch.args[0], ast.Tuple)
    keywords = {item.arg: item.value for item in launch.keywords}
    assert isinstance(keywords["shell"], ast.Constant) and keywords["shell"].value is False
    assert isinstance(keywords["close_fds"], ast.Constant) and keywords["close_fds"].value is True
    assert isinstance(keywords["start_new_session"], ast.Constant) and keywords["start_new_session"].value is True
    assert isinstance(keywords["env"], ast.Call)


def test_identity_probes_are_fixed_and_run_local() -> None:
    source = Path(inspect.getfile(subject)).read_text(encoding="utf-8")
    assert '(str(_CODEX_EXECUTABLE), "--version")' in source
    assert '("/usr/bin/codesign", "--verify", "--strict", "--verbose=2", str(_CODEX_EXECUTABLE))' in source
    assert '("/usr/bin/codesign", "-dv", "--verbose=4", str(_CODEX_EXECUTABLE))' in source
    assert "start_new_session=True" in source
    assert "os.killpg(process.pid, signal.SIGKILL)" in source
    assert subject._EXPECTED_CODEX_TEAM_ID == "2DC432GLL2"
    assert "_EXPECTED_CODEX_SHA256" not in source
    assert "_EXPECTED_USER_AGENT" not in source
    assert 'component == Path("/Applications")' in source
    assert "not bool(info.st_mode & 0o002)" in source


def test_no_network_browser_or_automatic_delivery_surface() -> None:
    banned_imports = {"aiohttp", "http", "mcp", "requests", "socket", "urllib", "webbrowser"}
    for module in (subject, discovery):
        source = Path(inspect.getfile(module)).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        }
        imported |= {(node.module or "").split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert imported.isdisjoint(banned_imports)
    assert {"daemon", "scheduler", "watch", "retry", "post_message"}.isdisjoint(vars(subject))
    assert {"daemon", "scheduler", "watch", "retry", "post_message"}.isdisjoint(vars(discovery))


def test_protocol_and_operation_allowlists_are_closed() -> None:
    assert subject.PROTOCOL_PROFILE.request_methods == {
        "initialize",
        "thread/list",
        "thread/read",
        "thread/resume",
        "turn/start",
    }
    assert subject.PROTOCOL_PROFILE.client_notifications == {"initialized"}
    assert subject.PROTOCOL_PROFILE.server_notifications == {
        "thread/status/changed",
        "turn/started",
        "turn/completed",
        "error",
    }
    assert subject.D8_POLICY_SHA256 == "db58a1e73a934719f4df7b9e07a4217a289cb8f4b3b748ce16a0e537df8036b6"
    assert subject.DISCOVERY_PROTOCOL_PROFILE.request_methods == {"initialize", "thread/list"}


def test_live_process_is_not_used_by_fake_qualification(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("live app-server launch attempted")

    monkeypatch.setattr(subject.subprocess, "Popen", forbidden)
    result = subject._qualify_fake_peer(tmp_path)
    assert set(result["real_operation_counters"].values()) == {0}  # type: ignore[union-attr]


def test_stderr_scanner_is_bounded_private_and_categorical() -> None:
    os = __import__("os")
    pytest = __import__("pytest")
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "rb", buffering=0)
    scanner = subject._StderrScanner(reader, subject._Deadline.start(session_seconds=1, operation_seconds=0.5))
    scanner.start()
    secret = b"sk-" + b"A" * 24
    os.write(write_fd, b"prefix " + secret + b" suffix\n")
    os.close(write_fd)
    scanner._thread.join(timeout=1)
    with pytest.raises(subject.DeliveryError) as captured:
        scanner.check()
    assert str(captured.value) == "app-server stderr rejected: stderr_sensitive_pattern"
    assert secret.decode() not in str(captured.value)
    reader.close()


def test_stderr_scanner_rejects_more_than_64_kib_without_persisting_bytes() -> None:
    os = __import__("os")
    pytest = __import__("pytest")
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "rb", buffering=0)
    scanner = subject._StderrScanner(reader, subject._Deadline.start(session_seconds=2, operation_seconds=1))
    scanner.start()
    for _ in range(17):
        os.write(write_fd, b"x" * 4096)
    os.close(write_fd)
    scanner._thread.join(timeout=1)
    with pytest.raises(subject.DeliveryError) as captured:
        scanner.check()
    assert str(captured.value) == "app-server stderr rejected: stderr_limit_exceeded"
    assert "x" * 32 not in str(captured.value)
    reader.close()


def test_process_cleanup_uses_owned_group_and_bounded_wait() -> None:
    source = Path(inspect.getfile(subject)).read_text(encoding="utf-8")
    assert "os.killpg" in source
    assert "signal.SIGTERM" in source
    assert "signal.SIGKILL" in source
    assert "cleanup_end" in source
    assert "process_reap_timeout" in source
    assert "stderr_helper_alive" in source
