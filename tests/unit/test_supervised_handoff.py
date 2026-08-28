from __future__ import annotations

import copy
import gc
import json
import pickle
from hashlib import sha256
from types import MappingProxyType

import pytest

from ultracode.supervised_handoff import (
    DeliveryEventKind,
    DeliveryState,
    ReadinessError,
    SealedReadinessRequest,
    make_mock_event,
    parse_handoff_authority,
    parse_sanitized_observation,
    prepare_dry_run,
    replay_mock_lifecycle,
    seal_readiness_request,
)

RESPONSE = b"synthetic response\n"
COMMIT = "a" * 40
HASH = sha256(RESPONSE).hexdigest()
PATH = "Prompts/F017/MacBook-Pro-M2-Max/017__F017__MacBook-Pro-M2-Max__Feature-Loop-D7-readiness__response.md"
URL = f"https://github.com/MahdiHedhli/PulsarMLX-Prompts/blob/{COMMIT}/{PATH}"
ROUTE = "F017_TEST_ROUTE"
OWNER = "F017_TEST_OWNER"


def canonical(v: object) -> bytes:
    return json.dumps(v, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


def req():
    return seal_readiness_request(
        feature_id="F017",
        machine_model="MacBook Pro M2 Max",
        prior_sequence=17,
        current_sequence=18,
        expected_response_commit=COMMIT,
        expected_response_path=PATH,
        expected_response_sha256=HASH,
        verified_response_bytes=RESPONSE,
        route_alias_key=ROUTE,
    )  # type: ignore[no-untyped-def]


def h() -> dict[str, object]:
    return {
        "feature_id": "F017",
        "machine_model": "MacBook Pro M2 Max",
        "response_sha256": HASH,
        "response_url": URL,
        "sequence": 17,
        "status": "PASS",
    }


def o() -> dict[str, object]:
    return {
        "application_symbol": "MOCK_CHAT_APP",
        "feature_marker": "F017",
        "host_symbol": "MOCK_LOCAL_HOST",
        "observation_nonce": "0" * 32,
        "observation_version": 1,
        "origin": "MOCK_LOCAL_ONLY",
        "prior_sequence": 17,
        "response_commit": COMMIT,
        "response_path": PATH,
        "response_sha256": HASH,
        "route_alias_key": ROUTE,
    }


def dry():  # type: ignore[no-untyped-def]
    r = req()
    return prepare_dry_run(
        r, parse_handoff_authority(canonical(h()), r), parse_sanitized_observation(canonical(o()), r)
    )


def test_twenty_deterministic_reconstructions_and_mock_receipt():
    seen = set()
    for _ in range(20):
        d = dry()
        events = (
            make_mock_event(
                kind=DeliveryEventKind.PREPARE, owner_id=OWNER, idempotency_key=d.idempotency_key, ordinal=1
            ),
            make_mock_event(
                kind=DeliveryEventKind.MOCK_RECEIPT_ACCEPTED,
                owner_id=OWNER,
                idempotency_key=d.idempotency_key,
                ordinal=2,
                receipt_sha256="b" * 64,
            ),
        )
        s = replay_mock_lifecycle(d, owner_id=OWNER, events=events)
        seen.add((d.canonical_payload_sha256, d.idempotency_key, s.event_log_sha256))
        assert s.state is DeliveryState.MOCK_DELIVERED
    assert len(seen) == 1
    assert seen == {
        (
            "ac0ea67d5ec7cd887597e008b3ba0a720fc34e1931182444d684a7c7c166af23",
            "2c668f41c86a0896fbf1e6664a41d0942b8d12d76f6b54103f8f8c7f92c340c4",
            "b0426f5e67dd65de3cdccb3850bc7c3aa27536866f6dfa24e5d073ca42615abf",
        )
    }


@pytest.mark.parametrize("field", sorted(h()))
def test_handoff_field_delete_and_coerce(field: str):
    a = h()
    del a[field]
    with pytest.raises(ReadinessError):
        parse_handoff_authority(canonical(a), req())
    a = h()
    a[field] = "17" if field == "sequence" else 1
    with pytest.raises(ReadinessError):
        parse_handoff_authority(canonical(a), req())


@pytest.mark.parametrize(
    "url",
    [
        URL.replace("https", "http"),
        URL.replace("github.com", "evil.example"),
        URL.replace("github.com", "GitHub.com"),
        URL.replace("github.com", "github.com."),
        URL.replace("github.com", "github.com:443"),
        URL.replace("github.com", "u@github.com"),
        URL.replace("MahdiHedhli", "Other"),
        URL.replace("PulsarMLX-Prompts", "Other"),
        URL.replace("/blob/", "/raw/"),
        URL.replace(COMMIT, "a" * 39),
        URL.replace(COMMIT, "A" * 40),
        URL.replace(COMMIT, "main"),
        URL.replace("Prompts/F017", "Prompts/F018"),
        URL.replace("__response.md", "__prompt.md"),
        URL.replace("017__", "016__"),
        URL.replace("/Prompts/", "/%50rompts/"),
        URL.replace("/Prompts/", "//Prompts/"),
        URL.replace("/Prompts/", "/x/../Prompts/"),
        URL + "?x",
        URL + "#x",
        "https://example.invalid/?to=" + URL,
        URL.replace("github.com", "gith\u0443b.com"),
    ],
)
def test_url_mutations_fail(url: str):
    a = h()
    a["response_url"] = url
    with pytest.raises(ReadinessError):
        parse_handoff_authority(canonical(a), req())


def test_duplicate_unknown_noncanonical_fail():
    with pytest.raises(ReadinessError):
        parse_handoff_authority(canonical(h() | {"alias": "x"}), req())
    duplicate = canonical(h()).replace(b'{"feature_id":"F017"', b'{"feature_id":"F017","feature_id":"F017"')
    with pytest.raises(ReadinessError):
        parse_handoff_authority(duplicate, req())
    with pytest.raises(ReadinessError):
        parse_handoff_authority(json.dumps(h()).encode(), req())


@pytest.mark.parametrize("field", sorted(o()))
def test_observation_field_mutations_fail(field: str):
    a = o()
    del a[field]
    with pytest.raises(ReadinessError):
        parse_sanitized_observation(canonical(a), req())
    a = o()
    a[field] = 2 if field in {"prior_sequence", "observation_version"} else "CHANGED"
    with pytest.raises(ReadinessError):
        parse_sanitized_observation(canonical(a), req())


def test_prepare_rejects_observation_bound_to_another_request():
    first = req()
    second = seal_readiness_request(
        feature_id="F017",
        machine_model="MacBook Pro M2 Max",
        prior_sequence=17,
        current_sequence=18,
        expected_response_commit="b" * 40,
        expected_response_path=PATH,
        expected_response_sha256=HASH,
        verified_response_bytes=RESPONSE,
        route_alias_key=ROUTE,
    )
    second_handoff = h() | {"response_url": URL.replace(COMMIT, "b" * 40)}
    observation = parse_sanitized_observation(canonical(o()), first)
    authority = parse_handoff_authority(canonical(second_handoff), second)
    with pytest.raises(ReadinessError, match="disagree"):
        prepare_dry_run(second, authority, observation)


@pytest.mark.parametrize("receipt", ["ATTACKER_CONTROLLED", 12345])
def test_nonreceipt_events_reject_any_receipt_value(receipt: object):
    with pytest.raises(ReadinessError, match="receipt identity"):
        make_mock_event(
            kind=DeliveryEventKind.PREPARE,
            owner_id=OWNER,
            idempotency_key=dry().idempotency_key,
            ordinal=1,
            receipt_sha256=receipt,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_response_commit", None),
        ("expected_response_path", None),
        ("expected_response_sha256", None),
        ("route_alias_key", None),
    ],
)
def test_seal_rejects_nonstring_regex_inputs(field: str, value: object):
    arguments: dict[str, object] = {
        "feature_id": "F017",
        "machine_model": "MacBook Pro M2 Max",
        "prior_sequence": 17,
        "current_sequence": 18,
        "expected_response_commit": COMMIT,
        "expected_response_path": PATH,
        "expected_response_sha256": HASH,
        "verified_response_bytes": RESPONSE,
        "route_alias_key": ROUTE,
    }
    arguments[field] = value
    with pytest.raises(ReadinessError):
        seal_readiness_request(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("feature_id", object()),
        ("machine_model", b"MacBook Pro M2 Max"),
        ("prior_sequence", True),
        ("current_sequence", 18.0),
    ],
)
def test_frontier_identity_requires_exact_types(field: str, value: object):
    arguments: dict[str, object] = {
        "feature_id": "F017",
        "machine_model": "MacBook Pro M2 Max",
        "prior_sequence": 17,
        "current_sequence": 18,
        "expected_response_commit": COMMIT,
        "expected_response_path": PATH,
        "expected_response_sha256": HASH,
        "verified_response_bytes": RESPONSE,
        "route_alias_key": ROUTE,
    }
    arguments[field] = value
    with pytest.raises(ReadinessError):
        seal_readiness_request(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("owner_id", "idempotency_key"),
    [(None, "a" * 64), (OWNER, b"a" * 64)],
)
def test_mock_event_rejects_nonstring_regex_inputs(owner_id: object, idempotency_key: object):
    with pytest.raises(ReadinessError):
        make_mock_event(
            kind=DeliveryEventKind.PREPARE,
            owner_id=owner_id,  # type: ignore[arg-type]
            idempotency_key=idempotency_key,  # type: ignore[arg-type]
            ordinal=1,
        )


