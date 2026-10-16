# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the multi-version Fern site build."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_PATH = Path(__file__).with_name("build_fern_site.py")
SPEC = importlib.util.spec_from_file_location("build_fern_site", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
BUILD = importlib.util.module_from_spec(SPEC)
sys.modules["build_fern_site"] = BUILD
SPEC.loader.exec_module(BUILD)

PAGE = (
    "git checkout ${VSS_DOCS_GIT_REF:-develop}\n"
    "export VSS_RT_VLM_TAG=${VSS_DOCS_SBSA_IMAGE_TAG:-develop-latest-sbsa}\n"
    "image: vss:${VSS_DOCS_IMAGE_TAG:-develop-latest}\n"
    "Keep ${VSS_DATA_DIR} and API path GET /assets/list.\n"
    "![diagram](/assets/images/diagram.png)\n"
    '<img src="/assets/images/diagram.png" />\n'
    "See [quickstart](/vss/getting-started/quickstart#run) and [home](/vss/dev/intro).\n"
    "External https://docs.nvidia.com/vss/x stays.\n"
)
DOCS_YML = """\
instances:
  - url: example.docs.buildwithfern.com/vss

settings:
  substitute-env-vars: false

# comment above the block
versions:
{entries}
redirects:
  - source: "/old"
    destination: "/vss/new"
"""
DEV_ENTRY = "  - display-name: Dev\n    slug: dev\n    path: ../docs/index.yml\n"


def release_entry(version: str) -> str:
    return f'  - display-name: "{version}"\n    slug: "{version}"\n    ref: v{version}\n'


def run_git(root: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c", "user.name=Test",
            "-c", "user.email=test@example.com",
            "-c", "commit.gpgsign=false",
            "-c", "tag.gpgsign=false",
            "-C", str(root),
            *args,
        ],
        check=True,
        capture_output=True,
    )


def make_repo(root: Path, entries: str, page: str = PAGE) -> None:
    (root / "docs/assets/images").mkdir(parents=True)
    (root / "docs/assets/images/diagram.png").write_bytes(b"png")
    (root / "docs/index.yml").write_text("navigation:\n  - page: Page\n    path: ./page.mdx\n")
    (root / "docs/page.mdx").write_text(page, encoding="utf-8")
    (root / "fern").mkdir()
    (root / "fern/docs.yml").write_text(DOCS_YML.format(entries=entries), encoding="utf-8")
    (root / "fern/assets").symlink_to("../docs/assets")
    (root / BUILD.OPENAPI_PATH).parent.mkdir(parents=True)
    (root / BUILD.OPENAPI_PATH).write_text("{}\n", encoding="utf-8")
    run_git(root, "init", "-q")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-q", "-m", "init")


def tag_release(root: Path, version: str, marker: str) -> None:
    """Commit a release-only marker line, tag it, then drop it from the checkout."""
    page = root / "docs/page.mdx"
    original = page.read_text(encoding="utf-8")
    page.write_text(original + marker + "\n", encoding="utf-8")
    run_git(root, "commit", "-q", "-am", f"release {version}")
    run_git(root, "tag", f"v{version}")
    page.write_text(original, encoding="utf-8")
    run_git(root, "commit", "-q", "-am", "back to dev")


def expect_error(fragment: str, call) -> None:
    try:
        call()
    except BUILD.BuildError as error:
        assert fragment in str(error), str(error)
    else:
        raise AssertionError(f"expected BuildError containing {fragment!r}")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_dev_only_build_stamps_develop_values() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / "repo", Path(directory) / "out"
        source.mkdir()
        make_repo(source, DEV_ENTRY)

        summary = BUILD.build(source, output)

        page = read(output / "docs/page.mdx")
        assert "git checkout develop\n" in page
        assert "VSS_RT_VLM_TAG=develop-latest-sbsa\n" in page
        assert "image: vss:develop-latest\n" in page
        assert "${VSS_DATA_DIR}" in page
        assert "](/assets/images/diagram.png)" in page
        assert "](/vss/getting-started/quickstart#run)" in page
        assert (output / "fern/assets").is_symlink()
        assert (output / BUILD.OPENAPI_PATH).is_file()
        docs_yml = read(output / "fern/docs.yml")
        assert 'path: "../docs/index.yml"' in docs_yml
        assert 'source: "/old"' in docs_yml
        assert summary == ["Dev (default): develop / develop-latest / develop-latest-sbsa"]
        assert "${VSS_DOCS_GIT_REF:-develop}" in read(source / "docs/page.mdx")


def test_release_tags_become_prepared_path_versions() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / "repo", Path(directory) / "out"
        source.mkdir()
        make_repo(source, release_entry("1.2.0") + DEV_ENTRY)
        tag_release(source, "1.2.0", "RELEASE-1.2.0")

        BUILD.build(source, output)

        release_root = output / "fern/versions/v1.2.0"
        release_page = read(release_root / "docs/page.mdx")
        assert "RELEASE-1.2.0" in release_page
        assert "git checkout v1.2.0\n" in release_page
        assert "VSS_RT_VLM_TAG=1.2.0-sbsa\n" in release_page
        assert "image: vss:1.2.0\n" in release_page
        assert "](/versions/v1.2.0/docs/assets/images/diagram.png)" in release_page
        assert 'src="/versions/v1.2.0/docs/assets/images/diagram.png"' in release_page
        assert "GET /assets/list" in release_page
        assert "](/vss/getting-started/quickstart#run)" in release_page
        assert (release_root / "docs/assets/images/diagram.png").is_file()
        assert (release_root / BUILD.OPENAPI_PATH).is_file()

        dev_page = read(output / "docs/page.mdx")
        assert "RELEASE-1.2.0" not in dev_page
        assert "](/vss/dev/getting-started/quickstart#run)" in dev_page
        assert "](/vss/dev/intro)" in dev_page
        assert "https://docs.nvidia.com/vss/x" in dev_page

        docs_yml = read(output / "fern/docs.yml")
        release_at = docs_yml.index('path: "./versions/v1.2.0/docs/index.yml"')
        assert release_at < docs_yml.index('path: "../docs/index.yml"')
        assert "ref:" not in docs_yml


