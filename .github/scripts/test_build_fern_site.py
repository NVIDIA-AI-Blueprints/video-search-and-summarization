# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the multi-version Fern site build."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

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
    "![diagram](./assets/images/diagram.png)\n"
    '<img src="./assets/images/diagram.png" />\n'
    "See [quickstart](/getting-started/quickstart#run) and [home](/intro).\n"
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
STATIC_ENTRY = "  - display-name: Archives\n    slug: archives\n    path: ./versions/archives.yml\n"
STATIC_NAV = "navigation:\n  - page: Archives\n    path: ./archives.mdx\n    slug: /\n"
STATIC_PAGE = "Earlier releases: ${VSS_DOCS_GIT_REF:-develop} stays as written.\n"


def add_static_version(root: Path) -> None:
    (root / "fern/versions").mkdir()
    (root / "fern/versions/archives.yml").write_text(STATIC_NAV, encoding="utf-8")
    (root / "fern/versions/archives.mdx").write_text(STATIC_PAGE, encoding="utf-8")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-q", "-m", "archives")


def release_entry(version: str, ref: str | None = None) -> str:
    return (
        f'  - display-name: "{version}"\n    slug: "{version}"\n    ref: {ref or "v" + version}\n'
    )


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
    (root / BUILD.OPENAPI_PATH).parent.mkdir(parents=True)
    (root / BUILD.OPENAPI_PATH).write_text("{}\n", encoding="utf-8")
    run_git(root, "init", "-q")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-q", "-m", "init")


def tag_release(root: Path, version: str, marker: str, tag: str | None = None) -> None:
    """Commit a release-only marker line, tag it, then drop it from the checkout."""
    page = root / "docs/page.mdx"
    original = page.read_text(encoding="utf-8")
    page.write_text(original + marker + "\n", encoding="utf-8")
    run_git(root, "commit", "-q", "-am", f"release {version}")
    run_git(root, "tag", tag or f"v{version}")
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
        assert "](./assets/images/diagram.png)" in page
        assert "](/getting-started/quickstart#run)" in page
        assert (output / BUILD.OPENAPI_PATH).is_file()
        docs_yml = read(output / "fern/docs.yml")
        assert 'path: "../docs/index.yml"' in docs_yml
        assert 'source: "/old"' in docs_yml
        assert summary == ["Dev (default): develop / develop-latest / develop-latest-sbsa"]
        assert "${VSS_DOCS_GIT_REF:-develop}" in read(source / "docs/page.mdx")


def test_nightly_preview_pins_tag_and_images_and_keeps_other_versions() -> None:
    for annotated in (False, True):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "repo", Path(directory) / "out"
            source.mkdir()
            make_repo(source, release_entry("1.2.0") + DEV_ENTRY + STATIC_ENTRY)
            tag_release(source, "1.2.0", "RELEASE-ONLY")
            add_static_version(source)
            nightly = "nightly-20261025-1" if annotated else "nightly-20261025"
            args = ("-a", "-m", "nightly build") if annotated else ()
            run_git(source, "tag", *args, nightly)

            summary = BUILD.build(source, output, nightly=nightly)

            page = read(output / "docs/page.mdx")
            assert f"git checkout {nightly}\n" in page
            assert f"image: vss:{nightly}\n" in page
            assert f"VSS_RT_VLM_TAG={nightly}-sbsa\n" in page
            assert "${VSS_DATA_DIR}" in page
            assert "RELEASE-ONLY" not in page
            assert "VSS_DOCS_" not in page
            versions = read(output / "fern/docs.yml")
            assert versions.index(f'"{nightly} (preview)"') < versions.index('"1.2.0"')
            assert f'slug: "{nightly}"' in versions
            assert 'slug: "dev"' not in versions
            assert "image: vss:1.2.0\n" in read(output / "fern/releases/v1.2.0/docs/page.mdx")
            assert read(output / "fern/versions/archives.mdx") == STATIC_PAGE
            assert summary[0] == f"{nightly} (preview) (default): {nightly} / {nightly} / {nightly}-sbsa"
            assert read(source / "docs/page.mdx") == PAGE