def test_replay_rejects_nonstring_owner():
    with pytest.raises(ReadinessError):
        replay_mock_lifecycle(dry(), owner_id=None, events=())  # type: ignore[arg-type]


def test_uninitialized_and_mapping_proxy_forged_requests_fail_closed():
    uninitialized = SealedReadinessRequest.__new__(SealedReadinessRequest)
    with pytest.raises(ReadinessError, match="not sealed"):
        parse_handoff_authority(canonical(h()), uninitialized)

    forged = SealedReadinessRequest.__new__(SealedReadinessRequest)
    object.__setattr__(forged, "_values", MappingProxyType({}))
    with pytest.raises(ReadinessError, match="not sealed"):
        parse_handoff_authority(canonical(h()), forged)

    legitimate = req()
    copied = SealedReadinessRequest.__new__(SealedReadinessRequest)
    object.__setattr__(copied, "_values", object.__getattribute__(legitimate, "_values"))
    with pytest.raises(ReadinessError, match="not sealed"):
        parse_handoff_authority(canonical(h()), copied)

    replaced = req()
    object.__setattr__(replaced, "_values", MappingProxyType(dict(object.__getattribute__(replaced, "_values"))))
    with pytest.raises(ReadinessError, match="not sealed"):
        parse_handoff_authority(canonical(h()), replaced)

    assert not hasattr(legitimate, "_proof")


