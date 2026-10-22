"""Tests for the generated README landing page, the DOCS pages and metadata."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import build_readme

ROOT = Path(__file__).resolve().parents[1]


def _metadata() -> dict:
    return json.loads((ROOT / "meta" / "project.json").read_text(encoding="utf-8"))


def _documents() -> dict:
    metadata = _metadata()
    return build_readme.render_documents(metadata, metadata["version"])


def test_generated_documents_are_current() -> None:
    assert build_readme.main(["--check"]) == 0


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
    metadata["features"] = [
        {"title": "Only feature", "summary": "Only summary", "items": ["Only item"]}
    ]

    rendered = build_readme.render(metadata, metadata["version"])

    assert "A distinct tagline for the test." in rendered
    assert "- **[Only feature](README/DOCS/FEATS.md#only-feature)** - Only summary" in rendered


def test_landing_stays_a_short_page() -> None:
    rendered = build_readme.render(_metadata(), _metadata()["version"])

    assert len(rendered.splitlines()) <= 80
    assert "## Features" in rendered
    assert "## Documentation" in rendered


def test_landing_feature_links_use_real_anchors() -> None:
    metadata = _metadata()
    features_page = next(doc["path"] for doc in metadata["docs"] if doc["page"] == "features")
    rendered = build_readme.render(metadata, metadata["version"])
    anchors = build_readme.anchors(_documents()[features_page])

    for target in build_readme.LINK_RE.findall(rendered):
        path, _, anchor = target.partition("#")
        if path == features_page and anchor:
            assert anchor in anchors


def test_every_feature_item_reaches_the_docs_page() -> None:
    metadata = _metadata()
    documents = build_readme.render_documents(metadata, metadata["version"])
    features_page = next(doc["path"] for doc in metadata["docs"] if doc["page"] == "features")

    for feature in metadata["features"]:
        assert f"## {feature['title']}" in documents[features_page]
        for item in feature["items"]:
            assert item in documents[features_page]


def test_every_docs_entry_has_a_page() -> None:
    metadata = _metadata()
    documents = build_readme.render_documents(metadata, metadata["version"])

    for doc in metadata["docs"]:
        assert doc["path"] in documents
        assert documents[doc["path"]].startswith(build_readme.MARKER)
        assert "[Back to FuzzySurgical](../../README.md)" in documents[doc["path"]]
        assert f"]({doc['path']})" in documents["README.md"]


def test_generated_links_and_anchors_resolve() -> None:
    assert build_readme.check_links(_documents(), ROOT) == []


def test_broken_anchor_is_reported() -> None:
    documents = _documents()
    documents["README.md"] += "\n[Broken](README/DOCS/FEATS.md#no-such-heading)\n"

    problems = build_readme.check_links(documents, ROOT)

    assert any("missing anchor '#no-such-heading'" in problem for problem in problems)


def test_missing_docs_page_is_reported() -> None:
    documents = _documents()
    del documents["README/DOCS/USAGE.md"]

    problems = build_readme.check_links(documents, ROOT)

    assert any(
        "ungenerated page README/DOCS/USAGE.md" in problem for problem in problems
    )


def test_feature_page_is_required() -> None:
    metadata = _metadata()
    metadata["docs"] = [doc for doc in metadata["docs"] if doc["page"] != "features"]

    with pytest.raises(build_readme.MetadataError, match=r"docs.*features"):
        build_readme.render(metadata, metadata["version"])


def test_duplicate_docs_path_is_rejected() -> None:
    metadata = _metadata()
    metadata["docs"][1]["path"] = metadata["docs"][0]["path"]

    with pytest.raises(build_readme.MetadataError, match="duplicate docs path"):
        build_readme.render(metadata, metadata["version"])


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

    assert build_readme.main(["--output", str(stale), "--check"]) == 1


def test_check_reports_a_stale_docs_page() -> None:
    metadata = _metadata()
    documents = build_readme.render_documents(metadata, metadata["version"])

    for relative, content in documents.items():
        path = ROOT / relative
        assert path.read_text(encoding="utf-8") == content


def test_license_file_matches_metadata() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert license_text.startswith("MIT License")
    assert "Copyright (c) 2026 Crissy Moon" in license_text
    assert "THE SOFTWARE IS PROVIDED" in license_text
