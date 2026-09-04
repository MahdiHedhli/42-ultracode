"""Durable foreground task discovery and one-confirmation delivery pilot."""

from __future__ import annotations

import os
import select
import stat
import time
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import TextIO

from .supervised_delivery import (
    DISCOVERY_PROTOCOL_PROFILE,
    DeliveryError,
    DeliveryOutcome,
    ThreadListing,
    ThreadListingEntry,
    _canonical,
    _CodexProcess,
    _Deadline,
    _Journal,
    _JsonlSession,
    _read_owned_regular,
    _require_production_identity,
    _seal_production_executable,
    _strict_object,
    deliver_foreground,
)

__all__ = (
    "PILOT_EXIT_CODES",
    "PilotConfig",
    "PilotOutcome",
    "run_supervised_pilot",
)

_ALIAS = "F017_D8_PILOT_TARGET"
_LOCATOR_BASENAME = "f017-d8-pilot-sequence38.json"
_SEQUENCE39_LOCATOR_BASENAME = "f017-d8-pilot-sequence39.json"
_LOCATOR_BASENAMES = frozenset({_LOCATOR_BASENAME, _SEQUENCE39_LOCATOR_BASENAME})
_MESSAGE_SHA256 = "039cf28debcb905e3a94876e9fc938b2964e9ed9df86a61d9a202ee1ad3452d9"
_MESSAGE_BYTES = 224
_MAX_SELECTION_SECONDS = 600.0
_MAX_CORRECTIONS = 2
_SAFE_THREAD = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
_BIDI = frozenset(chr(value) for value in range(0x202A, 0x202F)) | frozenset(
    chr(value) for value in range(0x2066, 0x206A)
)
_ZERO_WIDTH = frozenset({"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"})


class PilotOutcome(StrEnum):
    DELIVERED = "DELIVERED"
    ROUTE_TARGET_NOT_SELECTED = "ROUTE_TARGET_NOT_SELECTED"
    TOOLING_FAILURE_BEFORE_LIST = "TOOLING_FAILURE_BEFORE_LIST"
    ROUTE_CONFLICT = "ROUTE_CONFLICT"
    HUMAN_DECLINED_NO_ATTEMPT = "HUMAN_DECLINED_NO_ATTEMPT"
    FAILED_BEFORE_WRITE = "FAILED_BEFORE_WRITE"
    UNCERTAIN = "UNCERTAIN"
    POST_LIST_TOOLING_TERMINAL = "POST_LIST_TOOLING_TERMINAL"


PILOT_EXIT_CODES: Mapping[PilotOutcome, int] = {
    PilotOutcome.DELIVERED: 0,
    PilotOutcome.ROUTE_TARGET_NOT_SELECTED: 30,
    PilotOutcome.TOOLING_FAILURE_BEFORE_LIST: 31,
    PilotOutcome.ROUTE_CONFLICT: 32,
    PilotOutcome.HUMAN_DECLINED_NO_ATTEMPT: 33,
    PilotOutcome.FAILED_BEFORE_WRITE: 34,
    PilotOutcome.UNCERTAIN: 35,
    PilotOutcome.POST_LIST_TOOLING_TERMINAL: 36,
}


@dataclass(frozen=True, slots=True)
class PilotConfig:
    message_path: Path
    locator_basename: str
    report_path: Path
    prompt_commit: str
    prompt_sha256: str

    def validate(self) -> None:
        if self.locator_basename not in _LOCATOR_BASENAMES:
            raise DeliveryError("pilot locator basename is not authorized")
        if len(self.prompt_commit) != 40 or any(c not in "0123456789abcdef" for c in self.prompt_commit):
            raise DeliveryError("pilot prompt commit is invalid")
        if len(self.prompt_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.prompt_sha256):
            raise DeliveryError("pilot prompt SHA-256 is invalid")


@dataclass(frozen=True, slots=True)
class _TtyIdentity:
    device: int
    rdevice: int
    terminal_name: str
    session: int
    process_group: int


@dataclass(frozen=True, slots=True)
class _RouteFiles:
    root: Path
    registry: Path
    locator: Path
    journal: Path
    launch_marker: Path
    message: Path
    registry_bytes: bytes
    locator_bytes: bytes


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _safe_existing_ancestry(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if not current.exists():
            break
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode):
            raise DeliveryError("pilot path ancestry contains a symlink")
        if info.st_uid not in {0, os.getuid()}:
            raise DeliveryError("pilot path ancestry has a foreign owner")
        if info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
            raise DeliveryError("pilot path ancestry is writable by another user")