def test_missing_tag_is_skipped_and_dev_becomes_default() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / "repo", Path(directory) / "out"
        source.mkdir()
        make_repo(source, release_entry("9.9.9") + DEV_ENTRY)

        summary = BUILD.build(source, output)

        assert "warning: skipped v9.9.9: tag does not exist yet" in summary
        assert not (output / "fern/versions").exists()
        assert "](/vss/getting-started/quickstart#run)" in read(output / "docs/page.mdx")


def test_no_releases_builds_only_the_checkout() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / "repo", Path(directory) / "out"
        source.mkdir()
        make_repo(source, release_entry("1.2.0") + DEV_ENTRY)
        tag_release(source, "1.2.0", "RELEASE-1.2.0")

        BUILD.build(source, output, include_releases=False)

        assert not (output / "fern/versions").exists()
        assert "./versions/" not in read(output / "fern/docs.yml")


def test_release_preview_relabels_checkout_as_default() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / "repo", Path(directory) / "out"
        source.mkdir()
        make_repo(source, release_entry("1.3.0") + release_entry("1.2.0") + DEV_ENTRY)
        tag_release(source, "1.2.0", "RELEASE-1.2.0")
        tag_release(source, "1.3.0", "RELEASE-1.3.0")

        BUILD.build(source, output, release="1.3.0")

        page = read(output / "docs/page.mdx")
        assert "git checkout v1.3.0\n" in page
        assert "image: vss:1.3.0\n" in page
        assert "](/vss/getting-started/quickstart#run)" in page
        assert not (output / "fern/versions/v1.3.0").exists()
        older = read(output / "fern/versions/v1.2.0/docs/page.mdx")
        assert "](/vss/1.2.0/getting-started/quickstart#run)" in older

        docs_yml = read(output / "fern/docs.yml")
        assert docs_yml.index('"1.3.0 (preview)"') < docs_yml.index('display-name: "1.2.0"')
        assert '"Dev"' not in docs_yml


def test_require_tag_fails_when_release_is_not_listed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "repo"
        source.mkdir()
        make_repo(source, DEV_ENTRY)
        tag_release(source, "1.2.0", "RELEASE-1.2.0")

        expect_error(
            "v1.2.0 is not listed",
            lambda: BUILD.build(source, Path(directory) / "out", require_tag="v1.2.0"),
        )


def test_rejects_unknown_or_malformed_markers_and_fern_escapes() -> None:
    cases = {
        "${VSS_DOCS_GIT_REFS:-develop}\n": "unknown docs marker",
        "${VSS_DOCS_GIT_REF}\n": "must be written ${VSS_DOCS_GIT_REF:-default}",
        "${VSS_DOCS_GIT_REF:-}\n": "must be written",
        r"\$\{VSS_DATA_DIR\}" + "\n": "Fern escape",
    }
    for page, fragment in cases.items():
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "repo"
            source.mkdir()
            make_repo(source, DEV_ENTRY, page=page)
            expect_error(fragment, lambda: BUILD.build(source, Path(directory) / "out"))


def test_rejects_invalid_versions_blocks() -> None:
    cases = {
        DEV_ENTRY + "    hidden: true\n": "unsupported version key 'hidden'",
        DEV_ENTRY.replace("    slug: dev\n", ""): "needs display-name and slug",
        release_entry("1.2.0"): "exactly one version must be a path: entry",
        DEV_ENTRY + "  - display-name: Next\n    slug: next\n    ref: develop\n": (
            "not a release tag"
        ),
        DEV_ENTRY + release_entry("1.2.0").replace('slug: "1.2.0"', "slug: dev"): "duplicate",
    }
    for entries, fragment in cases.items():
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "repo"
            source.mkdir()
            make_repo(source, entries)
            expect_error(fragment, lambda: BUILD.build(source, Path(directory) / "out"))


def test_rejects_existing_output_and_tags_without_index() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / "repo", Path(directory) / "out"
        source.mkdir()
        make_repo(source, release_entry("1.2.0") + DEV_ENTRY)
        output.mkdir()
        expect_error("output path already exists", lambda: BUILD.build(source, output))

        output.rmdir()
        run_git(source, "rm", "-q", "docs/index.yml")
        run_git(source, "commit", "-q", "-m", "old layout")
        run_git(source, "tag", "v1.2.0")
        run_git(source, "revert", "--no-edit", "HEAD")
        expect_error("has no docs/index.yml", lambda: BUILD.build(source, output))


if __name__ == "__main__":
    test_dev_only_build_stamps_develop_values()
    test_release_tags_become_prepared_path_versions()
    test_missing_tag_is_skipped_and_dev_becomes_default()
    test_no_releases_builds_only_the_checkout()
    test_release_preview_relabels_checkout_as_default()
    test_require_tag_fails_when_release_is_not_listed()
    test_rejects_unknown_or_malformed_markers_and_fern_escapes()
    test_rejects_invalid_versions_blocks()
    test_rejects_existing_output_and_tags_without_index()
    print("build_fern_site tests passed")
