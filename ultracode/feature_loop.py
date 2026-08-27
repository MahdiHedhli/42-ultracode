"""Thin Feature Loop Protocol adapter around the v0.1 controller.

The adapter protects the Git artifact frontier.  It does not replace controller
leases, events, replay, lifecycle rules, or idempotency.  Resolved machine-local
aliases are deliberately kept outside every serializable adapter record.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import NoReturn, cast

from .controller import Controller, TurnClaim
from .protocol import JsonObject, JsonValue, validate_relative_path


class FeatureLoopError(RuntimeError):
    """Base class for fail-closed Feature Loop adapter errors."""


class ManifestError(FeatureLoopError):
    """A prompt-control document is malformed or outside the supported schema."""


class FrontierError(FeatureLoopError):
    """The prompt-control frontier is not the exact expected execution frontier."""


class PrivacyError(FeatureLoopError):
    """Durable content contains a policy-prohibited identifier."""


class PublicationError(FeatureLoopError):
    """A response publication is unsafe, conflicting, or incomplete."""


_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")

PROMPT_SCHEMA = "pulsarmlx.graph-prompt/1.0.0"
FEATURE_SCHEMA = "pulsarmlx.feature-loop/1.0.0"
STATE_SCHEMA = "pulsarmlx.feature-loop-state/1.0.0"
PRIVACY_SCHEMA = "pulsarmlx.prompt-privacy-policy/1.0.0"


def _fail(message: str) -> NoReturn:
    raise ManifestError(message)


def _yaml_scalar(value: str, line: int) -> JsonValue:
    value = value.strip()
    if not value:
        _fail(f"line {line}: empty scalar")
    if value.startswith(("&", "*", "!", "|", ">", "[", "{")):
        _fail(f"line {line}: unsupported YAML feature")
    if value.startswith('"'):
        try:
            parsed: JsonValue = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ManifestError(f"line {line}: invalid quoted scalar") from exc
        if not isinstance(parsed, str):
            _fail(f"line {line}: quoted scalar must be text")
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            _fail(f"line {line}: invalid single-quoted scalar")
        return value[1:-1].replace("''", "'")
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value):
        return int(value)
    return value


@dataclass(frozen=True)
class _YamlLine:
    indent: int
    content: str
    number: int


def parse_restricted_yaml(text: str) -> JsonObject:
    """Parse the small deterministic YAML subset used by Feature Loop control files.

    Anchors, tags, flow collections, block scalars, tabs, duplicate keys, and
    irregular indentation are rejected rather than interpreted ambiguously.
    """

    lines: list[_YamlLine] = []
    for number, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw:
            _fail(f"line {number}: tabs are prohibited")
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            _fail(f"line {number}: indentation must use two-space steps")
        lines.append(_YamlLine(indent, raw[indent:], number))
    if not lines:
        _fail("YAML document is empty")

    def split_key(content: str, line: int) -> tuple[str, str]:
        if ":" not in content:
            _fail(f"line {line}: mapping entry lacks ':'")
        key, value = content.split(":", 1)
        key = key.strip()
        if not _KEY.fullmatch(key):
            _fail(f"line {line}: invalid mapping key")
        return key, value.strip()

    def parse_block(index: int, indent: int) -> tuple[JsonValue, int]:
        if index >= len(lines) or lines[index].indent != indent:
            _fail("invalid nested indentation")
        is_list = lines[index].content == "-" or lines[index].content.startswith("- ")
        if is_list:
            result_list: list[JsonValue] = []
            while index < len(lines) and lines[index].indent == indent:
                current = lines[index]
                if current.content != "-" and not current.content.startswith("- "):
                    _fail(f"line {current.number}: mixed list and mapping block")
                tail = current.content[1:].strip()
                index += 1
                if not tail:
                    if index >= len(lines) or lines[index].indent != indent + 2:
                        _fail(f"line {current.number}: list item requires nested content")
                    item, index = parse_block(index, indent + 2)
                    result_list.append(item)
                    continue
                if ":" not in tail:
                    result_list.append(_yaml_scalar(tail, current.number))
                    continue
                key, value = split_key(tail, current.number)
                item_map: JsonObject = {}
                if value:
                    item_map[key] = _yaml_scalar(value, current.number)
                else:
                    if index >= len(lines) or lines[index].indent != indent + 2:
                        _fail(f"line {current.number}: mapping value requires nested content")
                    nested, index = parse_block(index, indent + 2)
                    item_map[key] = nested
                if index < len(lines) and lines[index].indent == indent + 2:
                    siblings, index = parse_block(index, indent + 2)
                    if not isinstance(siblings, dict):
                        _fail(f"line {lines[index - 1].number}: list mapping siblings must be a mapping")
                    for sibling_key, sibling_value in siblings.items():
                        if sibling_key in item_map:
                            _fail(f"duplicate key: {sibling_key}")
                        item_map[sibling_key] = sibling_value
                result_list.append(item_map)
            return result_list, index

        result_map: JsonObject = {}
        while index < len(lines) and lines[index].indent == indent:
            current = lines[index]
            if current.content == "-" or current.content.startswith("- "):
                _fail(f"line {current.number}: mixed mapping and list block")
            key, value = split_key(current.content, current.number)
            if key in result_map:
                _fail(f"line {current.number}: duplicate key {key}")
            index += 1
            if value:
                result_map[key] = _yaml_scalar(value, current.number)
            else:
                if index >= len(lines) or lines[index].indent != indent + 2:
                    _fail(f"line {current.number}: mapping value requires nested content")
                nested, index = parse_block(index, indent + 2)
                result_map[key] = nested
        return result_map, index

    parsed, end = parse_block(0, lines[0].indent)
    if lines[0].indent != 0 or end != len(lines) or not isinstance(parsed, dict):
        _fail("document root must be one mapping at indentation zero")
    return parsed


def _required_string(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{key} must be non-empty text")
    return value.strip()


def _required_int(data: Mapping[str, object], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError(f"{key} must be an integer")
    return value


@dataclass(frozen=True)
class FeatureManifest:
    schema: str
    feature_id: str
    status: str
    state_file: str
    machine_sequences: Mapping[str, int]

    @classmethod
    def from_yaml(cls, text: str) -> FeatureManifest:
        raw = parse_restricted_yaml(text)
        schema = _required_string(raw, "schema")
        if schema != FEATURE_SCHEMA:
            raise ManifestError(f"unsupported feature manifest schema: {schema}")
        sequences = raw.get("latest_machine_sequence")
        if not isinstance(sequences, dict) or not sequences:
            raise ManifestError("latest_machine_sequence must be a non-empty mapping")
        typed_sequences: dict[str, int] = {}
        for machine, sequence in sequences.items():
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
                raise ManifestError("machine sequence values must be non-negative integers")
            typed_sequences[str(machine)] = sequence
        return cls(
            schema=schema,
            feature_id=_required_string(raw, "feature_id"),
            status=_required_string(raw, "status"),
            state_file=validate_relative_path(_required_string(raw, "state_file")),
            machine_sequences=typed_sequences,
        )


@dataclass(frozen=True)
class LatestResponse:
    path: str
    sha256: str | None
    status: str | None
    machine_model: str | None
    sequence: int | None
    commit: str | None

    @property
    def complete(self) -> bool:
        return all(
            value is not None for value in (self.sha256, self.status, self.machine_model, self.sequence, self.commit)
        )


@dataclass(frozen=True)
class LatestPrompt:
    path: str
    sha256: str
    commit: str


def _optional_string(data: Mapping[str, object], key: str) -> str | None:
    if key not in data:
        return None
    return _required_string(data, key)


def _optional_nonnegative_int(data: Mapping[str, object], key: str) -> int | None:
    if key not in data:
        return None
    value = _required_int(data, key)
    if value < 0:
        raise ManifestError(f"{key} must be non-negative")
    return value


@dataclass(frozen=True)
class FeatureState:
    schema: str
    feature_id: str
    state: str
    current_machine: str
    current_sequence: int
    latest_response: LatestResponse | None
    latest_prompt: LatestPrompt | None

    @property
    def latest_response_path(self) -> str | None:
        return self.latest_response.path if self.latest_response is not None else None

    @classmethod
    def from_json(cls, text: str) -> FeatureState:
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ManifestError("STATE.json is invalid JSON") from exc
        if not isinstance(raw, dict):
            raise ManifestError("STATE.json root must be an object")
        schema = _required_string(raw, "schema")
        if schema != STATE_SCHEMA:
            raise ManifestError(f"unsupported feature state schema: {schema}")
        latest = raw.get("latest_response")
        latest_response: LatestResponse | None = None
        if latest is not None:
            if not isinstance(latest, dict):
                raise ManifestError("latest_response must be an object or null")
            latest_sha = _optional_string(latest, "sha256")
            if latest_sha is not None and not _SHA256.fullmatch(latest_sha):
                raise ManifestError("latest_response.sha256 is invalid")
            latest_commit = _optional_string(latest, "commit")
            if latest_commit is not None and not _COMMIT.fullmatch(latest_commit):
                raise ManifestError("latest_response.commit is invalid")
            latest_response = LatestResponse(
                path=validate_relative_path(_required_string(latest, "path")),
                sha256=latest_sha,
                status=_optional_string(latest, "status"),
                machine_model=_optional_string(latest, "machine_model"),
                sequence=_optional_nonnegative_int(latest, "sequence"),
                commit=latest_commit,
            )
        sequence = _required_int(raw, "current_sequence")
        if sequence < 0:
            raise ManifestError("current_sequence must be non-negative")
        latest_prompt_raw = raw.get("latest_prompt")
        latest_prompt: LatestPrompt | None = None
        if latest_prompt_raw is not None:
            if not isinstance(latest_prompt_raw, dict):
                raise ManifestError("latest_prompt must be an object or null")
            prompt_sha = _required_string(latest_prompt_raw, "sha256")
            prompt_commit = _required_string(latest_prompt_raw, "commit")
            if not _SHA256.fullmatch(prompt_sha) or not _COMMIT.fullmatch(prompt_commit):
                raise ManifestError("latest_prompt identity is invalid")
            latest_prompt = LatestPrompt(
                path=validate_relative_path(_required_string(latest_prompt_raw, "path")),
                sha256=prompt_sha,
                commit=prompt_commit,
            )
        if sequence >= 1:
            if latest_response is None or not latest_response.complete:
                raise ManifestError("sequence 1+ requires complete latest_response identity")
            if latest_prompt is None:
                raise ManifestError("sequence 1+ requires complete latest_prompt identity")
        return cls(
            schema=schema,
            feature_id=_required_string(raw, "feature_id"),
            state=_required_string(raw, "state"),
            current_machine=_required_string(raw, "current_machine"),
            current_sequence=sequence,
            latest_response=latest_response,
            latest_prompt=latest_prompt,
        )


@dataclass(frozen=True)
class PrivacyPolicy:
    schema: str
    feature_id: str
    allowed: tuple[str, ...]
    prohibited: tuple[str, ...]
    aliases: Mapping[str, str]

    @classmethod
    def from_yaml(cls, text: str) -> PrivacyPolicy:
        raw = parse_restricted_yaml(text)
        schema = _required_string(raw, "schema")
        if schema != PRIVACY_SCHEMA:
            raise ManifestError(f"unsupported privacy policy schema: {schema}")
        allowed = raw.get("allowed")
        prohibited = raw.get("prohibited")
        aliases = raw.get("local_aliases")
        if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
            raise ManifestError("privacy allowed must be a list of strings")
        if not isinstance(prohibited, list) or not all(isinstance(item, str) for item in prohibited):
            raise ManifestError("privacy prohibited must be a list of strings")
        if not isinstance(aliases, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in aliases.items()
        ):
            raise ManifestError("privacy local_aliases must be a string mapping")
        return cls(
            schema=schema,
            feature_id=_required_string(raw, "feature_id"),
            allowed=tuple(cast(list[str], allowed)),
            prohibited=tuple(cast(list[str], prohibited)),
            aliases=cast(dict[str, str], aliases),
        )


_ENVELOPE_FIELDS = frozenset(
    {
        "schema",
        "feature_id",
        "sequence",
        "machine_model",
        "machine_architecture",
        "phase",
        "human_gate",
        "prompt_control_base_commit",
        "expected_parent_response_path",
        "expected_parent_response_sha256",
        "response_path",
        "response_checksum_path",
        "handoff_path",
        "source_repository",
        "source_mutation",
        "original_checkpoint_access",
        "full_model_inference",
        "automatic_chat_posting",
    }
)


@dataclass(frozen=True)
class PromptAuthorizationProfile:
    """Trusted controller policy that untrusted prompt front matter must match."""

    schema: str
    feature_id: str
    machine_model: str
    machine_architecture: str
    phase: str
    human_gate: str
    source_repository: str
    source_mutation: str
    original_checkpoint_access: str
    full_model_inference: str
    automatic_chat_posting: str

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ManifestError(f"trusted authorization field must be non-empty: {field_name}")
        if self.schema != PROMPT_SCHEMA:
            raise ManifestError(f"unsupported trusted prompt schema: {self.schema}")

    def values(self) -> Mapping[str, str]:
        return {field_name: cast(str, getattr(self, field_name)) for field_name in self.__dataclass_fields__}


@dataclass(frozen=True)
class PromptEnvelope:
    schema: str
    feature_id: str
    sequence: int
    machine_model: str
    machine_slug: str
    machine_architecture: str
    phase: str
    human_gate: str
    expected_parent_response_path: str
    expected_parent_response_sha256: str
    response_path: str
    response_checksum_path: str
    handoff_path: str
    source_repository: str
    source_mutation: str
    original_checkpoint_access: str
    full_model_inference: str
    automatic_chat_posting: str
    prompt_control_base_commit: str

    def require_authorization(self, trusted: PromptAuthorizationProfile) -> None:
        for field_name, expected in trusted.values().items():
            if getattr(self, field_name) != expected:
                raise FrontierError(f"prompt authorization mismatch: {field_name}")

    @classmethod
    def from_markdown(cls, text: str) -> PromptEnvelope:
        if not text.startswith("---\n"):
            raise ManifestError("prompt must start with YAML front matter")
        try:
            front, _body = text[4:].split("\n---\n", 1)
        except ValueError as exc:
            raise ManifestError("prompt front matter is not terminated") from exc
        raw = parse_restricted_yaml(front)
        unknown = set(raw) - _ENVELOPE_FIELDS
        missing = _ENVELOPE_FIELDS - set(raw)
        if unknown or missing:
            raise ManifestError(
                f"prompt front matter fields differ: missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        sequence = _required_int(raw, "sequence")
        if sequence < 0:
            raise ManifestError("sequence must be non-negative")
        parent_sha = _required_string(raw, "expected_parent_response_sha256")
        if not _SHA256.fullmatch(parent_sha):
            raise ManifestError("expected parent SHA-256 is invalid")
        base = _required_string(raw, "prompt_control_base_commit")
        if not _COMMIT.fullmatch(base):
            raise ManifestError("prompt-control base commit is invalid")
        machine_model = _required_string(raw, "machine_model")
        return cls(
            schema=_required_string(raw, "schema"),
            feature_id=_required_string(raw, "feature_id"),
            sequence=sequence,
            machine_model=machine_model,
            machine_slug=machine_model.replace(" ", "-"),
            machine_architecture=_required_string(raw, "machine_architecture"),
            phase=_required_string(raw, "phase"),
            human_gate=_required_string(raw, "human_gate"),
            expected_parent_response_path=validate_relative_path(
                _required_string(raw, "expected_parent_response_path")
            ),
            expected_parent_response_sha256=parent_sha,
            response_path=validate_relative_path(_required_string(raw, "response_path")),
            response_checksum_path=validate_relative_path(_required_string(raw, "response_checksum_path")),
            handoff_path=validate_relative_path(_required_string(raw, "handoff_path")),
            source_repository=_required_string(raw, "source_repository"),
            source_mutation=_required_string(raw, "source_mutation"),
            original_checkpoint_access=_required_string(raw, "original_checkpoint_access"),
            full_model_inference=_required_string(raw, "full_model_inference"),
            automatic_chat_posting=_required_string(raw, "automatic_chat_posting"),
            prompt_control_base_commit=base,
        )


_VERIFIED_PROMPT_TOKEN = object()


class VerifiedPromptIdentity:
    """Sealed identity produced only after exact Git prompt verification."""

    prompt_path: str
    prompt_commit: str
    prompt_sha256: str
    sidecar_path: str
    envelope: PromptEnvelope
    _authorization_profile: PromptAuthorizationProfile
    _sealed: bool

    __slots__ = (
        "_authorization_profile",
        "_sealed",
        "envelope",
        "prompt_commit",
        "prompt_path",
        "prompt_sha256",
        "sidecar_path",
    )

    def __init__(
        self,
        prompt_path: str,
        prompt_commit: str,
        prompt_sha256: str,
        sidecar_path: str,
        envelope: PromptEnvelope,
        authorization_profile: PromptAuthorizationProfile,
        *,
        _token: object,
    ) -> None:
        if _token is not _VERIFIED_PROMPT_TOKEN:
            raise FrontierError("verified prompt identities must come from GitPromptTransport")
        object.__setattr__(self, "prompt_path", prompt_path)
        object.__setattr__(self, "prompt_commit", prompt_commit)
        object.__setattr__(self, "prompt_sha256", prompt_sha256)
        object.__setattr__(self, "sidecar_path", sidecar_path)
        object.__setattr__(self, "envelope", envelope)
        object.__setattr__(self, "_authorization_profile", authorization_profile)
        object.__setattr__(self, "_sealed", True)

    @property
    def authorization_profile(self) -> PromptAuthorizationProfile:
        return self._authorization_profile

    def __setattr__(self, _name: str, _value: object) -> NoReturn:
        raise FrontierError("verified prompt identity is immutable")


class GitPromptTransport:
    """Read exact Git objects without changing the checkout or index."""

    def __init__(self, repository: str | Path) -> None:
        original = Path(repository)
        if original.is_symlink():
            raise FrontierError("prompt repository may not be a symlink")
        self.repository = original.resolve()
        if not (self.repository / ".git").exists():
            raise FrontierError("prompt repository is not a Git checkout")

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.repository), *args],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        if completed.returncode:
            raise FrontierError(f"Git object operation failed: {' '.join(args[:2])}")
        return completed.stdout

    def resolve_commit(self, commit: str) -> str:
        if not _COMMIT.fullmatch(commit):
            raise FrontierError("commit must be a full lowercase SHA-1")
        resolved = self._git("rev-parse", "--verify", f"{commit}^{{commit}}").strip()
        if resolved != commit:
            raise FrontierError("commit resolution differs from requested identity")
        return resolved

    def read(self, commit: str, path: str) -> bytes:
        self.resolve_commit(commit)
        safe_path = validate_relative_path(path)
        completed = subprocess.run(
            ["git", "-C", str(self.repository), "show", f"{commit}:{safe_path}"],
            check=False,
            capture_output=True,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        if completed.returncode:
            raise FrontierError(f"artifact is absent at exact commit: {safe_path}")
        return completed.stdout

    def exists(self, commit: str, path: str) -> bool:
        safe_path = validate_relative_path(path)
        completed = subprocess.run(
            ["git", "-C", str(self.repository), "cat-file", "-e", f"{commit}:{safe_path}"],
            check=False,
            capture_output=True,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        return completed.returncode == 0

    def verify_sha256(self, commit: str, path: str, expected: str) -> bytes:
        if not _SHA256.fullmatch(expected):
            raise FrontierError("expected SHA-256 is invalid")
        content = self.read(commit, path)
        if sha256(content).hexdigest() != expected:
            raise FrontierError(f"SHA-256 mismatch for {validate_relative_path(path)}")
        return content

    def require_ancestor(self, ancestor: str, descendant: str) -> None:
        self.resolve_commit(ancestor)
        self.resolve_commit(descendant)
        completed = subprocess.run(
            ["git", "-C", str(self.repository), "merge-base", "--is-ancestor", ancestor, descendant],
            check=False,
            capture_output=True,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        if completed.returncode:
            raise FrontierError("prompt commit does not descend from required control commit")

    def verify_prompt_identity(
        self,
        *,
        prompt_commit: str,
        prompt_path: str,
        expected_sha256: str,
        sidecar_path: str,
        authorization_profile: PromptAuthorizationProfile,
    ) -> VerifiedPromptIdentity:
        """Bind exact prompt bytes, sidecar, envelope, commit, and ancestry."""

        safe_prompt = validate_relative_path(prompt_path)
        safe_sidecar = validate_relative_path(sidecar_path)
        if safe_sidecar != f"{safe_prompt}.sha256":
            raise FrontierError("prompt sidecar must be adjacent to the prompt")
        prompt = self.verify_sha256(prompt_commit, safe_prompt, expected_sha256)
        try:
            envelope = PromptEnvelope.from_markdown(prompt.decode("utf-8", errors="strict"))
            sidecar = self.read(prompt_commit, safe_sidecar).decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise FrontierError("prompt or sidecar is not UTF-8") from exc
        lines = sidecar.splitlines()
        if len(lines) != 1:
            raise FrontierError("prompt sidecar must contain exactly one line")
        fields = lines[0].split()
        if len(fields) != 2 or fields[0] != expected_sha256 or fields[1] != Path(safe_prompt).name:
            raise FrontierError("prompt sidecar identity mismatch")
        envelope.require_authorization(authorization_profile)
        self.require_ancestor(envelope.prompt_control_base_commit, prompt_commit)
        return VerifiedPromptIdentity(
            safe_prompt,
            prompt_commit,
            expected_sha256,
            safe_sidecar,
            envelope,
            authorization_profile,
            _token=_VERIFIED_PROMPT_TOKEN,
        )


@dataclass(frozen=True)
class FrontierBinding:
    feature_id: str
    machine_model: str
    sequence: int
    prompt_commit: str
    prompt_sha256: str
    response_path: str
    result_identity: str

    def to_dict(self) -> JsonObject:
        payload: JsonObject = {
            "feature_id": self.feature_id,
            "machine_model": self.machine_model,
            "sequence": self.sequence,
            "prompt_commit": self.prompt_commit,
            "prompt_sha256": self.prompt_sha256,
            "response_path": self.response_path,
            "result_identity": self.result_identity,
        }
        validate_durable_payload(payload, location="frontier-binding")
        return payload


def guard_frontier(
    *,
    identity: VerifiedPromptIdentity,
    transport: GitPromptTransport,
    live_commit: str,
    feature_path: str = "Prompts/F017/FEATURE.yaml",
    state_path: str = "Prompts/F017/STATE.json",
) -> None:
    """Require the exact feature/machine/sequence frontier before lease claim."""

    transport.resolve_commit(live_commit)
    transport.require_ancestor(identity.prompt_commit, live_commit)
    envelope = identity.envelope
    envelope.require_authorization(identity.authorization_profile)
    manifest = FeatureManifest.from_yaml(transport.read(live_commit, feature_path).decode("utf-8"))
    state = FeatureState.from_json(transport.read(live_commit, state_path).decode("utf-8"))
    if manifest.status != state.state or state.state != FeatureLoopState.PROMPT_AVAILABLE.value:
        raise FrontierError("feature and state protocol status mismatch")
    if manifest.state_file != validate_relative_path(state_path):
        raise FrontierError("feature state path mismatch")
    if envelope.feature_id != manifest.feature_id or envelope.feature_id != state.feature_id:
        raise FrontierError("feature identity mismatch")
    expected_sequence = manifest.machine_sequences.get(envelope.machine_slug)
    if expected_sequence != envelope.sequence:
        raise FrontierError("feature manifest machine sequence mismatch")
    if state.current_machine != envelope.machine_slug or state.current_sequence != envelope.sequence:
        raise FrontierError("STATE.json machine frontier mismatch")
    if state.latest_prompt is None or (
        state.latest_prompt.path != identity.prompt_path
        or state.latest_prompt.commit != identity.prompt_commit
        or state.latest_prompt.sha256 != identity.prompt_sha256
    ):
        raise FrontierError("live state prompt identity mismatch")
    parent_identity = state.latest_response
    if parent_identity is None or parent_identity.path != envelope.expected_parent_response_path:
        raise FrontierError("expected parent response does not match STATE.json")
    if parent_identity.sha256 != envelope.expected_parent_response_sha256:
        raise FrontierError("expected parent response SHA-256 does not match STATE.json")
    if not parent_identity.complete:
        raise FrontierError("expected parent response identity is incomplete")
    assert parent_identity.commit is not None
    assert parent_identity.sequence is not None
    if parent_identity.sequence != envelope.sequence - 1:
        raise FrontierError("expected parent response sequence mismatch")
    if parent_identity.machine_model != envelope.machine_model or parent_identity.status != "PASS":
        raise FrontierError("expected parent response machine or status mismatch")
    transport.require_ancestor(parent_identity.commit, live_commit)
    parent = transport.verify_sha256(
        parent_identity.commit,
        envelope.expected_parent_response_path,
        envelope.expected_parent_response_sha256,
    )
    if not parent:
        raise FrontierError("expected parent response is empty")
    for path in (envelope.response_path, envelope.response_checksum_path, envelope.handoff_path):
        if transport.exists(identity.prompt_commit, path) or transport.exists(live_commit, path):
            raise FrontierError(f"terminal artifact already exists: {path}")


def claim_after_guards(
    controller: Controller,
    *,
    run_id: str,
    worker_id: str,
    idempotency_key: str,
    identity: VerifiedPromptIdentity,
    transport: GitPromptTransport,
    live_commit: str,
) -> TurnClaim:
    """Acquire the existing controller lease only after all Git guards pass."""

    guard_frontier(identity=identity, transport=transport, live_commit=live_commit)
    return controller.claim_turn(
        run_id,
        worker_id=worker_id,
        idempotency_key=idempotency_key,
    )


class ResolvedAlias:
    """Transport capability that refuses every default serialization route."""

    _alias: str
    __value: str

    __slots__ = ("__value", "_alias")

    def __init__(self, alias: str, value: str) -> None:
        object.__setattr__(self, "_alias", alias)
        object.__setattr__(self, "_ResolvedAlias__value", value)

    @property
    def alias(self) -> str:
        return self._alias

    def reveal_for_transport(self) -> str:
        return self.__value

    def __str__(self) -> str:
        return self.alias

    def __repr__(self) -> str:
        return f"ResolvedAlias(alias={self.alias!r})"

    def __copy__(self) -> NoReturn:
        raise FeatureLoopError("resolved aliases cannot be copied")

    def __deepcopy__(self, _memo: object) -> NoReturn:
        raise FeatureLoopError("resolved aliases cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise FeatureLoopError("resolved aliases cannot be pickled")

    def __reduce_ex__(self, _protocol: object) -> NoReturn:
        raise FeatureLoopError("resolved aliases cannot be pickled")


class LocalAliasResolver:
    """Resolve private aliases in memory while denying prohibited capabilities."""

    def __init__(self, values: Mapping[str, str], *, denied: Sequence[str] = ()) -> None:
        cleaned: dict[str, str] = {}
        for alias, value in values.items():
            if not alias.strip() or not value.strip():
                raise FeatureLoopError("alias names and values must be non-empty")
            cleaned[alias] = value
        if len(set(cleaned.values())) != len(cleaned):
            raise FeatureLoopError("alias values are ambiguous")
        if any(value in cleaned for value in cleaned.values()):
            raise FeatureLoopError("alias configuration is circular or alias-name-only")
        self._values = cleaned
        self._denied = frozenset(denied)
        self.requests: list[str] = []

    def resolve(self, alias: str) -> ResolvedAlias:
        self.requests.append(alias)
        if alias in self._denied:
            raise FeatureLoopError(f"alias resolution prohibited: {alias}")
        value = self._values.get(alias)
        if value is None:
            raise FeatureLoopError(f"alias is unavailable: {alias}")
        return ResolvedAlias(alias, value)


def validate_durable_payload(value: object, *, location: str = "$") -> None:
    """Reject capabilities and unsupported objects before durable serialization."""

    if isinstance(value, ResolvedAlias):
        raise FeatureLoopError(f"resolved alias prohibited in durable payload at {location}")
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise FeatureLoopError(f"durable payload key is not text at {location}")
            validate_durable_payload(item, location=f"{location}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            validate_durable_payload(item, location=f"{location}[{index}]")
        return
    raise FeatureLoopError(f"unsupported durable payload object at {location}: {type(value).__name__}")


_BUILTIN_PRIVACY_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "absolute-home-paths": re.compile(r"/(?:Users|home)/[^/\s]+(?:/[^\s]*)?"),
    "private-ip-addresses": re.compile(
        r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
    ),
    "internal-dns-names": re.compile(r"\b[A-Za-z0-9-]+\.(?:local|lan|internal)\b", re.I),
    "mac-addresses": re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"),
    "mount-and-share-names": re.compile(r"/(?:Volumes|mnt)/[^\s]+"),
    "credentials-tokens-cookies-and-secrets": re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{20,}|github_pat_[A-Za-z0-9_]{20,}|(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,})\b"
    ),
    "raw-private-chatgpt-conversation-urls": re.compile(
        r"https?://(?:chatgpt\.com|chat\.openai\.com)/(?:c|share)/", re.I
    ),
}

_CONTEXTUAL_PRIVACY_CATEGORIES = frozenset(
    {
        "personal-names",
        "local-usernames",
        "hostnames",
        "serial-numbers",
        "actual-notification-topic-names",
        "unrelated-client-tenant-or-lab-topology",
    }
)


class PrivacyScanner:
    def __init__(
        self,
        policy: PrivacyPolicy,
        *,
        category_markers: Mapping[str, Sequence[ResolvedAlias]] | None = None,
    ) -> None:
        self.policy = policy
        supplied = {key: tuple(values) for key, values in (category_markers or {}).items()}
        unknown_supplied = set(supplied) - set(policy.prohibited)
        if unknown_supplied:
            raise PrivacyError(f"privacy markers supplied for non-prohibited categories: {sorted(unknown_supplied)}")
        seen_values: set[str] = set()
        for category in policy.prohibited:
            if category not in _BUILTIN_PRIVACY_PATTERNS and category not in _CONTEXTUAL_PRIVACY_CATEGORIES:
                raise PrivacyError(f"privacy category has no detector: {category}")
            markers = supplied.get(category, ())
            if category in _CONTEXTUAL_PRIVACY_CATEGORIES and not markers:
                raise PrivacyError(f"privacy category requires private marker provider: {category}")
            for marker in markers:
                if not isinstance(marker, ResolvedAlias):
                    raise PrivacyError(f"privacy marker must be a resolved capability: {category}")
                value = marker.reveal_for_transport()
                if not value.strip() or value == marker.alias or value in seen_values:
                    raise PrivacyError(f"privacy marker is blank, circular, or ambiguous: {category}")
                seen_values.add(value)
        self.category_markers = supplied

    def scan(self, text: str) -> None:
        for category in self.policy.prohibited:
            pattern = _BUILTIN_PRIVACY_PATTERNS.get(category)
            if pattern is not None and pattern.search(text):
                raise PrivacyError(f"privacy category detected: {category}")
            for marker in self.category_markers.get(category, ()):
                if marker.reveal_for_transport() in text:
                    raise PrivacyError(f"privacy category detected: {category}")

    def scan_staged_diff(self, repository: str | Path) -> None:
        root = str(Path(repository).resolve())
        names = subprocess.run(
            ["git", "-C", root, "diff", "--cached", "--name-only", "--no-ext-diff"],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        diff = subprocess.run(
            ["git", "-C", root, "diff", "--cached", "--unified=0", "--no-ext-diff", "--no-color", "--text"],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        if names.returncode or diff.returncode:
            raise PrivacyError("unable to inspect complete staged publication diff")
        added = [line[1:] for line in diff.stdout.splitlines() if line.startswith("+") and not line.startswith("+++")]
        self.scan(f"{names.stdout}\n" + "\n".join(added))


class FeatureLoopState(StrEnum):
    PROMPT_AVAILABLE = "PROMPT_AVAILABLE"
    EXECUTING_CODEX = "EXECUTING_CODEX"
    RESPONSE_COMMITTED = "RESPONSE_COMMITTED"
    NTFY_DELIVERED = "NTFY_DELIVERED"
    CHAT_HANDOFF_PENDING = "CHAT_HANDOFF_PENDING"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class PublicationRequest:
    response_path: str
    checksum_path: str
    handoff_path: str
    state_path: str
    response_markdown: str
    feature_id: str
    sequence: int
    machine_model: str
    status: str
    prompt_path: str
    prompt_commit: str
    prompt_sha256: str
    response_commit: str
    result_identity: str
    state_document: JsonObject
    expected_state_sha256: str
    feature_path: str
    feature_document: str
    expected_feature_sha256: str


@dataclass(frozen=True)
class PublicationResult:
    status: str
    response_sha256: str
    completed_steps: tuple[str, ...]
    push_error: str | None = None


class PublicationCoordinator:
    """Prepare deterministic artifacts once and retry only incomplete transport."""

    def __init__(self, repository: str | Path, scanner: PrivacyScanner) -> None:
        original = Path(repository)
        if original.is_symlink():
            raise PublicationError("publication repository may not be a symlink")
        self.repository = original.resolve()
        self.scanner = scanner

    def _target(self, relative: str) -> Path:
        safe = validate_relative_path(relative)
        current = self.repository
        for part in Path(safe).parts[:-1]:
            current = current / part
            if current.exists() and current.is_symlink():
                raise PublicationError("publication path traverses a symlink")
        target = self.repository / safe
        if self.repository not in target.parent.resolve().parents and target.parent.resolve() != self.repository:
            raise PublicationError("publication target escapes repository")
        return target

    @staticmethod
    def _write_atomic(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.ultracode-partial")
        if temporary.exists():
            raise PublicationError("unexplained adjacent partial exists")
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            if temporary.exists():
                temporary.unlink()
            raise

    def _write_once(self, path: Path, content: bytes) -> None:
        if path.exists():
            if path.is_symlink() or path.read_bytes() != content:
                raise PublicationError(f"conflicting existing artifact: {path.name}")
            return
        self._write_atomic(path, content)

    def _replace_expected(self, path: Path, content: bytes, expected_sha256: str) -> None:
        if not _SHA256.fullmatch(expected_sha256):
            raise PublicationError("expected state SHA-256 is malformed")
        if not path.is_file() or path.is_symlink():
            raise PublicationError("expected feature state is absent or unsafe")
        current = path.read_bytes()
        if current == content:
            return
        if sha256(current).hexdigest() != expected_sha256:
            raise PublicationError("feature state frontier changed before publication")
        temporary = path.with_name(f".{path.name}.ultracode-partial")
        if temporary.exists():
            raise PublicationError("unexplained adjacent partial exists")
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            if temporary.exists():
                temporary.unlink()
            raise

    @staticmethod
    def _project_feature_document(document: str, *, status: str, machine_slug: str, sequence: int) -> bytes:
        before = FeatureManifest.from_yaml(document)
        lines = document.splitlines(keepends=True)
        status_hits = 0
        sequence_hits = 0
        in_sequences = False
        projected: list[str] = []
        for line in lines:
            if line.startswith("status:"):
                line = f"status: {status}\n"
                status_hits += 1
            if line.startswith("latest_machine_sequence:"):
                in_sequences = True
            elif in_sequences and line and not line.startswith("  "):
                in_sequences = False
            if in_sequences and line.startswith(f"  {machine_slug}:"):
                line = f"  {machine_slug}: {sequence}\n"
                sequence_hits += 1
            projected.append(line)
        if status_hits != 1 or sequence_hits != 1:
            raise PublicationError("feature document cannot be projected deterministically")
        result = "".join(projected)
        after = FeatureManifest.from_yaml(result)
        if after.feature_id != before.feature_id or after.state_file != before.state_file:
            raise PublicationError("feature projection changed immutable identity")
        if after.status != status or after.machine_sequences.get(machine_slug) != sequence:
            raise PublicationError("feature projection failed")
        return result.encode("utf-8")

    @staticmethod
    def _require_expected(path: Path, content: bytes, expected_sha256: str) -> None:
        if not _SHA256.fullmatch(expected_sha256):
            raise PublicationError("expected control-document SHA-256 is malformed")
        if not path.is_file() or path.is_symlink():
            raise PublicationError("expected control document is absent or unsafe")
        current = path.read_bytes()
        if current != content and sha256(current).hexdigest() != expected_sha256:
            raise PublicationError("control-document frontier changed before publication")

    def prepare(
        self,
        request: PublicationRequest,
        *,
        step_observer: Callable[[str], None] | None = None,
    ) -> PublicationResult:
        if request.sequence < 0 or not _COMMIT.fullmatch(request.prompt_commit):
            raise PublicationError("publication identity is malformed")
        if not _SHA256.fullmatch(request.prompt_sha256):
            raise PublicationError("publication prompt SHA-256 is malformed")
        if not _COMMIT.fullmatch(request.response_commit):
            raise PublicationError("response commit must be a full SHA-1")
        response_bytes = request.response_markdown.encode("utf-8")
        response_hash = sha256(response_bytes).hexdigest()
        checksum_bytes = f"{response_hash}  {Path(request.response_path).name}\n".encode()
        GitPromptTransport(self.repository).verify_sha256(
            request.response_commit,
            request.response_path,
            response_hash,
        )
        handoff: JsonObject = {
            "response_url": (
                "https://github.com/MahdiHedhli/PulsarMLX-Prompts/blob/"
                f"{request.response_commit}/"
                f"{validate_relative_path(request.response_path)}"
            ),
            "response_sha256": response_hash,
            "feature_id": request.feature_id,
            "sequence": request.sequence,
            "machine_model": request.machine_model,
            "status": request.status,
        }
        state = dict(request.state_document)
        state["state"] = FeatureLoopState.CHAT_HANDOFF_PENDING.value
        state["current_machine"] = request.machine_model.replace(" ", "-")
        state["current_sequence"] = request.sequence
        state["latest_prompt"] = {
            "path": validate_relative_path(request.prompt_path),
            "commit": request.prompt_commit,
            "sha256": request.prompt_sha256,
        }
        state["latest_response"] = {
            "path": request.response_path,
            "sha256": response_hash,
            "status": request.status,
            "result_identity": request.result_identity,
            "commit": request.response_commit,
            "machine_model": request.machine_model,
            "sequence": request.sequence,
        }
        feature_bytes = self._project_feature_document(
            request.feature_document,
            status=FeatureLoopState.CHAT_HANDOFF_PENDING.value,
            machine_slug=request.machine_model.replace(" ", "-"),
            sequence=request.sequence,
        )
        state_bytes = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode()
        validate_durable_payload(handoff, location="handoff")
        validate_durable_payload(state, location="state")
        payloads = (
            ("response", self._target(request.response_path), response_bytes),
            ("checksum", self._target(request.checksum_path), checksum_bytes),
            (
                "handoff",
                self._target(request.handoff_path),
                (json.dumps(handoff, indent=2, sort_keys=True) + "\n").encode(),
            ),
            ("feature", self._target(request.feature_path), feature_bytes),
            ("state", self._target(request.state_path), state_bytes),
        )
        for _name, _path, content in payloads:
            self.scanner.scan(content.decode("utf-8"))
        feature_target = self._target(request.feature_path)
        state_target = self._target(request.state_path)
        self._require_expected(feature_target, feature_bytes, request.expected_feature_sha256)
        self._require_expected(state_target, state_bytes, request.expected_state_sha256)
        completed: list[str] = []
        for name, path, content in payloads:
            if name == "feature":
                self._replace_expected(path, content, request.expected_feature_sha256)
            elif name == "state":
                self._replace_expected(path, content, request.expected_state_sha256)
            else:
                self._write_once(path, content)
            completed.append(name)
            if step_observer is not None:
                step_observer(name)
        return PublicationResult("PREPARED", response_hash, tuple(completed))

    def prepare_response(self, request: PublicationRequest) -> PublicationResult:
        """Prepare only response bytes and checksum before their identity commit."""

        response_bytes = request.response_markdown.encode("utf-8")
        response_hash = sha256(response_bytes).hexdigest()
        checksum_bytes = f"{response_hash}  {Path(request.response_path).name}\n".encode()
        self.scanner.scan(request.response_markdown)
        self.scanner.scan(checksum_bytes.decode())
        self._write_once(self._target(request.response_path), response_bytes)
        self._write_once(self._target(request.checksum_path), checksum_bytes)
        return PublicationResult("RESPONSE_PREPARED", response_hash, ("response", "checksum"))

    def publish(
        self,
        request: PublicationRequest,
        push: Callable[[], None],
        *,
        step_observer: Callable[[str], None] | None = None,
    ) -> PublicationResult:
        prepared = self.prepare(request, step_observer=step_observer)
        try:
            push()
        except Exception as exc:
            return PublicationResult(
                "PUSH_PENDING",
                prepared.response_sha256,
                prepared.completed_steps,
                type(exc).__name__,
            )
        return PublicationResult("PUBLISHED", prepared.response_sha256, (*prepared.completed_steps, "push"))


def notification_record(
    *,
    alias: str,
    resolver: LocalAliasResolver,
    transport: Callable[[str, str], None],
    feature_id: str,
    sequence: int,
    artifact_identity: str,
) -> JsonObject:
    """Deliver through a private value while returning only allowlisted evidence."""

    resolved = resolver.resolve(alias)
    message = f"{feature_id} | sequence {sequence} | response {artifact_identity}"
    status = "PASS"
    try:
        transport(resolved.reveal_for_transport(), message)
    except Exception:
        status = "FAIL"
    record: JsonObject = {
        "topic_alias": alias,
        "status": status,
        "feature_id": feature_id,
        "sequence": sequence,
        "artifact_identity": artifact_identity,
    }
    validate_durable_payload(record, location="notification")
    return record


@dataclass(frozen=True)
class RepositoryFingerprint:
    head: str
    tree: str
    branch: str
    tracked_clean: bool

    @classmethod
    def capture(cls, repository: str | Path) -> RepositoryFingerprint:
        root = Path(repository).resolve()

        def git(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", "-C", str(root), *args],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            )

        head = git("rev-parse", "HEAD")
        tree = git("rev-parse", "HEAD^{tree}")
        branch = git("branch", "--show-current")
        unstaged = git("diff", "--quiet")
        staged = git("diff", "--cached", "--quiet")
        if any(item.returncode not in {0, 1} for item in (unstaged, staged)) or any(
            item.returncode for item in (head, tree, branch)
        ):
            raise FeatureLoopError("repository fingerprint failed")
        return cls(
            head=head.stdout.strip(),
            tree=tree.stdout.strip(),
            branch=branch.stdout.strip(),
            tracked_clean=unstaged.returncode == 0 and staged.returncode == 0,
        )


def bind_frontier(controller: Controller, run_id: str, binding: FrontierBinding) -> str:
    """Persist an alias-free Feature Loop identity beside controller events."""

    return controller.add_artifact(run_id, "feature-loop-binding", binding.to_dict())
