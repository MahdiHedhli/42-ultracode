"""Integration tests for the thin Feature Loop Git artifact adapter."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from ultracode.controller import Controller
from ultracode.feature_loop import (
    FeatureManifest,
    FeatureState,
    FrontierError,
    GitPromptTransport,
    LocalAliasResolver,
    PrivacyPolicy,
    PrivacyScanner,
    PromptEnvelope,
    PublicationCoordinator,
    PublicationError,
    PublicationRequest,
    bind_frontier,
    claim_after_guards,
    guard_frontier,
    notification_record,
)
from ultracode.protocol import RunState

FEATURE = """\
schema: pulsarmlx.feature-loop/1.0.0
feature_id: F017
status: BOOTSTRAP_REQUIRED
state_file: Prompts/F017/STATE.json
machines:
  - model: MacBook Pro M2 Max
    slug: MacBook-Pro-M2-Max
    roles:
      - feature-loop-dogfood
latest_machine_sequence:
  MacBook-Pro-M2-Max: 0
"""

POLICY = """\
schema: pulsarmlx.prompt-privacy-policy/1.0.0
feature_id: F017
allowed:
  - feature-identifiers
prohibited:
  - absolute-home-paths
  - credentials-tokens-cookies-and-secrets
  - actual-notification-topic-names
local_aliases:
  notification_topic: NTFY_TOPIC_ALIAS
  checkpoint_root: CHECKPOINT_ROOT
"""

PARENT = "parent response\n"
PARENT_SHA = sha256(PARENT.encode()).hexdigest()


def prompt_markdown(parent_sha: str = PARENT_SHA) -> str:
    return f"""---
schema: pulsarmlx.graph-prompt/1.0.0
feature_id: F017
sequence: 0
machine_model: MacBook Pro M2 Max
machine_architecture: arm64
phase: dry-round-trip
human_gate: NOT_REQUIRED_CHECKPOINT_FREE_DRY_RUN
prompt_control_base_commit: {"a" * 40}
expected_parent_response_path: Prompts/F017/parent.md
expected_parent_response_sha256: {parent_sha}
response_path: Prompts/F017/MacBook-Pro-M2-Max/000__response.md
response_checksum_path: Prompts/F017/MacBook-Pro-M2-Max/000__response.md.sha256
handoff_path: Prompts/F017/MacBook-Pro-M2-Max/000__handoff.json
source_repository: MahdiHedhli/PulsarMLX
source_mutation: PROHIBITED
original_checkpoint_access: PROHIBITED
full_model_inference: PROHIBITED
automatic_chat_posting: PROHIBITED
---