def _ensure_private_directory(path: Path) -> None:
    _safe_existing_ancestry(path.parent)
    if path.exists():
        info = os.lstat(path)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise DeliveryError("pilot directory ownership or mode is unsafe")
        return
    path.mkdir(mode=0o700)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise DeliveryError("pilot directory creation was unsafe")
    _fsync_directory(path.parent)


def _private_tree(path: Path) -> None:
    missing: list[Path] = []
    current = path.absolute()
    while not current.exists():
        missing.append(current)
        current = current.parent
    _safe_existing_ancestry(current)
    for item in reversed(missing):
        _ensure_private_directory(item)


def _write_exclusive_atomic(path: Path, content: bytes) -> None:
    _safe_existing_ancestry(path.parent)
    if path.exists() or path.is_symlink():
        raise FileExistsError(path.name)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(6).hex()}.partial")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise DeliveryError("pilot private write was partial")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        _fsync_directory(path.parent)
    except BaseException:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()
        raise
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise DeliveryError("pilot private file ownership or mode is unsafe")


def _capture_tty_identity(fd: int) -> _TtyIdentity:
    info = os.fstat(fd)
    if not stat.S_ISCHR(info.st_mode) or not os.isatty(fd):
        raise DeliveryError("pilot requires the real foreground terminal")
    process_group = os.getpgrp()
    foreground = os.tcgetpgrp(fd)
    session = os.getsid(0)
    if foreground != process_group or session == os.getpid():
        raise DeliveryError("pilot process does not own the foreground terminal")
    return _TtyIdentity(info.st_dev, info.st_rdev, os.ttyname(fd), session, process_group)


def _revalidate_tty(fd: int, expected: _TtyIdentity) -> None:
    if _capture_tty_identity(fd) != expected:
        raise DeliveryError("pilot terminal identity changed")


@contextmanager
def _open_foreground_tty() -> Iterator[tuple[TextIO, TextIO, int, _TtyIdentity]]:
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open("/dev/tty", flags)
    reader: TextIO | None = None
    writer: TextIO | None = None
    try:
        identity = _capture_tty_identity(fd)
        reader = os.fdopen(os.dup(fd), "r", encoding="utf-8", errors="strict", buffering=1)
        writer = os.fdopen(os.dup(fd), "w", encoding="utf-8", errors="strict", buffering=1)
        yield reader, writer, fd, identity
    finally:
        if reader is not None:
            reader.close()
        if writer is not None:
            writer.close()
        os.close(fd)


def _terminal_text(value: str, *, columns: int = 120) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    result: list[str] = []
    width = 0
    combining_run = 0
    for char in normalized:
        code = ord(char)
        category = unicodedata.category(char)
        combining = bool(unicodedata.combining(char))
        combining_run = combining_run + 1 if combining else 0
        unsafe = (
            code < 0x20
            or 0x7F <= code <= 0x9F
            or category == "Cf"
            or char in _BIDI
            or char in _ZERO_WIDTH
            or combining_run > 3
        )
        rendered = f"\\u{code:04x}" if unsafe else char
        char_width = 0 if combining and not unsafe else (2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1)
        if width + max(1, char_width) > columns:
            break
        result.append(rendered)
        width += max(1, char_width)
    return "".join(result)


def _eligible_bucket(count: int) -> str:
    if count == 0:
        return "0"
    if count == 1:
        return "1"
    if count < 10:
        return "2-9"
    return "10+"


def _method_counts(session: _JsonlSession | None) -> dict[str, object]:
    if session is None:
        return {
            "inbound_notifications": {},
            "inbound_responses": 0,
            "inbound_server_requests": 0,
            "outbound_notifications": {},
            "outbound_requests": {},
        }
    requests: dict[str, int] = {}
    notifications: dict[str, int] = {}
    for method in session.methods:
        requests[method] = requests.get(method, 0) + 1
    for method in session.notifications:
        notifications[method] = notifications.get(method, 0) + 1
    return {
        "inbound_notifications": dict(sorted(session.inbound_notifications.items())),
        "inbound_responses": session.inbound_responses,
        "inbound_server_requests": session.inbound_server_requests,
        "outbound_notifications": dict(sorted(notifications.items())),
        "outbound_requests": dict(sorted(requests.items())),
    }


