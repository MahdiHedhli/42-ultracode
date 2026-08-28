"""Pure, transport-free F017 D7 supervised-handoff readiness boundary."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import NoReturn, cast
from weakref import WeakKeyDictionary

__all__ = (
    "DeliveryEventKind",
    "DeliverySnapshot",
    "DeliveryState",
    "HandoffAuthority",
    "PreparedDryRun",
    "ReadinessError",
    "ReadinessEvent",
    "SanitizedObservation",
    "SealedReadinessRequest",
    "make_mock_event",
    "parse_handoff_authority",
    "parse_sanitized_observation",
    "prepare_dry_run",
    "replay_mock_lifecycle",
    "seal_readiness_request",
)
_TOKEN = object()
_PROXY_TYPE: type[object] = type(MappingProxyType({}))
_SHA = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SYMBOL = re.compile(r"[A-Z][A-Z0-9_]{2,63}")
_NONCE = re.compile(r"[0-9a-f]{32}")
_PATH = re.compile(
    r"Prompts/F017/MacBook-Pro-M2-Max/(?P<s>[0-9]{3})__F017__MacBook-Pro-M2-Max__[A-Za-z0-9-]+__response[.]md"
)
_HANDOFF = frozenset({"feature_id", "machine_model", "response_sha256", "response_url", "sequence", "status"})
_OBSERVATION = frozenset(
    {
        "application_symbol",
        "feature_marker",
        "host_symbol",
        "observation_nonce",
        "observation_version",
        "origin",
        "prior_sequence",
        "response_commit",
        "response_path",
        "response_sha256",
        "route_alias_key",
    }
)


class ReadinessError(ValueError):
    """The closed readiness contract was violated."""


class DeliveryState(StrEnum):
    UNPREPARED = "UNPREPARED"
    PREPARED_READINESS_ONLY = "PREPARED_READINESS_ONLY"
    MOCK_ATTEMPT_FAILED_RETRYABLE = "MOCK_ATTEMPT_FAILED_RETRYABLE"
    MOCK_DELIVERED = "MOCK_DELIVERED"
    TERMINAL_DUPLICATE_REJECTED = "TERMINAL_DUPLICATE_REJECTED"


class DeliveryEventKind(StrEnum):
    PREPARE = "PREPARE"
    MOCK_ATTEMPT_FAILED = "MOCK_ATTEMPT_FAILED"
    MOCK_ATTEMPT_UNCERTAIN = "MOCK_ATTEMPT_UNCERTAIN"
    MOCK_RETRY_PREPARED = "MOCK_RETRY_PREPARED"
    MOCK_RECEIPT_ACCEPTED = "MOCK_RECEIPT_ACCEPTED"
    MOCK_DUPLICATE_REJECTED = "MOCK_DUPLICATE_REJECTED"


def _sealed_metaclass() -> type[type]:
    class SealedMeta(type):
        def __new__(mcls, name: str, bases: tuple[type, ...], namespace: dict[str, object], **kwargs: object) -> type:
            if any(isinstance(base, mcls) and "_internal_sealed_root" not in vars(base) for base in bases):
                raise ReadinessError("sealed readiness objects cannot be subclassed")
            return super().__new__(mcls, name, bases, namespace, **kwargs)

    return SealedMeta


def _sealed_registry() -> tuple[object, object]:
    records: WeakKeyDictionary[object, object] = WeakKeyDictionary()

    def register(record: object, values: object) -> None:
        records[record] = values

    def contains(record: object, values: object) -> bool:
        return records.get(record) is values

    return register, contains


_register_sealed, _is_registered_sealed = _sealed_registry()


class _Sealed(metaclass=_sealed_metaclass()):  # type: ignore[metaclass]
    __slots__ = ("__weakref__", "_values")
    _internal_sealed_root = True
    _values: Mapping[str, object]

    def __init__(self, values: Mapping[str, object], *, _token: object) -> None:
        if _token is not _TOKEN:
            raise ReadinessError("object must be created by the closed boundary")
        sealed_values = MappingProxyType(dict(values))
        object.__setattr__(self, "_values", sealed_values)
        _register_sealed(self, sealed_values)  # type: ignore[operator]

    def __setattr__(self, _name: str, _value: object) -> NoReturn:
        raise ReadinessError("sealed readiness objects are immutable")

    def __copy__(self) -> NoReturn:
        raise ReadinessError("sealed readiness objects cannot be copied")

    def __deepcopy__(self, _memo: object) -> NoReturn:
        raise ReadinessError("sealed readiness objects cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise ReadinessError("sealed readiness objects cannot be serialized")

    def __reduce_ex__(self, _protocol: object) -> NoReturn:
        raise ReadinessError("sealed readiness objects cannot be serialized")

    def _get(self, key: str) -> object:
        return self._values[key]


class SealedReadinessRequest(_Sealed):
    __slots__ = ()

    @property
    def feature_id(self) -> str:
        return cast(str, self._get("feature_id"))

    @property
    def machine_model(self) -> str:
        return cast(str, self._get("machine_model"))

    @property
    def prior_sequence(self) -> int:
        return cast(int, self._get("prior_sequence"))

    @property
    def current_sequence(self) -> int:
        return cast(int, self._get("current_sequence"))

    @property
    def response_commit(self) -> str:
        return cast(str, self._get("response_commit"))

    @property
    def response_path(self) -> str:
        return cast(str, self._get("response_path"))

    @property
    def response_sha256(self) -> str:
        return cast(str, self._get("response_sha256"))

    @property
    def response_url(self) -> str:
        return cast(str, self._get("response_url"))

    @property
    def route_alias_key(self) -> str:
        return cast(str, self._get("route_alias_key"))


class HandoffAuthority(_Sealed):
    __slots__ = ()


class SanitizedObservation(_Sealed):
    __slots__ = ()


class ReadinessEvent(_Sealed):
    __slots__ = ()


class PreparedDryRun(_Sealed):
    __slots__ = ()

    @property
    def canonical_payload_sha256(self) -> str:
        return cast(str, self._get("canonical_payload_sha256"))

    @property
    def idempotency_key(self) -> str:
        return cast(str, self._get("idempotency_key"))


class DeliverySnapshot(_Sealed):
    __slots__ = ()

    @property
    def state(self) -> DeliveryState:
        return cast(DeliveryState, self._get("state"))

    @property
    def event_log_sha256(self) -> str:
        return cast(str, self._get("event_log_sha256"))


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


def _bad_constant(_value: str) -> NoReturn:
    raise ReadinessError("non-finite JSON is prohibited")


def _load(raw: bytes, fields: frozenset[str], label: str) -> dict[str, object]:
    duplicate = False

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        nonlocal duplicate
        out: dict[str, object] = {}
        for key, value in items:
            duplicate |= key in out
            out[key] = value
        return out

    try:
        value = cast(
            object, json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs, parse_constant=_bad_constant)
        )
    except (AttributeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReadinessError(f"{label} is not canonical JSON") from exc
    if duplicate or not isinstance(value, dict):
        raise ReadinessError(f"{label} must be one duplicate-free object")
    result = cast(dict[str, object], value)
    if set(result) != fields or _canonical(result) != raw:
        raise ReadinessError(f"{label} violates the closed canonical contract")
    return result


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or not value.isascii():
        raise ReadinessError(f"{field} must be non-empty ASCII")
    return value


def _integer(value: object, field: str) -> int:
    if type(value) is not int:
        raise ReadinessError(f"{field} must be an integer")
    return value


def _value(record: _Sealed, key: str) -> object:
    return record._get(key)


def _require_sealed(record: object, expected: type[_Sealed]) -> _Sealed:
    if type(record) is not expected:
        raise ReadinessError("readiness object has the wrong sealed type")
    sealed = record
    try:
        values = object.__getattribute__(sealed, "_values")
    except AttributeError as exc:
        raise ReadinessError("readiness object is not sealed") from exc
    if not isinstance(values, _PROXY_TYPE) or not _is_registered_sealed(sealed, values):  # type: ignore[operator]
        raise ReadinessError("readiness object is not sealed")
    return sealed


def seal_readiness_request(
    *,
    feature_id: str,
    machine_model: str,
    prior_sequence: int,
    current_sequence: int,
    expected_response_commit: str,
    expected_response_path: str,
    expected_response_sha256: str,
    verified_response_bytes: bytes,
    route_alias_key: str,
) -> SealedReadinessRequest:
    if (feature_id, machine_model, prior_sequence, current_sequence) != ("F017", "MacBook Pro M2 Max", 17, 18):
        raise ReadinessError("frontier identity mismatch")
    if not isinstance(expected_response_path, str):
        raise ReadinessError("response identity is invalid")
    match = _PATH.fullmatch(expected_response_path)
    if (
        not isinstance(expected_response_commit, str)
        or not _COMMIT.fullmatch(expected_response_commit)
        or not isinstance(expected_response_sha256, str)
        or not _SHA.fullmatch(expected_response_sha256)
        or match is None
        or int(match.group("s")) != 17
    ):
        raise ReadinessError("response identity is invalid")
    if (
        not isinstance(verified_response_bytes, bytes)
        or sha256(verified_response_bytes).hexdigest() != expected_response_sha256
    ):
        raise ReadinessError("verified response bytes do not match")
    if not isinstance(route_alias_key, str) or not _SYMBOL.fullmatch(route_alias_key):
        raise ReadinessError("route key must remain symbolic")
    url = f"https://github.com/MahdiHedhli/PulsarMLX-Prompts/blob/{expected_response_commit}/{expected_response_path}"
    return SealedReadinessRequest(
        {
            "feature_id": feature_id,
            "machine_model": machine_model,
            "prior_sequence": prior_sequence,
            "current_sequence": current_sequence,
            "response_commit": expected_response_commit,
            "response_path": expected_response_path,
            "response_sha256": expected_response_sha256,
            "response_url": url,
            "route_alias_key": route_alias_key,
        },
        _token=_TOKEN,
    )


def parse_handoff_authority(raw: bytes, request: SealedReadinessRequest) -> HandoffAuthority:
    _require_sealed(request, SealedReadinessRequest)
    data = _load(raw, _HANDOFF, "handoff")
    got = (
        _text(data["feature_id"], "feature_id"),
        _text(data["machine_model"], "machine_model"),
        _text(data["response_sha256"], "response_sha256"),
        _text(data["response_url"], "response_url"),
        _integer(data["sequence"], "sequence"),
        _text(data["status"], "status"),
    )
    expected = (
        request.feature_id,
        request.machine_model,
        request.response_sha256,
        request.response_url,
        request.prior_sequence,
        "PASS",
    )
    if got != expected:
        raise ReadinessError("handoff identity mismatch")
    return HandoffAuthority(
        data | {"response_commit": request.response_commit, "response_path": request.response_path}, _token=_TOKEN
    )


def parse_sanitized_observation(raw: bytes, request: SealedReadinessRequest) -> SanitizedObservation:
    _require_sealed(request, SealedReadinessRequest)
    data = _load(raw, _OBSERVATION, "observation")
    expected: dict[str, object] = {
        "application_symbol": "MOCK_CHAT_APP",
        "feature_marker": request.feature_id,
        "host_symbol": "MOCK_LOCAL_HOST",
        "observation_version": 1,
        "origin": "MOCK_LOCAL_ONLY",
        "prior_sequence": request.prior_sequence,
        "response_commit": request.response_commit,
        "response_path": request.response_path,
        "response_sha256": request.response_sha256,
        "route_alias_key": request.route_alias_key,
    }
    for key, wanted in expected.items():
        got = _integer(data[key], key) if isinstance(wanted, int) else _text(data[key], key)
        if got != wanted:
            raise ReadinessError("sanitized observation identity mismatch")
    if not _NONCE.fullmatch(_text(data["observation_nonce"], "observation_nonce")):
        raise ReadinessError("observation nonce is invalid")
    return SanitizedObservation(data, _token=_TOKEN)


def prepare_dry_run(
    request: SealedReadinessRequest, handoff: HandoffAuthority, observation: SanitizedObservation
) -> PreparedDryRun:
    _require_sealed(request, SealedReadinessRequest)
    _require_sealed(handoff, HandoffAuthority)
    _require_sealed(observation, SanitizedObservation)
    identities = (
        _value(handoff, "response_commit"),
        _value(handoff, "response_path"),
        _value(handoff, "response_sha256"),
        _value(handoff, "sequence"),
        _value(observation, "response_commit"),
        _value(observation, "response_path"),
        _value(observation, "response_sha256"),
        _value(observation, "prior_sequence"),
        _value(observation, "route_alias_key"),
    )
    if identities != (
        request.response_commit,
        request.response_path,
        request.response_sha256,
        request.prior_sequence,
        request.response_commit,
        request.response_path,
        request.response_sha256,
        request.prior_sequence,
        request.route_alias_key,
    ):
        raise ReadinessError("handoff and observation disagree")
    payload: dict[str, object] = {
        "feature_id": request.feature_id,
        "machine_model": request.machine_model,
        "sequence": request.current_sequence,
        "response_commit": request.response_commit,
        "response_path": request.response_path,
        "response_sha256": request.response_sha256,
        "response_url": request.response_url,
        "route_alias_key": request.route_alias_key,
        "observation_nonce": _value(observation, "observation_nonce"),
        "observation_version": 1,
        "posture": "READINESS_ONLY_NOT_DELIVERABLE",
        "posting_capability": "ABSENT",
    }
    payload_hash = sha256(_canonical(payload)).hexdigest()
    payload["canonical_payload_sha256"] = payload_hash
    payload["idempotency_key"] = sha256(f"F017:D7:READINESS:{payload_hash}".encode()).hexdigest()
    return PreparedDryRun(payload, _token=_TOKEN)


def _validate_mock_event(
    kind: object, owner_id: object, idempotency_key: object, ordinal: object, receipt_sha256: object
) -> tuple[DeliveryEventKind, str, str, int, str | None]:
    if (
        not isinstance(kind, DeliveryEventKind)
        or not isinstance(owner_id, str)
        or not _SYMBOL.fullmatch(owner_id)
        or not isinstance(idempotency_key, str)
        or not _SHA.fullmatch(idempotency_key)
        or type(ordinal) is not int
        or ordinal < 1
    ):
        raise ReadinessError("mock event identity is invalid")
    if kind is DeliveryEventKind.MOCK_RECEIPT_ACCEPTED:
        if not isinstance(receipt_sha256, str) or not _SHA.fullmatch(receipt_sha256):
            raise ReadinessError("mock receipt identity is invalid")
    elif receipt_sha256 is not None:
        raise ReadinessError("mock receipt identity is invalid")
    return kind, owner_id, idempotency_key, ordinal, receipt_sha256


def make_mock_event(
    *, kind: DeliveryEventKind, owner_id: str, idempotency_key: str, ordinal: int, receipt_sha256: str | None = None
) -> ReadinessEvent:
    kind, owner_id, idempotency_key, ordinal, receipt_sha256 = _validate_mock_event(
        kind, owner_id, idempotency_key, ordinal, receipt_sha256
    )
    return ReadinessEvent(
        {
            "kind": kind,
            "owner_id": owner_id,
            "idempotency_key": idempotency_key,
            "ordinal": ordinal,
            "receipt_sha256": receipt_sha256,
        },
        _token=_TOKEN,
    )


def replay_mock_lifecycle(
    prepared: PreparedDryRun, *, owner_id: str, events: tuple[ReadinessEvent, ...]
) -> DeliverySnapshot:
    _require_sealed(prepared, PreparedDryRun)
    if not isinstance(owner_id, str) or not _SYMBOL.fullmatch(owner_id) or not isinstance(events, tuple):
        raise ReadinessError("mock owner or history is invalid")
    state = DeliveryState.UNPREPARED
    ledger: list[dict[str, object]] = []
    transitions = {
        (DeliveryState.UNPREPARED, DeliveryEventKind.PREPARE): DeliveryState.PREPARED_READINESS_ONLY,
        (
            DeliveryState.PREPARED_READINESS_ONLY,
            DeliveryEventKind.MOCK_ATTEMPT_FAILED,
        ): DeliveryState.MOCK_ATTEMPT_FAILED_RETRYABLE,
        (
            DeliveryState.PREPARED_READINESS_ONLY,
            DeliveryEventKind.MOCK_ATTEMPT_UNCERTAIN,
        ): DeliveryState.MOCK_ATTEMPT_FAILED_RETRYABLE,
        (
            DeliveryState.MOCK_ATTEMPT_FAILED_RETRYABLE,
            DeliveryEventKind.MOCK_RETRY_PREPARED,
        ): DeliveryState.PREPARED_READINESS_ONLY,
        (DeliveryState.PREPARED_READINESS_ONLY, DeliveryEventKind.MOCK_RECEIPT_ACCEPTED): DeliveryState.MOCK_DELIVERED,
        (
            DeliveryState.PREPARED_READINESS_ONLY,
            DeliveryEventKind.MOCK_DUPLICATE_REJECTED,
        ): DeliveryState.TERMINAL_DUPLICATE_REJECTED,
        (
            DeliveryState.MOCK_ATTEMPT_FAILED_RETRYABLE,
            DeliveryEventKind.MOCK_DUPLICATE_REJECTED,
        ): DeliveryState.TERMINAL_DUPLICATE_REJECTED,
    }
    for ordinal, event in enumerate(events, 1):
        _require_sealed(event, ReadinessEvent)
        kind, event_owner, event_key, event_ordinal, receipt = _validate_mock_event(
            _value(event, "kind"),
            _value(event, "owner_id"),
            _value(event, "idempotency_key"),
            _value(event, "ordinal"),
            _value(event, "receipt_sha256"),
        )
        if state in {DeliveryState.MOCK_DELIVERED, DeliveryState.TERMINAL_DUPLICATE_REJECTED}:
            raise ReadinessError("terminal mock state cannot replay")
        if (event_ordinal, event_owner, event_key) != (
            ordinal,
            owner_id,
            prepared.idempotency_key,
        ):
            raise ReadinessError("mock event identity mismatch")
        next_state = transitions.get((state, kind))
        if next_state is None:
            raise ReadinessError("illegal or duplicate mock transition")
        state = next_state
        ledger.append(
            {
                "kind": kind.value,
                "ordinal": ordinal,
                "owner_id": owner_id,
                "idempotency_key": prepared.idempotency_key,
                "receipt_sha256": receipt,
            }
        )
    return DeliverySnapshot({"state": state, "event_log_sha256": sha256(_canonical(ledger)).hexdigest()}, _token=_TOKEN)
