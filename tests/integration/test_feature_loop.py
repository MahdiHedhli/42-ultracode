"""Integration tests for the fail-closed Feature Loop Git adapter."""

from __future__ import annotations

import json
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


def prompt_markdown(base_commit: str, parent_sha: str = PARENT_SHA) -> str:
    return f"""---
schema: pulsarmlx.graph-prompt/1.0.0
feature_id: F017
sequence: 1
machine_model: MacBook Pro M2 Max
machine_architecture: arm64
phase: D2-security-repair
human_gate: NOT_REQUIRED_CHECKPOINT_FREE_REPAIR
prompt_control_base_commit: {base_commit}
expected_parent_response_path: {PARENT_PATH}
expected_parent_response_sha256: {parent_sha}
response_path: {RESPONSE_PATH}
response_checksum_path: {CHECKSUM_PATH}
handoff_path: {HANDOFF_PATH}
source_repository: MahdiHedhli/PulsarMLX
source_mutation: PROHIBITED
original_checkpoint_access: PROHIBITED
full_model_inference: PROHIBITED
automatic_chat_posting: PROHIBITED
---

# Synthetic repair prompt
"""


def state_document(prompt_commit: str, parent_commit: str) -> dict[str, Any]:
    return {
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
        )


def disposable_prompt_repo(tmp_path: Path) -> PromptFixture:
    repo = tmp_path / "prompts"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    target = repo / "Prompts/F017/MacBook-Pro-M2-Max"
    target.mkdir(parents=True)
    (target / "000__response.md").write_text(PARENT)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "parent")
    base = git(repo, "rev-parse", "HEAD")

    prompt = prompt_markdown(base)
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
        FrontierBinding(
            "F017",
            "MacBook Pro M2 Max",
            1,
            fixture.prompt_commit,
            fixture.prompt_sha,
            RESPONSE_PATH,
            "result-1",
        ),
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
        )
        guard_frontier(identity=identity, transport=fixture.transport, live_commit=latest)
    with pytest.raises(FrontierError, match="absent"):
        fixture.transport.verify_prompt_identity(
            prompt_commit=fixture.base_commit,
            prompt_path=PROMPT_PATH,
            expected_sha256=fixture.prompt_sha,
            sidecar_path=PROMPT_SIDECAR,
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
        )


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
                    "feature_id": "F017",
                    "state": "PROMPT_AVAILABLE",
                    "current_machine": "MacBook-Pro-M2-Max",
                    "current_sequence": 1,
                    "latest_response": {"path": PARENT_PATH},
                    "latest_prompt": None,
                }
            )
        )
