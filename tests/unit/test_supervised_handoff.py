from __future__ import annotations

import copy
import json
import pickle
from hashlib import sha256

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
