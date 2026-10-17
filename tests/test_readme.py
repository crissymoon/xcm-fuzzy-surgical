"""Tests for the generated README and project metadata."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import build_readme

ROOT = Path(__file__).resolve().parents[1]


def _metadata() -> dict:
    return json.loads((ROOT / "meta" / "project.json").read_text(encoding="utf-8"))


def test_generated_readme_is_current() -> None:
    result = build_readme.main(["--check"])

    assert result == 0


def test_render_contains_project_identity() -> None:
    metadata = _metadata()
    rendered = build_readme.render(metadata, metadata["version"])

    assert "# FuzzySurgical" in rendered
    assert "Crissy Moon" in rendered
    assert "https://xcaliburmoon.net/" in rendered
    assert "https://github.com/crissymoon/xcm-fuzzy-surgical" in rendered
    assert "MIT License" in rendered
    assert rendered.startswith(build_readme.MARKER)
    assert rendered.endswith("\n")


def test_render_uses_metadata_values() -> None:
    metadata = _metadata()
    metadata["tagline"] = "A distinct tagline for the test."
    metadata["highlights"] = ["Only highlight"]

    rendered = build_readme.render(metadata, metadata["version"])

    assert "A distinct tagline for the test." in rendered
    assert "- Only highlight" in rendered


def test_version_mismatch_is_rejected() -> None:
    metadata = _metadata()
    metadata["version"] = "9.9.9"

    with pytest.raises(build_readme.MetadataError, match="version mismatch"):
        build_readme.render(metadata, "0.1.0")


def test_missing_required_key_is_rejected() -> None:
    metadata = _metadata()
    del metadata["license"]

    with pytest.raises(build_readme.MetadataError, match="missing metadata keys"):
        build_readme.render(metadata, metadata["version"])


def test_check_reports_stale_output(tmp_path: Path) -> None:
    stale = tmp_path / "README.md"
    stale.write_text("# stale\n", encoding="utf-8")

    result = build_readme.main(["--output", str(stale), "--check"])

    assert result == 1


def test_license_file_matches_metadata() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert license_text.startswith("MIT License")
    assert "Copyright (c) 2026 Crissy Moon" in license_text
    assert "THE SOFTWARE IS PROVIDED" in license_text