def test_nightly_preview_requires_matching_tag_and_exclusive_mode() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / "repo", Path(directory) / "out"
        source.mkdir()
        make_repo(source, DEV_ENTRY)
        nightly = "nightly-20261025"
        expect_error("tag is not available locally", lambda: BUILD.build(source, output, nightly=nightly))
        run_git(source, "tag", nightly)
        expect_error(
            "mutually exclusive",
            lambda: BUILD.build(source, output, nightly=nightly, release="1.2.0"),
        )
        for invalid in ("develop", "docs/nightly-20261025", "nightly-", "nightly-test/nested"):
            expect_error("not a flat nightly-* tag", lambda: BUILD.build(source, output, nightly=invalid))
        run_git(source, "commit", "--allow-empty", "-q", "-m", "later commit")
        # A same-named branch must not hide a mismatch with the tag.
        run_git(source, "branch", nightly)
        expect_error("HEAD must match", lambda: BUILD.build(source, output, nightly=nightly))
        assert not output.exists()


def test_nightly_preview_cli_builds_only_checkout() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / "repo", Path(directory) / "out"
        source.mkdir()
        make_repo(source, release_entry("1.2.0") + DEV_ENTRY)
        run_git(source, "tag", "nightly-20261025")
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--source", str(source), "--output", str(output),
             "--nightly", "nightly-20261025", "--no-releases"],
            text=True, capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        assert "nightly-20261025 (preview) (default)" in result.stdout
        assert "./releases/" not in read(output / "fern/docs.yml")


def test_release_tags_become_prepared_path_versions() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / "repo", Path(directory) / "out"
        source.mkdir()
        make_repo(source, release_entry("1.2.0") + DEV_ENTRY)
        tag_release(source, "1.2.0", "RELEASE-1.2.0")

        BUILD.build(source, output)

        release_root = output / "fern/releases/v1.2.0"
        release_page = read(release_root / "docs/page.mdx")
        assert "RELEASE-1.2.0" in release_page
        assert "git checkout v1.2.0\n" in release_page
        assert "VSS_RT_VLM_TAG=1.2.0-sbsa\n" in release_page
        assert "image: vss:1.2.0\n" in release_page
        assert "](./assets/images/diagram.png)" in release_page
        assert 'src="./assets/images/diagram.png"' in release_page
        assert "GET /assets/list" in release_page
        assert "](/getting-started/quickstart#run)" in release_page
        assert (release_root / "docs/assets/images/diagram.png").is_file()
        assert (release_root / BUILD.OPENAPI_PATH).is_file()

        dev_page = read(output / "docs/page.mdx")
        assert "RELEASE-1.2.0" not in dev_page
        assert "](/getting-started/quickstart#run)" in dev_page
        assert "](/intro)" in dev_page
        assert "https://docs.nvidia.com/vss/x" in dev_page

        docs_yml = read(output / "fern/docs.yml")
        release_at = docs_yml.index('path: "./releases/v1.2.0/docs/index.yml"')
        assert release_at < docs_yml.index('path: "../docs/index.yml"')
        assert "ref:" not in docs_yml


def test_missing_tag_is_skipped_and_dev_becomes_default() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / "repo", Path(directory) / "out"
        source.mkdir()
        make_repo(source, release_entry("9.9.9") + DEV_ENTRY)

        summary = BUILD.build(source, output)

        assert "warning: skipped v9.9.9: tag does not exist yet" in summary
        assert not (output / "fern/releases").exists()
        assert "](/getting-started/quickstart#run)" in read(output / "docs/page.mdx")


def test_no_releases_builds_only_the_checkout() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / "repo", Path(directory) / "out"
        source.mkdir()
        make_repo(source, release_entry("1.2.0") + DEV_ENTRY)
        tag_release(source, "1.2.0", "RELEASE-1.2.0")

        BUILD.build(source, output, include_releases=False)

        assert not (output / "fern/releases").exists()
        assert "./releases/" not in read(output / "fern/docs.yml")


def test_fetch_skips_only_confirmed_missing_remote_tags() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, remote = Path(directory) / "repo", Path(directory) / "remote"
        source.mkdir()
        remote.mkdir()
        make_repo(source, release_entry("1.2.0") + release_entry("9.9.9") + DEV_ENTRY)
        make_repo(remote, DEV_ENTRY)
        tag_release(remote, "1.2.0", "REMOTE-RELEASE")
        run_git(source, "remote", "add", "origin", str(remote))

        output = Path(directory) / "out"
        summary = BUILD.build(source, output, fetch=True)

        assert "warning: skipped v9.9.9: tag does not exist yet" in summary
        assert "REMOTE-RELEASE" in read(output / "fern/releases/v1.2.0/docs/page.mdx")
        assert not (output / "fern/releases/v9.9.9").exists()