def test_mapping_proxy_backing_dict_mutation_invalidates_seal():
    request = req()
    proxy = object.__getattribute__(request, "_values")
    backing = next(item for item in gc.get_referents(proxy) if isinstance(item, dict))
    backing["response_url"] = "https://attacker.example/payload"
    with pytest.raises(ReadinessError, match="not sealed"):
        parse_handoff_authority(canonical(h()), request)

    prepared = dry()
    event = make_mock_event(
        kind=DeliveryEventKind.PREPARE,
        owner_id=OWNER,
        idempotency_key=prepared.idempotency_key,
        ordinal=1,
    )
    event_proxy = object.__getattribute__(event, "_values")
    event_backing = next(item for item in gc.get_referents(event_proxy) if isinstance(item, dict))
    event_backing["kind"] = DeliveryEventKind.MOCK_RECEIPT_ACCEPTED
    event_backing["receipt_sha256"] = "b" * 64
    with pytest.raises(ReadinessError, match="not sealed"):
        replay_mock_lifecycle(prepared, owner_id=OWNER, events=(event,))


def test_metaclass_subclass_cannot_bypass_subclass_guard():
    sealed_meta = type(SealedReadinessRequest)

    class BypassMeta(sealed_meta):
        def __new__(mcls, name, bases, namespace, **kwargs):  # type: ignore[no-untyped-def]
            return type.__new__(mcls, name, bases, namespace, **kwargs)

    with pytest.raises(ReadinessError, match="cannot be subclassed"):
        BypassMeta("Evil", (SealedReadinessRequest,), {"__slots__": ()})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", []),
        ("owner_id", None),
        ("idempotency_key", b"a" * 64),
        ("ordinal", True),
        ("receipt_sha256", None),
        ("receipt_sha256", {}),
    ],
)
def test_replay_revalidates_forged_event_fields(field: str, value: object):
    prepared = dry()
    event = make_mock_event(
        kind=DeliveryEventKind.MOCK_RECEIPT_ACCEPTED,
        owner_id=OWNER,
        idempotency_key=prepared.idempotency_key,
        ordinal=1,
        receipt_sha256="b" * 64,
    )
    values = dict(object.__getattribute__(event, "_values"))
    values[field] = value
    object.__setattr__(event, "_values", MappingProxyType(values))
    with pytest.raises(ReadinessError):
        replay_mock_lifecycle(prepared, owner_id=OWNER, events=(event,))