def _write_report(
    path: Path,
    *,
    outcome: PilotOutcome,
    phase: str,
    list_shown: bool,
    eligible_count: int,
    session: _JsonlSession | None,
    rejected_entries: int,
    post_guard: str,
    failure_class: str,
) -> None:
    record = {
        "eligible_count_bucket": _eligible_bucket(eligible_count),
        "failure_class": failure_class,
        "list_shown": list_shown,
        "method_counts": _method_counts(session),
        "notification_count": sum(session.inbound_notifications.values()) if session is not None else 0,
        "per_entry_rejection_count": rejected_entries,
        "phase_reached": phase,
        "post_terminal_route_digest_guard": post_guard,
        "schema": "42-ultracode.d8-supervised-pilot-sanitized/1.0.0",
        "status": outcome.value,
    }
    _write_exclusive_atomic(path, _canonical(record) + b"\n")


def _present_and_select(
    listing: ThreadListing,
    *,
    reader: TextIO,
    writer: TextIO,
    tty_fd: int,
    identity: _TtyIdentity,
) -> ThreadListingEntry | None:
    _revalidate_tty(tty_fd, identity)
    if not listing.entries:
        writer.write("No eligible Codex tasks are available.\n")
        writer.flush()
        return None
    writer.write("Select the intended Codex task:\n")
    for number, entry in enumerate(listing.entries, 1):
        label = entry.name or entry.preview
        writer.write(
            f"{number}. {_terminal_text(label)} | {_terminal_text(entry.source_kind)} | "
            f"{_terminal_text(entry.cwd)} | {_terminal_text(entry.status)} | {_terminal_text(entry.thread_id[:12])}\n"
        )
    writer.write("Enter a listed number or q: ")
    writer.flush()
    deadline = time.monotonic() + _MAX_SELECTION_SECONDS
    corrections = 0
    while True:
        _revalidate_tty(tty_fd, identity)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        ready, _writable, _errors = select.select([reader.fileno()], [], [], remaining)
        if not ready:
            return None
        answer = reader.readline(32)
        if answer == "":
            return None
        selected = answer.strip()
        if selected == "q":
            return None
        if selected.isascii() and selected.isdigit():
            number = int(selected)
            if 1 <= number <= len(listing.entries):
                return listing.entries[number - 1]
        corrections += 1
        if corrections > _MAX_CORRECTIONS:
            return None
        writer.write("Invalid selection. Enter a listed number or q: ")
        writer.flush()


def _configuration_root() -> Path:
    return Path.home() / ".config" / "42-ultracode"


def _data_root() -> Path:
    return Path.home() / ".local" / "share" / "42-ultracode" / "routes"


def _publish_route(entry: ThreadListingEntry, locator_basename: str) -> _RouteFiles:
    config_root = _configuration_root()
    data_root = _data_root()
    _private_tree(config_root)
    _private_tree(data_root)
    locator = config_root / locator_basename
    if locator.exists() or locator.is_symlink():
        raise FileExistsError(locator_basename)
    sequence = "sequence39" if locator_basename == _SEQUENCE39_LOCATOR_BASENAME else "sequence38"
    root = data_root / f"f017-{sequence}-{os.urandom(16).hex()}"
    root.mkdir(mode=0o700)
    _fsync_directory(root.parent)
    registry = root / "route-registry.json"
    journal = root / "delivery-journal.jsonl"
    launch_marker = root / "launch-marker.json"
    message = root / "message.txt"
    registry_bytes = (
        _canonical(
            {
                "aliases": {_ALIAS: {"cwd": entry.cwd, "source_kind": entry.source_kind, "thread_id": entry.thread_id}},
                "version": 2,
            }
        )
        + b"\n"
    )
    locator_bytes = (
        _canonical(
            {
                "journal": os.fspath(journal),
                "launch_marker": os.fspath(launch_marker),
                "route_registry": os.fspath(registry),
                "version": 1,
            }
        )
        + b"\n"
    )
    try:
        _write_exclusive_atomic(registry, registry_bytes)
        _write_exclusive_atomic(locator, locator_bytes)
    except BaseException:
        if not locator.exists():
            for child in (registry, message, launch_marker, journal):
                if child.exists() and not child.is_symlink():
                    child.unlink()
            if root.exists() and not any(root.iterdir()):
                root.rmdir()
        raise
    return _RouteFiles(root, registry, locator, journal, launch_marker, message, registry_bytes, locator_bytes)


