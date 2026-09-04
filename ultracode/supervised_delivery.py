"""Fail-closed F017 D8 supervised Codex task delivery boundary."""

from __future__ import annotations

import fcntl
import io
import json
import os
import plistlib
import posixpath
import re
import secrets
import select
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO, NoReturn, SupportsIndex, TextIO, cast
from weakref import WeakKeyDictionary

from .protocol import ExecutionResult, ResultStatus, ValidationError

__all__ = (
    "APP_SERVER_ARGV",
    "D8_POLICY_SHA256",
    "DISCOVERY_PROTOCOL_PROFILE",
    "DeliveryError",
    "DeliveryOutcome",
    "DeliveryPreview",
    "ProtocolProfile",
    "ThreadListing",
    "ThreadListingEntry",
    "confirm_preview",
    "deliver_foreground",
    "preflight_qualification_result",
)

D8_POLICY_ID = "f017-m2-d8-supervised-chat-delivery-transport-v1"
D8_POLICY_SHA256 = "db58a1e73a934719f4df7b9e07a4217a289cb8f4b3b748ce16a0e537df8036b6"
APP_SERVER_ARGV = ("app-server", "--listen", "stdio://")
_CODEX_EXECUTABLE = Path("/Applications/Codex.app/Contents/Resources/codex")
_MAX_MESSAGE_BYTES = 64 * 1024
_MAX_LINE_BYTES = 1024 * 1024
_MAX_JSON_DEPTH = 16
_MAX_JSON_NODES = 8192
_MAX_STDERR_BYTES = 64 * 1024
_MAX_LIST_PAGES = 1024
_MAX_INTERLEAVED_NOTIFICATIONS = 256
_DEFAULT_SESSION_SECONDS = 60.0
_DEFAULT_OPERATION_SECONDS = 5.0
_CLEANUP_TAIL_SECONDS = 0.5
_CLEANUP_SECONDS = 3.0
_SYMBOL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_EXPECTED_CODEX_AUTHORITY = "Developer ID Application: OpenAI OpCo, LLC (2DC432GLL2)"
_EXPECTED_CODEX_TEAM_ID = "2DC432GLL2"
_SYNTHETIC_USER_AGENT = "codex-cli synthetic"
_CODEX_CLI_VERSION = re.compile(r"codex-cli [0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?\Z")
_CODEX_APP_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)+\Z")
_CODEX_APP_BUILD = re.compile(r"[1-9][0-9]*\Z")
_QUALIFICATION_RESULT = "D8_SUPERVISED_DELIVERY_STABLE_ELIGIBILITY_RESULT: PASS_PENDING_PLANNER_REVIEW"
_SECRET_BYTES = (
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(rb"(?i)bearer\s+[A-Za-z0-9._~+/-]{12,}"),
    re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token|secret|password|credential)\s*[=:]"),
)


class DeliveryError(RuntimeError):
    """The closed D8 delivery contract was violated."""


class DeliveryOutcome(StrEnum):
    DELIVERED = "DELIVERED"
    FAILED_BEFORE_WRITE = "FAILED_BEFORE_WRITE"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True, slots=True)
class ProtocolProfile:
    request_methods: frozenset[str]
    client_notifications: frozenset[str]
    server_notifications: frozenset[str]
    schema_names: frozenset[str]


@dataclass(slots=True)
class _OperationCounters:
    real_app_server_launches: int = 0
    real_alias_resolutions: int = 0
    real_task_reads: int = 0
    real_task_resumes: int = 0
    real_turns: int = 0
    real_posts: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "app_server_launches": self.real_app_server_launches,
            "posts": self.real_posts,
            "real_alias_resolutions": self.real_alias_resolutions,
            "real_task_reads": self.real_task_reads,
            "real_task_resumes": self.real_task_resumes,
            "real_turns": self.real_turns,
        }


PROTOCOL_PROFILE = ProtocolProfile(
    request_methods=frozenset({"initialize", "thread/list", "thread/read", "thread/resume", "turn/start"}),
    client_notifications=frozenset({"initialized"}),
    server_notifications=frozenset({"thread/status/changed", "turn/started", "turn/completed", "error"}),
    schema_names=frozenset(
        {
            "v1/InitializeParams.json",
            "v1/InitializeResponse.json",
            "v2/ErrorNotification.json",
            "v2/ThreadListParams.json",
            "v2/ThreadListResponse.json",
            "v2/ThreadReadParams.json",
            "v2/ThreadReadResponse.json",
            "v2/ThreadResumeParams.json",
            "v2/ThreadResumeResponse.json",
            "v2/ThreadStatusChangedNotification.json",
            "v2/TurnCompletedNotification.json",
            "v2/TurnStartParams.json",
            "v2/TurnStartResponse.json",
            "v2/TurnStartedNotification.json",
        }
    ),
)

DISCOVERY_PROTOCOL_PROFILE = ProtocolProfile(
    request_methods=frozenset({"initialize", "thread/list"}),
    client_notifications=frozenset({"initialized"}),
    server_notifications=frozenset({"thread/status/changed"}),
    schema_names=frozenset(
        {
            "v1/InitializeParams.json",
            "v1/InitializeResponse.json",
            "v2/ThreadListParams.json",
            "v2/ThreadListResponse.json",
            "v2/ThreadStatusChangedNotification.json",
        }
    ),
)


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True).encode(
            "ascii"
        )
    except (RecursionError, TypeError, ValueError) as exc:
        raise DeliveryError("value is not canonical JSON") from exc


def _sha(value: bytes) -> str:
    return sha256(value).hexdigest()


def _symbol(value: str, field: str) -> str:
    if type(value) is not str or not (3 <= len(value) <= 64) or any(ch not in _SYMBOL_CHARS for ch in value):
        raise DeliveryError(f"{field} must be a closed ASCII symbol")
    return value


def _strict_object(raw: bytes, *, label: str, max_bytes: int = _MAX_LINE_BYTES) -> dict[str, object]:
    if type(raw) is not bytes or not raw or len(raw) > max_bytes:
        raise DeliveryError(f"{label} is outside the size contract")
    duplicate = False

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        nonlocal duplicate
        result: dict[str, object] = {}
        for key, value in items:
            duplicate |= key in result
            result[key] = value
        return result

    def bad_constant(_value: str) -> NoReturn:
        raise DeliveryError(f"{label} contains a non-finite value")

    try:
        parsed = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs, parse_constant=bad_constant)
    except DeliveryError:
        raise
    except (RecursionError, UnicodeError, ValueError) as exc:
        raise DeliveryError(f"{label} is not valid JSON") from exc
    if duplicate or type(parsed) is not dict:
        raise DeliveryError(f"{label} must be one duplicate-free object")
    nodes = 0

    def bound(value: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if depth > _MAX_JSON_DEPTH or nodes > _MAX_JSON_NODES:
            raise DeliveryError(f"{label} exceeds the structural bound")
        if type(value) is dict:
            for key, item in cast(dict[object, object], value).items():
                if type(key) is not str or len(key) > 256:
                    raise DeliveryError(f"{label} contains an invalid key")
                bound(item, depth + 1)
        elif type(value) is list:
            for item in cast(list[object], value):
                bound(item, depth + 1)
        elif type(value) is str and len(value) > _MAX_LINE_BYTES:
            raise DeliveryError(f"{label} contains an oversized string")

    bound(parsed, 0)
    return cast(dict[str, object], parsed)


@dataclass(slots=True)
class _Deadline:
    session_end: float
    operation_seconds: float

    @classmethod
    def start(
        cls,
        *,
        session_seconds: float = _DEFAULT_SESSION_SECONDS,
        operation_seconds: float = _DEFAULT_OPERATION_SECONDS,
    ) -> _Deadline:
        if session_seconds <= 0 or operation_seconds <= 0 or operation_seconds > session_seconds:
            raise DeliveryError("deadline configuration violates the closed contract")
        return cls(time.monotonic() + session_seconds, operation_seconds)

    def timeout(self, operation: str) -> float:
        remaining = self.session_end - time.monotonic()
        if remaining <= 0:
            raise DeliveryError(f"{operation} exceeded the session deadline")
        return min(remaining, self.operation_seconds)

    def check(self, operation: str) -> None:
        if time.monotonic() >= self.session_end:
            raise DeliveryError(f"{operation} exceeded the session deadline")


@dataclass(frozen=True, slots=True)
class _ExecutableRecord:
    path: Path
    device: int
    inode: int
    size: int
    mode: int
    uid: int
    mtime_ns: int
    sha256: str


def _executable_boundary() -> tuple[type[object], Callable[[Path], object], Callable[[object], _ExecutableRecord]]:
    token = object()
    records: WeakKeyDictionary[object, _ExecutableRecord] = WeakKeyDictionary()

    class ExecutableAuthority:
        __slots__ = ("__weakref__",)

        def __init__(self, record: _ExecutableRecord, authority: object) -> None:
            if authority is not token:
                raise DeliveryError("executable authority constructor is closed")
            records[self] = record

        def __copy__(self) -> NoReturn:
            raise DeliveryError("executable authority cannot be copied")

        def __deepcopy__(self, _memo: object) -> NoReturn:
            raise DeliveryError("executable authority cannot be copied")

        def __reduce__(self) -> NoReturn:
            raise DeliveryError("executable authority cannot be serialized")

    def inspect(path: Path) -> _ExecutableRecord:
        if not isinstance(path, Path) or not path.is_absolute():
            raise DeliveryError("Codex executable must be an explicit absolute native path")
        current = path
        chain: list[Path] = []
        while True:
            chain.append(current)
            if current == current.parent:
                break
            current = current.parent
        for component in chain:
            try:
                info = component.lstat()
            except OSError as exc:
                raise DeliveryError("Codex executable authority is unavailable") from exc
            if stat.S_ISLNK(info.st_mode):
                raise DeliveryError("Codex executable authority must not traverse symlinks")
            if component != path and (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.getuid()}):
                raise DeliveryError("Codex executable parent authority is invalid")
            canonical_applications = (
                component == Path("/Applications")
                and info.st_uid == 0
                and bool(info.st_mode & 0o020)
                and not bool(info.st_mode & 0o002)
            )
            if component != path and info.st_mode & 0o022 and not canonical_applications:
                raise DeliveryError("Codex executable parent is writable by another principal")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise DeliveryError("Codex executable cannot be opened safely") from exc
        digest = sha256()
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid not in {0, os.getuid()}
                or info.st_mode & 0o022
                or not info.st_mode & 0o111
            ):
                raise DeliveryError("Codex executable ownership or mode is unsafe")
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        finally:
            os.close(fd)
        return _ExecutableRecord(
            path=path,
            device=info.st_dev,
            inode=info.st_ino,
            size=info.st_size,
            mode=info.st_mode,
            uid=info.st_uid,
            mtime_ns=info.st_mtime_ns,
            sha256=digest.hexdigest(),
        )

    def seal(path: Path) -> object:
        return ExecutableAuthority(inspect(path), token)

    def validate(authority: object) -> _ExecutableRecord:
        if type(authority) is not ExecutableAuthority:
            raise DeliveryError("Codex executable authority is not sealed")
        expected = records.get(authority)
        if expected is None or inspect(expected.path) != expected:
            raise DeliveryError("Codex executable authority drifted")
        return expected

    return ExecutableAuthority, seal, validate


