"""Integration tests for the fail-closed Feature Loop Git adapter."""

from __future__ import annotations

import copy
import json
import pickle
import subprocess
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from ultracode.controller import Controller
from ultracode.feature_loop import (
    FeatureManifest,
    FeatureState,
    FrontierBinding,
    FrontierError,
    GitPromptTransport,
    LocalAliasResolver,
    PrivacyPolicy,
    PrivacyScanner,
    PublicationCoordinator,
    PublicationError,
    PublicationRequest,
    ReviewedPromptPolicy,
    VerifiedPromptIdentity,
    bind_frontier,
    claim_after_guards,
    guard_frontier,
    notification_record,
)
from ultracode.protocol import RunState

PROMPT_PATH = "Prompts/F017/MacBook-Pro-M2-Max/001__prompt.md"
PROMPT_SIDECAR = f"{PROMPT_PATH}.sha256"
PARENT_PATH = "Prompts/F017/MacBook-Pro-M2-Max/000__response.md"
RESPONSE_PATH = "Prompts/F017/MacBook-Pro-M2-Max/001__response.md"
CHECKSUM_PATH = f"{RESPONSE_PATH}.sha256"
HANDOFF_PATH = "Prompts/F017/MacBook-Pro-M2-Max/001__handoff.json"
FEATURE_PATH = "Prompts/F017/FEATURE.yaml"
STATE_PATH = "Prompts/F017/STATE.json"
PARENT = "parent response\n"
PARENT_SHA = sha256(PARENT.encode()).hexdigest()

POLICY_ID = ReviewedPromptPolicy.F017_M2_D2_POLICY_TRUST_ANCHOR_REPAIR
D4_POLICY_ID = ReviewedPromptPolicy.F017_M2_D4_CHECKPOINT_FREE_REPACK_INVESTIGATION
D5_POLICY_ID = ReviewedPromptPolicy.F017_M2_D5_BOUNDED_CHECKPOINT_FREE_REPACK_WRITE
D5R1_POLICY_ID = ReviewedPromptPolicy.F017_M2_D5R1_BOUNDED_CHECKPOINT_FREE_REPACK_REPAIR
D6_POLICY_ID = ReviewedPromptPolicy.F017_M2_D6_CHECKPOINT_FREE_SYNTHETIC_REPACK_ROUND_TRIP
AUTHORIZATION = {
    "schema": "pulsarmlx.graph-prompt/1.0.0",
    "feature_id": "F017",
    "machine_model": "MacBook Pro M2 Max",
    "machine_architecture": "arm64",
    "phase": "Feature-Loop-D2-policy-trust-anchor-repair",
    "human_gate": "NOT_REQUIRED_CHECKPOINT_FREE_REPAIR",
    "source_repository": "MahdiHedhli/PulsarMLX",
    "source_mutation": "PROHIBITED",
    "original_checkpoint_access": "PROHIBITED",
    "full_model_inference": "PROHIBITED",
    "automatic_chat_posting": "PROHIBITED",
}
D4_AUTHORIZATION = {
    **AUTHORIZATION,
    "phase": "Feature-Loop-D4-checkpoint-free-repack-investigation",
    "human_gate": "NOT_REQUIRED_CHECKPOINT_FREE_READ_ONLY",
}
D5_AUTHORIZATION = {
    **AUTHORIZATION,
    "phase": "Feature-Loop-D5-bounded-checkpoint-free-repack-write",
    "human_gate": "PLANNER_ACCEPTED_D4_CHECKPOINT_FREE_WRITE",
    "source_mutation": "BOUNDED_CHECKPOINT_FREE_REPACK_BRANCH_ONLY",
}
D5R1_AUTHORIZATION = {
    **AUTHORIZATION,
    "phase": "Feature-Loop-D5R1-bounded-checkpoint-free-repack-repair",
    "human_gate": "PLANNER_ACCEPTED_D5_SCOPE_EXPANSION_REPAIR",
    "source_mutation": "BOUNDED_CHECKPOINT_FREE_REPACK_DUPLICATE_ROLE_REPAIR_BRANCH_ONLY",
}
D6_AUTHORIZATION = {
    **AUTHORIZATION,
    "phase": "Feature-Loop-D6-checkpoint-free-synthetic-repack-round-trip",
    "human_gate": "PLANNER_ACCEPTED_D5_CHECKPOINT_FREE_PLAN_QUALIFICATION",
    "source_mutation": "BOUNDED_CHECKPOINT_FREE_SYNTHETIC_REPACK_TESTS_BRANCH_ONLY",
}

FEATURE = """\
schema: pulsarmlx.feature-loop/1.0.0
feature_id: F017
status: PROMPT_AVAILABLE
state_file: Prompts/F017/STATE.json
latest_machine_sequence:
  MacBook-Pro-M2-Max: 1
"""

POLICY = """\
schema: pulsarmlx.prompt-privacy-policy/1.0.0
feature_id: F017
allowed:
  - feature-identifiers
prohibited:
  - absolute-home-paths
  - credentials-tokens-cookies-and-secrets
local_aliases:
  notification_topic: NTFY_TOPIC_ALIAS
  checkpoint_root: CHECKPOINT_ROOT
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def prompt_markdown(
    base_commit: str,
    parent_sha: str = PARENT_SHA,
    authorization: dict[str, str] = AUTHORIZATION,
) -> str:
    return f"""---
schema: {authorization["schema"]}
feature_id: {authorization["feature_id"]}
sequence: 1
machine_model: {authorization["machine_model"]}
machine_architecture: {authorization["machine_architecture"]}
phase: {authorization["phase"]}
human_gate: {authorization["human_gate"]}
prompt_control_base_commit: {base_commit}
expected_parent_response_path: {PARENT_PATH}
expected_parent_response_sha256: {parent_sha}
response_path: {RESPONSE_PATH}
response_checksum_path: {CHECKSUM_PATH}
handoff_path: {HANDOFF_PATH}
source_repository: {authorization["source_repository"]}
source_mutation: {authorization["source_mutation"]}
original_checkpoint_access: {authorization["original_checkpoint_access"]}
full_model_inference: {authorization["full_model_inference"]}
automatic_chat_posting: {authorization["automatic_chat_posting"]}
---

