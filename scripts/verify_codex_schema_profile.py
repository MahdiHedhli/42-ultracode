#!/usr/bin/env python3
"""Qualify the installed stable Codex schema by consumed semantic shape."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import cast

from ultracode.supervised_delivery import PROTOCOL_PROFILE

_VERSION = re.compile(r"codex-cli [0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?\Z")
_MAX_SCHEMA_BYTES = 8 * 1024 * 1024

_TOP_SHAPES: dict[str, tuple[set[str], set[str]]] = {
    "v1/InitializeParams.json": ({"capabilities", "clientInfo"}, {"clientInfo"}),
    "v1/InitializeResponse.json": (
        {"codexHome", "platformFamily", "platformOs", "userAgent"},
        {"codexHome", "platformFamily", "platformOs", "userAgent"},
    ),
    "v2/ErrorNotification.json": (
        {"error", "threadId", "turnId", "willRetry"},
        {"error", "threadId", "turnId", "willRetry"},
    ),
    "v2/ThreadListParams.json": (
        {
            "archived",
            "cursor",
            "cwd",
            "limit",
            "modelProviders",
            "searchTerm",
            "sectionId",
            "sortDirection",
            "sortKey",
            "sourceKinds",
            "useStateDbOnly",
        },
        set(),
    ),
    "v2/ThreadListResponse.json": ({"backwardsCursor", "data", "nextCursor"}, {"data"}),
    "v2/ThreadReadParams.json": ({"includeTurns", "threadId"}, {"threadId"}),
    "v2/ThreadReadResponse.json": ({"thread"}, {"thread"}),
    "v2/ThreadResumeParams.json": (
        {
            "approvalPolicy",
            "approvalsReviewer",
            "baseInstructions",
            "config",
            "cwd",
            "developerInstructions",
            "excludeTurns",
            "model",
            "modelProvider",
            "personality",
            "sandbox",
            "serviceTier",
            "threadId",
        },
        {"threadId"},
    ),
    "v2/ThreadResumeResponse.json": (
        {
            "approvalPolicy",
            "approvalsReviewer",
            "cwd",
            "instructionSources",
            "itemsBackwardsCursor",
            "model",
            "modelProvider",
            "reasoningEffort",
            "sandbox",
            "serviceTier",
            "thread",
            "turnsBackwardsCursor",
        },
        {"approvalPolicy", "approvalsReviewer", "cwd", "model", "modelProvider", "sandbox", "thread"},
    ),
    "v2/ThreadStatusChangedNotification.json": ({"status", "threadId"}, {"status", "threadId"}),
    "v2/TurnCompletedNotification.json": ({"threadId", "turn"}, {"threadId", "turn"}),
    "v2/TurnStartParams.json": (
        {
            "approvalPolicy",
            "approvalsReviewer",
            "clientUserMessageId",
            "cwd",
            "effort",
            "input",
            "model",
            "outputSchema",
            "personality",
            "sandboxPolicy",
            "serviceTier",
            "serviceTierForTurn",
            "summary",
            "threadId",
            "toolOutput",
            "turnTrigger",
        },
        {"input", "threadId"},
    ),
    "v2/TurnStartResponse.json": ({"turn"}, {"turn"}),
    "v2/TurnStartedNotification.json": ({"threadId", "turn"}, {"threadId", "turn"}),
}

_THREAD_PROPERTIES = {
    "agentNickname",
    "agentRole",
    "cliVersion",
    "createdAt",
    "cwd",
    "ephemeral",
    "forkedFromId",
    "gitInfo",
    "historyMode",
    "id",
    "model",
    "modelProvider",
    "name",
    "parentThreadId",
    "path",
    "preview",
    "projectId",
    "recencyAt",
    "reasoningEffort",
    "section",
    "sectionEnteredAt",
    "sessionId",
    "source",
    "status",
    "threadSource",
    "turns",
    "updatedAt",
}
_THREAD_REQUIRED = {
    "cliVersion",
    "createdAt",
    "cwd",
    "ephemeral",
    "id",
    "modelProvider",
    "preview",
    "projectId",
    "sessionId",
    "source",
    "status",
    "turns",
    "updatedAt",
}
_TURN_PROPERTIES = {"completedAt", "durationMs", "error", "id", "items", "itemsView", "startedAt", "status"}
_TURN_REQUIRED = {"id", "items", "status"}


def _object(value: object, label: str) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError(f"{label} is not an object")
    return cast(dict[str, object], value)


def _require_shape(value: object, properties: set[str], required: set[str], label: str) -> None:
    selected = _object(value, label)
    actual_properties = set(_object(selected.get("properties"), f"{label}.properties"))
    actual_required = set(cast(list[object], selected.get("required", [])))
    if actual_properties != properties or actual_required != required:
        raise ValueError(f"{label} semantic shape changed")


def _verify_thread_definitions(schema: dict[str, object], label: str) -> None:
    definitions = _object(schema.get("definitions"), f"{label}.definitions")
    _require_shape(definitions.get("Thread"), _THREAD_PROPERTIES, _THREAD_REQUIRED, f"{label}.Thread")
    history = _object(definitions.get("ThreadHistoryMode"), f"{label}.ThreadHistoryMode")
    if history.get("type") != "string" or history.get("enum") != ["legacy", "paginated"]:
        raise ValueError(f"{label} history mode changed")
    source = _object(definitions.get("ThreadSource"), f"{label}.ThreadSource")
    if source.get("type") != "string":
        raise ValueError(f"{label} thread source changed")


def _verify_turn_definitions(schema: dict[str, object], label: str) -> None:
    definitions = _object(schema.get("definitions"), f"{label}.definitions")
    _require_shape(definitions.get("Turn"), _TURN_PROPERTIES, _TURN_REQUIRED, f"{label}.Turn")
    status = _object(definitions.get("TurnStatus"), f"{label}.TurnStatus")
    if status.get("type") != "string" or status.get("enum") != [
        "completed",
        "interrupted",
        "failed",
        "inProgress",
    ]:
        raise ValueError(f"{label} turn status changed")


def _verify_status_definition(schema: dict[str, object]) -> None:
    definitions = _object(schema.get("definitions"), "status.definitions")
    active_flag = _object(definitions.get("ThreadActiveFlag"), "ThreadActiveFlag")
    if active_flag.get("type") != "string" or active_flag.get("enum") != [
        "waitingOnApproval",
        "waitingOnUserInput",
    ]:
        raise ValueError("thread active flags changed")
    status = _object(definitions.get("ThreadStatus"), "ThreadStatus")
    variants = status.get("oneOf")
    if type(variants) is not list or len(variants) != 4:
        raise ValueError("thread status variants changed")
    kinds: set[str] = set()
    for index, raw in enumerate(variants):
        variant = _object(raw, f"ThreadStatus[{index}]")
        properties = _object(variant.get("properties"), f"ThreadStatus[{index}].properties")
        type_schema = _object(properties.get("type"), f"ThreadStatus[{index}].type")
        enum = type_schema.get("enum")
        if type(enum) is not list or len(enum) != 1 or type(enum[0]) is not str:
            raise ValueError("thread status discriminator changed")
        kind = cast(str, enum[0])
        kinds.add(kind)
        expected = {"activeFlags", "type"} if kind == "active" else {"type"}
        if set(properties) != expected or set(cast(list[object], variant.get("required", []))) != expected:
            raise ValueError("thread status fields changed")
    if kinds != {"notLoaded", "idle", "systemError", "active"}:
        raise ValueError("thread status kinds changed")


def _load_schema(root: Path, name: str) -> tuple[dict[str, object], str]:
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or unsafe schema: {name}")
    raw = path.read_bytes()
    if not raw or len(raw) > _MAX_SCHEMA_BYTES:
        raise ValueError(f"schema size is unsafe: {name}")
    return _object(json.loads(raw), name), hashlib.sha256(raw).hexdigest()


def qualify(root: Path, codex_version: str) -> dict[str, object]:
    if _VERSION.fullmatch(codex_version) is None:
        raise ValueError("Codex version is not canonical")
    if set(_TOP_SHAPES) != set(PROTOCOL_PROFILE.schema_names):
        raise ValueError("schema-name authority is inconsistent")
    schemas: dict[str, dict[str, object]] = {}
    hashes: dict[str, str] = {}
    for name in sorted(PROTOCOL_PROFILE.schema_names):
        schemas[name], hashes[name] = _load_schema(root, name)
        _require_shape(schemas[name], *_TOP_SHAPES[name], name)
    for name in ("v2/ThreadListResponse.json", "v2/ThreadReadResponse.json", "v2/ThreadResumeResponse.json"):
        _verify_thread_definitions(schemas[name], name)
    for name in (
        "v2/ThreadListResponse.json",
        "v2/ThreadReadResponse.json",
        "v2/ThreadResumeResponse.json",
        "v2/TurnStartResponse.json",
        "v2/TurnStartedNotification.json",
        "v2/TurnCompletedNotification.json",
    ):
        _verify_turn_definitions(schemas[name], name)
    _verify_status_definition(schemas["v2/ThreadStatusChangedNotification.json"])
    return {
        "codex_version": codex_version,
        "schema_sha256": hashes,
        "semantic_profile": "f017-d8-supervised-delivery/0.152-compatible",
        "status": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("current", type=Path)
    parser.add_argument("--codex-version", required=True)
    args = parser.parse_args()
    print(json.dumps(qualify(args.current, args.codex_version), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