def test_remote_lookup_failure_aborts_build() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / "repo", Path(directory) / "out"
        source.mkdir()
        make_repo(source, release_entry("1.2.0") + DEV_ENTRY)
        run_git(source, "remote", "add", "origin", str(Path(directory) / "unreachable.git"))

        expect_error(
            "v1.2.0: remote lookup failed:",
            lambda: BUILD.build(source, output, fetch=True),
        )
        assert not output.exists()


def test_fetch_failure_after_successful_lookup_aborts_build() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, remote = Path(directory) / "repo", Path(directory) / "remote"
        source.mkdir()
        remote.mkdir()
        make_repo(source, release_entry("1.2.0") + DEV_ENTRY)
        make_repo(remote, DEV_ENTRY)
        tag_release(remote, "1.2.0", "REMOTE-RELEASE")
        run_git(source, "remote", "add", "origin", str(remote))
        original_git = BUILD.git

        def fail_fetch(source, *args, **kwargs):
            if args[0] == "fetch":
                return subprocess.CompletedProcess(args, 128, "", "connection interrupted")
            return original_git(source, *args, **kwargs)

        output = Path(directory) / "out"
        with patch.object(BUILD, "git", side_effect=fail_fetch):
            expect_error(
                "v1.2.0: fetch failed: connection interrupted",
                lambda: BUILD.build(source, output, fetch=True),
            )
        assert not output.exists()


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
        assert "](/getting-started/quickstart#run)" in page
        assert not (output / "fern/releases/v1.3.0").exists()
        older = read(output / "fern/releases/v1.2.0/docs/page.mdx")
        assert "](/getting-started/quickstart#run)" in older

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


def test_docs_tag_is_built_with_its_release_values() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / "repo", Path(directory) / "out"
        source.mkdir()
        make_repo(source, release_entry("1.2.0", "docs-v1.2.0-1") + DEV_ENTRY)
        tag_release(source, "1.2.0", "DOCS-REFRESH", tag="docs-v1.2.0-1")

        summary = BUILD.build(source, output)

        page = read(output / "fern/releases/docs-v1.2.0-1/docs/page.mdx")
        assert "DOCS-REFRESH" in page
        assert "git checkout v1.2.0\n" in page
        assert "VSS_RT_VLM_TAG=1.2.0-sbsa\n" in page
        assert "image: vss:1.2.0\n" in page
        docs_yml = read(output / "fern/docs.yml")
        assert 'path: "./releases/docs-v1.2.0-1/docs/index.yml"' in docs_yml
        assert 'slug: "1.2.0"' in docs_yml
        assert "1.2.0 (default): v1.2.0 / 1.2.0 / 1.2.0-sbsa" in summary


def test_release_preview_replaces_a_docs_tag_of_the_same_release() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / "repo", Path(directory) / "out"
        source.mkdir()
        make_repo(source, release_entry("1.2.0", "docs-v1.2.0-1") + DEV_ENTRY)
        tag_release(source, "1.2.0", "DOCS-REFRESH", tag="docs-v1.2.0-1")

        BUILD.build(source, output, release="1.2.0")

        assert not (output / "fern/releases").exists()
        assert '"1.2.0 (preview)"' in read(output / "fern/docs.yml")


def test_require_tag_checks_a_docs_tag_strictly() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "repo"
        source.mkdir()
        unscoped = "[x](/vss/getting-started/quickstart)\n"
        make_repo(source, release_entry("1.2.0", "docs-v1.2.0-1") + DEV_ENTRY, page=unscoped)
        tag_release(source, "1.2.0", "DOCS-REFRESH", tag="docs-v1.2.0-1")
        (source / "docs/page.mdx").write_text(PAGE, encoding="utf-8")
        run_git(source, "commit", "-q", "-am", "scoped links")

        summary = BUILD.build(source, Path(directory) / "out")
        assert any(line.startswith("warning: docs-v1.2.0-1 has 1 link(s)") for line in summary)

        expect_error(
            "docs-v1.2.0-1: links must omit the /vss base",
            lambda: BUILD.build(source, Path(directory) / "out2", require_tag="docs-v1.2.0-1"),
        )


