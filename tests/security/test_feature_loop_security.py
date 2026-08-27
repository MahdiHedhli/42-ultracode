"""Adversarial privacy, path, and capability tests for Feature Loop."""

from __future__ import annotations

import copy
import json
import pickle
import subprocess
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from pathlib import Path

import pytest

from tests.integration.test_feature_loop import POLICY, scanner
from ultracode.feature_loop import (
    FeatureLoopError,
    GitPromptTransport,
    LocalAliasResolver,
    ManifestError,
    PrivacyError,
    PrivacyPolicy,
    PrivacyScanner,
    PublicationCoordinator,
    ResolvedAlias,
    parse_restricted_yaml,
    validate_durable_payload,
)

BUILTIN = {
    "absolute-home-paths": "/" + "Users" + "/private/project",
    "private-ip-addresses": ".".join(("192", "168", "1", "20")),
    "internal-dns-names": "host" + ".internal",
    "mac-addresses": ":".join(("aa", "bb", "cc", "dd", "ee", "ff")),
    "mount-and-share-names": "/" + "Volumes" + "/private-share",
    "credentials-tokens-cookies-and-secrets": "sk-" + "0123456789abcdefghijklmnopqrstuv",
    "raw-private-chatgpt-conversation-urls": "https://" + "chatgpt.com" + "/c/private",
}

CONTEXTUAL = {
    "personal-names": "PRIVATE_PERSON",
    "local-usernames": "PRIVATE_USER",
    "hostnames": "PRIVATE_HOST",
    "serial-numbers": "PRIVATE_SERIAL",
    "actual-notification-topic-names": "PRIVATE_TOPIC",
    "unrelated-client-tenant-or-lab-topology": "PRIVATE_TOPOLOGY",
}


def policy(category: str) -> PrivacyPolicy:
    return PrivacyPolicy(
        schema="pulsarmlx.prompt-privacy-policy/1.0.0",
        feature_id="F017",
        allowed=(),
        prohibited=(category,),
        aliases={},
    )


def full_scanner() -> PrivacyScanner:
    aliases = {f"marker-{index}": value for index, value in enumerate(CONTEXTUAL.values())}
    resolver = LocalAliasResolver(aliases)
    markers = {category: [resolver.resolve(f"marker-{index}")] for index, category in enumerate(CONTEXTUAL)}
    return PrivacyScanner(
        PrivacyPolicy(
            schema="pulsarmlx.prompt-privacy-policy/1.0.0",
            feature_id="F017",
            allowed=(),
            prohibited=tuple((*BUILTIN.keys(), *CONTEXTUAL.keys())),
            aliases={},
        ),
        category_markers=markers,
    )


@pytest.mark.parametrize("category", sorted(BUILTIN))
def test_builtin_privacy_categories_are_structurally_detected(category: str) -> None:
    with pytest.raises(PrivacyError, match=category):
        PrivacyScanner(policy(category)).scan(BUILTIN[category])


@pytest.mark.parametrize("category", sorted(CONTEXTUAL))
def test_contextual_privacy_categories_require_private_marker_capabilities(category: str) -> None:
    with pytest.raises(PrivacyError, match="requires private marker"):
        PrivacyScanner(policy(category))
    alias = f"{category}-alias"
    resolver = LocalAliasResolver({alias: CONTEXTUAL[category]})
    detector = PrivacyScanner(policy(category), category_markers={category: [resolver.resolve(alias)]})
    with pytest.raises(PrivacyError, match=category):
        detector.scan(CONTEXTUAL[category])


def test_unknown_category_and_raw_marker_fail_scanner_construction() -> None:
    with pytest.raises(PrivacyError, match="no detector"):
        PrivacyScanner(policy("unsupported-category"))
    with pytest.raises(PrivacyError, match="resolved capability"):
        PrivacyScanner(  # type: ignore[arg-type]
            policy("local-usernames"), category_markers={"local-usernames": ["raw"]}
        )


def test_blank_circular_and_ambiguous_alias_configurations_fail() -> None:
    with pytest.raises(FeatureLoopError, match="non-empty"):
        LocalAliasResolver({"A": ""})
    with pytest.raises(FeatureLoopError, match="circular"):
        LocalAliasResolver({"A": "B", "B": "A"})
    with pytest.raises(FeatureLoopError, match="ambiguous"):
        LocalAliasResolver({"A": "same", "B": "same"})


