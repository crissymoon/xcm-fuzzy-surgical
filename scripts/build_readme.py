"""Render README.md and README/DOCS from meta/project.json.

The JSON file is the single source of truth for documentation content. The root
README stays a short landing page: each feature is one linked sentence pointing
at a page under README/DOCS. Run this script after editing the JSON, or use
--check before committing to reject a stale page or a broken link.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import posixpath
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from readme_docs import (  # noqa: F401  (MARKER is part of this module API)
        FEATURES_PAGE,
        MARKER,
        SUPPORTED_PAGES,
        anchors,
        code_block,
        render_docs,
        slug,
    )
else:
    from .readme_docs import (  # noqa: F401  (MARKER is part of this module API)
        FEATURES_PAGE,
        MARKER,
        SUPPORTED_PAGES,
        anchors,
        code_block,
        render_docs,
        slug,
    )

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_METADATA = ROOT / "meta" / "project.json"
DEFAULT_OUTPUT = ROOT / "README.md"
PACKAGE_INIT = ROOT / "fuzzy_surgical" / "__init__.py"

REQUIRED_KEYS = (
    "schema_version",
    "name",
    "display_name",
    "version",
    "tagline",
    "about",
    "summary",
    "author",
    "repository",
    "license",
    "python",
    "platforms",
    "features",
    "docs",
    "install",
    "quickstart",
    "cli",
    "controls",
    "safety",
    "development",
    "requirements",
    "community",
    "links",
)

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)\s]+)\)")


class MetadataError(RuntimeError):
    """Raised when the metadata or version source is invalid."""


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise MetadataError(f"metadata file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MetadataError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MetadataError(f"{path} must contain a JSON object")
    return data


def _package_version(path: Path = PACKAGE_INIT) -> str:
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise MetadataError(f"package version file not found: {path}") from exc
    tree = ast.parse(source, filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__version__":
                value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    return value.value
    raise MetadataError(f"__version__ not found in {path}")


def _require_mapping(data: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise MetadataError(f"'{key}' must be an object")
    return value


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MetadataError(f"'{label}' must be a non-empty string")
    return value


def _require_list(value: Any, label: str) -> List[Any]:
    if not isinstance(value, list) or not value:
        raise MetadataError(f"'{label}' must be a non-empty list")
    return value


def _validate_features(features: List[Any]) -> None:
    seen: set = set()
    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            raise MetadataError(f"'features[{index}]' must be an object")
        title = _require_text(feature.get("title"), f"features[{index}].title")
        _require_text(feature.get("summary"), f"features[{index}].summary")
        items = _require_list(feature.get("items"), f"features[{index}].items")
        for item in items:
            _require_text(item, f"features[{index}].items")
        anchor = slug(title)
        if anchor in seen:
            raise MetadataError(f"duplicate feature heading: {title!r}")
        seen.add(anchor)


def _validate_docs(docs: List[Any]) -> None:
    pages: set = set()
    paths: set = set()
    for index, doc in enumerate(docs):
        if not isinstance(doc, dict):
            raise MetadataError(f"'docs[{index}]' must be an object")
        page = _require_text(doc.get("page"), f"docs[{index}].page")
        if page not in SUPPORTED_PAGES:
            raise MetadataError(
                f"'docs[{index}].page' must be one of {', '.join(SUPPORTED_PAGES)}"
            )
        path = _require_text(doc.get("path"), f"docs[{index}].path")
        if not path.startswith("README/") or not path.endswith(".md"):
            raise MetadataError(f"'docs[{index}].path' must live under README/ and end in .md")
        _require_text(doc.get("title"), f"docs[{index}].title")
        _require_text(doc.get("summary"), f"docs[{index}].summary")
        if page in pages:
            raise MetadataError(f"duplicate docs page: {page!r}")
        if path in paths:
            raise MetadataError(f"duplicate docs path: {path!r}")
        pages.add(page)
        paths.add(path)
    if FEATURES_PAGE not in pages:
        raise MetadataError(f"'docs' must include the {FEATURES_PAGE!r} page")


def validate(metadata: Dict[str, Any], version: str) -> None:
    missing = [key for key in REQUIRED_KEYS if key not in metadata]
    if missing:
        raise MetadataError("missing metadata keys: " + ", ".join(missing))
    if metadata["version"] != version:
        raise MetadataError(
            f"version mismatch: meta/project.json has {metadata['version']!r} "
            f"but fuzzy_surgical/__init__.py has {version!r}"
        )
    for key in ("tagline", "about", "summary"):
        _require_text(metadata[key], key)
    author = _require_mapping(metadata, "author")
    if not author.get("name") or not author.get("website"):
        raise MetadataError("'author' requires name and website")
    license_info = _require_mapping(metadata, "license")
    if license_info.get("spdx") != "MIT":
        raise MetadataError("'license.spdx' must be MIT")
    repository = _require_mapping(metadata, "repository")
    for key in ("url", "web_url", "slug"):
        if not repository.get(key):
            raise MetadataError(f"'repository.{key}' is required")
    _validate_features(_require_list(metadata["features"], "features"))
    _validate_docs(_require_list(metadata["docs"], "docs"))
    for section in ("install", "quickstart", "cli", "safety", "development"):
        _require_mapping(metadata, section)
    _require_text(metadata["quickstart"].get("landing_note"), "quickstart.landing_note")
    _require_list(metadata["controls"], "controls")
    _require_list(metadata["requirements"], "requirements")


def _badges(metadata: Dict[str, Any]) -> str:
    repository = metadata["repository"]["web_url"]
    python_requires = metadata["python"]["requires"]
    website = metadata["links"]["website"]
    website_host = urlparse(website).netloc or website
    return "\n".join(
        [
            f"[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)]"
            f"({repository}/blob/main/LICENSE)",
            f"[![Python {python_requires}+](https://img.shields.io/badge/python-"
            f"{python_requires}%2B-blue.svg)]({repository})",
            f"[![Website](https://img.shields.io/badge/website-{website_host}-black.svg)]"
            f"({website})",
        ]
    )


def _features_path(metadata: Dict[str, Any]) -> str:
    for doc in metadata["docs"]:
        if doc["page"] == FEATURES_PAGE:
            return str(doc["path"])
    raise MetadataError(f"'docs' must include the {FEATURES_PAGE!r} page")


def _render_landing(metadata: Dict[str, Any]) -> List[str]:
    features_path = _features_path(metadata)
    author = metadata["author"]
    license_info = metadata["license"]
    repository = metadata["links"]["repository"]
    quickstart = metadata["quickstart"]
    install_commands = metadata["install"]["blocks"][0]["commands"]

    lines: List[str] = [
        MARKER,
        "",
        f"# {metadata['display_name']}",
        "",
        _badges(metadata),
        "",
        metadata["tagline"],
        "",
        metadata["about"],
        "",
        f"**Version:** {metadata['version']}",
        "",
        "## Features",
        "",
    ]
    for feature in metadata["features"]:
        target = f"{features_path}#{slug(feature['title'])}"
        lines.append(f"- **[{feature['title']}]({target})** - {feature['summary']}")
    lines.extend(
        [
            "",
            "## Quick start",
            "",
            code_block("bash", install_commands),
            "",
            quickstart["landing_note"],
            "",
            "## Documentation",
            "",
        ]
    )
    for doc in metadata["docs"]:
        lines.append(f"- [{doc['title']}]({doc['path']}) - {doc['summary']}")
    lines.extend(
        [
            "",
            "## License",
            "",
            f"{license_info['summary']} See [LICENSE](LICENSE) for the full text.",
            "",
            f"{author['name']} - [{author['website']}]({author['website']})",
            "",
            f"Repository: [{repository}]({repository})",
            "",
        ]
    )
    return lines


def render(metadata: Dict[str, Any], version: str) -> str:
    """Render the root README landing page."""

    validate(metadata, version)
    text = "\n".join(_render_landing(metadata)).rstrip() + "\n"
    return text.replace("\n\n\n", "\n\n")


def render_documents(metadata: Dict[str, Any], version: str) -> Dict[str, str]:
    """Render the landing page and every detail page, keyed by repository path."""

    documents = {"README.md": render(metadata, version)}
    documents.update(render_docs(metadata))
    return documents


def check_links(documents: Dict[str, str], root: Path) -> List[str]:
    """Return a problem line for every relative link or anchor that cannot resolve.

    Targets may live inside the pending documents or on disk. External URLs are
    ignored. A resolved target must be a file, and its anchor must name a heading
    or an explicit id.
    """

    cache: Dict[str, Optional[set]] = {}

    def anchors_for(relative: str) -> Optional[set]:
        if relative in documents:
            return anchors(documents[relative])
        if relative in cache:
            return cache[relative]
        path = root / relative
        found: Optional[set] = None
        if path.is_file():
            try:
                found = anchors(path.read_text(encoding="utf-8"))
            except OSError:
                found = set()
        cache[relative] = found
        return found

    problems: List[str] = []
    for relative, text in documents.items():
        base = posixpath.dirname(relative)
        for number, line in enumerate(text.splitlines(), 1):
            for target in LINK_RE.findall(line):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                file_part, _, anchor = target.partition("#")
                if not file_part:
                    if anchor not in anchors(text):
                        problems.append(
                            f"{relative}:{number}: missing anchor '#{anchor}'"
                        )
                    continue
                joined = posixpath.normpath(posixpath.join(base, file_part))
                if joined.startswith("..") or posixpath.isabs(joined):
                    problems.append(
                        f"{relative}:{number}: link escapes the repository: {target}"
                    )
                    continue
                found = anchors_for(joined)
                if found is None:
                    problems.append(f"{relative}:{number}: missing file {joined}")
                elif anchor and anchor not in found:
                    problems.append(
                        f"{relative}:{number}: missing anchor '#{anchor}' in {joined}"
                    )

    landing = documents.get("README.md", "")
    for doc in re.findall(r"\]\((README/[^)#]+\.md)\)", landing):
        if doc not in documents:
            problems.append(f"README.md: links to ungenerated page {doc}")
    return problems


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=str(path.parent), delete=False
    )
    try:
        with handle:
            handle.write(content)
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _read_current(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate README.md and README/DOCS from project metadata."
    )
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail when a page is stale")
    parser.add_argument("--stdout", action="store_true", help="print the landing page")
    args = parser.parse_args(argv)

    try:
        metadata = _read_json(args.metadata)
        version = _package_version()
        documents = render_documents(metadata, version)
    except MetadataError as exc:
        print(f"build_readme: {exc}", file=sys.stderr)
        return 2

    problems = check_links(documents, ROOT)
    if problems:
        for problem in problems:
            print(f"build_readme: {problem}", file=sys.stderr)
        return 1

    if args.stdout:
        sys.stdout.write(documents["README.md"])
        return 0

    single_file = args.output.resolve() != DEFAULT_OUTPUT.resolve()
    targets: Iterable[str]
    if single_file:
        targets = ["README.md"]
    else:
        targets = documents.keys()

    stale: List[str] = []
    for relative in targets:
        path = args.output if single_file else ROOT / relative
        if args.check:
            if _read_current(path) != documents[relative]:
                stale.append(str(path))
            continue
        _write_atomic(path, documents[relative])

    if args.check:
        if stale:
            for path in stale:
                print(f"build_readme: {path} is out of date", file=sys.stderr)
            print(
                "build_readme: run python3 scripts/build_readme.py and commit the result",
                file=sys.stderr,
            )
            return 1
        return 0

    print(f"build_readme: wrote {len(list(targets))} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
