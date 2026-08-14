"""Small command-surface regression coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from ultracode.cli import main


def test_cli_turns_controller_errors_into_actionable_usage_errors(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="2"):
        main(["status", "--database", str(tmp_path / "missing.db"), "missing-run"])