def test_require_tag_fails_when_a_listed_docs_tag_is_missing() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "repo"
        source.mkdir()
        make_repo(source, release_entry("1.2.0", "docs-v1.2.0-1") + DEV_ENTRY)

        expect_error(
            "docs-v1.2.0-1 is listed in fern/docs.yml but the tag is not available",
            lambda: BUILD.build(source, Path(directory) / "out", require_tag="docs-v1.2.0-1"),
        )
        expect_error(
            "--require-tag 'v1.2.0-docs1' is not",
            lambda: BUILD.build(source, Path(directory) / "out", require_tag="v1.2.0-docs1"),
        )


def test_static_version_is_built_as_is_and_kept_in_previews() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "repo"
        source.mkdir()
        make_repo(source, release_entry("1.2.0") + DEV_ENTRY + STATIC_ENTRY)
        add_static_version(source)
        tag_release(source, "1.2.0", "RELEASE-1.2.0")

        output = Path(directory) / "out"
        summary = BUILD.build(source, output)

        assert read(output / "fern/versions/archives.mdx") == STATIC_PAGE
        docs_yml = read(output / "fern/docs.yml")
        assert docs_yml.index('"./releases/v1.2.0/docs/index.yml"') < docs_yml.index(
            '"../docs/index.yml"'
        ) < docs_yml.index('path: "./versions/archives.yml"')
        assert "Archives (/archives): static, fern/versions/archives.yml" in summary

        preview = Path(directory) / "preview"
        BUILD.build(source, preview, release="1.3.0")
        docs_yml = read(preview / "fern/docs.yml")
        assert docs_yml.index('"1.3.0 (preview)"') < docs_yml.index('path: "./versions/archives.yml"')

        dev_only = Path(directory) / "dev-only"
        BUILD.build(source, dev_only, include_releases=False)
        assert 'path: "./versions/archives.yml"' in read(dev_only / "fern/docs.yml")


