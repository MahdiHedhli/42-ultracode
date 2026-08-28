from __future__ import annotations

import ast
import inspect
from pathlib import Path

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
    assert len(launches) == 1
    launch = launches[0]
    assert isinstance(launch.args[0], ast.Name) and launch.args[0].id == "APP_SERVER_ARGV"
    keywords = {item.arg: item.value for item in launch.keywords}
    assert isinstance(keywords["shell"], ast.Constant) and keywords["shell"].value is False


def test_no_network_browser_or_automatic_delivery_surface() -> None:
    source = Path(inspect.getfile(subject)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned_imports = {"aiohttp", "http", "mcp", "requests", "socket", "urllib", "webbrowser"}
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    imported |= {(node.module or "").split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported.isdisjoint(banned_imports)
    assert {"daemon", "scheduler", "watch", "retry", "post_message"}.isdisjoint(vars(subject))


def test_protocol_and_operation_allowlists_are_closed() -> None:
    assert subject.PROTOCOL_PROFILE.request_methods == {
        "initialize",
        "thread/read",
        "thread/resume",
        "turn/start",
    }
    assert subject.PROTOCOL_PROFILE.client_notifications == {"initialized"}
    assert subject.PROTOCOL_PROFILE.server_notifications == {"turn/started", "turn/completed", "error"}
    assert subject.D8_POLICY_SHA256 == "db58a1e73a934719f4df7b9e07a4217a289cb8f4b3b748ce16a0e537df8036b6"


def test_live_process_is_not_used_by_fake_qualification(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("live app-server launch attempted")

    monkeypatch.setattr(subject.subprocess, "Popen", forbidden)
    result = subject._qualify_fake_peer(tmp_path)
    assert set(result["real_operation_counters"].values()) == {0}  # type: ignore[union-attr]
