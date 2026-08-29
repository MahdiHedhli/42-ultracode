#!/usr/bin/env python3
"""Verify raw Codex schema pins and the accepted 0.146 -> 0.149 semantic path set."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ultracode.supervised_delivery import PROTOCOL_PROFILE, _verify_schema_hashes

BASELINE_SHA256 = {
    "v1/InitializeParams.json": "4f576f99e285beb28f71f48a72b887c1f517dada86fee348fe2af0a35511de23",
    "v1/InitializeResponse.json": "86dcd236d0576a82c85b933586dc45731260eab1b6edb3447b03f790277322b1",
    "v2/ErrorNotification.json": "1ec871b02771300a26a34e41a7cfaf7484330a8c37c197d1ac133e753b083a09",
    "v2/ThreadListParams.json": "3b37cf361c29b959cf29828db3017c0a5e38d9c24de5fbd089bd44d42f05d5f0",
    "v2/ThreadListResponse.json": "5b01b0c03141c2a15559879294ef065daac9715615d7df65371baf5f119d9958",
    "v2/ThreadReadParams.json": "db97080f82facc3259dbb9404e9f0df81e360619f4cd73983a9d99d25f5089ee",
    "v2/ThreadReadResponse.json": "dd1f9df782fc0e0a9d752dbf6f725634355b4889f9393074c9a71f768dcb2990",
    "v2/ThreadResumeParams.json": "1dc47d294d0de32f334e0829893d743ec64393ebcf00d7212c9c55b03c34ed23",
    "v2/ThreadResumeResponse.json": "a729b3d290402b1e7ee11661001dc194b59f0b5743cbe9e64cd6720862179865",
    "v2/ThreadStatusChangedNotification.json": "146af6d3702c4f3c844bd10b6b6b3e2b872e958a8d7d822157c19aaa6dc085f6",
    "v2/TurnCompletedNotification.json": "5b5f2ca515658ea6fcce7e961d1c3feddb3f48c0dcc813260c7ccf77a2d016af",
    "v2/TurnStartParams.json": "48a0ee95b669b47f5557c68b99a4d459b50577ccce8ebc5976532f50e3c6d059",
    "v2/TurnStartResponse.json": "099184dc9d6195cd965b8a90ee5d1cb05c87d9b329acecdfbd63f358e660d568",
    "v2/TurnStartedNotification.json": "e268134e79cae246e39f110e67bd2efbb49ce9a572520a85a96a7325eaf31e03",
}

EXPECTED_CHANGED_PATHS = {
    "v1/InitializeParams.json": [
        "/definitions/InitializeCapabilities/properties/extensions",
        "/definitions/InitializeCapabilities/properties/mcpServerOpenaiFormElicitation/description",
    ],
    "v2/ErrorNotification.json": ["/definitions/CodexErrorInfo/oneOf"],
    "v2/ThreadListParams.json": [
        "/definitions/ThreadSortKey/enum",
        "/properties/isPinned",
        "/properties/sectionId",
    ],
    "v2/ThreadListResponse.json": [
        "/definitions/AgentMessageDelivery",
        "/definitions/CodexErrorInfo/oneOf",
        "/definitions/CommandAction/oneOf",
        "/definitions/ImageGenerationFailure",
        "/definitions/Thread/properties/isPinned",
        "/definitions/Thread/properties/projectId",
        "/definitions/Thread/properties/section",
        "/definitions/Thread/properties/sectionEnteredAt",
        "/definitions/Thread/required",
        "/definitions/ThreadItem/oneOf",
        "/definitions/ThreadSection",
        "/definitions/ThreadSectionAppearance",
    ],
    "v2/ThreadReadResponse.json": [],
    "v2/ThreadResumeParams.json": ["/definitions/ResponseItem/oneOf"],
    "v2/ThreadResumeResponse.json": [],
    "v2/TurnCompletedNotification.json": [
        "/definitions/AgentMessageDelivery",
        "/definitions/CodexErrorInfo/oneOf",
        "/definitions/CommandAction/oneOf",
        "/definitions/ImageGenerationFailure",
        "/definitions/ThreadItem/oneOf",
    ],
    "v2/TurnStartResponse.json": [],
    "v2/TurnStartedNotification.json": [],
}

SHARED_CHANGE_GROUPS = {
    "v2/ThreadReadResponse.json": "v2/ThreadListResponse.json",
    "v2/ThreadResumeResponse.json": "v2/ThreadListResponse.json",
    "v2/TurnStartResponse.json": "v2/TurnCompletedNotification.json",
    "v2/TurnStartedNotification.json": "v2/TurnCompletedNotification.json",
}


def _hashes(root: Path, names: set[str]) -> dict[str, str]:
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(names)}


def _diff_paths(old: object, new: object, path: str = "") -> list[str]:
    if type(old) is not type(new):
        return [path or "/"]
    if type(old) is dict:
        old_map = dict(old)
        new_map = dict(new)
        paths = [f"{path}/{key}" for key in sorted(set(old_map) ^ set(new_map))]
        for key in sorted(set(old_map) & set(new_map)):
            paths.extend(_diff_paths(old_map[key], new_map[key], f"{path}/{key}"))
        return paths
    if type(old) is list:
        return [] if old == new else [path or "/"]
    return [] if old == new else [path or "/"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("current", type=Path)
    args = parser.parse_args()
    names = set(PROTOCOL_PROFILE.schema_sha256)
    if _hashes(args.baseline, names) != BASELINE_SHA256:
        raise SystemExit("baseline raw schema hashes do not match")
    current_hashes = _hashes(args.current, names)
    _verify_schema_hashes(current_hashes)
    changed: dict[str, list[str]] = {}
    for name in sorted(names):
        old = json.loads((args.baseline / name).read_text(encoding="utf-8"))
        new = json.loads((args.current / name).read_text(encoding="utf-8"))
        paths = sorted(set(_diff_paths(old, new)))
        if paths:
            changed[name] = paths
    expected = {
        name: EXPECTED_CHANGED_PATHS.get(source, EXPECTED_CHANGED_PATHS.get(name, []))
        for name, source in {**{name: name for name in EXPECTED_CHANGED_PATHS}, **SHARED_CHANGE_GROUPS}.items()
    }
    if changed != {name: sorted(paths) for name, paths in expected.items()}:
        raise SystemExit("semantic schema path classification does not match")
    print(
        json.dumps(
            {
                "baseline_cli": "codex-cli 0.146.0",
                "baseline_sha256": BASELINE_SHA256,
                "changed": changed,
                "current_cli": PROTOCOL_PROFILE.codex_version,
                "current_sha256": current_hashes,
                "generation_command": (
                    "/Applications/Codex.app/Contents/Resources/codex "
                    "app-server generate-json-schema --out <new-private-directory>"
                ),
                "unchanged": sorted(names - set(changed)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