@pytest.mark.parametrize(
    "field",
    ["html", "dom", "page_text", "conversation", "url", "cookie", "token", "clipboard", "accessibility", "screenshot"],
)
def test_private_fields_rejected_without_value_exposure(field: str):
    private = "PRIVATE_MUST_NOT_APPEAR"
    with pytest.raises(ReadinessError) as caught:
        parse_sanitized_observation(canonical(o() | {field: private}), req())
    assert private not in str(caught.value)


def test_sealing_retry_restart_terminal_and_zero_spies():
    d = dry()
    for value in (req(), d):
        with pytest.raises((ReadinessError, TypeError)):
            copy.copy(value)
        with pytest.raises((ReadinessError, TypeError)):
            copy.deepcopy(value)
        with pytest.raises((ReadinessError, TypeError)):
            pickle.dumps(value)
        with pytest.raises(ReadinessError):
            type("Widened", (type(value),), {})
        with pytest.raises(ReadinessError):
            type("Widened", (type(value),), {"__module__": "ultracode.supervised_handoff"})
        with pytest.raises(ReadinessError):
            type("Widened", (type(value),), {"__name__": type(value).__name__})
    with pytest.raises(ReadinessError):
        SealedReadinessRequest({}, _token=object())

    def event(kind, ordinal, receipt=None):
        return make_mock_event(
            kind=kind, owner_id=OWNER, idempotency_key=d.idempotency_key, ordinal=ordinal, receipt_sha256=receipt
        )  # type: ignore[no-untyped-def]

    history = (event(DeliveryEventKind.PREPARE, 1), event(DeliveryEventKind.MOCK_ATTEMPT_UNCERTAIN, 2))
    assert (
        replay_mock_lifecycle(d, owner_id=OWNER, events=history).event_log_sha256
        == replay_mock_lifecycle(d, owner_id=OWNER, events=history).event_log_sha256
    )
    complete = (
        *history,
        event(DeliveryEventKind.MOCK_RETRY_PREPARED, 3),
        event(DeliveryEventKind.MOCK_RECEIPT_ACCEPTED, 4, "b" * 64),
    )
    with pytest.raises(ReadinessError, match="terminal"):
        replay_mock_lifecycle(
            d, owner_id=OWNER, events=(*complete, event(DeliveryEventKind.MOCK_DUPLICATE_REJECTED, 5))
        )
    with pytest.raises(ReadinessError):
        replay_mock_lifecycle(
            d, owner_id=OWNER, events=(event(DeliveryEventKind.PREPARE, 1), event(DeliveryEventKind.PREPARE, 2))
        )
    calls = []

    def spy() -> None:
        calls.append(1)

    assert spy and calls == []
