"""Adversarial privacy, path, and capability tests for Feature Loop."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.test_feature_loop import POLICY, prompt_markdown
from ultracode.feature_loop import (
    FeatureLoopError,
    GitPromptTransport,
    LocalAliasResolver,
    ManifestError,
    PrivacyError,
    PrivacyPolicy,
    PrivacyScanner,
    PromptEnvelope,
    PublicationCoordinator,
    parse_restricted_yaml,
)


@pytest.mark.parametrize(
    ("category", "marker"),
    [
        ("personal-names", "PRIVATE_PERSON"),
        ("local-usernames", "PRIVATE_USER"),
        ("absolute-home-paths", "/Users/private/project"),
        ("hostnames", "PRIVATE_HOST"),
        ("private-ip-addresses", "192.168.1.20"),
        ("internal-dns-names", "host.internal"),
        ("mac-addresses", "aa:bb:cc:dd:ee:ff"),
        ("serial-numbers", "PRIVATE_SERIAL"),
        ("mount-and-share-names", "/Volumes/private-share"),
        ("credentials-tokens-cookies-and-secrets", "sk-0123456789abcdefghijklmnopqrstuv"),
        ("raw-private-chatgpt-conversation-urls", "https://chatgpt.com/c/private"),
        ("actual-notification-topic-names", "PRIVATE_TOPIC"),
        ("unrelated-client-tenant-or-lab-topology", "PRIVATE_TOPOLOGY"),
    ],
)
def test_every_prohibited_privacy_category_fails_closed(category: str, marker: str) -> None:
    policy = PrivacyPolicy(
        feature_id="F017",
        allowed=(),
        prohibited=(category,),
        aliases={},
    )
    scanner = PrivacyScanner(policy, category_markers={category: [marker]})
    with pytest.raises(PrivacyError, match=category):
        scanner.scan(f"evidence={marker}")


def test_resolved_alias_is_nonserializable_by_default_and_checkpoint_is_never_resolved() -> None:
    resolver = LocalAliasResolver(
        {"NTFY_TOPIC_ALIAS": "private-topic", "CHECKPOINT_ROOT": "must-not-be-used"},
        denied=("CHECKPOINT_ROOT",),
    )
    resolved = resolver.resolve("NTFY_TOPIC_ALIAS")
    assert str(resolved) == "NTFY_TOPIC_ALIAS"
    assert "private-topic" not in repr(resolved)
    with pytest.raises(FeatureLoopError, match="prohibited"):
        resolver.resolve("CHECKPOINT_ROOT")
    assert resolver.requests == ["NTFY_TOPIC_ALIAS", "CHECKPOINT_ROOT"]


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


def test_prompt_parser_rejects_unknown_fields_and_path_traversal() -> None:
    with pytest.raises(ManifestError, match="fields differ"):
        PromptEnvelope.from_markdown(
            prompt_markdown().replace("phase: dry-round-trip", "phase: dry-round-trip\nextra: x")
        )
    with pytest.raises(Exception, match=r"relative|traverse"):
        PromptEnvelope.from_markdown(
            prompt_markdown().replace(
                "response_path: Prompts/F017/MacBook-Pro-M2-Max/000__response.md",
                "response_path: ../escape.md",
            )
        )


def test_symlinked_repository_and_publication_parent_are_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / ".git").mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(Exception, match="symlink"):
        GitPromptTransport(link)
    policy = PrivacyPolicy.from_yaml(POLICY)
    publication_root = tmp_path / "publication"
    publication_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (publication_root / "Prompts").symlink_to(outside, target_is_directory=True)
    coordinator = PublicationCoordinator(publication_root, PrivacyScanner(policy))
    with pytest.raises(Exception, match="symlink"):
        coordinator._target("Prompts/F017/response.md")
