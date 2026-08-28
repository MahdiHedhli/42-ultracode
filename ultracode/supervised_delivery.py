"""Fail-closed F017 D8 supervised Codex task delivery boundary."""

from __future__ import annotations

import fcntl
import io
import json
import os
import secrets
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, NoReturn, SupportsIndex, TextIO, cast
from weakref import WeakKeyDictionary

__all__ = (
    "APP_SERVER_ARGV",
    "D8_POLICY_SHA256",
    "DeliveryError",
    "DeliveryOutcome",
    "DeliveryPreview",
    "ProtocolProfile",
    "confirm_preview",
    "deliver_foreground",
)

D8_POLICY_ID = "f017-m2-d8-supervised-chat-delivery-transport-v1"
D8_POLICY_SHA256 = "db58a1e73a934719f4df7b9e07a4217a289cb8f4b3b748ce16a0e537df8036b6"
APP_SERVER_ARGV = ("codex", "app-server", "--listen", "stdio://")
_MAX_MESSAGE_BYTES = 64 * 1024
_MAX_LINE_BYTES = 1024 * 1024
_SYMBOL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


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
    request_methods=frozenset({"initialize", "thread/read", "thread/resume", "turn/start"}),
    client_notifications=frozenset({"initialized"}),
    server_notifications=frozenset({"turn/started", "turn/completed", "error"}),
    schema_sha256=MappingProxyType(
        {
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
    return cast(dict[str, object], parsed)


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


def _resolve_alias(path: Path, alias: str) -> str:
    alias = _symbol(alias, "target_alias")
    data = _strict_object(_read_owned_regular(path, max_bytes=65536), label="route registry", max_bytes=65536)
    if set(data) != {"aliases", "version"} or data["version"] != 1 or type(data["aliases"]) is not dict:
        raise DeliveryError("route registry violates the closed schema")
    aliases = cast(dict[object, object], data["aliases"])
    if any(type(key) is not str or type(value) is not str for key, value in aliases.items()):
        raise DeliveryError("route registry entries must be strings")
    thread_id = aliases.get(alias)
    safe_thread_chars = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
    if (
        type(thread_id) is not str
        or not thread_id
        or len(thread_id) > 256
        or any(char not in safe_thread_chars for char in thread_id)
    ):
        raise DeliveryError("target alias is missing or invalid")
    return thread_id


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
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._real_operations = real_operations
        self._counters = counters or _OperationCounters()
        self._next_id = 1
        self._turn_start_count = 0
        self.methods: list[str] = []
        self.notifications: list[str] = []

    def _write(self, message: Mapping[str, object]) -> None:
        line = _canonical(dict(message)) + b"\n"
        result = self._writer.write(line)
        if result is not None and result != len(line):
            raise DeliveryError("transport write was partial")
        self._writer.flush()

    def _read(self) -> dict[str, object]:
        line = self._reader.readline(_MAX_LINE_BYTES + 1)
        if not line or len(line) > _MAX_LINE_BYTES or not line.endswith(b"\n"):
            raise DeliveryError("transport response is missing, partial, or oversized")
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
    def _thread_identity(result: Mapping[str, object], expected: str) -> None:
        thread = result.get("thread")
        if type(thread) is not dict or cast(dict[object, object], thread).get("id") != expected:
            raise DeliveryError("thread response identity mismatch")

    def prepare(self, thread_id: str) -> None:
        initialized = self._request(
            "initialize", {"clientInfo": {"name": "42-ultracode", "title": "42 Ultracode", "version": "0.1.0"}}
        )
        if not {"codexHome", "platformFamily", "platformOs", "userAgent"}.issubset(initialized):
            raise DeliveryError("initialize response is incomplete")
        user_agent = initialized["userAgent"]
        if type(user_agent) is not str or "0.146.0" not in user_agent:
            raise DeliveryError("app-server version does not match the frozen protocol profile")
        self._notify("initialized", {})
        read = self._request("thread/read", {"includeTurns": False, "threadId": thread_id})
        self._thread_identity(read, thread_id)
        resumed = self._request("thread/resume", {"threadId": thread_id})
        self._thread_identity(resumed, thread_id)

    def start_and_wait(self, thread_id: str, message: bytes) -> str:
        text = message.decode("utf-8")
        started = self._request("turn/start", {"input": [{"text": text, "type": "text"}], "threadId": thread_id})
        turn = started.get("turn")
        if type(turn) is not dict or type(cast(dict[object, object], turn).get("id")) is not str:
            raise DeliveryError("turn/start response identity is invalid")
        turn_id = cast(str, cast(dict[object, object], turn)["id"])
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
            if values.get("threadId") != thread_id:
                raise DeliveryError("server notification thread mismatch")
            if method == "error":
                raise DeliveryError("app-server reported a turn error")
            notified_turn = values.get("turn")
            if type(notified_turn) is not dict or cast(dict[object, object], notified_turn).get("id") != turn_id:
                raise DeliveryError("server notification turn mismatch")
            if method == "turn/started":
                if saw_started:
                    raise DeliveryError("duplicate turn/started notification")
                saw_started = True
                continue
            status = cast(dict[object, object], notified_turn).get("status")
            if not saw_started or status != "completed":
                raise DeliveryError("turn did not complete successfully")
            return turn_id


class _CodexProcess:
    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> tuple[BinaryIO, BinaryIO]:
        self._process = subprocess.Popen(
            APP_SERVER_ARGV,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
        )
        if self._process.stdin is None or self._process.stdout is None:
            self.__exit__(None, None, None)
            raise DeliveryError("app-server stdio pipes are unavailable")
        return cast(BinaryIO, self._process.stdout), cast(BinaryIO, self._process.stdin)

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        if self._process is None:
            return
        for stream in (self._process.stdin, self._process.stdout):
            if stream is not None:
                stream.close()
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
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
) -> tuple[DeliveryOutcome, _JsonlSession | None]:
    consume = _consume_capability
    if not callable(consume):
        raise DeliveryError("confirmation boundary is unavailable")
    consume(capability, preview)
    real_operations = streams is None
    operation_counters = counters or _OperationCounters()
    thread_id = _resolve_alias(route_registry, preview.target_alias)
    if real_operations:
        operation_counters.real_alias_resolutions += 1
    attempt = attempt_id or secrets.token_hex(16)
    with _Journal(journal_path) as journal:
        prior = journal.terminal_for(payload_sha256=preview.payload_sha256, target_alias=preview.target_alias)
        if prior in {DeliveryOutcome.DELIVERED, DeliveryOutcome.UNCERTAIN}:
            raise DeliveryError(f"delivery is terminal: {prior.value}")

        def run(reader: BinaryIO, writer: BinaryIO) -> tuple[DeliveryOutcome, _JsonlSession]:
            session = _JsonlSession(
                reader,
                writer,
                real_operations=real_operations,
                counters=operation_counters,
            )
            try:
                session.prepare(thread_id)
            except (BrokenPipeError, DeliveryError, OSError):
                journal.append(event="FAILED_BEFORE_WRITE", attempt_id=attempt, preview=preview, thread_id=thread_id)
                return DeliveryOutcome.FAILED_BEFORE_WRITE, session
            journal.append(event="ATTEMPT_STARTED", attempt_id=attempt, preview=preview, thread_id=thread_id)
            try:
                session.start_and_wait(thread_id, preview.message)
            except (BrokenPipeError, DeliveryError, OSError):
                journal.append(event="UNCERTAIN", attempt_id=attempt, preview=preview, thread_id=thread_id)
                return DeliveryOutcome.UNCERTAIN, session
            journal.append(event="DELIVERED", attempt_id=attempt, preview=preview, thread_id=thread_id)
            return DeliveryOutcome.DELIVERED, session

        if streams is not None:
            return run(*streams)
        operation_counters.real_app_server_launches += 1
        with _CodexProcess() as process_streams:
            return run(*process_streams)


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
    outcome, _session = _perform(
        preview=preview,
        capability=capability,
        route_registry=route_registry,
        journal_path=journal_path,
    )
    return outcome


def _fake_transcript(thread_id: str = "synthetic-thread") -> bytes:
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
                "userAgent": "codex-cli 0.146.0 synthetic-peer/1",
            },
        },
        {"id": 2, "jsonrpc": "2.0", "result": {"thread": {"id": thread_id}}},
        {"id": 3, "jsonrpc": "2.0", "result": {"thread": {"id": thread_id}}},
        {"id": 4, "jsonrpc": "2.0", "result": {"turn": turn}},
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
    _write_exclusive_owned(route, _canonical({"aliases": {"SYNTHETIC_TARGET": "synthetic-thread"}, "version": 1}))
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