_ExecutableAuthority, _seal_executable, _validate_executable = _executable_boundary()


def _minimal_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    inherited = source if source is not None else os.environ
    allowed = ("HOME", "TMPDIR", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "CODEX_HOME")
    environment = {key: inherited[key] for key in allowed if inherited.get(key)}
    environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    return environment


_FIXED_IDENTITY_PROBES = frozenset(
    {
        (str(_CODEX_EXECUTABLE), "--version"),
        ("/usr/bin/codesign", "--verify", "--strict", "--verbose=2", str(_CODEX_EXECUTABLE)),
        ("/usr/bin/codesign", "-dv", "--verbose=4", str(_CODEX_EXECUTABLE)),
    }
)


def _run_fixed_identity_probe(argv: tuple[str, ...]) -> tuple[bytes, bytes]:
    if argv not in _FIXED_IDENTITY_PROBES:
        raise DeliveryError("identity probe is outside the fixed command allowlist")
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_minimal_environment(),
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise DeliveryError("cannot launch installed Codex Desktop identity probe") from exc
    try:
        stdout, stderr = process.communicate(timeout=10)
    except BaseException as exc:
        group_issue: OSError | None = None
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as cleanup_exc:
            group_issue = cleanup_exc
            try:
                process.kill()
            except ProcessLookupError:
                pass
            except OSError:
                pass
        try:
            stdout, stderr = process.communicate(timeout=3)
        except (OSError, subprocess.SubprocessError) as cleanup_exc:
            raise DeliveryError("identity probe process could not be reaped") from cleanup_exc
        if group_issue is not None:
            raise DeliveryError(
                "identity probe process group could not be contained; direct child reaped"
            ) from group_issue
        raise DeliveryError("installed Codex Desktop identity probe did not complete") from exc
    if process.returncode != 0:
        raise DeliveryError("installed Codex Desktop identity probe failed")
    if len(stdout) > _MAX_STDERR_BYTES or len(stderr) > _MAX_STDERR_BYTES:
        raise DeliveryError("installed Codex Desktop identity probe exceeded its output bound")
    return stdout, stderr


def _read_app_info_plist() -> Mapping[str, object]:
    path = _CODEX_EXECUTABLE.parents[1] / "Info.plist"
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise DeliveryError("Codex Desktop application identity is unavailable") from exc
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or info.st_mode & 0o022
            or info.st_size <= 0
            or info.st_size > _MAX_LINE_BYTES
        ):
            raise DeliveryError("Codex Desktop application identity is unsafe")
        raw = bytearray()
        while len(raw) < info.st_size:
            chunk = os.read(fd, min(65536, info.st_size - len(raw)))
            if not chunk:
                raise DeliveryError("Codex Desktop application identity changed during read")
            raw.extend(chunk)
        if os.read(fd, 1):
            raise DeliveryError("Codex Desktop application identity changed during read")
    finally:
        os.close(fd)
    try:
        value = plistlib.loads(bytes(raw))
    except (plistlib.InvalidFileException, ValueError) as exc:
        raise DeliveryError("Codex Desktop application identity is invalid") from exc
    if type(value) is not dict:
        raise DeliveryError("Codex Desktop application identity is invalid")
    return cast(dict[str, object], value)


@dataclass(frozen=True, slots=True)
class _DesktopIdentity:
    cli_version: str
    authority: str
    team_id: str
    app_version: str
    app_build: str


def _inspect_desktop_identity() -> _DesktopIdentity:
    try:
        version_stdout, _version_stderr = _run_fixed_identity_probe((str(_CODEX_EXECUTABLE), "--version"))
        _run_fixed_identity_probe(("/usr/bin/codesign", "--verify", "--strict", "--verbose=2", str(_CODEX_EXECUTABLE)))
        _signature_stdout, signature_stderr = _run_fixed_identity_probe(
            ("/usr/bin/codesign", "-dv", "--verbose=4", str(_CODEX_EXECUTABLE))
        )
        version = version_stdout.decode("utf-8", "strict").strip()
        signature = signature_stderr.decode("utf-8", "strict")
        info = _read_app_info_plist()
    except (OSError, UnicodeError, ValueError) as exc:
        raise DeliveryError("cannot establish installed Codex Desktop identity") from exc
    authority = next(
        (line.removeprefix("Authority=") for line in signature.splitlines() if line.startswith("Authority=")),
        "",
    )
    team_id = next(
        (line.removeprefix("TeamIdentifier=") for line in signature.splitlines() if line.startswith("TeamIdentifier=")),
        "",
    )
    return _DesktopIdentity(
        cli_version=version,
        authority=authority,
        team_id=team_id,
        app_version=str(info.get("CFBundleShortVersionString", "")),
        app_build=str(info.get("CFBundleVersion", "")),
    )


def _require_production_identity(authority: object) -> _DesktopIdentity:
    _validate_executable(authority)
    identity = _inspect_desktop_identity()
    if (
        identity.authority != _EXPECTED_CODEX_AUTHORITY
        or identity.team_id != _EXPECTED_CODEX_TEAM_ID
        or _CODEX_CLI_VERSION.fullmatch(identity.cli_version) is None
        or _CODEX_APP_VERSION.fullmatch(identity.app_version) is None
        or _CODEX_APP_BUILD.fullmatch(identity.app_build) is None
    ):
        raise DeliveryError("installed Codex Desktop identity does not match the trusted production identity")
    return identity


def _seal_production_executable() -> object:
    authority = _seal_executable(_CODEX_EXECUTABLE)
    _require_production_identity(authority)
    return authority


def preflight_qualification_result(data: Mapping[str, object]) -> ExecutionResult:
    """Construct the production result envelope before a controller claim."""
    try:
        result = ExecutionResult.from_dict(data)
    except ValidationError as exc:
        raise DeliveryError("controller result envelope is invalid") from exc
    if result.status is not ResultStatus.COMPLETED:
        raise DeliveryError("successful qualification result status must be completed")
    if result.summary != _QUALIFICATION_RESULT or result.blockers:
        raise DeliveryError("qualification semantics or blockers violate the closed result contract")
    return result


def _direct_path(path: Path, label: str) -> Path:
    if not isinstance(path, Path):
        raise DeliveryError(f"{label} path must use the native path type")
    absolute = path.absolute()
    try:
        resolved_parent = absolute.parent.resolve(strict=True)
    except OSError as exc:
        raise DeliveryError(f"{label} parent is unavailable") from exc
    if resolved_parent != absolute.parent:
        raise DeliveryError(f"{label} parent must not traverse a symlink")
    return absolute


def _durable_sync(fd: int, *, full_storage: bool) -> None:
    os.fsync(fd)
    if full_storage and sys.platform == "darwin":
        try:
            fcntl.fcntl(fd, 51)  # F_FULLFSYNC from <sys/fcntl.h>.
        except OSError as exc:
            raise DeliveryError("full storage synchronization failed") from exc