def _revalidate_route(files: _RouteFiles) -> None:
    registry = _read_owned_regular(files.registry, max_bytes=65536)
    locator = _read_owned_regular(files.locator, max_bytes=65536)
    if registry != files.registry_bytes or locator != files.locator_bytes:
        raise DeliveryError("private route identity changed")
    loc = _strict_object(locator, label="pilot locator", max_bytes=65536)
    if set(loc) != {"journal", "launch_marker", "route_registry", "version"} or loc["version"] != 1:
        raise DeliveryError("pilot locator violates the closed schema")
    expected = {
        "journal": os.fspath(files.journal),
        "launch_marker": os.fspath(files.launch_marker),
        "route_registry": os.fspath(files.registry),
    }
    if any(loc.get(key) != value for key, value in expected.items()):
        raise DeliveryError("pilot locator identity changed")


def _render_combined_preview(entry: ThreadListingEntry, message: bytes, writer: TextIO) -> None:
    text = message.decode("utf-8", "strict")
    writer.write("=== SELECTED CODEX TASK ===\n")
    writer.write(f"name: {_terminal_text(entry.name or entry.preview)}\n")
    writer.write(f"source: {_terminal_text(entry.source_kind)}\n")
    writer.write(f"cwd: {_terminal_text(entry.cwd)}\n")
    writer.write(f"status: {_terminal_text(entry.status)}\n")
    writer.write(f"id-prefix: {_terminal_text(entry.thread_id[:12])}\n")
    writer.write("=== EXACT DELIVERY MESSAGE ===\n")
    writer.write(text)
    if not text.endswith("\n"):
        writer.write("\n")
    writer.write("The next challenge approves one delivery to this selected task.\n")
    writer.flush()


def _post_terminal_guard(files: _RouteFiles, *, journal_expected: bool) -> str:
    try:
        _revalidate_route(files)
        if journal_expected:
            with _Journal(files.journal):
                pass
        elif files.journal.exists() or files.journal.is_symlink():
            raise DeliveryError("unexpected pilot journal")
        return "PASS"
    except (DeliveryError, OSError):
        return "FAIL"


class _PilotTerminal(Exception):
    """Internal control flow after a terminal outcome has been classified."""