# Synthetic repair prompt
"""


def state_document(prompt_commit: str, parent_commit: str) -> dict[str, Any]:
    return {
        "schema": "pulsarmlx.feature-loop-state/1.0.0",
        "feature_id": "F017",
        "state": "PROMPT_AVAILABLE",
        "current_machine": "MacBook-Pro-M2-Max",
        "current_sequence": 1,
        "dogfood_stage": "DRY_RUN",
        "latest_prompt": {
            "path": PROMPT_PATH,
            "sha256": "placeholder",
            "commit": prompt_commit,
        },
        "latest_response": {
            "path": PARENT_PATH,
            "sha256": PARENT_SHA,
            "status": "PASS",
            "machine_model": "MacBook Pro M2 Max",
            "sequence": 0,
            "commit": parent_commit,
        },
    }


@dataclass(frozen=True)
class PromptFixture:
    repo: Path
    base_commit: str
    prompt_commit: str
    live_commit: str
    prompt_sha: str
    transport: GitPromptTransport

    @property
    def identity(self) -> VerifiedPromptIdentity:
        return self.transport.verify_prompt_identity(
            prompt_commit=self.prompt_commit,
            prompt_path=PROMPT_PATH,
            expected_sha256=self.prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=POLICY_ID,
        )


def disposable_prompt_repo(tmp_path: Path, authorization: dict[str, str] = AUTHORIZATION) -> PromptFixture:
    repo = tmp_path / "prompts"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    target = repo / "Prompts/F017/MacBook-Pro-M2-Max"
    target.mkdir(parents=True)
    (target / "000__response.md").write_text(PARENT)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "parent")
    base = git(repo, "rev-parse", "HEAD")

    prompt = prompt_markdown(base, authorization=authorization)
    prompt_sha = sha256(prompt.encode()).hexdigest()
    (repo / PROMPT_PATH).write_text(prompt)
    (repo / PROMPT_SIDECAR).write_text(f"{prompt_sha}  {Path(PROMPT_PATH).name}\n")
    (repo / FEATURE_PATH).write_text(FEATURE)
    initial = state_document(base, base)
    initial["latest_prompt"] = {"path": PROMPT_PATH, "sha256": prompt_sha, "commit": base}
    (repo / STATE_PATH).write_text(json.dumps(initial, indent=2, sort_keys=True) + "\n")
    (repo / "Prompts/F017/PRIVACY-POLICY.yaml").write_text(POLICY)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "prompt")
    prompt_commit = git(repo, "rev-parse", "HEAD")

    live = state_document(prompt_commit, base)
    live["latest_prompt"] = {"path": PROMPT_PATH, "sha256": prompt_sha, "commit": prompt_commit}
    (repo / STATE_PATH).write_text(json.dumps(live, indent=2, sort_keys=True) + "\n")
    git(repo, "add", STATE_PATH)
    git(repo, "commit", "-qm", "frontier")
    return PromptFixture(
        repo,
        base,
        prompt_commit,
        git(repo, "rev-parse", "HEAD"),
        prompt_sha,
        GitPromptTransport(repo),
    )


def ready_controller(tmp_path: Path) -> tuple[Controller, str]:
    controller = Controller(tmp_path / "controller.db")
    run = controller.create_run("Dry Feature Loop.", idempotency_key="create")
    controller.submit_instruction(
        run.run_id,
        {
            "instruction_id": "dry",
            "goal": "Validate synthetic Git identity only.",
            "context": ["Checkpoint access is prohibited."],
            "constraints": ["Perform no source writes."],
            "done_when": "The guarded claim is recorded.",
        },
        idempotency_key="instruction",
    )
    return controller, run.run_id


def scanner() -> PrivacyScanner:
    return PrivacyScanner(PrivacyPolicy.from_yaml(POLICY))


def publication_request(fixture: PromptFixture) -> PublicationRequest:
    state_text = (fixture.repo / STATE_PATH).read_text()
    feature_text = (fixture.repo / FEATURE_PATH).read_text()
    return PublicationRequest(
        response_path=RESPONSE_PATH,
        checksum_path=CHECKSUM_PATH,
        handoff_path=HANDOFF_PATH,
        state_path=STATE_PATH,
        response_markdown="# Sanitized response\n",
        feature_id="F017",
        sequence=1,
        machine_model="MacBook Pro M2 Max",
        status="PASS",
        prompt_path=PROMPT_PATH,
        prompt_commit=fixture.prompt_commit,
        prompt_sha256=fixture.prompt_sha,
        response_commit=fixture.prompt_commit,
        result_identity="result-1",
        state_document=json.loads(state_text),
        expected_state_sha256=sha256(state_text.encode()).hexdigest(),
        feature_path=FEATURE_PATH,
        feature_document=feature_text,
        expected_feature_sha256=sha256(feature_text.encode()).hexdigest(),
    )


def commit_response(fixture: PromptFixture, coordinator: PublicationCoordinator) -> PublicationRequest:
    request = publication_request(fixture)
    coordinator.prepare_response(request)
    git(fixture.repo, "add", request.response_path, request.checksum_path)
    git(fixture.repo, "commit", "-qm", "response")
    return replace(request, response_commit=git(fixture.repo, "rev-parse", "HEAD"))


def test_exact_prompt_sidecar_parent_and_guarded_lease(tmp_path: Path) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    identity = fixture.identity
    guard_frontier(identity=identity, transport=fixture.transport, live_commit=fixture.live_commit)
    controller, run_id = ready_controller(tmp_path)
    claim = claim_after_guards(
        controller,
        run_id=run_id,
        worker_id="worker",
        idempotency_key="claim",
        identity=identity,
        transport=fixture.transport,
        live_commit=fixture.live_commit,
    )
    bind_frontier(
        controller,
        run_id,
        FrontierBinding.from_identity(identity, result_identity="result-1"),
    )
    assert claim.worker_id == "worker"
    assert controller.get_run(run_id).state is RunState.CODEX_RUNNING


def test_wrong_prompt_hash_produces_zero_lease_claims(tmp_path: Path) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    controller, run_id = ready_controller(tmp_path)
    before = controller.history(run_id)
    with pytest.raises(FrontierError, match="SHA-256"):
        fixture.transport.verify_prompt_identity(
            prompt_commit=fixture.prompt_commit,
            prompt_path=PROMPT_PATH,
            expected_sha256="0" * 64,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=POLICY_ID,
        )
    assert controller.history(run_id) == before
    assert controller.get_run(run_id).state is RunState.READY_FOR_CODEX


def test_correct_bytes_at_wrong_path_or_commit_fail(tmp_path: Path) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    wrong = fixture.repo / "Prompts/F017/wrong.md"
    wrong.write_bytes((fixture.repo / PROMPT_PATH).read_bytes())
    (fixture.repo / "Prompts/F017/wrong.md.sha256").write_text(f"{fixture.prompt_sha}  wrong.md\n")
    git(fixture.repo, "add", ".")
    git(fixture.repo, "commit", "-qm", "wrong identity")
    latest = git(fixture.repo, "rev-parse", "HEAD")
    with pytest.raises(FrontierError, match="live state prompt identity"):
        identity = fixture.transport.verify_prompt_identity(
            prompt_commit=latest,
            prompt_path="Prompts/F017/wrong.md",
            expected_sha256=fixture.prompt_sha,
            sidecar_path="Prompts/F017/wrong.md.sha256",
            policy_id=POLICY_ID,
        )
        guard_frontier(identity=identity, transport=fixture.transport, live_commit=latest)
    with pytest.raises(FrontierError, match="absent"):
        fixture.transport.verify_prompt_identity(
            prompt_commit=fixture.base_commit,
            prompt_path=PROMPT_PATH,
            expected_sha256=fixture.prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=POLICY_ID,
        )


@pytest.mark.parametrize("sidecar", ["", "bad", "0" * 64, "{sha}  renamed.md\n", "{sha}  x\nextra\n"])
def test_malformed_missing_renamed_or_conflicting_sidecar_fails(tmp_path: Path, sidecar: str) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    (fixture.repo / PROMPT_SIDECAR).write_text(sidecar.format(sha=fixture.prompt_sha))
    git(fixture.repo, "add", PROMPT_SIDECAR)
    git(fixture.repo, "commit", "-qm", "bad sidecar")
    bad_commit = git(fixture.repo, "rev-parse", "HEAD")
    with pytest.raises(FrontierError, match="sidecar"):
        fixture.transport.verify_prompt_identity(
            prompt_commit=bad_commit,
            prompt_path=PROMPT_PATH,
            expected_sha256=fixture.prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=POLICY_ID,
        )


AUTHORIZATION_MUTATIONS = [
    ("schema", "pulsarmlx.graph-prompt/9.9.9"),
    ("feature_id", "F999"),
    ("machine_model", "Other Machine"),
    ("machine_architecture", "x86_64"),
    ("phase", "other-phase"),
    ("human_gate", "BYPASSED"),
    ("source_repository", "attacker/repository"),
    ("source_mutation", "ALLOWED"),
    ("original_checkpoint_access", "ALLOWED"),
    ("full_model_inference", "ALLOWED"),
    ("automatic_chat_posting", "ALLOWED"),
]


@pytest.mark.parametrize(("field", "replacement"), AUTHORIZATION_MUTATIONS)
def test_every_prompt_authorization_field_is_bound_before_lease(tmp_path: Path, field: str, replacement: str) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    prompt = (fixture.repo / PROMPT_PATH).read_text()
    prompt = prompt.replace(f"{field}: {AUTHORIZATION[field]}", f"{field}: {replacement}", 1)
    prompt_sha = sha256(prompt.encode()).hexdigest()
    (fixture.repo / PROMPT_PATH).write_text(prompt)
    (fixture.repo / PROMPT_SIDECAR).write_text(f"{prompt_sha}  {Path(PROMPT_PATH).name}\n")
    git(fixture.repo, "add", PROMPT_PATH, PROMPT_SIDECAR)
    git(fixture.repo, "commit", "-qm", f"mutate {field}")
    prompt_commit = git(fixture.repo, "rev-parse", "HEAD")
    state = json.loads((fixture.repo / STATE_PATH).read_text())
    state["latest_prompt"] = {"path": PROMPT_PATH, "sha256": prompt_sha, "commit": prompt_commit}
    (fixture.repo / STATE_PATH).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    git(fixture.repo, "add", STATE_PATH)
    git(fixture.repo, "commit", "-qm", "publish malicious frontier")
    controller, run_id = ready_controller(tmp_path)
    history = controller.history(run_id)
    repository_head = git(fixture.repo, "rev-parse", "HEAD")
    repository_status = git(fixture.repo, "status", "--porcelain=v1")

    with pytest.raises(FrontierError, match=f"authorization mismatch: {field}"):
        fixture.transport.verify_prompt_identity(
            prompt_commit=prompt_commit,
            prompt_path=PROMPT_PATH,
            expected_sha256=prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=POLICY_ID,
        )

    assert controller.history(run_id) == history
    assert controller.get_run(run_id).state is RunState.READY_FOR_CODEX
    assert git(fixture.repo, "rev-parse", "HEAD") == repository_head
    assert git(fixture.repo, "status", "--porcelain=v1") == repository_status == ""


def test_verified_identity_cannot_be_rebound_to_another_authorization_policy(tmp_path: Path) -> None:
    identity = disposable_prompt_repo(tmp_path).identity
    with pytest.raises(FrontierError, match="immutable"):
        identity.policy_id = "attacker-policy"  # type: ignore[misc]
    with pytest.raises(FrontierError, match="immutable"):
        identity.policy_sha256 = "0" * 64  # type: ignore[misc]


def test_unknown_policy_and_public_profile_injection_fail_before_lease(tmp_path: Path) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    controller, run_id = ready_controller(tmp_path)
    history = controller.history(run_id)
    with pytest.raises(FrontierError, match="unknown reviewed prompt policy"):
        fixture.transport.verify_prompt_identity(
            prompt_commit="not-a-commit",
            prompt_path="not/a/prompt.md",
            expected_sha256="0" * 64,
            sidecar_path="not/a/prompt.md.sha256",
            policy_id="attacker-policy",
        )
    with pytest.raises(TypeError, match="unexpected keyword"):
        fixture.transport.verify_prompt_identity(  # type: ignore[call-arg]
            prompt_commit=fixture.prompt_commit,
            prompt_path=PROMPT_PATH,
            expected_sha256=fixture.prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            authorization_profile=object(),
        )
    assert controller.history(run_id) == history
    assert controller.get_run(run_id).state is RunState.READY_FOR_CODEX


def test_matching_widened_prompt_cannot_mint_matching_policy(tmp_path: Path) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    prompt = (fixture.repo / PROMPT_PATH).read_text()
    replacements = {
        "human_gate": "AUTOMATIC_EVENT_06_AUTHORITY",
        "source_mutation": "ALLOWED",
        "original_checkpoint_access": "ALLOWED",
        "full_model_inference": "ALLOWED",
        "automatic_chat_posting": "ALLOWED",
    }
    for field, replacement in replacements.items():
        prompt = prompt.replace(f"{field}: {AUTHORIZATION[field]}", f"{field}: {replacement}", 1)
    prompt_sha = sha256(prompt.encode()).hexdigest()
    (fixture.repo / PROMPT_PATH).write_text(prompt)
    (fixture.repo / PROMPT_SIDECAR).write_text(f"{prompt_sha}  {Path(PROMPT_PATH).name}\n")
    git(fixture.repo, "add", PROMPT_PATH, PROMPT_SIDECAR)
    git(fixture.repo, "commit", "-qm", "matching widened prompt")
    prompt_commit = git(fixture.repo, "rev-parse", "HEAD")
    state = json.loads((fixture.repo / STATE_PATH).read_text())
    state["latest_prompt"] = {"path": PROMPT_PATH, "sha256": prompt_sha, "commit": prompt_commit}
    (fixture.repo / STATE_PATH).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    git(fixture.repo, "add", STATE_PATH)
    git(fixture.repo, "commit", "-qm", "matching widened frontier")
    controller, run_id = ready_controller(tmp_path)
    history = controller.history(run_id)
    head = git(fixture.repo, "rev-parse", "HEAD")
    with pytest.raises(FrontierError, match="authorization mismatch"):
        fixture.transport.verify_prompt_identity(
            prompt_commit=prompt_commit,
            prompt_path=PROMPT_PATH,
            expected_sha256=prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=POLICY_ID,
        )
    assert controller.history(run_id) == history
    assert controller.get_run(run_id).state is RunState.READY_FOR_CODEX
    assert git(fixture.repo, "rev-parse", "HEAD") == head
    assert git(fixture.repo, "status", "--porcelain=v1") == ""


def test_policy_identity_is_durable_and_registry_policy_rejects_serialization(tmp_path: Path) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    identity = fixture.identity
    binding = FrontierBinding.from_identity(identity, result_identity="result-1")
    assert FrontierBinding.from_dict(binding.to_dict()) == binding
    assert binding.policy_id == identity.policy_id == POLICY_ID.value
    assert binding.policy_sha256 == "d0187ae426b82d676c5cb370be669f58e17b75eae4036597a758d40c6949446b"
    with pytest.raises(FrontierError, match="digest mismatch"):
        replace(binding, policy_sha256="0" * 64)
    policy = identity._policy
    with pytest.raises(FrontierError, match="copied"):
        copy.copy(policy)
    with pytest.raises(FrontierError, match="copied"):
        copy.deepcopy(policy)
    with pytest.raises(FrontierError, match="serialized"):
        pickle.dumps(policy)
    with pytest.raises(TypeError):
        json.dumps(policy)
    with pytest.raises(TypeError, match="unexpected keyword"):
        fixture.transport.verify_prompt_identity(  # type: ignore[call-arg]
            prompt_commit=fixture.prompt_commit,
            prompt_path=PROMPT_PATH,
            expected_sha256=fixture.prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=POLICY_ID,
            policy_resolver=lambda _policy_id: policy,
        )


def test_exact_d4_policy_registration_and_cross_policy_rejection(tmp_path: Path) -> None:
    old_fixture = disposable_prompt_repo(tmp_path / "old")
    d4_fixture = disposable_prompt_repo(tmp_path / "d4", D4_AUTHORIZATION)
    d4_identity = d4_fixture.transport.verify_prompt_identity(
        prompt_commit=d4_fixture.prompt_commit,
        prompt_path=PROMPT_PATH,
        expected_sha256=d4_fixture.prompt_sha,
        sidecar_path=PROMPT_SIDECAR,
        policy_id=D4_POLICY_ID,
    )
    assert d4_identity.policy_sha256 == "89bb8b74fadda6562eb791cf1f932de9307896e578d3f32c59688457277f59af"
    binding = FrontierBinding.from_identity(d4_identity, result_identity="d4-policy-result")
    assert FrontierBinding.from_dict(binding.to_dict()) == binding
    with pytest.raises(FrontierError, match="authorization mismatch"):
        d4_fixture.transport.verify_prompt_identity(
            prompt_commit=d4_fixture.prompt_commit,
            prompt_path=PROMPT_PATH,
            expected_sha256=d4_fixture.prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=POLICY_ID,
        )
    with pytest.raises(FrontierError, match="authorization mismatch"):
        old_fixture.transport.verify_prompt_identity(
            prompt_commit=old_fixture.prompt_commit,
            prompt_path=PROMPT_PATH,
            expected_sha256=old_fixture.prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=D4_POLICY_ID,
        )


@pytest.mark.parametrize(("field", "replacement"), AUTHORIZATION_MUTATIONS)
def test_every_d4_policy_field_is_bound(tmp_path: Path, field: str, replacement: str) -> None:
    fixture = disposable_prompt_repo(tmp_path, D4_AUTHORIZATION)
    prompt = (fixture.repo / PROMPT_PATH).read_text()
    prompt = prompt.replace(f"{field}: {D4_AUTHORIZATION[field]}", f"{field}: {replacement}", 1)
    prompt_sha = sha256(prompt.encode()).hexdigest()
    (fixture.repo / PROMPT_PATH).write_text(prompt)
    (fixture.repo / PROMPT_SIDECAR).write_text(f"{prompt_sha}  {Path(PROMPT_PATH).name}\n")
    git(fixture.repo, "add", PROMPT_PATH, PROMPT_SIDECAR)
    git(fixture.repo, "commit", "-qm", f"mutate d4 {field}")
    with pytest.raises(FrontierError, match=f"authorization mismatch: {field}"):
        fixture.transport.verify_prompt_identity(
            prompt_commit=git(fixture.repo, "rev-parse", "HEAD"),
            prompt_path=PROMPT_PATH,
            expected_sha256=prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=D4_POLICY_ID,
        )


def test_exact_d5_policy_registration_and_cross_policy_rejection(tmp_path: Path) -> None:
    fixtures = {
        POLICY_ID: disposable_prompt_repo(tmp_path / "d2", AUTHORIZATION),
        D4_POLICY_ID: disposable_prompt_repo(tmp_path / "d4", D4_AUTHORIZATION),
        D5_POLICY_ID: disposable_prompt_repo(tmp_path / "d5", D5_AUTHORIZATION),
    }
    d5 = fixtures[D5_POLICY_ID]
    identity = d5.transport.verify_prompt_identity(
        prompt_commit=d5.prompt_commit,
        prompt_path=PROMPT_PATH,
        expected_sha256=d5.prompt_sha,
        sidecar_path=PROMPT_SIDECAR,
        policy_id=D5_POLICY_ID,
    )
    assert identity.policy_sha256 == "7528e762df0b32e4a6d69869c34065f90ff44616ea95404c4e1f84f5b6eff839"
    binding = FrontierBinding.from_identity(identity, result_identity="d5-policy-result")
    assert FrontierBinding.from_dict(binding.to_dict()) == binding
    controller, run_id = ready_controller(tmp_path)
    artifact_id = bind_frontier(controller, run_id, binding)
    replayed = Controller(tmp_path / "controller.db").artifacts(run_id)
    assert replayed[-1]["artifact_id"] == artifact_id
    assert FrontierBinding.from_dict(replayed[-1]["content"]) == binding  # type: ignore[arg-type]
    policy = identity._policy
    with pytest.raises(FrontierError, match="copied"):
        copy.copy(policy)
    with pytest.raises(FrontierError, match="copied"):
        copy.deepcopy(policy)
    with pytest.raises(FrontierError, match="serialized"):
        pickle.dumps(policy)
    with pytest.raises(TypeError):
        json.dumps(policy)
    for fixture_policy, fixture in fixtures.items():
        for selected_policy in fixtures:
            if fixture_policy is selected_policy:
                continue
            with pytest.raises(FrontierError, match="authorization mismatch"):
                fixture.transport.verify_prompt_identity(
                    prompt_commit=fixture.prompt_commit,
                    prompt_path=PROMPT_PATH,
                    expected_sha256=fixture.prompt_sha,
                    sidecar_path=PROMPT_SIDECAR,
                    policy_id=selected_policy,
                )


def test_exact_all_five_policy_registration_and_cross_rejection(tmp_path: Path) -> None:
    fixtures = {
        POLICY_ID: disposable_prompt_repo(tmp_path / "d2", AUTHORIZATION),
        D4_POLICY_ID: disposable_prompt_repo(tmp_path / "d4", D4_AUTHORIZATION),
        D5_POLICY_ID: disposable_prompt_repo(tmp_path / "d5", D5_AUTHORIZATION),
        D5R1_POLICY_ID: disposable_prompt_repo(tmp_path / "d5r1", D5R1_AUTHORIZATION),
        D6_POLICY_ID: disposable_prompt_repo(tmp_path / "d6", D6_AUTHORIZATION),
    }
    expected = {
        POLICY_ID: "d0187ae426b82d676c5cb370be669f58e17b75eae4036597a758d40c6949446b",
        D4_POLICY_ID: "89bb8b74fadda6562eb791cf1f932de9307896e578d3f32c59688457277f59af",
        D5_POLICY_ID: "7528e762df0b32e4a6d69869c34065f90ff44616ea95404c4e1f84f5b6eff839",
        D5R1_POLICY_ID: "b2a00e4f895339c6ddf63e9c6624e0b3dfc7bdbc76d2a70ca534126227a575eb",
        D6_POLICY_ID: "4f8e4e2c982dc71c477da455dc0029db73d813d60d60490f9690385fbdd39bcc",
    }
    identities = {}
    for policy_id, fixture in fixtures.items():
        identity = fixture.transport.verify_prompt_identity(
            prompt_commit=fixture.prompt_commit,
            prompt_path=PROMPT_PATH,
            expected_sha256=fixture.prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=policy_id,
        )
        assert identity.policy_sha256 == expected[policy_id]
        identities[policy_id] = identity
    for fixture_policy, fixture in fixtures.items():
        for selected_policy in fixtures:
            if fixture_policy is selected_policy:
                continue
            with pytest.raises(FrontierError, match="authorization mismatch"):
                fixture.transport.verify_prompt_identity(
                    prompt_commit=fixture.prompt_commit,
                    prompt_path=PROMPT_PATH,
                    expected_sha256=fixture.prompt_sha,
                    sidecar_path=PROMPT_SIDECAR,
                    policy_id=selected_policy,
                )
    identity = identities[D6_POLICY_ID]
    binding = FrontierBinding.from_identity(identity, result_identity="d6-policy-result")
    controller, run_id = ready_controller(tmp_path)
    artifact_id = bind_frontier(controller, run_id, binding)
    replayed = Controller(tmp_path / "controller.db").artifacts(run_id)
    assert replayed[-1]["artifact_id"] == artifact_id
    assert FrontierBinding.from_dict(replayed[-1]["content"]) == binding  # type: ignore[arg-type]
    with pytest.raises(FrontierError, match="copied"):
        copy.copy(identity._policy)
    with pytest.raises(FrontierError, match="copied"):
        copy.deepcopy(identity._policy)
    with pytest.raises(FrontierError, match="serialized"):
        pickle.dumps(identity._policy)
    with pytest.raises(TypeError):
        json.dumps(identity._policy)


@pytest.mark.parametrize(("field", "replacement"), AUTHORIZATION_MUTATIONS)
def test_every_d5_policy_field_is_bound(tmp_path: Path, field: str, replacement: str) -> None:
    fixture = disposable_prompt_repo(tmp_path, D5_AUTHORIZATION)
    prompt = (
        (fixture.repo / PROMPT_PATH)
        .read_text()
        .replace(f"{field}: {D5_AUTHORIZATION[field]}", f"{field}: {replacement}", 1)
    )
    prompt_sha = sha256(prompt.encode()).hexdigest()
    (fixture.repo / PROMPT_PATH).write_text(prompt)
    (fixture.repo / PROMPT_SIDECAR).write_text(f"{prompt_sha}  {Path(PROMPT_PATH).name}\n")
    git(fixture.repo, "add", PROMPT_PATH, PROMPT_SIDECAR)
    git(fixture.repo, "commit", "-qm", f"mutate d5 {field}")
    with pytest.raises(FrontierError, match=f"authorization mismatch: {field}"):
        fixture.transport.verify_prompt_identity(
            prompt_commit=git(fixture.repo, "rev-parse", "HEAD"),
            prompt_path=PROMPT_PATH,
            expected_sha256=prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=D5_POLICY_ID,
        )


@pytest.mark.parametrize(("field", "replacement"), AUTHORIZATION_MUTATIONS)
def test_every_d5r1_policy_field_is_bound_before_lease(tmp_path: Path, field: str, replacement: str) -> None:
    fixture = disposable_prompt_repo(tmp_path, D5R1_AUTHORIZATION)
    prompt = (
        (fixture.repo / PROMPT_PATH)
        .read_text()
        .replace(f"{field}: {D5R1_AUTHORIZATION[field]}", f"{field}: {replacement}", 1)
    )
    prompt_sha = sha256(prompt.encode()).hexdigest()
    (fixture.repo / PROMPT_PATH).write_text(prompt)
    (fixture.repo / PROMPT_SIDECAR).write_text(f"{prompt_sha}  {Path(PROMPT_PATH).name}\n")
    git(fixture.repo, "add", PROMPT_PATH, PROMPT_SIDECAR)
    git(fixture.repo, "commit", "-qm", f"mutate d5r1 {field}")
    controller, run_id = ready_controller(tmp_path)
    history = controller.history(run_id)
    resolver = LocalAliasResolver(
        {"SOURCE_REPO_ROOT": "synthetic-source", "CHECKPOINT_ROOT": "synthetic-checkpoint"},
        denied=("SOURCE_REPO_ROOT", "CHECKPOINT_ROOT"),
    )
    with pytest.raises(FrontierError, match=f"authorization mismatch: {field}"):
        fixture.transport.verify_prompt_identity(
            prompt_commit=git(fixture.repo, "rev-parse", "HEAD"),
            prompt_path=PROMPT_PATH,
            expected_sha256=prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=D5R1_POLICY_ID,
        )
    assert resolver.requests == []
    assert controller.history(run_id) == history
    assert controller.get_run(run_id).state is RunState.READY_FOR_CODEX
    assert git(fixture.repo, "status", "--porcelain=v1") == ""


@pytest.mark.parametrize(("field", "replacement"), AUTHORIZATION_MUTATIONS)
def test_every_d6_policy_field_is_bound_before_lease(tmp_path: Path, field: str, replacement: str) -> None:
    fixture = disposable_prompt_repo(tmp_path, D6_AUTHORIZATION)
    prompt = (
        (fixture.repo / PROMPT_PATH)
        .read_text()
        .replace(f"{field}: {D6_AUTHORIZATION[field]}", f"{field}: {replacement}", 1)
    )
    prompt_sha = sha256(prompt.encode()).hexdigest()
    (fixture.repo / PROMPT_PATH).write_text(prompt)
    (fixture.repo / PROMPT_SIDECAR).write_text(f"{prompt_sha}  {Path(PROMPT_PATH).name}\n")
    git(fixture.repo, "add", PROMPT_PATH, PROMPT_SIDECAR)
    git(fixture.repo, "commit", "-qm", f"mutate d6 {field}")
    controller, run_id = ready_controller(tmp_path)
    history = controller.history(run_id)
    resolver = LocalAliasResolver(
        {"SOURCE_REPO_ROOT": "synthetic-source", "CHECKPOINT_ROOT": "synthetic-checkpoint"},
        denied=("SOURCE_REPO_ROOT", "CHECKPOINT_ROOT"),
    )
    with pytest.raises(FrontierError, match=f"authorization mismatch: {field}"):
        fixture.transport.verify_prompt_identity(
            prompt_commit=git(fixture.repo, "rev-parse", "HEAD"),
            prompt_path=PROMPT_PATH,
            expected_sha256=prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=D6_POLICY_ID,
        )
    assert resolver.requests == []
    assert controller.history(run_id) == history
    assert controller.get_run(run_id).state is RunState.READY_FOR_CODEX
    assert git(fixture.repo, "status", "--porcelain=v1") == ""


def test_reviewed_registry_is_immutable_and_rejects_unsafe_policy_minting() -> None:
    import ultracode.feature_loop as feature_loop

    registry = feature_loop._REVIEWED_POLICIES
    assert set(registry) == {POLICY_ID, D4_POLICY_ID, D5_POLICY_ID, D5R1_POLICY_ID, D6_POLICY_ID}
    with pytest.raises(TypeError):
        registry[D4_POLICY_ID] = registry[POLICY_ID]  # type: ignore[index]
    with pytest.raises(TypeError):
        feature_loop._POLICY_BOUNDARIES[D5_POLICY_ID]["source_mutation"] = "ALLOWED"  # type: ignore[index]
    with pytest.raises(FrontierError, match="widens a prohibited capability"):
        feature_loop._mint_reviewed_policy(  # type: ignore[attr-defined]
            D4_POLICY_ID,
            **{**D4_AUTHORIZATION, "original_checkpoint_access": "ALLOWED"},
        )
    with pytest.raises(FrontierError, match="unsafe human gate"):
        feature_loop._mint_reviewed_policy(  # type: ignore[attr-defined]
            D4_POLICY_ID,
            **{**D4_AUTHORIZATION, "human_gate": "AUTOMATIC_EVENT_06_AUTHORITY"},
        )
    for policy_id, authorization in (
        (POLICY_ID, AUTHORIZATION),
        (D4_POLICY_ID, D4_AUTHORIZATION),
        (D5_POLICY_ID, D5_AUTHORIZATION),
        (D5R1_POLICY_ID, D5R1_AUTHORIZATION),
        (D6_POLICY_ID, D6_AUTHORIZATION),
    ):
        for field in ("original_checkpoint_access", "full_model_inference", "automatic_chat_posting"):
            with pytest.raises(FrontierError, match="widens a prohibited capability"):
                feature_loop._mint_reviewed_policy(  # type: ignore[attr-defined]
                    policy_id,
                    **{**authorization, field: "ALLOWED"},
                )
    for policy_id, authorization in ((POLICY_ID, AUTHORIZATION), (D4_POLICY_ID, D4_AUTHORIZATION)):
        with pytest.raises(FrontierError, match="policy-specific source mutation"):
            feature_loop._mint_reviewed_policy(  # type: ignore[attr-defined]
                policy_id,
                **{**authorization, "source_mutation": "BOUNDED_CHECKPOINT_FREE_REPACK_BRANCH_ONLY"},
            )
    for source_mutation in ("ALLOWED", "PROHIBITED", "BOUNDED_OTHER_BRANCH_ONLY"):
        with pytest.raises(FrontierError, match="policy-specific source mutation"):
            feature_loop._mint_reviewed_policy(  # type: ignore[attr-defined]
                D5_POLICY_ID,
                **{**D5_AUTHORIZATION, "source_mutation": source_mutation},
            )
    with pytest.raises(FrontierError, match="unsafe human gate"):
        feature_loop._mint_reviewed_policy(  # type: ignore[attr-defined]
            D5_POLICY_ID,
            **{**D5_AUTHORIZATION, "human_gate": "NOT_REQUIRED_CHECKPOINT_FREE_WRITE"},
        )
    for source_mutation in (
        "ALLOWED",
        "PROHIBITED",
        "BOUNDED_CHECKPOINT_FREE_REPACK_BRANCH_ONLY",
        "BOUNDED_OTHER_BRANCH_ONLY",
    ):
        with pytest.raises(FrontierError, match="policy-specific source mutation"):
            feature_loop._mint_reviewed_policy(  # type: ignore[attr-defined]
                D5R1_POLICY_ID,
                **{**D5R1_AUTHORIZATION, "source_mutation": source_mutation},
            )
    with pytest.raises(FrontierError, match="unsafe human gate"):
        feature_loop._mint_reviewed_policy(  # type: ignore[attr-defined]
            D5R1_POLICY_ID,
            **{**D5R1_AUTHORIZATION, "human_gate": "PLANNER_ACCEPTED_D4_CHECKPOINT_FREE_WRITE"},
        )
    with pytest.raises(FrontierError, match="policy-specific phase"):
        feature_loop._mint_reviewed_policy(  # type: ignore[attr-defined]
            D5R1_POLICY_ID,
            **{**D5R1_AUTHORIZATION, "phase": D5_AUTHORIZATION["phase"]},
        )
    for source_mutation in (
        "ALLOWED",
        "PROHIBITED",
        "BOUNDED_CHECKPOINT_FREE_REPACK_BRANCH_ONLY",
        "BOUNDED_CHECKPOINT_FREE_REPACK_DUPLICATE_ROLE_REPAIR_BRANCH_ONLY",
        "BOUNDED_OTHER_BRANCH_ONLY",
    ):
        with pytest.raises(FrontierError, match="policy-specific source mutation"):
            feature_loop._mint_reviewed_policy(  # type: ignore[attr-defined]
                D6_POLICY_ID,
                **{**D6_AUTHORIZATION, "source_mutation": source_mutation},
            )
    with pytest.raises(FrontierError, match="unsafe human gate"):
        feature_loop._mint_reviewed_policy(  # type: ignore[attr-defined]
            D6_POLICY_ID,
            **{**D6_AUTHORIZATION, "human_gate": "PLANNER_ACCEPTED_WRONG_GATE"},
        )
    with pytest.raises(FrontierError, match="policy-specific phase"):
        feature_loop._mint_reviewed_policy(  # type: ignore[attr-defined]
            D6_POLICY_ID,
            **{**D6_AUTHORIZATION, "phase": D5R1_AUTHORIZATION["phase"]},
        )


def test_matching_widened_d5_prompt_has_zero_lease_and_never_resolves_source_aliases(
    tmp_path: Path,
) -> None:
    fixture = disposable_prompt_repo(tmp_path, D5_AUTHORIZATION)
    prompt = (
        (fixture.repo / PROMPT_PATH)
        .read_text()
        .replace(
            "source_mutation: BOUNDED_CHECKPOINT_FREE_REPACK_BRANCH_ONLY",
            "source_mutation: BOUNDED_OTHER_BRANCH_ONLY",
            1,
        )
    )
    prompt_sha = sha256(prompt.encode()).hexdigest()
    (fixture.repo / PROMPT_PATH).write_text(prompt)
    (fixture.repo / PROMPT_SIDECAR).write_text(f"{prompt_sha}  {Path(PROMPT_PATH).name}\n")
    git(fixture.repo, "add", PROMPT_PATH, PROMPT_SIDECAR)
    git(fixture.repo, "commit", "-qm", "matching widened d5 prompt")
    controller, run_id = ready_controller(tmp_path)
    history = controller.history(run_id)
    resolver = LocalAliasResolver(
        {"SOURCE_REPO_ROOT": "synthetic-source", "CHECKPOINT_ROOT": "synthetic-checkpoint"},
        denied=("SOURCE_REPO_ROOT", "CHECKPOINT_ROOT"),
    )
    with pytest.raises(FrontierError, match="authorization mismatch: source_mutation"):
        fixture.transport.verify_prompt_identity(
            prompt_commit=git(fixture.repo, "rev-parse", "HEAD"),
            prompt_path=PROMPT_PATH,
            expected_sha256=prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=D5_POLICY_ID,
        )
    assert resolver.requests == []
    assert controller.history(run_id) == history
    assert controller.get_run(run_id).state is RunState.READY_FOR_CODEX
    assert git(fixture.repo, "status", "--porcelain=v1") == ""


def test_matching_widened_d5r1_prompt_has_zero_lease_and_never_resolves_aliases(
    tmp_path: Path,
) -> None:
    fixture = disposable_prompt_repo(tmp_path, D5R1_AUTHORIZATION)
    prompt = (
        (fixture.repo / PROMPT_PATH)
        .read_text()
        .replace(
            "source_mutation: BOUNDED_CHECKPOINT_FREE_REPACK_DUPLICATE_ROLE_REPAIR_BRANCH_ONLY",
            "source_mutation: BOUNDED_CHECKPOINT_FREE_REPACK_BRANCH_ONLY",
            1,
        )
    )
    prompt_sha = sha256(prompt.encode()).hexdigest()
    (fixture.repo / PROMPT_PATH).write_text(prompt)
    (fixture.repo / PROMPT_SIDECAR).write_text(f"{prompt_sha}  {Path(PROMPT_PATH).name}\n")
    git(fixture.repo, "add", PROMPT_PATH, PROMPT_SIDECAR)
    git(fixture.repo, "commit", "-qm", "matching widened d5r1 prompt")
    controller, run_id = ready_controller(tmp_path)
    history = controller.history(run_id)
    resolver = LocalAliasResolver(
        {"SOURCE_REPO_ROOT": "synthetic-source", "CHECKPOINT_ROOT": "synthetic-checkpoint"},
        denied=("SOURCE_REPO_ROOT", "CHECKPOINT_ROOT"),
    )
    with pytest.raises(FrontierError, match="authorization mismatch: source_mutation"):
        fixture.transport.verify_prompt_identity(
            prompt_commit=git(fixture.repo, "rev-parse", "HEAD"),
            prompt_path=PROMPT_PATH,
            expected_sha256=prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=D5R1_POLICY_ID,
        )
    assert resolver.requests == []
    assert controller.history(run_id) == history
    assert controller.get_run(run_id).state is RunState.READY_FOR_CODEX
    assert git(fixture.repo, "status", "--porcelain=v1") == ""


def test_matching_widened_d6_prompt_has_zero_lease_and_never_resolves_aliases(
    tmp_path: Path,
) -> None:
    fixture = disposable_prompt_repo(tmp_path, D6_AUTHORIZATION)
    prompt = (
        (fixture.repo / PROMPT_PATH)
        .read_text()
        .replace(
            "source_mutation: BOUNDED_CHECKPOINT_FREE_SYNTHETIC_REPACK_TESTS_BRANCH_ONLY",
            "source_mutation: BOUNDED_CHECKPOINT_FREE_REPACK_BRANCH_ONLY",
            1,
        )
    )
    prompt_sha = sha256(prompt.encode()).hexdigest()
    (fixture.repo / PROMPT_PATH).write_text(prompt)
    (fixture.repo / PROMPT_SIDECAR).write_text(f"{prompt_sha}  {Path(PROMPT_PATH).name}\n")
    git(fixture.repo, "add", PROMPT_PATH, PROMPT_SIDECAR)
    git(fixture.repo, "commit", "-qm", "matching widened d6 prompt")
    controller, run_id = ready_controller(tmp_path)
    history = controller.history(run_id)
    resolver = LocalAliasResolver(
        {"SOURCE_REPO_ROOT": "synthetic-source", "CHECKPOINT_ROOT": "synthetic-checkpoint"},
        denied=("SOURCE_REPO_ROOT", "CHECKPOINT_ROOT"),
    )
    with pytest.raises(FrontierError, match="authorization mismatch: source_mutation"):
        fixture.transport.verify_prompt_identity(
            prompt_commit=git(fixture.repo, "rev-parse", "HEAD"),
            prompt_path=PROMPT_PATH,
            expected_sha256=prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
            policy_id=D6_POLICY_ID,
        )
    assert resolver.requests == []
    assert controller.history(run_id) == history
    assert controller.get_run(run_id).state is RunState.READY_FOR_CODEX
    assert git(fixture.repo, "status", "--porcelain=v1") == ""


@pytest.mark.parametrize(
    ("document", "parser"),
    [
        (FEATURE, FeatureManifest.from_yaml),
        (json.dumps(state_document("a" * 40, "b" * 40)), FeatureState.from_json),
        (POLICY, PrivacyPolicy.from_yaml),
    ],
)
def test_control_document_schema_mismatch_fails_closed(document: str, parser: Any) -> None:
    with pytest.raises(Exception, match=r"unsupported .* schema"):
        parser(document.replace("/1.0.0", "/9.9.9", 1))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("path", "Prompts/F017/other.md", "parent response"),
        ("sha256", "0" * 64, "SHA-256"),
        ("sequence", 7, "sequence"),
        ("machine_model", "Other Machine", "machine or status"),
        ("status", "BLOCKED", "machine or status"),
        ("commit", "f" * 40, "Git object operation"),
    ],
)
def test_complete_parent_identity_is_load_bearing(tmp_path: Path, field: str, value: object, message: str) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    state = json.loads((fixture.repo / STATE_PATH).read_text())
    state["latest_response"][field] = value
    (fixture.repo / STATE_PATH).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    git(fixture.repo, "add", STATE_PATH)
    git(fixture.repo, "commit", "-qm", "bad parent")
    with pytest.raises(FrontierError, match=message):
        guard_frontier(
            identity=fixture.identity,
            transport=fixture.transport,
            live_commit=git(fixture.repo, "rev-parse", "HEAD"),
        )


def test_pass_only_frontier_rejects_blocked_parent_with_zero_lease(tmp_path: Path) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    state = json.loads((fixture.repo / STATE_PATH).read_text())
    state["latest_response"]["status"] = "BLOCKED"
    (fixture.repo / STATE_PATH).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    git(fixture.repo, "add", STATE_PATH)
    git(fixture.repo, "commit", "-qm", "blocked parent")
    controller, run_id = ready_controller(tmp_path)
    history = controller.history(run_id)
    with pytest.raises(FrontierError, match="machine or status mismatch"):
        claim_after_guards(
            controller,
            run_id=run_id,
            worker_id="worker",
            idempotency_key="blocked-parent-claim",
            identity=fixture.identity,
            transport=fixture.transport,
            live_commit=git(fixture.repo, "rev-parse", "HEAD"),
        )
    assert controller.history(run_id) == history
    assert controller.get_run(run_id).state is RunState.READY_FOR_CODEX
    assert git(fixture.repo, "status", "--porcelain=v1") == ""


def test_pass_recovery_attestation_uses_normal_frontier_path(tmp_path: Path) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    state = json.loads((fixture.repo / STATE_PATH).read_text())
    state["latest_response"]["result_identity"] = "BLOCKED_PARENT_RECOVERY_ATTESTATION"
    (fixture.repo / STATE_PATH).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    git(fixture.repo, "add", STATE_PATH)
    git(fixture.repo, "commit", "-qm", "pass recovery attestation")
    controller, run_id = ready_controller(tmp_path)
    claim = claim_after_guards(
        controller,
        run_id=run_id,
        worker_id="worker",
        idempotency_key="recovery-claim",
        identity=fixture.identity,
        transport=fixture.transport,
        live_commit=git(fixture.repo, "rev-parse", "HEAD"),
    )
    assert claim.worker_id == "worker"
    assert controller.get_run(run_id).state is RunState.CODEX_RUNNING


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("path", "Prompts/F017/other.md", "parent response"),
        ("sha256", "0" * 64, "SHA-256"),
        ("machine_model", "Other Machine", "machine or status"),
        ("sequence", 7, "sequence"),
        ("status", "BLOCKED", "machine or status"),
        ("commit", "f" * 40, "Git object operation"),
        ("result_identity", "MUTATED_RECOVERY_ATTESTATION", "SHA-256"),
    ],
)
def test_recovery_parent_mutation_fails_before_lease(tmp_path: Path, field: str, value: object, message: str) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    state = json.loads((fixture.repo / STATE_PATH).read_text())
    state["latest_response"]["result_identity"] = "BLOCKED_PARENT_RECOVERY_ATTESTATION"
    if field == "result_identity":
        (fixture.repo / PARENT_PATH).write_text(f"{PARENT}{value}\n")
        git(fixture.repo, "add", PARENT_PATH)
        git(fixture.repo, "commit", "-qm", "mutate recovery identity")
        state["latest_response"]["commit"] = git(fixture.repo, "rev-parse", "HEAD")
    else:
        state["latest_response"][field] = value
    (fixture.repo / STATE_PATH).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    git(fixture.repo, "add", STATE_PATH)
    git(fixture.repo, "commit", "-qm", f"mutate recovery {field}")
    controller, run_id = ready_controller(tmp_path)
    history = controller.history(run_id)
    with pytest.raises(FrontierError, match=message):
        claim_after_guards(
            controller,
            run_id=run_id,
            worker_id="worker",
            idempotency_key=f"mutated-recovery-{field}",
            identity=fixture.identity,
            transport=fixture.transport,
            live_commit=git(fixture.repo, "rev-parse", "HEAD"),
        )
    assert controller.history(run_id) == history
    assert controller.get_run(run_id).state is RunState.READY_FOR_CODEX
    assert git(fixture.repo, "status", "--porcelain=v1") == ""


@pytest.mark.parametrize("duplicate", [RESPONSE_PATH, CHECKSUM_PATH, HANDOFF_PATH])
def test_terminal_and_duplicate_artifacts_block_rerun(tmp_path: Path, duplicate: str) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    target = fixture.repo / duplicate
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("existing")
    git(fixture.repo, "add", duplicate)
    git(fixture.repo, "commit", "-qm", "duplicate")
    controller, run_id = ready_controller(tmp_path)
    with pytest.raises(FrontierError, match="already exists"):
        claim_after_guards(
            controller,
            run_id=run_id,
            worker_id="worker",
            idempotency_key="claim",
            identity=fixture.identity,
            transport=fixture.transport,
            live_commit=git(fixture.repo, "rev-parse", "HEAD"),
        )
    assert controller.get_run(run_id).state is RunState.READY_FOR_CODEX


def test_feature_state_status_or_sequence_mismatch_blocks(tmp_path: Path) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    (fixture.repo / FEATURE_PATH).write_text(FEATURE.replace("PROMPT_AVAILABLE", "BLOCKED"))
    git(fixture.repo, "add", FEATURE_PATH)
    git(fixture.repo, "commit", "-qm", "mixed frontier")
    with pytest.raises(FrontierError, match="status mismatch"):
        guard_frontier(
            identity=fixture.identity,
            transport=fixture.transport,
            live_commit=git(fixture.repo, "rev-parse", "HEAD"),
        )


def test_publication_projects_feature_and_state_together_and_retries(tmp_path: Path) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    coordinator = PublicationCoordinator(fixture.repo, scanner())
    request = commit_response(fixture, coordinator)
    order: list[str] = []
    result = coordinator.prepare(request, step_observer=order.append)
    retry = coordinator.prepare(request)
    assert order == ["response", "checksum", "handoff", "feature", "state"]
    assert result.response_sha256 == retry.response_sha256
    feature = FeatureManifest.from_yaml((fixture.repo / FEATURE_PATH).read_text())
    state = FeatureState.from_json((fixture.repo / STATE_PATH).read_text())
    assert feature.status == state.state == "CHAT_HANDOFF_PENDING"
    assert feature.machine_sequences["MacBook-Pro-M2-Max"] == state.current_sequence == 1
    assert state.latest_response is not None and state.latest_response.commit == request.response_commit
    handoff = json.loads((fixture.repo / HANDOFF_PATH).read_text())
    assert set(handoff) == {
        "feature_id",
        "machine_model",
        "response_sha256",
        "response_url",
        "sequence",
        "status",
    }


@pytest.mark.parametrize("expected_field", ["expected_feature_sha256", "expected_state_sha256"])
def test_feature_or_state_expected_hash_race_blocks_all_control_writes(tmp_path: Path, expected_field: str) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    coordinator = PublicationCoordinator(fixture.repo, scanner())
    request = commit_response(fixture, coordinator)
    raced = replace(request, **{expected_field: "0" * 64})
    before_state = (fixture.repo / STATE_PATH).read_bytes()
    before_feature = (fixture.repo / FEATURE_PATH).read_bytes()
    with pytest.raises(PublicationError, match="frontier changed"):
        coordinator.prepare(raced)
    assert (fixture.repo / STATE_PATH).read_bytes() == before_state
    assert (fixture.repo / FEATURE_PATH).read_bytes() == before_feature


def test_isolated_remote_movement_requires_fast_forward_ancestry(tmp_path: Path) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    (fixture.repo / "forward.txt").write_text("forward\n")
    git(fixture.repo, "add", "forward.txt")
    git(fixture.repo, "commit", "-qm", "forward")
    forward = git(fixture.repo, "rev-parse", "HEAD")
    fixture.transport.require_ancestor(fixture.live_commit, forward)

    git(fixture.repo, "checkout", "-qb", "divergent", fixture.base_commit)
    (fixture.repo / "divergent.txt").write_text("divergent\n")
    git(fixture.repo, "add", "divergent.txt")
    git(fixture.repo, "commit", "-qm", "divergent")
    divergent = git(fixture.repo, "rev-parse", "HEAD")
    with pytest.raises(FrontierError, match="does not descend"):
        fixture.transport.require_ancestor(fixture.live_commit, divergent)


def test_interrupted_control_projection_resumes_without_repeating_response(tmp_path: Path) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    coordinator = PublicationCoordinator(fixture.repo, scanner())
    request = commit_response(fixture, coordinator)

    def interrupt(step: str) -> None:
        if step == "feature":
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        coordinator.prepare(request, step_observer=interrupt)
    resumed = coordinator.prepare(request)
    assert resumed.status == "PREPARED"
    assert FeatureState.from_json((fixture.repo / STATE_PATH).read_text()).state == "CHAT_HANDOFF_PENDING"


def test_push_and_notification_failure_do_not_duplicate_state_or_leak_alias(tmp_path: Path) -> None:
    fixture = disposable_prompt_repo(tmp_path)
    coordinator = PublicationCoordinator(fixture.repo, scanner())
    request = commit_response(fixture, coordinator)
    calls = 0

    def fail_push() -> None:
        nonlocal calls
        calls += 1
        raise OSError("offline")

    result = coordinator.publish(request, fail_push)
    resolver = LocalAliasResolver({"NTFY_TOPIC_ALIAS": "synthetic-private-topic"})
    notice = notification_record(
        alias="NTFY_TOPIC_ALIAS",
        resolver=resolver,
        transport=lambda _topic, _message: (_ for _ in ()).throw(OSError("offline")),
        feature_id="F017",
        sequence=1,
        artifact_identity=result.response_sha256,
    )
    assert calls == 1 and result.status == "PUSH_PENDING" and notice["status"] == "FAIL"
    assert "synthetic-private-topic" not in json.dumps(notice)


def test_state_sequence_one_rejects_historical_incomplete_identity() -> None:
    with pytest.raises(Exception, match="complete latest_response"):
        FeatureState.from_json(
            json.dumps(
                {
                    "schema": "pulsarmlx.feature-loop-state/1.0.0",
                    "feature_id": "F017",
                    "state": "PROMPT_AVAILABLE",
                    "current_machine": "MacBook-Pro-M2-Max",
                    "current_sequence": 1,
                    "latest_response": {"path": PARENT_PATH},
                    "latest_prompt": None,
                }
            )
        )