def _sync_parent(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path.parent, flags)
    try:
        _durable_sync(fd, full_storage=False)
    finally:
        os.close(fd)


def _write_exclusive_owned(path: Path, data: bytes) -> None:
    path = _direct_path(path, "fixture")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise DeliveryError("fixture destination must be a new owned file") from exc
    try:
        written = os.write(fd, data)
        if written != len(data):
            raise DeliveryError("fixture write was partial")
        _durable_sync(fd, full_storage=True)
    finally:
        os.close(fd)
    _sync_parent(path)


@dataclass(frozen=True, slots=True)
class DeliveryPreview:
    target_alias: str
    message: bytes
    policy_id: str = D8_POLICY_ID
    policy_sha256: str = D8_POLICY_SHA256

    def __post_init__(self) -> None:
        _symbol(self.target_alias, "target_alias")
        if type(self.message) is not bytes or not self.message or len(self.message) > _MAX_MESSAGE_BYTES:
            raise DeliveryError("message is outside the bounded byte contract")
        try:
            text = self.message.decode("utf-8", "strict")
        except UnicodeError as exc:
            raise DeliveryError("message must be strict UTF-8") from exc
        if "\x00" in text or self.policy_id != D8_POLICY_ID or self.policy_sha256 != D8_POLICY_SHA256:
            raise DeliveryError("preview violates the closed D8 policy")

    @property
    def payload_sha256(self) -> str:
        return _sha(self.message)

    @property
    def preview_sha256(self) -> str:
        return _sha(self.render().encode("utf-8"))

    def render(self) -> str:
        text = self.message.decode("utf-8")
        return (
            "=== 42 ULTRACODE SUPERVISED DELIVERY ===\n"
            f"policy: {self.policy_id}\n"
            f"target-alias: {self.target_alias}\n"
            f"payload-sha256: {self.payload_sha256}\n"
            f"payload-bytes: {len(self.message)}\n"
            "exact-payload-json: "
            f"{json.dumps(text, ensure_ascii=True)}"
        )


def _require_interactive_tty(input_stream: TextIO, output_stream: TextIO) -> None:
    try:
        input_tty = input_stream.isatty()
        output_tty = output_stream.isatty()
    except (AttributeError, OSError) as exc:
        raise DeliveryError("confirmation requires terminal streams") from exc
    if not input_tty or not output_tty:
        raise DeliveryError("confirmation requires an interactive TTY")


def _confirmation_boundary() -> tuple[object, object]:
    token = object()
    records: WeakKeyDictionary[object, tuple[str, str, float, list[bool], threading.Lock]] = WeakKeyDictionary()

    class Capability:
        __slots__ = ("__weakref__",)

        def __init__(self, *, preview: DeliveryPreview, expires_at: float, authority: object) -> None:
            if authority is not token:
                raise DeliveryError("capability constructor is closed")
            records[self] = (preview.preview_sha256, preview.target_alias, expires_at, [False], threading.Lock())

        def __copy__(self) -> NoReturn:
            raise DeliveryError("capability cannot be copied")

        def __deepcopy__(self, _memo: object) -> NoReturn:
            raise DeliveryError("capability cannot be copied")

        def __reduce__(self) -> NoReturn:
            raise DeliveryError("capability cannot be serialized")

        def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
            raise DeliveryError("capability cannot be serialized")

    def confirm(
        preview: DeliveryPreview,
        input_stream: TextIO,
        output_stream: TextIO,
        ttl_seconds: int,
    ) -> object:
        if type(preview) is not DeliveryPreview or type(ttl_seconds) is not int or not (1 <= ttl_seconds <= 300):
            raise DeliveryError("confirmation arguments violate the closed contract")
        _require_interactive_tty(input_stream, output_stream)
        challenge = secrets.token_hex(8).upper()
        output_stream.write(preview.render())
        output_stream.write(f"\nconfirmation-challenge: {challenge}\nre-enter challenge exactly: ")
        output_stream.flush()
        answer = input_stream.readline(128)
        if answer not in {challenge + "\n", challenge + "\r\n"}:
            raise DeliveryError("confirmation challenge mismatch")
        return Capability(preview=preview, expires_at=time.monotonic() + ttl_seconds, authority=token)

    def consume(capability: object, preview: DeliveryPreview) -> None:
        if type(preview) is not DeliveryPreview or type(capability) is not Capability:
            raise DeliveryError("capability is not sealed by the confirmation boundary")
        record = records.get(capability)
        if record is None:
            raise DeliveryError("capability is not sealed by the confirmation boundary")
        preview_sha256, alias, expires_at, used, lock = record
        with lock:
            if used[0]:
                raise DeliveryError("confirmation capability was already consumed")
            used[0] = True
            if time.monotonic() > expires_at:
                raise DeliveryError("confirmation capability expired")
            if (preview.preview_sha256, preview.target_alias) != (preview_sha256, alias):
                raise DeliveryError("confirmation capability does not match the preview")

    return confirm, consume


_confirm_capability, _consume_capability = _confirmation_boundary()


def confirm_preview(
    preview: DeliveryPreview,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    ttl_seconds: int = 60,
) -> object:
    """Require exact re-entry of a fresh TTY challenge for one preview."""
    confirm = _confirm_capability
    if not callable(confirm):
        raise DeliveryError("confirmation boundary is unavailable")
    return confirm(preview, input_stream, output_stream, ttl_seconds)


