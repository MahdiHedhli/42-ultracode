from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import ultracode.supervised_handoff as subject

BANNED = {
    "aiohttp",
    "applescript",
    "browser",
    "chrome",
    "http",
    "importlib",
    "mcp",
    "objc",
    "pyautogui",
    "requests",
    "socket",
    "subprocess",
    "urllib",
    "webbrowser",
}
CALLS = {"__import__", "compile", "eval", "exec", "getattr", "open", "setattr"}
VERBS = ("click", "deliver", "navigate", "paste", "post", "send", "submit", "type")


def audit(source: str) -> tuple[str, ...]:
    findings = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            findings += [f"import:{x.name}" for x in node.names if x.name.split(".")[0] in BANNED]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in BANNED:
            findings.append(f"import-from:{node.module}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in CALLS:
            findings.append(f"call:{node.func.id}")
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
            and node.name.startswith(VERBS)
        ):
            findings.append(f"public:{node.name}")
    return tuple(sorted(findings))


def test_source_and_exports_closed():
    assert audit(Path(inspect.getfile(subject)).read_text()) == ()
    assert all(not name.startswith(VERBS) for name in subject.__all__)


@pytest.mark.parametrize(
    ("source", "finding"),
    [
        ("import requests\n", "import:requests"),
        ("import subprocess as x\n", "import:subprocess"),
        ("from urllib.parse import urlparse\n", "import-from:urllib.parse"),
        ("def x():\n return __import__('socket')\n", "call:__import__"),
        ("def post_message():\n return None\n", "public:post_message"),
    ],
)
def test_static_mutations_fail(source: str, finding: str):
    assert finding in audit(source)


def test_no_injected_capability_parameters_or_live_entrypoints():
    forbidden = {"callback", "connector", "environment", "mapping", "profile", "resolver", "transport"}
    for name in subject.__all__:
        value = getattr(subject, name)
        if inspect.isfunction(value):
            assert set(inspect.signature(value).parameters).isdisjoint(forbidden)
    assert {"main", "cli", "daemon", "watch", "scheduler", "browser_adapter", "chat_adapter"}.isdisjoint(vars(subject))