# Dry prompt
"""


def state_json() -> str:
    return json.dumps(
        {
            "feature_id": "F017",
            "state": "BOOTSTRAP_REQUIRED",
            "current_machine": "MacBook-Pro-M2-Max",
            "current_sequence": 0,
            "latest_response": {"path": "Prompts/F017/parent.md"},
        }
    )


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def disposable_prompt_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "prompts"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    target = repo / "Prompts/F017/MacBook-Pro-M2-Max"
    target.mkdir(parents=True)
    (repo / "Prompts/F017/parent.md").write_text(PARENT)
    (repo / "Prompts/F017/FEATURE.yaml").write_text(FEATURE)
    (repo / "Prompts/F017/STATE.json").write_text(state_json())
    (repo / "Prompts/F017/PRIVACY-POLICY.yaml").write_text(POLICY)
    (target / "000__prompt.md").write_text(prompt_markdown())
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture")
    return repo, git(repo, "rev-parse", "HEAD")


def ready_controller(tmp_path: Path) -> tuple[Controller, str]:
    controller = Controller(tmp_path / "controller.db")
    run = controller.create_run("Dry Feature Loop.", idempotency_key="create")
    controller.submit_instruction(
        run.run_id,
        {
            "instruction_id": "dry",
            "goal": "Inspect repository identity only.",
            "context": ["Checkpoint access is prohibited."],
            "constraints": ["Perform no writes."],
            "done_when": "Identity is reported.",
        },
        idempotency_key="instruction",
    )
    return controller, run.run_id


def validated_frontier(repo: Path, commit: str) -> tuple[GitPromptTransport, PromptEnvelope]:
    transport = GitPromptTransport(repo)
    prompt = transport.read(commit, "Prompts/F017/MacBook-Pro-M2-Max/000__prompt.md")
    envelope = PromptEnvelope.from_markdown(prompt.decode())
    guard_frontier(
        envelope=envelope,
        prompt_commit=commit,
        prompt_sha256=sha256(prompt).hexdigest(),
        manifest=FeatureManifest.from_yaml(FEATURE),
        state=FeatureState.from_json(state_json()),
        transport=transport,
        live_commit=commit,
    )
    return transport, envelope


def publication_request(envelope: PromptEnvelope, commit: str, state: dict[str, object]) -> PublicationRequest:
    return PublicationRequest(
        response_path=envelope.response_path,
        checksum_path=envelope.response_checksum_path,
        handoff_path=envelope.handoff_path,
        state_path="Prompts/F017/STATE.json",
        response_markdown="# Sanitized response\n",
        feature_id="F017",
        sequence=0,
        machine_model="MacBook Pro M2 Max",
        status="PASS",
        prompt_commit=commit,
        prompt_sha256="a" * 64,
        response_commit=commit,
        result_identity="result-1",
        state_document=state,
        expected_state_sha256=sha256(state_json().encode()).hexdigest(),
    )


def commit_response(
    repo: Path,
    coordinator: PublicationCoordinator,
    request: PublicationRequest,
) -> PublicationRequest:
    coordinator.prepare_response(request)
    git(repo, "add", request.response_path, request.checksum_path)
    git(repo, "commit", "-qm", "response")
    return replace(request, response_commit=git(repo, "rev-parse", "HEAD"))


def test_strict_control_documents_and_exact_commit_fetch(tmp_path: Path) -> None:
    repo, commit = disposable_prompt_repo(tmp_path)
    transport, envelope = validated_frontier(repo, commit)
    prompt = transport.verify_sha256(
        commit,
        "Prompts/F017/MacBook-Pro-M2-Max/000__prompt.md",
        sha256(prompt_markdown().encode()).hexdigest(),
    )
    assert envelope.feature_id == "F017"
    assert FeatureManifest.from_yaml(FEATURE).machine_sequences["MacBook-Pro-M2-Max"] == 0
    assert PrivacyPolicy.from_yaml(POLICY).aliases["checkpoint_root"] == "CHECKPOINT_ROOT"
    assert prompt == prompt_markdown().encode()


def test_checksum_or_parent_mismatch_prevents_worker_execution(tmp_path: Path) -> None:
    repo, commit = disposable_prompt_repo(tmp_path)
    transport = GitPromptTransport(repo)
    with pytest.raises(FrontierError, match="SHA-256"):
        transport.verify_sha256(commit, "Prompts/F017/MacBook-Pro-M2-Max/000__prompt.md", "0" * 64)
    controller, run_id = ready_controller(tmp_path)
    before = controller.history(run_id)
    with pytest.raises(FrontierError, match="SHA-256"):
        claim_after_guards(
            controller,
            run_id=run_id,
            worker_id="worker",
            idempotency_key="claim",
            guard=lambda: guard_frontier(
                envelope=PromptEnvelope.from_markdown(prompt_markdown("0" * 64)),
                prompt_commit=commit,
                prompt_sha256="a" * 64,
                manifest=FeatureManifest.from_yaml(FEATURE),
                state=FeatureState.from_json(state_json()),
                transport=transport,
                live_commit=commit,
            ),
        )
    assert controller.history(run_id) == before


@pytest.mark.parametrize("duplicate", ["response", "handoff"])
def test_existing_terminal_artifact_rejects_sequence_before_lease(tmp_path: Path, duplicate: str) -> None:
    repo, _commit = disposable_prompt_repo(tmp_path)
    envelope = PromptEnvelope.from_markdown(prompt_markdown())
    path = envelope.response_path if duplicate == "response" else envelope.handoff_path
    target = repo / path
    target.write_text("existing")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", f"add {duplicate}")
    live = git(repo, "rev-parse", "HEAD")
    transport = GitPromptTransport(repo)
    controller, run_id = ready_controller(tmp_path)
    with pytest.raises(FrontierError, match="already exists"):
        claim_after_guards(
            controller,
            run_id=run_id,
            worker_id="worker",
            idempotency_key="claim",
            guard=lambda: guard_frontier(
                envelope=envelope,
                prompt_commit=live,
                prompt_sha256="a" * 64,
                manifest=FeatureManifest.from_yaml(FEATURE),
                state=FeatureState.from_json(state_json()),
                transport=transport,
                live_commit=live,
            ),
        )
    assert controller.get_run(run_id).state is RunState.READY_FOR_CODEX


def test_lease_is_claimed_only_after_git_guards_and_binding_is_alias_free(tmp_path: Path) -> None:
    repo, commit = disposable_prompt_repo(tmp_path)
    transport, envelope = validated_frontier(repo, commit)
    controller, run_id = ready_controller(tmp_path)
    claim = claim_after_guards(
        controller,
        run_id=run_id,
        worker_id="worker",
        idempotency_key="claim",
        guard=lambda: guard_frontier(
            envelope=envelope,
            prompt_commit=commit,
            prompt_sha256="a" * 64,
            manifest=FeatureManifest.from_yaml(FEATURE),
            state=FeatureState.from_json(state_json()),
            transport=transport,
            live_commit=commit,
        ),
    )
    from ultracode.feature_loop import FrontierBinding

    bind_frontier(
        controller,
        run_id,
        FrontierBinding("F017", "MacBook Pro M2 Max", 0, commit, "a" * 64, envelope.response_path, "result-1"),
    )
    assert claim.worker_id == "worker"
    assert controller.get_run(run_id).state is RunState.CODEX_RUNNING
    assert "CHECKPOINT_ROOT" not in json.dumps(controller.artifacts(run_id))


def test_publication_order_checksum_handoff_state_and_deterministic_retry(tmp_path: Path) -> None:
    repo, commit = disposable_prompt_repo(tmp_path)
    _, envelope = validated_frontier(repo, commit)
    scanner = PrivacyScanner(PrivacyPolicy.from_yaml(POLICY))
    coordinator = PublicationCoordinator(repo, scanner)
    original_state = json.loads(state_json())
    request = commit_response(repo, coordinator, publication_request(envelope, commit, original_state))
    order: list[str] = []
    result = coordinator.prepare(request, step_observer=order.append)
    retry = coordinator.prepare(request)
    assert order == ["response", "checksum", "handoff", "state"]
    assert result.response_sha256 == retry.response_sha256
    sidecar = (repo / envelope.response_checksum_path).read_text().split()
    assert sidecar == [result.response_sha256, Path(envelope.response_path).name]
    handoff = json.loads((repo / envelope.handoff_path).read_text())
    assert set(handoff) == {"response_url", "response_sha256", "feature_id", "sequence", "machine_model", "status"}
    assert json.loads((repo / "Prompts/F017/STATE.json").read_text())["state"] == "CHAT_HANDOFF_PENDING"


def test_partial_publication_resume_and_conflicting_object_fail_closed(tmp_path: Path) -> None:
    repo, commit = disposable_prompt_repo(tmp_path)
    _, envelope = validated_frontier(repo, commit)
    coordinator = PublicationCoordinator(repo, PrivacyScanner(PrivacyPolicy.from_yaml(POLICY)))
    request = commit_response(
        repo,
        coordinator,
        publication_request(envelope, commit, json.loads(state_json())),
    )

    def fail_after_checksum(step: str) -> None:
        if step == "checksum":
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        coordinator.prepare(request, step_observer=fail_after_checksum)
    resumed = coordinator.prepare(request)
    assert resumed.status == "PREPARED"
    (repo / envelope.response_path).write_text("conflict")
    with pytest.raises(PublicationError, match="conflicting"):
        coordinator.prepare(request)


def test_push_and_notification_failure_do_not_repeat_result_or_leak_alias(tmp_path: Path) -> None:
    repo, commit = disposable_prompt_repo(tmp_path)
    _, envelope = validated_frontier(repo, commit)
    coordinator = PublicationCoordinator(repo, PrivacyScanner(PrivacyPolicy.from_yaml(POLICY)))
    request = commit_response(
        repo,
        coordinator,
        publication_request(envelope, commit, json.loads(state_json())),
    )
    push_calls = 0

    def fail_push() -> None:
        nonlocal push_calls
        push_calls += 1
        raise OSError("offline")

    result = coordinator.publish(request, fail_push)
    resolver = LocalAliasResolver({"NTFY_TOPIC_ALIAS": "private-topic"})
    notice = notification_record(
        alias="NTFY_TOPIC_ALIAS",
        resolver=resolver,
        transport=lambda _topic, _message: (_ for _ in ()).throw(OSError("offline")),
        feature_id="F017",
        sequence=0,
        artifact_identity=result.response_sha256,
    )
    assert result.status == "PUSH_PENDING"
    assert push_calls == 1
    assert notice["status"] == "FAIL"
    assert "private-topic" not in canonical_json_for_test(notice)


def canonical_json_for_test(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def test_source_first_no_mutation_dry_round_trip_and_prepublication_race(tmp_path: Path) -> None:
    repo, commit = disposable_prompt_repo(tmp_path)
    transport, envelope = validated_frontier(repo, commit)
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.name", "Fixture")
    git(source, "config", "user.email", "fixture@example.invalid")
    (source / "README.md").write_text("source\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "source")
    before = (git(source, "rev-parse", "HEAD"), git(source, "status", "--porcelain"))
    controller, run_id = ready_controller(tmp_path)
    claim_after_guards(
        controller,
        run_id=run_id,
        worker_id="worker",
        idempotency_key="claim",
        guard=lambda: guard_frontier(
            envelope=envelope,
            prompt_commit=commit,
            prompt_sha256="a" * 64,
            manifest=FeatureManifest.from_yaml(FEATURE),
            state=FeatureState.from_json(state_json()),
            transport=transport,
            live_commit=commit,
        ),
    )
    after = (git(source, "rev-parse", "HEAD"), git(source, "status", "--porcelain"))
    assert after == before
    (repo / envelope.response_path).write_text("racing actor")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "race")
    live = git(repo, "rev-parse", "HEAD")
    with pytest.raises(FrontierError, match="already exists"):
        guard_frontier(
            envelope=envelope,
            prompt_commit=commit,
            prompt_sha256="a" * 64,
            manifest=FeatureManifest.from_yaml(FEATURE),
            state=FeatureState.from_json(state_json()),
            transport=transport,
            live_commit=live,
        )