def _read_owned_regular(path: Path, *, max_bytes: int) -> bytes:
    path = _direct_path(path, "input")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise DeliveryError("route registry cannot be opened safely") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise DeliveryError("route registry ownership or mode is unsafe")
        if info.st_size <= 0 or info.st_size > max_bytes:
            raise DeliveryError("route registry is outside the size contract")
        chunks: list[bytes] = []
        remaining = info.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                raise DeliveryError("route registry changed during read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise DeliveryError("route registry changed during read")
        return b"".join(chunks)
    finally:
        os.close(fd)


@dataclass(frozen=True, slots=True)
class _RouteAuthority:
    thread_id: str
    source_kind: str
    cwd: str


@dataclass(frozen=True, slots=True)
class ThreadListingEntry:
    thread_id: str
    source_kind: str
    cwd: str
    status: str
    name: str | None
    preview: str


@dataclass(frozen=True, slots=True)
class ThreadListing:
    entries: tuple[ThreadListingEntry, ...]
    active_identities: frozenset[str]
    archived_identities: frozenset[str]
    rejected_entries: int


def _resolve_alias(path: Path, alias: str) -> _RouteAuthority:
    alias = _symbol(alias, "target_alias")
    data = _strict_object(_read_owned_regular(path, max_bytes=65536), label="route registry", max_bytes=65536)
    if set(data) != {"aliases", "version"} or data["version"] != 2 or type(data["aliases"]) is not dict:
        raise DeliveryError("route registry violates the closed schema")
    aliases = cast(dict[object, object], data["aliases"])
    if any(type(key) is not str or type(value) is not dict for key, value in aliases.items()):
        raise DeliveryError("route registry entries must be sealed objects")
    selected = aliases.get(alias)
    if type(selected) is not dict or set(selected) != {"cwd", "source_kind", "thread_id"}:
        raise DeliveryError("target alias is missing or invalid")
    route = cast(dict[object, object], selected)
    thread_id = route.get("thread_id")
    source_kind = route.get("source_kind")
    cwd = route.get("cwd")
    safe_thread_chars = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
    if (
        type(thread_id) is not str
        or not thread_id
        or len(thread_id) > 256
        or any(char not in safe_thread_chars for char in thread_id)
    ):
        raise DeliveryError("target alias is missing or invalid")
    if type(source_kind) is not str or source_kind not in {"cli", "vscode", "exec", "appServer"}:
        raise DeliveryError("target source kind is not a stable simple source")
    if (
        type(cwd) is not str
        or not cwd.startswith("/")
        or len(cwd) > 4096
        or "\x00" in cwd
        or posixpath.normpath(cwd) != cwd
        or "//" in cwd
        or any(component in {".", ".."} for component in cwd.split("/"))
    ):
        raise DeliveryError("target cwd is not an absolute bounded path")
    return _RouteAuthority(thread_id, source_kind, cwd)


class _Journal:
    _EVENTS = frozenset({"ATTEMPT_STARTED", "DELIVERED", "FAILED_BEFORE_WRITE", "UNCERTAIN"})

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd = -1
        self._records: list[dict[str, object]] = []

    def __enter__(self) -> _Journal:
        self._path = _direct_path(self._path, "journal")
        common = os.O_RDWR | os.O_APPEND | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        created = False
        try:
            self._fd = os.open(self._path, common | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            self._fd = os.open(self._path, common)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = os.fstat(self._fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise DeliveryError("journal ownership or mode is unsafe")
            if not created and info.st_size == 0:
                raise DeliveryError("an unexplained empty journal cannot be adopted")
            if created:
                _sync_parent(self._path)
            os.lseek(self._fd, 0, os.SEEK_SET)
            raw = b""
            while True:
                chunk = os.read(self._fd, 65536)
                if not chunk:
                    break
                raw += chunk
                if len(raw) > 8 * 1024 * 1024:
                    raise DeliveryError("journal exceeds the bounded size")
            self._records = self._validate(raw)
            return self
        except BaseException:
            os.close(self._fd)
            self._fd = -1
            raise

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    @classmethod
    def _validate(cls, raw: bytes) -> list[dict[str, object]]:
        if not raw:
            return []
        if not raw.endswith(b"\n"):
            raise DeliveryError("journal has a partial terminal record")
        records: list[dict[str, object]] = []
        previous = "0" * 64
        fixed = {
            "attempt_id",
            "event",
            "ordinal",
            "payload_sha256",
            "previous_sha256",
            "record_sha256",
            "target_alias",
            "thread_id_sha256",
        }
        for ordinal, line in enumerate(raw.splitlines(), 1):
            record = _strict_object(line, label="journal record")
            if set(record) != fixed or record["ordinal"] != ordinal or record["previous_sha256"] != previous:
                raise DeliveryError("journal sequence or schema is invalid")
            if type(record["event"]) is not str or record["event"] not in cls._EVENTS:
                raise DeliveryError("journal event is not allowed")
            claimed = record["record_sha256"]
            unsigned = dict(record)
            unsigned["record_sha256"] = ""
            if type(claimed) is not str or claimed != _sha(_canonical(unsigned)):
                raise DeliveryError("journal hash chain is invalid")
            previous = claimed
            records.append(record)
        return records

    def terminal_for(self, *, payload_sha256: str, target_alias: str) -> DeliveryOutcome | None:
        matching = [
            record
            for record in self._records
            if record["payload_sha256"] == payload_sha256 and record["target_alias"] == target_alias
        ]
        if not matching:
            return None
        event = matching[-1]["event"]
        if event == "DELIVERED":
            return DeliveryOutcome.DELIVERED
        if event == "FAILED_BEFORE_WRITE":
            return DeliveryOutcome.FAILED_BEFORE_WRITE
        return DeliveryOutcome.UNCERTAIN

    def append(
        self,
        *,
        event: str,
        attempt_id: str,
        preview: DeliveryPreview,
        thread_id: str,
    ) -> None:
        if event not in self._EVENTS or self._fd < 0:
            raise DeliveryError("journal append violates the closed contract")
        previous = cast(str, self._records[-1]["record_sha256"]) if self._records else "0" * 64
        record: dict[str, object] = {
            "attempt_id": attempt_id,
            "event": event,
            "ordinal": len(self._records) + 1,
            "payload_sha256": preview.payload_sha256,
            "previous_sha256": previous,
            "record_sha256": "",
            "target_alias": preview.target_alias,
            "thread_id_sha256": _sha(thread_id.encode("utf-8")),
        }
        record["record_sha256"] = _sha(_canonical(record))
        line = _canonical(record) + b"\n"
        written = os.write(self._fd, line)
        if written != len(line):
            raise DeliveryError("journal append was partial")
        _durable_sync(self._fd, full_storage=True)
        self._records.append(record)


class _JsonlSession:
    def __init__(
        self,
        reader: BinaryIO,
        writer: BinaryIO,
        *,
        real_operations: bool = False,
        counters: _OperationCounters | None = None,
        deadline: _Deadline | None = None,
        health_check: Callable[[], None] | None = None,
        expected_user_agent: str = _SYNTHETIC_USER_AGENT,
        protocol_profile: ProtocolProfile = PROTOCOL_PROFILE,
        ephemeral_field_required: bool = True,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._real_operations = real_operations
        self._counters = counters or _OperationCounters()
        self._next_id = 1
        self._turn_start_count = 0
        self._turn_request_written = False
        self._deadline = deadline or _Deadline.start()
        self._health_check = health_check or (lambda: None)
        self._expected_user_agent = expected_user_agent
        self._protocol_profile = protocol_profile
        self._ephemeral_field_required = ephemeral_field_required
        self._read_buffer = bytearray()
        self._target_thread_id = ""
        self._status = "notLoaded"
        self.methods: list[str] = []
        self.notifications: list[str] = []
        self.inbound_responses = 0
        self.inbound_server_requests = 0
        self.inbound_notifications: dict[str, int] = {}

    def _write(self, message: Mapping[str, object]) -> None:
        line = _canonical(dict(message)) + b"\n"
        self._health_check()
        try:
            fd = self._writer.fileno()
        except (AttributeError, io.UnsupportedOperation, OSError):
            result = self._writer.write(line)
            if result is not None and result != len(line):
                raise DeliveryError("transport write was partial") from None
            self._writer.flush()
            self._deadline.check("transport write")
            return
        view = memoryview(line)
        while view:
            timeout = self._deadline.timeout("transport write")
            _readable, writable, _errors = select.select([], [fd], [], timeout)
            if not writable:
                raise DeliveryError("transport write deadline expired")
            written = os.write(fd, view)
            if written <= 0:
                raise DeliveryError("transport write was partial")
            view = view[written:]
        self._health_check()

    def _read(self) -> dict[str, object]:
        self._health_check()
        try:
            fd = self._reader.fileno()
        except (AttributeError, io.UnsupportedOperation, OSError):
            line = self._reader.readline(_MAX_LINE_BYTES + 1)
            self._deadline.check("transport read")
        else:
            while b"\n" not in self._read_buffer and len(self._read_buffer) <= _MAX_LINE_BYTES:
                timeout = self._deadline.timeout("transport read")
                readable, _writable, _errors = select.select([fd], [], [], timeout)
                if not readable:
                    raise DeliveryError("transport read deadline expired")
                chunk = os.read(fd, min(65536, _MAX_LINE_BYTES + 1 - len(self._read_buffer)))
                if not chunk:
                    break
                self._read_buffer.extend(chunk)
            marker = self._read_buffer.find(b"\n")
            if marker < 0:
                line = bytes(self._read_buffer)
                self._read_buffer.clear()
            else:
                line = bytes(self._read_buffer[: marker + 1])
                del self._read_buffer[: marker + 1]
        if not line or len(line) > _MAX_LINE_BYTES or not line.endswith(b"\n"):
            raise DeliveryError("transport response is missing, partial, or oversized")
        self._health_check()
        return _strict_object(line[:-1], label="transport response")

    def _request(self, method: str, params: Mapping[str, object]) -> dict[str, object]:
        if method not in self._protocol_profile.request_methods:
            raise DeliveryError("method is outside the frozen protocol profile")
        if method == "turn/start":
            self._turn_start_count += 1
            if self._turn_start_count != 1:
                raise DeliveryError("exactly one turn/start is permitted")
        request_id = self._next_id
        self._next_id += 1
        self.methods.append(method)
        if self._real_operations:
            if method == "thread/read":
                self._counters.real_task_reads += 1
            elif method == "thread/resume":
                self._counters.real_task_resumes += 1
            elif method == "turn/start":
                self._counters.real_turns += 1
                self._counters.real_posts += 1
        self._write({"id": request_id, "jsonrpc": "2.0", "method": method, "params": dict(params)})
        if method == "turn/start":
            self._turn_request_written = True
        response = self._read()
        while "method" in response:
            if "id" in response:
                self.inbound_server_requests += 1
                raise DeliveryError("inbound server requests are forbidden")
            self._accept_interleaved_notification(
                response,
                allow_unrelated_status=method != "turn/start" and not self._turn_request_written,
            )
            response = self._read()
        if set(response) not in ({"id", "jsonrpc", "result"}, {"error", "id", "jsonrpc"}):
            raise DeliveryError("response violates the closed JSON-RPC shape")
        if response.get("jsonrpc") != "2.0" or response.get("id") != request_id:
            raise DeliveryError("response identity mismatch")
        if "error" in response:
            raise DeliveryError("app-server returned an error response")
        if type(response["result"]) is not dict:
            raise DeliveryError("response result must be an object")
        self.inbound_responses += 1
        return cast(dict[str, object], response["result"])

    def _notify(self, method: str, params: Mapping[str, object]) -> None:
        if method not in self._protocol_profile.client_notifications:
            raise DeliveryError("notification is outside the frozen protocol profile")
        self.notifications.append(method)
        self._write({"jsonrpc": "2.0", "method": method, "params": dict(params)})

    @staticmethod
    def _status_value(value: object) -> str:
        if type(value) is not dict:
            raise DeliveryError("thread status violates the stable schema")
        status = cast(dict[object, object], value)
        kind = status.get("type")
        if type(kind) is not str:
            raise DeliveryError("thread status violates the stable schema")
        if kind in {"notLoaded", "idle", "systemError"} and set(status) == {"type"}:
            return kind
        if kind == "active" and set(status) == {"activeFlags", "type"}:
            flags = status.get("activeFlags")
            if type(flags) is list and all(
                type(flag) is str and flag in {"waitingOnApproval", "waitingOnUserInput"} for flag in flags
            ):
                return "active"
        raise DeliveryError("thread status violates the stable schema")

    def _accept_interleaved_notification(
        self,
        message: Mapping[str, object],
        *,
        allow_unrelated_status: bool,
    ) -> None:
        if "id" in message:
            self.inbound_server_requests += 1
            raise DeliveryError("inbound server requests are prohibited")
        if set(message) != {"jsonrpc", "method", "params"} or message.get("jsonrpc") != "2.0":
            raise DeliveryError("status notification violates JSON-RPC")
        method = message.get("method")
        if type(method) is not str or method not in self._protocol_profile.server_notifications:
            self.inbound_notifications["unknown"] = self.inbound_notifications.get("unknown", 0) + 1
            raise DeliveryError("server notification is outside the frozen profile")
        count = sum(self.inbound_notifications.values()) + 1
        if count > _MAX_INTERLEAVED_NOTIFICATIONS:
            raise DeliveryError("interleaved notification budget exhausted")
        self.inbound_notifications[method] = self.inbound_notifications.get(method, 0) + 1
        if method != "thread/status/changed" or type(message.get("params")) is not dict:
            raise DeliveryError("status notification violates the selected schema")
        params = cast(dict[object, object], message["params"])
        thread_id = params.get("threadId")
        if set(params) != {"status", "threadId"} or type(thread_id) is not str or not thread_id or len(thread_id) > 256:
            raise DeliveryError("status notification violates the selected schema")
        status = self._status_value(params.get("status"))
        if thread_id != self._target_thread_id:
            if allow_unrelated_status:
                return
            raise DeliveryError("status notification target mismatch")
        self._status = status
        if status == "active" and self._turn_request_written:
            return
        if status not in {"notLoaded", "idle"}:
            raise DeliveryError("target thread became ineligible")

    def _accept_status_notification(self, message: Mapping[str, object]) -> None:
        self._accept_interleaved_notification(message, allow_unrelated_status=False)

    @staticmethod
    def _turn(value: object) -> dict[object, object]:
        if type(value) is not dict:
            raise DeliveryError("turn violates the selected schema")
        turn = cast(dict[object, object], value)
        required = {"id", "items", "status"}
        allowed = required | {"completedAt", "durationMs", "error", "itemsView", "startedAt"}
        if not required.issubset(turn) or not set(turn).issubset(allowed):
            raise DeliveryError("turn violates the selected field set")
        if type(turn["id"]) is not str or type(turn["items"]) is not list:
            raise DeliveryError("turn identity or items violate the selected schema")
        if type(turn["status"]) is not str or turn["status"] not in {
            "completed",
            "interrupted",
            "failed",
            "inProgress",
        }:
            raise DeliveryError("turn status violates the selected schema")
        for field in ("completedAt", "durationMs", "startedAt"):
            if field in turn and turn[field] is not None and type(turn[field]) is not int:
                raise DeliveryError("turn timing violates the selected schema")
        if "itemsView" in turn and (
            type(turn["itemsView"]) is not str or turn["itemsView"] not in {"notLoaded", "summary", "full"}
        ):
            raise DeliveryError("turn items view violates the selected schema")
        if "error" in turn and turn["error"] is not None and type(turn["error"]) is not dict:
            raise DeliveryError("turn error violates the selected schema")
        return turn

    @staticmethod
    def _capture_thread_identity(value: object) -> str | None:
        if type(value) is not dict:
            return None
        identifier = cast(dict[object, object], value).get("id")
        safe = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
        if (
            type(identifier) is not str
            or not identifier
            or len(identifier) > 256
            or any(c not in safe for c in identifier)
        ):
            return None
        return identifier

    @staticmethod
    def _thread_entry(value: object, *, ephemeral_field_required: bool) -> ThreadListingEntry:
        if type(value) is not dict:
            raise DeliveryError("thread violates the selected schema")
        thread = cast(dict[object, object], value)
        required = {
            "cliVersion",
            "createdAt",
            "cwd",
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
        if ephemeral_field_required:
            required.add("ephemeral")
        allowed = required | {
            "agentNickname",
            "agentRole",
            "threadSource",
            "forkedFromId",
            "gitInfo",
            "historyMode",
            "model",
            "name",
            "parentThreadId",
            "path",
            "recencyAt",
            "reasoningEffort",
            "section",
            "sectionEnteredAt",
        }
        if not ephemeral_field_required:
            allowed.discard("ephemeral")
        if not required.issubset(thread) or not set(thread).issubset(allowed):
            raise DeliveryError("thread violates the selected field set")
        string_fields = ("cliVersion", "cwd", "id", "modelProvider", "preview", "sessionId")
        if any(type(thread[key]) is not str or len(cast(str, thread[key])) > 4096 for key in string_fields):
            raise DeliveryError("thread identity fields violate the selected schema")
        if type(thread["createdAt"]) is not int or type(thread["updatedAt"]) is not int:
            raise DeliveryError("thread scalar fields violate the selected schema")
        if ephemeral_field_required and type(thread["ephemeral"]) is not bool:
            raise DeliveryError("thread scalar fields violate the selected schema")
        turns = thread["turns"]
        if type(turns) is not list or len(cast(list[object], turns)) > 128:
            raise DeliveryError("thread turns violate the selected schema")
        for turn in cast(list[object], turns):
            _JsonlSession._turn(turn)
        nullable_strings = (
            "agentNickname",
            "agentRole",
            "forkedFromId",
            "name",
            "parentThreadId",
            "path",
            "threadSource",
            "projectId",
        )
        if any(key in thread and thread[key] is not None and type(thread[key]) is not str for key in nullable_strings):
            raise DeliveryError("thread optional string violates the selected schema")
        if (
            "model" in thread
            and thread["model"] is not None
            and (type(thread["model"]) is not str or len(thread["model"]) > 4096)
        ):
            raise DeliveryError("thread model violates the selected schema")
        if (
            "reasoningEffort" in thread
            and thread["reasoningEffort"] is not None
            and (
                type(thread["reasoningEffort"]) is not str
                or not thread["reasoningEffort"]
                or len(thread["reasoningEffort"]) > 256
            )
        ):
            raise DeliveryError("thread reasoning effort violates the selected schema")
        if "recencyAt" in thread and thread["recencyAt"] is not None and type(thread["recencyAt"]) is not int:
            raise DeliveryError("thread recency violates the selected schema")
        if "historyMode" in thread:
            history_mode = thread["historyMode"]
            if type(history_mode) is not str or history_mode not in {"legacy", "paginated"}:
                raise DeliveryError("thread history mode violates the selected schema")
        if (
            "sectionEnteredAt" in thread
            and thread["sectionEnteredAt"] is not None
            and type(thread["sectionEnteredAt"]) is not int
        ):
            raise DeliveryError("thread section entry time violates the selected schema")
        if "section" in thread:
            section = thread["section"]
            if section is None:
                pass
            elif type(section) is not dict:
                raise DeliveryError("thread section violates the selected schema")
            else:
                selected_section = cast(dict[object, object], section)
                if not {"id", "name"}.issubset(selected_section) or not set(selected_section).issubset(
                    {"id", "name", "appearance"}
                ):
                    raise DeliveryError("thread section violates the selected field set")
                if type(selected_section["id"]) is not str or type(selected_section["name"]) is not str:
                    raise DeliveryError("thread section identity violates the selected schema")
                if "appearance" in selected_section and selected_section["appearance"] is not None:
                    appearance = selected_section["appearance"]
                    if type(appearance) is not dict:
                        raise DeliveryError("thread section appearance violates the selected schema")
                    selected_appearance = cast(dict[object, object], appearance)
                    if not set(selected_appearance).issubset({"color", "icon"}) or any(
                        value is not None and type(value) is not str for value in selected_appearance.values()
                    ):
                        raise DeliveryError("thread section appearance violates the selected schema")
        if "gitInfo" in thread and thread["gitInfo"] is not None and type(thread["gitInfo"]) is not dict:
            raise DeliveryError("thread git information violates the selected schema")
        identifier = _JsonlSession._capture_thread_identity(thread)
        source_kind = thread["source"]
        cwd = thread["cwd"]
        if identifier is None:
            raise DeliveryError("thread/list contains an invalid identity")
        if type(source_kind) is not str or source_kind not in {"cli", "vscode", "exec", "appServer"}:
            raise DeliveryError("thread source kind is not eligible")
        if (
            type(cwd) is not str
            or not cwd.startswith("/")
            or len(cwd) > 4096
            or "\x00" in cwd
            or posixpath.normpath(cwd) != cwd
            or "//" in cwd
            or any(component in {".", ".."} for component in cwd.split("/"))
        ):
            raise DeliveryError("thread cwd is not canonical")
        ephemeral = cast(bool, thread["ephemeral"]) if ephemeral_field_required else False
        if ephemeral:
            raise DeliveryError("ephemeral thread is ineligible")
        name = thread.get("name")
        return ThreadListingEntry(
            thread_id=identifier,
            source_kind=source_kind,
            cwd=cwd,
            status=_JsonlSession._status_value(thread["status"]),
            name=cast(str | None, name),
            preview=cast(str, thread["preview"]),
        )

    @staticmethod
    def _thread(value: object, route: _RouteAuthority) -> str:
        entry = _JsonlSession._thread_entry(value, ephemeral_field_required=True)
        if entry.source_kind != route.source_kind or entry.cwd != route.cwd:
            raise DeliveryError("thread source or cwd does not match the sealed filter")
        return entry.status

    def _list_page_set(
        self,
        *,
        archived: bool,
        identities: set[str],
        route: _RouteAuthority | None,
    ) -> tuple[list[ThreadListingEntry], int]:
        cursor: str | None = None
        cursors: set[str] = set()
        entries: list[ThreadListingEntry] = []
        rejected = 0
        try:
            for _page in range(_MAX_LIST_PAGES):
                params: dict[str, object] = {
                    "archived": archived,
                    "cursor": cursor,
                    "limit": 100,
                    "sortDirection": "desc",
                    "sortKey": "created_at",
                    "useStateDbOnly": True,
                }
                if route is not None:
                    params["cwd"] = route.cwd
                    params["sourceKinds"] = [route.source_kind]
                result = self._request("thread/list", params)
                if "data" not in result or not set(result).issubset({"backwardsCursor", "data", "nextCursor"}):
                    raise DeliveryError("thread/list response violates the selected schema")
                backwards = result.get("backwardsCursor")
                if backwards is not None and (type(backwards) is not str or not backwards or len(backwards) > 1024):
                    raise DeliveryError("thread/list backwards cursor is invalid")
                data = result["data"]
                if type(data) is not list or len(data) > 100:
                    raise DeliveryError("thread/list page violates the selected bound")
                page_new = 0
                for item in cast(list[object], data):
                    identifier = self._capture_thread_identity(item)
                    if identifier is not None and identifier in identities:
                        raise DeliveryError("thread/list contains an invalid or duplicate identity")
                    try:
                        entry = self._thread_entry(item, ephemeral_field_required=self._ephemeral_field_required)
                    except DeliveryError:
                        if route is not None and identifier == route.thread_id:
                            raise DeliveryError("target thread is malformed") from None
                        if archived and identifier is None:
                            raise DeliveryError("archived thread lacks a safe identity") from None
                        if identifier is not None:
                            identities.add(identifier)
                            page_new += 1
                        rejected += 1
                        continue
                    identifier = entry.thread_id
                    identities.add(identifier)
                    page_new += 1
                    if route is not None and (entry.source_kind != route.source_kind or entry.cwd != route.cwd):
                        raise DeliveryError("thread source or cwd does not match the sealed filter")
                    if not archived and entry.status in {"notLoaded", "idle"}:
                        entries.append(entry)
                next_cursor = result.get("nextCursor")
                if next_cursor is None:
                    return entries, rejected
                if page_new == 0:
                    raise DeliveryError("thread/list pagination made no progress")
                if type(next_cursor) is not str or not next_cursor or len(next_cursor) > 1024 or next_cursor in cursors:
                    raise DeliveryError("thread/list cursor is invalid or cyclic")
                cursors.add(next_cursor)
                cursor = next_cursor
            raise DeliveryError("thread/list pagination budget exhausted")
        finally:
            cursors.clear()

    def list_threads(self, route: _RouteAuthority | None = None) -> ThreadListing:
        active_ids: set[str] = set()
        archived_ids: set[str] = set()
        active, active_rejected = self._list_page_set(archived=False, identities=active_ids, route=route)
        _archived, archived_rejected = self._list_page_set(archived=True, identities=archived_ids, route=route)
        if active_ids & archived_ids:
            raise DeliveryError("thread/list membership is inconsistent across archive filters")
        eligible = tuple(entry for entry in active if entry.thread_id not in archived_ids)
        if route is not None:
            matching = [entry for entry in eligible if entry.thread_id == route.thread_id]
            if len(matching) != 1:
                raise DeliveryError("target must occur exactly once in the active listing")
            self._status = matching[0].status
        return ThreadListing(
            entries=eligible,
            active_identities=frozenset(active_ids),
            archived_identities=frozenset(archived_ids),
            rejected_entries=active_rejected + archived_rejected,
        )

    def initialize(self) -> None:
        initialized = self._request(
            "initialize", {"clientInfo": {"name": "42-ultracode", "title": "42 Ultracode", "version": "0.1.0"}}
        )
        if set(initialized) != {"codexHome", "platformFamily", "platformOs", "userAgent"}:
            raise DeliveryError("initialize response violates the selected schema")
        if any(type(initialized[key]) is not str for key in initialized):
            raise DeliveryError("initialize response types violate the selected schema")
        if initialized["userAgent"] != self._expected_user_agent:
            raise DeliveryError("app-server version does not match the sealed run-local identity")
        self._notify("initialized", {})

    def _exact_target(self, result: Mapping[str, object], route: _RouteAuthority, *, resume: bool = False) -> None:
        required = (
            {"thread"}
            if not resume
            else {"approvalPolicy", "approvalsReviewer", "cwd", "model", "modelProvider", "sandbox", "thread"}
        )
        allowed = (
            required
            if not resume
            else required
            | {"instructionSources", "itemsBackwardsCursor", "reasoningEffort", "serviceTier", "turnsBackwardsCursor"}
        )
        if not required.issubset(result) or not set(result).issubset(allowed):
            raise DeliveryError("thread response violates the selected schema")
        thread = result["thread"]
        status = self._thread(thread, route)
        selected = cast(dict[object, object], thread)
        if (
            selected.get("id") != route.thread_id
            or selected.get("source") != route.source_kind
            or selected.get("cwd") != route.cwd
        ):
            raise DeliveryError("thread response identity mismatch")
        if status not in {"notLoaded", "idle"}:
            raise DeliveryError("target thread is not idle")
        if resume and result.get("cwd") != route.cwd:
            raise DeliveryError("resume cwd mismatch")
        if resume:
            for field in ("itemsBackwardsCursor", "turnsBackwardsCursor"):
                value = result.get(field)
                if value is not None and (type(value) is not str or not value or len(value) > 1024):
                    raise DeliveryError("resume cursor violates the selected schema")
        self._status = status

    def prepare(self, route: _RouteAuthority) -> None:
        self._target_thread_id = route.thread_id
        self.initialize()
        self.list_threads(route)
        self._exact_target(self._request("thread/read", {"includeTurns": False, "threadId": route.thread_id}), route)
        self._exact_target(self._request("thread/resume", {"threadId": route.thread_id}), route, resume=True)
        self._exact_target(self._request("thread/read", {"includeTurns": False, "threadId": route.thread_id}), route)

    @staticmethod
    def _reject_error_notification(values: Mapping[object, object], thread_id: str, turn_id: str) -> NoReturn:
        if (
            set(values) != {"error", "threadId", "turnId", "willRetry"}
            or values.get("threadId") != thread_id
            or values.get("turnId") != turn_id
            or type(values.get("willRetry")) is not bool
            or type(values.get("error")) is not dict
        ):
            raise DeliveryError("error notification violates the selected schema")
        raise DeliveryError("app-server reported a turn error")

    def start_and_wait(self, thread_id: str, message: bytes) -> str:
        text = message.decode("utf-8")
        started = self._request("turn/start", {"input": [{"text": text, "type": "text"}], "threadId": thread_id})
        if set(started) != {"turn"}:
            raise DeliveryError("turn/start response violates the selected schema")
        turn = started.get("turn")
        selected_turn = self._turn(turn)
        if selected_turn.get("status") != "inProgress":
            raise DeliveryError("turn/start response violates the selected schema")
        turn_id = cast(str, selected_turn["id"])
        saw_started = False
        while True:
            message_obj = self._read()
            if "id" in message_obj:
                raise DeliveryError("duplicate or unsolicited response is prohibited")
            if set(message_obj) != {"jsonrpc", "method", "params"} or message_obj.get("jsonrpc") != "2.0":
                raise DeliveryError("server notification violates the closed JSON-RPC shape")
            method = message_obj.get("method")
            if type(method) is not str or method not in PROTOCOL_PROFILE.server_notifications:
                raise DeliveryError("server notification is outside the frozen profile")
            params = message_obj.get("params")
            if type(params) is not dict:
                raise DeliveryError("server notification params must be an object")
            values = cast(dict[object, object], params)
            if method == "thread/status/changed":
                self._accept_status_notification(message_obj)
                continue
            if method == "error":
                self._reject_error_notification(values, thread_id, turn_id)
            if set(values) != {"threadId", "turn"}:
                raise DeliveryError("server notification params violate the selected schema")
            if values.get("threadId") != thread_id:
                raise DeliveryError("server notification thread mismatch")
            notified_turn = values.get("turn")
            selected = self._turn(notified_turn)
            if selected.get("id") != turn_id:
                raise DeliveryError("server notification turn violates the selected schema")
            if method == "turn/started":
                if saw_started:
                    raise DeliveryError("duplicate turn/started notification")
                saw_started = True
                continue
            status = selected.get("status")
            if not saw_started or status != "completed":
                raise DeliveryError("turn did not complete successfully")
            return turn_id


class _StderrScanner:
    def __init__(self, stream: BinaryIO, deadline: _Deadline) -> None:
        self._stream = stream
        self._deadline = deadline
        self._stop = threading.Event()
        self._issue: str | None = None
        self._seen = 0
        self._tail = b""
        self._thread = threading.Thread(target=self._drain, name="ultracode-stderr-drain")

    def start(self) -> None:
        self._thread.start()

    def _drain(self) -> None:
        try:
            fd = self._stream.fileno()
            while not self._stop.is_set():
                readable, _writable, _errors = select.select([fd], [], [], 0.05)
                if not readable:
                    continue
                chunk = os.read(fd, 4096)
                if not chunk:
                    return
                self._seen += len(chunk)
                sample = self._tail + chunk
                self._tail = sample[-256:]
                if self._seen > _MAX_STDERR_BYTES:
                    self._issue = "stderr_limit_exceeded"
                    return
                if any(pattern.search(sample) for pattern in _SECRET_BYTES):
                    self._issue = "stderr_sensitive_pattern"
                    return
        except (OSError, ValueError):
            if not self._stop.is_set():
                self._issue = "stderr_transport_failure"

    def check(self) -> None:
        if self._issue is not None:
            raise DeliveryError(f"app-server stderr rejected: {self._issue}")

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def close(self, timeout: float) -> str | None:
        self._stop.set()
        self._thread.join(timeout=max(0.0, timeout))
        if self._thread.is_alive():
            return "stderr_helper_alive"
        if self._issue is not None:
            return "stderr_rejected"
        return None


def _stderr_thread_census() -> int:
    return sum(thread.name == "ultracode-stderr-drain" and thread.is_alive() for thread in threading.enumerate())


class _CodexProcess:
    def __init__(self, authority: object, deadline: _Deadline) -> None:
        self._authority = authority
        self._deadline = deadline
        self._process: subprocess.Popen[bytes] | None = None
        self._process_group_id: int | None = None
        self._stderr: _StderrScanner | None = None
        self.cleanup_issue: str | None = None
        self.cleanup_actions: list[str] = []
        self._stderr_thread_baseline = _stderr_thread_census()

    def __enter__(self) -> tuple[BinaryIO, BinaryIO, Callable[[], None]]:
        executable = _validate_executable(self._authority)
        started = time.monotonic()
        self._process = subprocess.Popen(
            (os.fspath(executable.path), *APP_SERVER_ARGV),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            env=_minimal_environment(),
            start_new_session=True,
        )
        self._process_group_id = self._process.pid
        try:
            try:
                process_group_id = os.getpgid(self._process.pid)
            except ProcessLookupError:
                process_group_id = self._process.pid
            except OSError as exc:
                self._process_group_id = None
                raise DeliveryError("app-server process-group identity is unavailable") from exc
            if process_group_id != self._process.pid:
                self._process_group_id = None
                raise DeliveryError("app-server did not establish the owned process group")
            if time.monotonic() - started > self._deadline.timeout("process spawn"):
                raise DeliveryError("process spawn deadline expired")
            _validate_executable(self._authority)
            if self._process.stdin is None or self._process.stdout is None or self._process.stderr is None:
                raise DeliveryError("app-server stdio pipes are unavailable")
            self._stderr = _StderrScanner(cast(BinaryIO, self._process.stderr), self._deadline)
            self._stderr.start()
            return cast(BinaryIO, self._process.stdout), cast(BinaryIO, self._process.stdin), self.check
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def check(self) -> None:
        if self._stderr is not None:
            self._stderr.check()
        if self._process is not None:
            status = self._process.poll()
            if status is not None and status != 0:
                raise DeliveryError("app-server exited before protocol completion")

    def _group_alive(self) -> tuple[bool, str | None]:
        if self._process_group_id is None or self._process_group_id <= 0:
            return False, "process_group_identity_missing"
        try:
            os.killpg(self._process_group_id, 0)
        except ProcessLookupError:
            return False, None
        except OSError:
            return True, "process_group_probe_failed"
        return True, None

    def _signal_group(self, sig: signal.Signals) -> str | None:
        if self._process_group_id is None or self._process_group_id <= 0:
            return "process_group_identity_missing"
        try:
            os.killpg(self._process_group_id, sig)
        except ProcessLookupError:
            return None
        except OSError:
            return "process_group_signal_failed"
        return None

    def _wait_group_absent(self, end: float) -> tuple[bool, set[str]]:
        issues: set[str] = set()
        while True:
            if self._process is not None:
                try:
                    self._process.poll()
                except OSError:
                    issues.add("process_poll_failed")
            alive, issue = self._group_alive()
            if issue is not None:
                issues.add(issue)
            if not alive:
                issues.discard("process_group_probe_failed")
                return True, issues
            if time.monotonic() >= end:
                return False, issues
            time.sleep(min(0.01, max(0.0, end - time.monotonic())))

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        if self._process is None:
            return
        cleanup_end = time.monotonic() + _CLEANUP_SECONDS
        group_cleanup_end = max(time.monotonic(), cleanup_end - _CLEANUP_TAIL_SECONDS)
        issues: set[str] = set()
        provisional_group_issues: set[str] = set()

        def remaining() -> float:
            return max(0.0, cleanup_end - time.monotonic())

        def record_group_issues(observed: Iterable[str | None]) -> None:
            for observed_issue in observed:
                if observed_issue is None:
                    continue
                if observed_issue in {"process_group_probe_failed", "process_group_signal_failed"}:
                    provisional_group_issues.add(observed_issue)
                else:
                    issues.add(observed_issue)

        try:
            for stream in (self._process.stdin, self._process.stdout):
                if stream is not None:
                    self.cleanup_actions.append("close_stream")
                    try:
                        stream.close()
                    except Exception:
                        issues.add("stream_close_failed")
            group_alive, issue = self._group_alive()
            record_group_issues((issue,))
            if self._process_group_id is None:
                self.cleanup_actions.append("terminate_unverified_child")
                try:
                    self._process.terminate()
                except Exception:
                    issues.add("direct_child_signal_failed")
                try:
                    self._process.wait(timeout=min(1.0, remaining()))
                except subprocess.TimeoutExpired:
                    self.cleanup_actions.append("kill_unverified_child")
                    try:
                        self._process.kill()
                    except Exception:
                        issues.add("direct_child_signal_failed")
                    try:
                        self._process.wait(timeout=min(1.0, remaining()))
                    except subprocess.TimeoutExpired:
                        issues.add("process_reap_timeout")
                    except OSError:
                        issues.add("process_wait_failed")
                except OSError:
                    issues.add("process_wait_failed")
            if group_alive:
                self.cleanup_actions.append("term_group")
                issue = self._signal_group(signal.SIGTERM)
                record_group_issues((issue,))
                self.cleanup_actions.append("term_wait")
                absent, group_issues = self._wait_group_absent(min(group_cleanup_end, time.monotonic() + 1.0))
                record_group_issues(group_issues)
                if absent:
                    provisional_group_issues.clear()
                if not absent:
                    self.cleanup_actions.append("kill_group")
                    issue = self._signal_group(signal.SIGKILL)
                    record_group_issues((issue,))
                    self.cleanup_actions.append("kill_wait")
                    absent, group_issues = self._wait_group_absent(group_cleanup_end)
                    record_group_issues(group_issues)
                    if absent:
                        provisional_group_issues.clear()
                    if not absent:
                        issues.add("process_group_still_alive")
            issues.update(provisional_group_issues)
            try:
                self.cleanup_actions.append("reap_child")
                self._process.wait(timeout=remaining())
            except subprocess.TimeoutExpired:
                issues.add("process_reap_timeout")
            except OSError:
                issues.add("process_wait_failed")
            if self._stderr is not None:
                self.cleanup_actions.append("join_stderr")
                try:
                    issue = self._stderr.close(remaining())
                    if issue is not None:
                        issues.add(issue)
                except Exception:
                    issues.add("stderr_helper_close_failed")
            if self._process.stderr is not None:
                self.cleanup_actions.append("close_stderr")
                try:
                    self._process.stderr.close()
                except Exception:
                    issues.add("stream_close_failed")
            try:
                if self._process.poll() is None:
                    issues.add("owned_process_alive")
            except OSError:
                issues.add("process_poll_failed")
            try:
                if self._stderr is not None and self._stderr.alive:
                    issues.add("stderr_helper_alive")
            except Exception:
                issues.add("stderr_helper_state_failed")
            try:
                if _stderr_thread_census() != self._stderr_thread_baseline:
                    issues.add("thread_census_mismatch")
            except Exception:
                issues.add("thread_census_failed")
        except Exception:
            issues.add("cleanup_internal_failure")
        finally:
            self.cleanup_issue = "+".join(sorted(issues)) if issues else None
            self._stderr = None
            self._process_group_id = None
            self._process = None


def _perform(
    *,
    preview: DeliveryPreview,
    capability: object,
    route_registry: Path,
    journal_path: Path,
    streams: tuple[BinaryIO, BinaryIO] | None = None,
    attempt_id: str | None = None,
    counters: _OperationCounters | None = None,
    executable_authority: object | None = None,
    deadline: _Deadline | None = None,
) -> tuple[DeliveryOutcome, _JsonlSession | None]:
    consume = _consume_capability
    if not callable(consume):
        raise DeliveryError("confirmation boundary is unavailable")
    consume(capability, preview)
    real_operations = streams is None
    operation_counters = counters or _OperationCounters()
    session_deadline = deadline or _Deadline.start()
    expected_user_agent = _SYNTHETIC_USER_AGENT
    if real_operations:
        if executable_authority is None:
            raise DeliveryError("real delivery requires sealed executable authority")
        expected_user_agent = _require_production_identity(executable_authority).cli_version
    route = _resolve_alias(route_registry, preview.target_alias)
    thread_id = route.thread_id
    if real_operations:
        operation_counters.real_alias_resolutions += 1
    attempt = attempt_id or secrets.token_hex(16)
    with _Journal(journal_path) as journal:
        prior = journal.terminal_for(payload_sha256=preview.payload_sha256, target_alias=preview.target_alias)
        if prior in {DeliveryOutcome.DELIVERED, DeliveryOutcome.UNCERTAIN}:
            raise DeliveryError(f"delivery is terminal: {prior.value}")

        attempt_started = False

        def run(
            reader: BinaryIO,
            writer: BinaryIO,
            health_check: Callable[[], None] | None = None,
            *,
            defer_success: bool = False,
        ) -> tuple[DeliveryOutcome, _JsonlSession]:
            nonlocal attempt_started
            session = _JsonlSession(
                reader,
                writer,
                real_operations=real_operations,
                counters=operation_counters,
                deadline=session_deadline,
                health_check=health_check,
                expected_user_agent=expected_user_agent,
            )
            try:
                session.prepare(route)
            except (BrokenPipeError, DeliveryError, OSError):
                journal.append(event="FAILED_BEFORE_WRITE", attempt_id=attempt, preview=preview, thread_id=thread_id)
                return DeliveryOutcome.FAILED_BEFORE_WRITE, session
            journal.append(event="ATTEMPT_STARTED", attempt_id=attempt, preview=preview, thread_id=thread_id)
            attempt_started = True
            try:
                session.start_and_wait(thread_id, preview.message)
            except (BrokenPipeError, DeliveryError, OSError):
                journal.append(event="UNCERTAIN", attempt_id=attempt, preview=preview, thread_id=thread_id)
                return DeliveryOutcome.UNCERTAIN, session
            if not defer_success:
                journal.append(event="DELIVERED", attempt_id=attempt, preview=preview, thread_id=thread_id)
            return DeliveryOutcome.DELIVERED, session

        if streams is not None:
            return run(*streams)
        operation_counters.real_app_server_launches += 1
        process = _CodexProcess(executable_authority, session_deadline)
        with process as process_streams:
            outcome, session = run(*process_streams, defer_success=True)
        if process.cleanup_issue is not None:
            if attempt_started and outcome is not DeliveryOutcome.UNCERTAIN:
                journal.append(event="UNCERTAIN", attempt_id=attempt, preview=preview, thread_id=thread_id)
                return DeliveryOutcome.UNCERTAIN, session
            return outcome, session
        if outcome is DeliveryOutcome.DELIVERED:
            journal.append(event="DELIVERED", attempt_id=attempt, preview=preview, thread_id=thread_id)
        return outcome, session


def deliver_foreground(
    *,
    message_path: Path,
    target_alias: str,
    route_registry: Path,
    journal_path: Path,
    input_stream: TextIO,
    output_stream: TextIO,
    _executable_authority: object | None = None,
) -> DeliveryOutcome:
    """Perform one foreground, human-confirmed delivery through fixed local stdio."""
    message = _read_owned_regular(message_path, max_bytes=_MAX_MESSAGE_BYTES)
    preview = DeliveryPreview(target_alias=target_alias, message=message)
    _require_interactive_tty(input_stream, output_stream)
    executable_authority = _executable_authority or _seal_production_executable()
    _require_production_identity(executable_authority)
    capability = confirm_preview(preview, input_stream=input_stream, output_stream=output_stream)
    outcome, _session = _perform(
        preview=preview,
        capability=capability,
        route_registry=route_registry,
        journal_path=journal_path,
        executable_authority=executable_authority,
    )
    return outcome


def _fake_transcript(thread_id: str = "synthetic-thread") -> bytes:
    thread = {
        "cliVersion": _SYNTHETIC_USER_AGENT.removeprefix("codex-cli "),
        "createdAt": 1,
        "cwd": "/synthetic/workspace",
        "ephemeral": False,
        "historyMode": "paginated",
        "id": thread_id,
        "modelProvider": "openai",
        "preview": "synthetic",
        "projectId": None,
        "section": None,
        "sectionEnteredAt": None,
        "sessionId": "synthetic-session",
        "source": "appServer",
        "status": {"type": "idle"},
        "turns": [],
        "updatedAt": 1,
    }
    other = dict(thread)
    other["id"] = "active-non-target-thread"
    archived_other = dict(thread)
    archived_other["id"] = "archived-non-target-thread"
    turn: dict[str, object] = {
        "completedAt": None,
        "durationMs": None,
        "error": None,
        "id": "synthetic-turn",
        "items": [],
        "itemsView": "full",
        "startedAt": None,
        "status": "inProgress",
    }
    completed = dict(turn)
    completed["status"] = "completed"
    messages: Sequence[Mapping[str, object]] = (
        {
            "id": 1,
            "jsonrpc": "2.0",
            "result": {
                "codexHome": "/synthetic/codex-home",
                "platformFamily": "unix",
                "platformOs": "macos",
                "userAgent": _SYNTHETIC_USER_AGENT,
            },
        },
        {"id": 2, "jsonrpc": "2.0", "result": {"data": [thread], "nextCursor": "page-2"}},
        {"id": 3, "jsonrpc": "2.0", "result": {"data": [other], "nextCursor": None}},
        {"id": 4, "jsonrpc": "2.0", "result": {"data": [archived_other], "nextCursor": None}},
        {"id": 5, "jsonrpc": "2.0", "result": {"thread": thread}},
        {
            "id": 6,
            "jsonrpc": "2.0",
            "result": {
                "approvalPolicy": "never",
                "approvalsReviewer": "user",
                "cwd": "/synthetic/workspace",
                "model": "synthetic",
                "modelProvider": "openai",
                "itemsBackwardsCursor": None,
                "sandbox": "read-only",
                "thread": thread,
                "turnsBackwardsCursor": None,
            },
        },
        {"id": 7, "jsonrpc": "2.0", "result": {"thread": thread}},
        {"id": 8, "jsonrpc": "2.0", "result": {"turn": turn}},
        {"jsonrpc": "2.0", "method": "turn/started", "params": {"threadId": thread_id, "turn": turn}},
        {
            "jsonrpc": "2.0",
            "method": "turn/completed",
            "params": {"threadId": thread_id, "turn": completed},
        },
    )
    return b"".join(_canonical(message) + b"\n" for message in messages)


def _qualify_fake_peer(root: Path) -> dict[str, object]:
    """Private deterministic no-live-transport qualification harness."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.getuid() or root_info.st_mode & 0o077:
        raise DeliveryError("synthetic qualification root is unsafe")
    route = root / "routes.json"
    _write_exclusive_owned(
        route,
        _canonical(
            {
                "aliases": {
                    "SYNTHETIC_TARGET": {
                        "cwd": "/synthetic/workspace",
                        "source_kind": "appServer",
                        "thread_id": "synthetic-thread",
                    }
                },
                "version": 2,
            }
        ),
    )
    preview = DeliveryPreview(target_alias="SYNTHETIC_TARGET", message=b"synthetic supervised delivery")

    class SyntheticInput:
        def isatty(self) -> bool:
            return True

        def readline(self, _size: int | None = -1) -> str:
            match = rendered.getvalue().rsplit("confirmation-challenge: ", 1)
            if len(match) != 2:
                raise DeliveryError("synthetic challenge was not rendered")
            return match[1].splitlines()[0] + "\n"

    class SyntheticOutput:
        def __init__(self) -> None:
            self._buffer = io.StringIO()

        def isatty(self) -> bool:
            return True

        def write(self, value: str) -> int:
            return self._buffer.write(value)

        def flush(self) -> None:
            self._buffer.flush()

        def getvalue(self) -> str:
            return self._buffer.getvalue()

    output = SyntheticOutput()
    rendered = output
    capability = confirm_preview(
        preview,
        input_stream=cast(TextIO, SyntheticInput()),
        output_stream=cast(TextIO, output),
    )
    reader = io.BytesIO(_fake_transcript())
    writer = io.BytesIO()
    counters = _OperationCounters()
    outcome, session = _perform(
        preview=preview,
        capability=capability,
        route_registry=route,
        journal_path=root / "journal.jsonl",
        streams=(reader, writer),
        attempt_id="0" * 32,
        counters=counters,
    )
    assert session is not None
    return {
        "outcome": outcome.value,
        "request_methods": session.methods,
        "client_notifications": session.notifications,
        "transcript_sha256": _sha(writer.getvalue()),
        "journal_sha256": _sha((root / "journal.jsonl").read_bytes()),
        "real_operation_counters": counters.to_dict(),
        "static_exclusions": ["automatic_loops", "browser_operations", "mcp_operations"],
    }