def test_static_version_must_exist() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "repo"
        source.mkdir()
        make_repo(source, DEV_ENTRY + STATIC_ENTRY)
        expect_error(
            "fern/versions/archives.yml does not exist",
            lambda: BUILD.build(source, Path(directory) / "out"),
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


def test_checkout_rejects_unscoped_links_but_release_tags_only_warn() -> None:
    forms = [
        "[x](/vss/getting-started/quickstart)\n",
        "[x](/vss)\n",
        "[x](/vss#anchor)\n",
        "[x](/vss?q=1)\n",
        "[x](</vss/intro>)\n",
        "[x]: /vss/intro\n",
        "[x]: /vss\n",
        '<a href="/vss/intro">x</a>\n',
        "<a href='/vss/intro'>x</a>\n",
        '<a href={"/vss/intro"}>x</a>\n',
        "<a href={'/vss'}>x</a>\n",
        "![x](/assets/images/diagram.png)\n",
        '<img src="/assets/images/diagram.png" />\n',
        "<img src='/assets/images/diagram.png' />\n",
        "[img]: /assets/images/diagram.png\n",
    ]
    for form in forms:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "repo"
            source.mkdir()
            make_repo(source, DEV_ENTRY, page=form)
            expect_error("page.mdx:1", lambda: BUILD.build(source, Path(directory) / "out"))

    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / "repo", Path(directory) / "out"
        source.mkdir()
        make_repo(source, release_entry("1.2.0") + DEV_ENTRY, page=forms[0])
        tag_release(source, "1.2.0", "RELEASE-1.2.0")
        run_git(source, "rm", "-q", "-f", "docs/page.mdx")
        (source / "docs/page.mdx").write_text(PAGE, encoding="utf-8")
        run_git(source, "add", "docs/page.mdx")
        run_git(source, "commit", "-q", "-m", "scoped links")

        summary = BUILD.build(source, output)

        assert any(line.startswith("warning: v1.2.0 has 1 link(s)") for line in summary)
        assert (output / "fern/releases/v1.2.0/docs/page.mdx").is_file()

        expect_error(
            "v1.2.0: links must omit the /vss base",
            lambda: BUILD.build(source, Path(directory) / "out2", require_tag="v1.2.0"),
        )


def test_link_guard_allows_scoped_links_and_look_alikes() -> None:
    page = (
        "[root](/) [anchor](/#top) [page](/getting-started/quickstart)\n"
        "[agent](/vss-agent/intro) [img](./assets/images/diagram.png)\n"
        '<a href="/vision-agent">x</a> <img src="../assets/x.png" />\n'
        "External https://docs.nvidia.com/vss/x and `/vss/` in code.\n"
        "API GET /assets/list and `/assets/{asset_id}/index`.\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "repo"
        source.mkdir()
        make_repo(source, DEV_ENTRY, page=page)
        BUILD.build(source, Path(directory) / "out")


def test_link_guard_uses_the_docs_yml_base_path() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "repo"
        source.mkdir()
        make_repo(source, DEV_ENTRY, page="[x](/vss/intro)\n[y](/other/intro)\n")
        docs_yml = source / "fern/docs.yml"
        docs_yml.write_text(
            read(docs_yml).replace("buildwithfern.com/vss", "buildwithfern.com/other"),
            encoding="utf-8",
        )
        expect_error("omit the /other base", lambda: BUILD.build(source, Path(directory) / "out"))
        expect_error("page.mdx:2", lambda: BUILD.build(source, Path(directory) / "out2"))
        try:
            BUILD.build(source, Path(directory) / "out3")
        except BUILD.BuildError as error:
            assert "page.mdx:1" not in str(error), str(error)


def test_rejects_invalid_versions_blocks() -> None:
    cases = {
        DEV_ENTRY + "    hidden: true\n": "unsupported version key 'hidden'",
        DEV_ENTRY.replace("    slug: dev\n", ""): "needs display-name and slug",
        release_entry("1.2.0"): "exactly one version must be `path: ../docs/index.yml`",
        DEV_ENTRY.replace("../docs/index.yml", "./other.yml"): "exactly one version must be",
        STATIC_ENTRY + DEV_ENTRY: "first (default) version cannot be a static version",
        DEV_ENTRY + STATIC_ENTRY.replace("./versions/", "../versions/"): "must be a navigation file",
        DEV_ENTRY + STATIC_ENTRY.replace("./versions/", "./releases/"): "not fern/releases/",
        DEV_ENTRY + "  - display-name: Next\n    slug: next\n    ref: develop\n": (
            "not a release tag"
        ),
        DEV_ENTRY + release_entry("1.2.0").replace('slug: "1.2.0"', "slug: dev"): "duplicate",
    }
    for ref in ("docs-v1.2.0", "docs-v1.2.0-0", "v1.2.0-docs1", "docs-1.2.0-1", "v1.2.0rc0"):
        cases[DEV_ENTRY + release_entry("1.2.0", ref)] = "is not a release tag"
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
    test_nightly_preview_pins_tag_and_images_and_keeps_other_versions()
    test_nightly_preview_requires_matching_tag_and_exclusive_mode()
    test_nightly_preview_cli_builds_only_checkout()
    test_release_tags_become_prepared_path_versions()
    test_missing_tag_is_skipped_and_dev_becomes_default()
    test_no_releases_builds_only_the_checkout()
    test_fetch_skips_only_confirmed_missing_remote_tags()
    test_remote_lookup_failure_aborts_build()
    test_fetch_failure_after_successful_lookup_aborts_build()
    test_release_preview_relabels_checkout_as_default()
    test_require_tag_fails_when_release_is_not_listed()
    test_docs_tag_is_built_with_its_release_values()
    test_release_preview_replaces_a_docs_tag_of_the_same_release()
    test_require_tag_checks_a_docs_tag_strictly()
    test_require_tag_fails_when_a_listed_docs_tag_is_missing()
    test_static_version_is_built_as_is_and_kept_in_previews()
    test_static_version_must_exist()
    test_rejects_unknown_or_malformed_markers_and_fern_escapes()
    test_checkout_rejects_unscoped_links_but_release_tags_only_warn()
    test_link_guard_allows_scoped_links_and_look_alikes()
    test_link_guard_uses_the_docs_yml_base_path()
    test_rejects_invalid_versions_blocks()
    test_rejects_existing_output_and_tags_without_index()
    print("build_fern_site tests passed")