def test_resolved_alias_is_not_a_record_and_all_default_serialization_fails() -> None:
    resolver = LocalAliasResolver(
        {"NTFY_TOPIC_ALIAS": "synthetic-private-topic", "CHECKPOINT_ROOT": "must-not-resolve"},
        denied=("CHECKPOINT_ROOT",),
    )
    resolved = resolver.resolve("NTFY_TOPIC_ALIAS")
    assert isinstance(resolved, ResolvedAlias)
    assert str(resolved) == "NTFY_TOPIC_ALIAS"
    assert repr(resolved) == "ResolvedAlias(alias='NTFY_TOPIC_ALIAS')"
    assert "synthetic-private-topic" not in repr(resolved)
    assert not is_dataclass(resolved)
    with pytest.raises(TypeError):
        vars(resolved)
    with pytest.raises(TypeError):
        asdict(resolved)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        json.dumps(resolved)
    with pytest.raises(FeatureLoopError):
        copy.copy(resolved)
    with pytest.raises(FeatureLoopError):
        copy.deepcopy(resolved)
    with pytest.raises(FeatureLoopError):
        pickle.dumps(resolved)
    assert resolved.reveal_for_transport() == "synthetic-private-topic"
    with pytest.raises(FeatureLoopError, match="prohibited"):
        resolver.resolve("CHECKPOINT_ROOT")


@pytest.mark.parametrize(
    "payload",
    [
        lambda value: value,
        lambda value: [value],
        lambda value: {"outer": value},
        lambda value: {"outer": [{"inner": value}]},
    ],
)
def test_recursive_durable_payload_validation_rejects_alias_at_every_depth(
    payload: Callable[[ResolvedAlias], object],
) -> None:
    resolved = LocalAliasResolver({"A": "private-value"}).resolve("A")
    with pytest.raises(FeatureLoopError, match="durable payload"):
        validate_durable_payload(payload(resolved))


def test_complete_staged_diff_is_scanned(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
    (repo / "evidence.md").write_text("safe\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    (repo / "evidence.md").write_text(f"{BUILTIN['absolute-home-paths']}\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    with pytest.raises(PrivacyError, match="absolute-home-paths"):
        scanner().scan_staged_diff(repo)


def test_staged_diff_allows_removal_of_preexisting_prohibited_content(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    marker = BUILTIN["absolute-home-paths"]
    (repo / "evidence.md").write_text(f"{marker}\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "base",
        ],
        check=True,
    )
    (repo / "evidence.md").write_text("safe\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    scanner().scan_staged_diff(repo)


@pytest.mark.parametrize("surface", ["response", "state", "feature", "handoff", "notification"])
@pytest.mark.parametrize("category", sorted((*BUILTIN.keys(), *CONTEXTUAL.keys())))
def test_every_prohibited_category_is_rejected_on_every_durable_surface(category: str, surface: str) -> None:
    marker = BUILTIN.get(category, CONTEXTUAL.get(category))
    assert marker is not None
    with pytest.raises(PrivacyError, match=category):
        full_scanner().scan(f"{surface}: {marker}")


@pytest.mark.parametrize("category", sorted((*BUILTIN.keys(), *CONTEXTUAL.keys())))
def test_every_prohibited_category_is_rejected_in_complete_staged_diff(tmp_path: Path, category: str) -> None:
    marker = BUILTIN.get(category, CONTEXTUAL.get(category))
    assert marker is not None
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "evidence.md").write_text("safe\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "base",
        ],
        check=True,
    )
    (repo / "evidence.md").write_text(f"{marker}\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    with pytest.raises(PrivacyError, match=category):
        full_scanner().scan_staged_diff(repo)


@pytest.mark.parametrize(
    "bad_yaml",
    [
        "a:\n\tb: value\n",
        "a: 1\na: 2\n",
        "a: &anchor value\n",
        "- not-a-root-map\n",
        "a:\n   b: odd\n",
    ],
)
def test_malformed_or_ambiguous_yaml_is_rejected(bad_yaml: str) -> None:
    with pytest.raises(ManifestError):
        parse_restricted_yaml(bad_yaml)


def test_symlinked_repository_and_publication_parent_are_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / ".git").mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(Exception, match="symlink"):
        GitPromptTransport(link)
    publication_root = tmp_path / "publication"
    publication_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (publication_root / "Prompts").symlink_to(outside, target_is_directory=True)
    coordinator = PublicationCoordinator(publication_root, PrivacyScanner(PrivacyPolicy.from_yaml(POLICY)))
    with pytest.raises(Exception, match="symlink"):
        coordinator._target("Prompts/F017/response.md")