def run_supervised_pilot(
    config: PilotConfig,
    *,
    before_delivery: Callable[[], None] | None = None,
) -> PilotOutcome:
    """Discover, select, preview, and make at most one confirmed delivery."""

    phase = "PRECHECK"
    list_shown = False
    eligible_count = 0
    rejected_entries = 0
    session: _JsonlSession | None = None
    files: _RouteFiles | None = None
    outcome = PilotOutcome.TOOLING_FAILURE_BEFORE_LIST
    failure_class = "PRECHECK"
    post_guard = "NOT_REACHED"
    try:
        config.validate()
        message = _read_owned_regular(config.message_path, max_bytes=_MESSAGE_BYTES)
        if len(message) != _MESSAGE_BYTES or sha256(message).hexdigest() != _MESSAGE_SHA256:
            raise DeliveryError("pilot message identity mismatch")
        if config.report_path.exists() or config.report_path.is_symlink():
            raise DeliveryError("pilot sanitized report already exists")
        locator = _configuration_root() / config.locator_basename
        if locator.exists() or locator.is_symlink():
            outcome = PilotOutcome.ROUTE_CONFLICT
            failure_class = "ROUTE_CONFLICT"
            raise FileExistsError(config.locator_basename)
        authority = _seal_production_executable()
        identity = _require_production_identity(authority)
        phase = "DISCOVERY"
        with _open_foreground_tty() as (reader, writer, tty_fd, tty_identity):
            deadline = _Deadline.start(session_seconds=_MAX_SELECTION_SECONDS, operation_seconds=10.0)
            process = _CodexProcess(authority, deadline)
            with process as streams:
                app_reader, app_writer, health_check = streams
                session = _JsonlSession(
                    app_reader,
                    app_writer,
                    real_operations=True,
                    deadline=deadline,
                    health_check=health_check,
                    expected_user_agent=identity.cli_version,
                    protocol_profile=DISCOVERY_PROTOCOL_PROFILE,
                )
                session.initialize()
                listing = session.list_threads()
            if process.cleanup_issue is not None:
                raise DeliveryError("discovery process cleanup failed")
            rejected_entries = listing.rejected_entries
            eligible_count = len(listing.entries)
            phase = "LIST_SHOWN"
            list_shown = True
            selected = _present_and_select(listing, reader=reader, writer=writer, tty_fd=tty_fd, identity=tty_identity)
            if selected is None:
                outcome = PilotOutcome.ROUTE_TARGET_NOT_SELECTED
                failure_class = "TARGET_NOT_SELECTED"
                raise _PilotTerminal
            _revalidate_tty(tty_fd, tty_identity)
            files = _publish_route(selected, config.locator_basename)
            phase = "ROUTE_PUBLISHED"
            _revalidate_route(files)
            _render_combined_preview(selected, message, writer)
            phase = "PREVIEW_SHOWN"
            if before_delivery is not None:
                before_delivery()
            _revalidate_tty(tty_fd, tty_identity)
            if (
                files.journal.exists()
                or files.journal.is_symlink()
                or files.launch_marker.exists()
                or files.launch_marker.is_symlink()
            ):
                raise DeliveryError("reserved pilot lifecycle path already exists")
            marker = (
                _canonical(
                    {
                        "message_sha256": _MESSAGE_SHA256,
                        "prompt_commit": config.prompt_commit,
                        "prompt_sha256": config.prompt_sha256,
                        "registry_sha256": sha256(files.registry_bytes).hexdigest(),
                        "version": 1,
                    }
                )
                + b"\n"
            )
            _write_exclusive_atomic(files.launch_marker, marker)
            _write_exclusive_atomic(files.message, message)
            phase = "DELIVERY"
            try:
                delivery = deliver_foreground(
                    message_path=files.message,
                    target_alias=_ALIAS,
                    route_registry=files.registry,
                    journal_path=files.journal,
                    input_stream=reader,
                    output_stream=writer,
                    _executable_authority=authority,
                )
            except DeliveryError as exc:
                if "confirmation challenge mismatch" in str(exc):
                    outcome = PilotOutcome.HUMAN_DECLINED_NO_ATTEMPT
                    failure_class = "HUMAN_DECLINED"
                else:
                    outcome = PilotOutcome.POST_LIST_TOOLING_TERMINAL
                    failure_class = "DELIVERY_TOOLING"
                raise _PilotTerminal from exc
            if delivery is DeliveryOutcome.DELIVERED:
                outcome = PilotOutcome.DELIVERED
                failure_class = "NONE"
            elif delivery is DeliveryOutcome.FAILED_BEFORE_WRITE:
                outcome = PilotOutcome.FAILED_BEFORE_WRITE
                failure_class = "DELIVERY_PREWRITE"
            else:
                outcome = PilotOutcome.UNCERTAIN
                failure_class = "DELIVERY_UNCERTAIN"
    except _PilotTerminal:
        pass
    except FileExistsError:
        pass
    except (DeliveryError, OSError, UnicodeError, ValueError):
        if list_shown:
            outcome = PilotOutcome.POST_LIST_TOOLING_TERMINAL
            failure_class = "POST_LIST_TOOLING"
        else:
            outcome = PilotOutcome.TOOLING_FAILURE_BEFORE_LIST
            failure_class = "DISCOVERY_TOOLING"
    finally:
        phase_at_terminal = phase
        if files is not None:
            journal_expected = files.journal.exists() and not files.journal.is_symlink()
            post_guard = _post_terminal_guard(files, journal_expected=journal_expected)
            if post_guard == "FAIL" and outcome is not PilotOutcome.UNCERTAIN:
                outcome = PilotOutcome.POST_LIST_TOOLING_TERMINAL
                failure_class = "POST_TERMINAL_ROUTE_GUARD"
        with suppress(DeliveryError, FileExistsError, OSError):
            _write_report(
                config.report_path,
                outcome=outcome,
                phase=phase_at_terminal,
                list_shown=list_shown,
                eligible_count=eligible_count,
                session=session,
                rejected_entries=rejected_entries,
                post_guard=post_guard,
                failure_class=failure_class,
            )
    return outcome
