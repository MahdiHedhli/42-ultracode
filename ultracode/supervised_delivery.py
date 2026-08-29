"""Fail-closed F017 D8 supervised Codex task delivery boundary."""

from __future__ import annotations

import fcntl
import io
import json
import os
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
from types import MappingProxyType
from typing import BinaryIO, NoReturn, SupportsIndex, TextIO, cast
from weakref import WeakKeyDictionary

from .protocol import ExecutionResult, ResultStatus, ValidationError

__all__ = (
    "APP_SERVER_ARGV",
    "D8_POLICY_SHA256",
    "DeliveryError",
    "DeliveryOutcome",
    "DeliveryPreview",
    "ProtocolProfile",
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
_DEFAULT_SESSION_SECONDS = 60.0
_DEFAULT_OPERATION_SECONDS = 5.0
_CLEANUP_TAIL_SECONDS = 0.5
_CLEANUP_SECONDS = 3.0
_SYMBOL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_EXPECTED_USER_AGENT = "codex-cli 0.146.0"
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
    codex_version: str
    request_methods: frozenset[str]
    client_notifications: frozenset[str]
    server_notifications: frozenset[str]
    schema_sha256: Mapping[str, str]


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
    codex_version="codex-cli 0.146.0",
    request_methods=frozenset({"initialize", "thread/list", "thread/read", "thread/resume", "turn/start"}),
    client_notifications=frozenset({"initialized"}),
    server_notifications=frozenset({"thread/status/changed", "turn/started", "turn/completed", "error"}),
    schema_sha256=MappingProxyType(
        {
            "v2/ThreadListParams.json": "3b37cf361c29b959cf29828db3017c0a5e38d9c24de5fbd089bd44d42f05d5f0",
            "v2/ThreadListResponse.json": "5b01b0c03141c2a15559879294ef065daac9715615d7df65371baf5f119d9958",
            "v2/ThreadReadResponse.json": "dd1f9df782fc0e0a9d752dbf6f725634355b4889f9393074c9a71f768dcb2990",
            "v2/ThreadResumeResponse.json": "a729b3d290402b1e7ee11661001dc194b59f0b5743cbe9e64cd6720862179865",
            "v2/TurnStartResponse.json": "099184dc9d6195cd965b8a90ee5d1cb05c87d9b329acecdfbd63f358e660d568",
            "v2/ThreadStatusChangedNotification.json": (
                "146af6d3702c4f3c844bd10b6b6b3e2b872e958a8d7d822157c19aaa6dc085f6"
            ),
            "v1/InitializeParams.json": "4f576f99e285beb28f71f48a72b887c1f517dada86fee348fe2af0a35511de23",
            "v1/InitializeResponse.json": "86dcd236d0576a82c85b933586dc45731260eab1b6edb3447b03f790277322b1",
            "v2/ThreadReadParams.json": "db97080f82facc3259dbb9404e9f0df81e360619f4cd73983a9d99d25f5089ee",
            "v2/ThreadResumeParams.json": "1dc47d294d0de32f334e0829893d743ec64393ebcf00d7212c9c55b03c34ed23",
            "v2/TurnStartParams.json": "48a0ee95b669b47f5557c68b99a4d459b50577ccce8ebc5976532f50e3c6d059",
            "v2/TurnStartedNotification.json": "e268134e79cae246e39f110e67bd2efbb49ce9a572520a85a96a7325eaf31e03",
            "v2/TurnCompletedNotification.json": "5b5f2ca515658ea6fcce7e961d1c3feddb3f48c0dcc813260c7ccf77a2d016af",
            "v2/ErrorNotification.json": "1ec871b02771300a26a34e41a7cfaf7484330a8c37c197d1ac133e753b083a09",
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


def _schema_profile_sha256() -> str:
    return _sha(_canonical(dict(PROTOCOL_PROFILE.schema_sha256)))


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
            if component != path and info.st_mode & 0o022:
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
        try:
            input_tty = input_stream.isatty()
            output_tty = output_stream.isatty()
        except (AttributeError, OSError) as exc:
            raise DeliveryError("confirmation requires terminal streams") from exc
        if not input_tty or not output_tty:
            raise DeliveryError("confirmation requires an interactive TTY")
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
    if source_kind not in {"cli", "vscode", "exec", "appServer"}:
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
            if record["event"] not in cls._EVENTS:
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
        self._read_buffer = bytearray()
        self._target_thread_id = ""
        self._status = "notLoaded"
        self.methods: list[str] = []
        self.notifications: list[str] = []

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
        if method not in PROTOCOL_PROFILE.request_methods:
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
        while response.get("method") == "thread/status/changed":
            self._accept_status_notification(response)
            response = self._read()
        if "method" in response:
            raise DeliveryError("server requests and out-of-order notifications are prohibited")
        if set(response) not in ({"id", "jsonrpc", "result"}, {"error", "id", "jsonrpc"}):
            raise DeliveryError("response violates the closed JSON-RPC shape")
        if response.get("jsonrpc") != "2.0" or response.get("id") != request_id:
            raise DeliveryError("response identity mismatch")
        if "error" in response:
            raise DeliveryError("app-server returned an error response")
        if type(response["result"]) is not dict:
            raise DeliveryError("response result must be an object")
        return cast(dict[str, object], response["result"])

    def _notify(self, method: str, params: Mapping[str, object]) -> None:
        if method not in PROTOCOL_PROFILE.client_notifications:
            raise DeliveryError("notification is outside the frozen protocol profile")
        self.notifications.append(method)
        self._write({"jsonrpc": "2.0", "method": method, "params": dict(params)})

    @staticmethod
    def _status_value(value: object) -> str:
        if type(value) is not dict:
            raise DeliveryError("thread status violates the stable schema")
        status = cast(dict[object, object], value)
        kind = status.get("type")
        if kind in {"notLoaded", "idle", "systemError"} and set(status) == {"type"}:
            return kind
        if kind == "active" and set(status) == {"activeFlags", "type"}:
            flags = status.get("activeFlags")
            if type(flags) is list and all(flag in {"waitingOnApproval", "waitingOnUserInput"} for flag in flags):
                return "active"
        raise DeliveryError("thread status violates the stable schema")

    def _accept_status_notification(self, message: Mapping[str, object]) -> None:
        if set(message) != {"jsonrpc", "method", "params"} or message.get("jsonrpc") != "2.0":
            raise DeliveryError("status notification violates JSON-RPC")
        if message.get("method") != "thread/status/changed" or type(message.get("params")) is not dict:
            raise DeliveryError("status notification violates the selected schema")
        params = cast(dict[object, object], message["params"])
        if set(params) != {"status", "threadId"} or params.get("threadId") != self._target_thread_id:
            raise DeliveryError("status notification target mismatch")
        self._status = self._status_value(params.get("status"))
        if self._status == "active" and self._turn_request_written:
            return
        if self._status not in {"notLoaded", "idle"}:
            raise DeliveryError("target thread became ineligible")

    @staticmethod
    def _thread(value: object, route: _RouteAuthority) -> str:
        if type(value) is not dict:
            raise DeliveryError("thread violates the selected schema")
        thread = cast(dict[object, object], value)
        required = {
            "cliVersion",
            "createdAt",
            "cwd",
            "ephemeral",
            "id",
            "modelProvider",
            "preview",
            "sessionId",
            "source",
            "status",
            "turns",
            "updatedAt",
        }
        allowed = required | {
            "agentNickname",
            "agentRole",
            "threadSource",
            "forkedFromId",
            "gitInfo",
            "isPinned",
            "name",
            "parentThreadId",
            "path",
            "recencyAt",
        }
        if not required.issubset(thread) or not set(thread).issubset(allowed):
            raise DeliveryError("thread violates the selected field set")
        string_fields = ("cliVersion", "cwd", "id", "modelProvider", "preview", "sessionId")
        if any(type(thread[key]) is not str or len(cast(str, thread[key])) > 4096 for key in string_fields):
            raise DeliveryError("thread identity fields violate the selected schema")
        if (
            type(thread["createdAt"]) is not int
            or type(thread["updatedAt"]) is not int
            or type(thread["ephemeral"]) is not bool
        ):
            raise DeliveryError("thread scalar fields violate the selected schema")
        turns = thread["turns"]
        if type(turns) is not list or len(cast(list[object], turns)) > 128:
            raise DeliveryError("thread turns violate the selected schema")
        for turn in cast(list[object], turns):
            if type(turn) is not dict:
                raise DeliveryError("thread turn violates the selected schema")
            selected_turn = cast(dict[object, object], turn)
            required_turn = {"id", "items", "status"}
            allowed_turn = required_turn | {"completedAt", "durationMs", "error", "itemsView", "startedAt"}
            if not required_turn.issubset(selected_turn) or not set(selected_turn).issubset(allowed_turn):
                raise DeliveryError("thread turn violates the selected field set")
            if type(selected_turn["id"]) is not str or type(selected_turn["items"]) is not list:
                raise DeliveryError("thread turn types violate the selected schema")
            if selected_turn["status"] not in {"completed", "interrupted", "failed", "inProgress"}:
                raise DeliveryError("thread turn status violates the selected schema")
        nullable_strings = (
            "agentNickname",
            "agentRole",
            "forkedFromId",
            "name",
            "parentThreadId",
            "path",
            "threadSource",
        )
        if any(key in thread and thread[key] is not None and type(thread[key]) is not str for key in nullable_strings):
            raise DeliveryError("thread optional string violates the selected schema")
        if "isPinned" in thread and type(thread["isPinned"]) is not bool:
            raise DeliveryError("thread pinned flag violates the selected schema")
        if "recencyAt" in thread and thread["recencyAt"] is not None and type(thread["recencyAt"]) is not int:
            raise DeliveryError("thread recency violates the selected schema")
        if "gitInfo" in thread and thread["gitInfo"] is not None and type(thread["gitInfo"]) is not dict:
            raise DeliveryError("thread git information violates the selected schema")
        if thread["source"] != route.source_kind or thread["cwd"] != route.cwd:
            raise DeliveryError("thread source or cwd does not match the sealed filter")
        return _JsonlSession._status_value(thread["status"])

    def _list_membership(
        self,
        route: _RouteAuthority,
        *,
        archived: bool,
        identities: set[str],
    ) -> int:
        cursor: str | None = None
        cursors: set[str] = set()
        target_count = 0
        try:
            for _page in range(32):
                params: dict[str, object] = {
                    "archived": archived,
                    "cursor": cursor,
                    "cwd": route.cwd,
                    "limit": 100,
                    "sortDirection": "desc",
                    "sortKey": "created_at",
                    "sourceKinds": [route.source_kind],
                    "useStateDbOnly": True,
                }
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
                    status = self._thread(item, route)
                    selected = cast(dict[object, object], item)
                    identifier = selected["id"]
                    if (
                        type(identifier) is not str
                        or not identifier
                        or len(identifier) > 256
                        or identifier in identities
                    ):
                        raise DeliveryError("thread/list contains an invalid or duplicate identity")
                    identities.add(identifier)
                    page_new += 1
                    if identifier == route.thread_id:
                        target_count += 1
                        if status not in {"notLoaded", "idle"}:
                            raise DeliveryError("target thread is not idle")
                        self._status = status
                next_cursor = result.get("nextCursor")
                if next_cursor is None:
                    return target_count
                if page_new == 0:
                    raise DeliveryError("thread/list pagination made no progress")
                if type(next_cursor) is not str or not next_cursor or len(next_cursor) > 1024 or next_cursor in cursors:
                    raise DeliveryError("thread/list cursor is invalid or cyclic")
                cursors.add(next_cursor)
                cursor = next_cursor
            raise DeliveryError("thread/list pagination budget exhausted")
        finally:
            cursors.clear()

    def _exact_target(self, result: Mapping[str, object], route: _RouteAuthority, *, resume: bool = False) -> None:
        required = (
            {"thread"}
            if not resume
            else {"approvalPolicy", "approvalsReviewer", "cwd", "model", "modelProvider", "sandbox", "thread"}
        )
        allowed = required if not resume else required | {"instructionSources", "reasoningEffort", "serviceTier"}
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
        self._status = status

    def prepare(self, route: _RouteAuthority) -> None:
        self._target_thread_id = route.thread_id
        initialized = self._request(
            "initialize", {"clientInfo": {"name": "42-ultracode", "title": "42 Ultracode", "version": "0.1.0"}}
        )
        if set(initialized) != {"codexHome", "platformFamily", "platformOs", "userAgent"}:
            raise DeliveryError("initialize response violates the selected schema")
        if any(type(initialized[key]) is not str for key in initialized):
            raise DeliveryError("initialize response types violate the selected schema")
        if initialized["userAgent"] != _EXPECTED_USER_AGENT:
            raise DeliveryError("app-server version does not match the frozen protocol profile")
        self._notify("initialized", {})
        active_ids: set[str] = set()
        archived_ids: set[str] = set()
        try:
            if self._list_membership(route, archived=False, identities=active_ids) != 1:
                raise DeliveryError("target must occur exactly once in the active listing")
            if self._list_membership(route, archived=True, identities=archived_ids) != 0:
                raise DeliveryError("target must be absent from the archived listing")
            if active_ids & archived_ids:
                raise DeliveryError("thread/list membership is inconsistent across archive filters")
        finally:
            active_ids.clear()
            archived_ids.clear()
        self._exact_target(self._request("thread/read", {"includeTurns": False, "threadId": route.thread_id}), route)
        self._exact_target(self._request("thread/resume", {"threadId": route.thread_id}), route, resume=True)
        self._exact_target(self._request("thread/read", {"includeTurns": False, "threadId": route.thread_id}), route)

    def start_and_wait(self, thread_id: str, message: bytes) -> str:
        text = message.decode("utf-8")
        started = self._request("turn/start", {"input": [{"text": text, "type": "text"}], "threadId": thread_id})
        if set(started) != {"turn"}:
            raise DeliveryError("turn/start response violates the selected schema")
        turn = started.get("turn")
        if type(turn) is not dict:
            raise DeliveryError("turn/start response identity is invalid")
        selected_turn = cast(dict[object, object], turn)
        if (
            set(selected_turn) != {"id", "items", "status"}
            or type(selected_turn.get("id")) is not str
            or type(selected_turn.get("items")) is not list
            or selected_turn.get("status") != "inProgress"
        ):
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
            if set(values) != {"threadId", "turn"}:
                raise DeliveryError("server notification params violate the selected schema")
            if values.get("threadId") != thread_id:
                raise DeliveryError("server notification thread mismatch")
            if method == "error":
                raise DeliveryError("app-server reported a turn error")
            notified_turn = values.get("turn")
            if type(notified_turn) is not dict:
                raise DeliveryError("server notification turn mismatch")
            selected = cast(dict[object, object], notified_turn)
            if (
                set(selected) != {"id", "items", "status"}
                or selected.get("id") != turn_id
                or type(selected.get("items")) is not list
            ):
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
            process_group_id = os.getpgid(self._process.pid)
        except ProcessLookupError:
            process_group_id = self._process.pid
        except OSError as exc:
            self._process_group_id = None
            self.__exit__(None, None, None)
            raise DeliveryError("app-server process-group identity is unavailable") from exc
        if process_group_id != self._process.pid:
            self._process_group_id = None
            self.__exit__(None, None, None)
            raise DeliveryError("app-server did not establish the owned process group")
        if time.monotonic() - started > self._deadline.timeout("process spawn"):
            self.__exit__(None, None, None)
            raise DeliveryError("process spawn deadline expired")
        try:
            _validate_executable(self._authority)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        if self._process.stdin is None or self._process.stdout is None or self._process.stderr is None:
            self.__exit__(None, None, None)
            raise DeliveryError("app-server stdio pipes are unavailable")
        self._stderr = _StderrScanner(cast(BinaryIO, self._process.stderr), self._deadline)
        self._stderr.start()
        return cast(BinaryIO, self._process.stdout), cast(BinaryIO, self._process.stdin), self.check

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
        if executable_authority is None:
            raise DeliveryError("real delivery requires sealed executable authority")
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
) -> DeliveryOutcome:
    """Perform one foreground, human-confirmed delivery through fixed local stdio."""
    message = _read_owned_regular(message_path, max_bytes=_MAX_MESSAGE_BYTES)
    preview = DeliveryPreview(target_alias=target_alias, message=message)
    capability = confirm_preview(preview, input_stream=input_stream, output_stream=output_stream)
    executable_authority = _seal_executable(_CODEX_EXECUTABLE)
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
        "cliVersion": "0.146.0",
        "createdAt": 1,
        "cwd": "/synthetic/workspace",
        "ephemeral": False,
        "id": thread_id,
        "modelProvider": "openai",
        "preview": "synthetic",
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
    turn = {"id": "synthetic-turn", "items": [], "status": "inProgress"}
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
                "userAgent": _EXPECTED_USER_AGENT,
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
                "sandbox": "read-only",
                "thread": thread,
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
