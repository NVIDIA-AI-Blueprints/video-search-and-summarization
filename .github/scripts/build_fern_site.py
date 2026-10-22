# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build the Fern input tree for every published docs version.

The committed ``fern/docs.yml`` lists the published versions: one ``ref:``
entry per release tag (``vX.Y.Z``) and exactly one ``path:`` entry, the
checkout being built (Dev). Fern never sees those refs. This script writes a
temporary, repository-shaped tree in which every version is a ``path:``
version:

* the checkout's ``docs/``, ``fern/`` and OpenAPI spec are copied to the
  output root;
* each listed tag's ``docs/`` and OpenAPI spec are extracted with
  ``git archive`` into ``fern/versions/<tag>/``;
* in each version, the ``${VSS_DOCS_*:-default}`` markers are replaced with
  that version's checkout ref and image tags. Links and images need no
  rewriting: page links omit the site base and version so Fern scopes them to
  the reader's version, and images are page-relative. Unscoped forms fail the
  build in the checkout and in the release being published (``--require-tag``);
  in older listed tags, which cannot change, they are reported as warnings;
* the ``versions:`` block of the output ``fern/docs.yml`` is rewritten to the
  prepared copies, default (first) version first.

Every Fern command (``check``, ``docs dev``, ``generate``) runs inside the
output tree. The source checkout is never modified. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = Path(
    "services/analytics/video-analytics-api/src/app/specification/openapi.json"
)
DOCS_YML = Path("fern/docs.yml")
VERSIONS_DIR = Path("fern/versions")
MARKDOWN_SUFFIXES = frozenset({".md", ".mdx"})

GIT_REF = "VSS_DOCS_GIT_REF"
IMAGE_TAG = "VSS_DOCS_IMAGE_TAG"
SBSA_IMAGE_TAG = "VSS_DOCS_SBSA_IMAGE_TAG"
DEV_VALUES = {
    GIT_REF: "develop",
    IMAGE_TAG: "develop-latest",
    SBSA_IMAGE_TAG: "develop-latest-sbsa",
}

RELEASE_PATTERN = re.compile(r"\d+\.\d+\.\d+")
RELEASE_TAG_PATTERN = re.compile(r"v\d+\.\d+\.\d+")
MARKER_PATTERN = re.compile(r"\$\{(?P<name>VSS_DOCS_\w*)(?P<rest>[^}\n]*)\}")
FERN_ESCAPE_PATTERN = re.compile(r"\\\$\\\{")
VERSION_KEYS = ("display-name", "slug", "path", "ref", "availability")


class BuildError(RuntimeError):
    """The source tree or the requested build cannot produce a valid site."""


@dataclass(frozen=True)
class Version:
    display_name: str
    slug: str
    path: str | None = None
    ref: str | None = None
    availability: str | None = None


def release_values(release: str) -> dict[str, str]:
    """Checkout ref and image tags a release's docs name."""
    return {
        GIT_REF: f"v{release}",
        IMAGE_TAG: release,
        SBSA_IMAGE_TAG: f"{release}-sbsa",
    }


# --- fern/docs.yml ------------------------------------------------------------


def _scalar(raw: str, location: str) -> str:
    raw = raw.strip()
    if raw.startswith('"'):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise BuildError(f"{location}: cannot parse {raw}") from error
        return str(value)
    if raw.startswith("'"):
        if not raw.endswith("'") or len(raw) < 2:
            raise BuildError(f"{location}: cannot parse {raw}")
        return raw[1:-1].replace("''", "'")
    return re.sub(r"\s+#.*$", "", raw)


def block_span(text: str, key: str) -> tuple[int, int]:
    """Character span of a top-level ``key:`` block (key line included)."""
    match = re.search(rf"^{key}:[ \t]*\n", text, re.MULTILINE)
    if match is None:
        raise BuildError(f"{DOCS_YML}: no top-level {key}: block")
    end = match.end()
    for line in text[match.end() :].splitlines(keepends=True):
        if line.strip() and not line[0].isspace():
            break
        end += len(line)
    return match.start(), end


def parse_versions(text: str) -> list[Version]:
    """Parse the ``versions:`` block: a list of flat ``key: value`` maps."""
    start, end = block_span(text, "versions")
    line_offset = text.count("\n", 0, start) + 1
    entries: list[dict[str, str]] = []
    block = text[start:end].splitlines()[1:]
    for number, line in enumerate(block, start=line_offset + 1):
        location = f"{DOCS_YML}:{number}"
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        item = re.fullmatch(r"\s*-\s+(?P<pair>.*)", line)
        if item is not None:
            entries.append({})
            stripped = item.group("pair")
        elif not entries:
            raise BuildError(f"{location}: expected a list item under versions:")
        pair = re.fullmatch(r"(?P<key>[\w-]+):\s*(?P<value>.*)", stripped)
        if pair is None:
            raise BuildError(f"{location}: expected `key: value`, got {stripped!r}")
        key = pair.group("key")
        if key not in VERSION_KEYS:
            raise BuildError(f"{location}: unsupported version key {key!r}")
        if key in entries[-1]:
            raise BuildError(f"{location}: duplicate version key {key!r}")
        entries[-1][key] = _scalar(pair.group("value"), location)

    versions = [
        Version(
            display_name=entry.get("display-name", ""),
            slug=entry.get("slug", ""),
            path=entry.get("path"),
            ref=entry.get("ref"),
            availability=entry.get("availability"),
        )
        for entry in entries
    ]
    validate_versions(versions)
    return versions


def validate_versions(versions: list[Version]) -> None:
    if not versions:
        raise BuildError(f"{DOCS_YML}: versions: is empty")
    slugs: set[str] = set()
    for version in versions:
        label = version.display_name or version.slug or "<unnamed>"
        if not version.display_name or not version.slug:
            raise BuildError(f"{DOCS_YML}: version {label!r} needs display-name and slug")
        if (version.path is None) == (version.ref is None):
            raise BuildError(f"{DOCS_YML}: version {label!r} must set exactly one of path or ref")
        if version.ref is not None and not RELEASE_TAG_PATTERN.fullmatch(version.ref):
            raise BuildError(
                f"{DOCS_YML}: version {label!r} ref {version.ref!r} is not a release tag (vX.Y.Z)"
            )
        if version.slug in slugs:
            raise BuildError(f"{DOCS_YML}: duplicate version slug {version.slug!r}")
        slugs.add(version.slug)
    if sum(version.path is not None for version in versions) != 1:
        raise BuildError(f"{DOCS_YML}: exactly one version must be a path: entry (the checkout)")


def render_versions(versions: list[Version]) -> str:
    lines = ["versions:\n"]
    for version in versions:
        fields = {
            "display-name": version.display_name,
            "slug": version.slug,
            "path": version.path,
            "availability": version.availability,
        }
        first = True
        for key, value in fields.items():
            if value is None:
                continue
            lines.append(f"  {'- ' if first else '  '}{key}: {json.dumps(value)}\n")
            first = False
    return "".join(lines) + "\n"


def site_base_path(text: str) -> str:
    """URL base path of the first ``instances:`` url, e.g. ``/vss``, or ``""``."""
    start, end = block_span(text, "instances")
    match = re.search(r"^\s*-?\s*url:\s*(?P<url>.+)$", text[start:end], re.MULTILINE)
    if match is None:
        raise BuildError(f"{DOCS_YML}: no instances url")
    _host, _, path = _scalar(match.group("url"), f"{DOCS_YML} instances").partition("/")
    path = path.strip("/")
    return f"/{path}" if path else ""


# --- content transforms -------------------------------------------------------


def iter_markdown(root: Path):
    for path in sorted(root.rglob("*")):
        if (
            path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() in MARKDOWN_SUFFIXES
        ):
            yield path


def stamp_text(text: str, values: dict[str, str], location: str) -> str:
    """Replace ``${VSS_DOCS_*:-default}`` markers; reject anything else in that namespace."""
    issues: list[str] = []

    def replace_marker(match: re.Match[str]) -> str:
        name, rest = match.group("name"), match.group("rest")
        if name not in values:
            issues.append(f"unknown docs marker ${{{name}...}}")
        elif not rest.startswith(":-") or len(rest) == 2:
            issues.append(f"marker ${{{name}{rest}}} must be written ${{{name}:-default}}")
        else:
            return values[name]
        return match.group(0)

    stamped = MARKER_PATTERN.sub(replace_marker, text)
    if FERN_ESCAPE_PATTERN.search(text):
        issues.append(r"Fern escape \$\{ renders literally; write ${NAME}")
    if issues:
        raise BuildError("\n".join(f"{location}: {issue}" for issue in issues))
    return stamped


def unscoped_link_pattern(base: str) -> re.Pattern[str]:
    """Links that name the site base, and images rooted at ``/assets/``.

    Covers Markdown links and images, reference definitions, and ``href``/``src``
    attributes (either quote, optionally inside ``{...}``). The base matches
    alone (``/vss``) or followed by a path, anchor or query.
    """
    targets = ["assets/"]
    if base:
        targets.insert(0, rf"{re.escape(base.strip('/'))}(?=[/#?)\"'>\s]|$)")
    target = rf"/(?:{'|'.join(targets)})"
    lead = (
        r"\]\(\s*<?"
        r"|(?:href|src)\s*=\s*\{?\s*[\"']"
        r"|^\s*\[[^\]]+\]:\s*<?"
    )
    return re.compile(rf"(?:{lead}){target}", re.MULTILINE)


def unscoped_links(text: str, location: str, pattern: re.Pattern[str]) -> list[str]:
    """Locations of links and images that Fern cannot scope to a version."""
    return [
        f"{location}:{number}: {line.strip()[:80]}"
        for number, line in enumerate(text.splitlines(), start=1)
        if pattern.search(line)
    ]


def transform_version(
    root: Path, values: dict[str, str], *, base: str, strict_links: bool, label: str
) -> list[str]:
    """Stamp one version's ``docs/``; return links Fern cannot scope to a version.

    ``base`` is the site base path from ``fern/docs.yml``. With ``strict_links``
    (the checkout, or the release being published) unscoped links fail the
    build; for an older immutable tag they are only reported.
    """
    pattern = unscoped_link_pattern(base)
    offenders: list[str] = []
    for path in iter_markdown(root / "docs"):
        original = path.read_text(encoding="utf-8")
        location = str(path.relative_to(root))
        text = stamp_text(original, values, location)
        offenders += unscoped_links(original, location, pattern)
        if text != original:
            path.write_text(text, encoding="utf-8")
    if strict_links and offenders:
        named_base = f"omit the {base} base" if base else "omit the site base"
        raise BuildError(
            f"{label}: links must {named_base} and images must be page-relative "
            "(see docs/README.md, Links and images):\n" + "\n".join(offenders)
        )
    return offenders


# --- git ----------------------------------------------------------------------


def git(source: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(source), *args], check=check, text=True, capture_output=True
    )


def tag_exists(source: Path, tag: str) -> bool:
    verify = git(source, "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}", check=False)
    return verify.returncode == 0


def fetch_tag(source: Path, tag: str) -> None:
    remote = git(source, "ls-remote", "--exit-code", "--refs", "origin", f"refs/tags/{tag}", check=False)
    if remote.returncode == 2:
        return  # The remote was reached, but the tag does not exist yet.
    if remote.returncode != 0:
        raise BuildError(f"{tag}: remote lookup failed: {remote.stderr.strip()}")
    refspec = f"refs/tags/{tag}:refs/tags/{tag}"
    fetched = git(source, "fetch", "--no-tags", "--depth=1", "origin", refspec, check=False)
    if fetched.returncode != 0:
        raise BuildError(f"{tag}: fetch failed: {fetched.stderr.strip()}")


def has_path(source: Path, tag: str, path: str) -> bool:
    return git(source, "cat-file", "-e", f"{tag}:{path}", check=False).returncode == 0


def extract_tag(source: Path, tag: str, destination: Path) -> None:
    """``git archive`` a tag's docs and OpenAPI spec into ``destination``."""
    if not has_path(source, tag, "docs/index.yml"):
        raise BuildError(f"{tag}: has no docs/index.yml (it predates versioned docs)")
    paths = ["docs"]
    if has_path(source, tag, OPENAPI_PATH.as_posix()):
        paths.append(OPENAPI_PATH.as_posix())
    destination.mkdir(parents=True)
    with subprocess.Popen(
        ["git", "-C", str(source), "archive", "--format=tar", tag, "--", *paths],
        stdout=subprocess.PIPE,
    ) as process:
        assert process.stdout is not None
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            archive.extractall(destination, filter="data")
    if process.returncode != 0:
        raise BuildError(f"{tag}: git archive failed")


# --- build --------------------------------------------------------------------


def copy_checkout(source: Path, output: Path) -> None:
    output.mkdir(parents=True)
    ignored = shutil.ignore_patterns(".fern", "node_modules")
    for relative in (Path("docs"), Path("fern")):
        shutil.copytree(source / relative, output / relative, symlinks=True, ignore=ignored)
    shutil.rmtree(output / VERSIONS_DIR, ignore_errors=True)
    (output / OPENAPI_PATH).parent.mkdir(parents=True)
    shutil.copy2(source / OPENAPI_PATH, output / OPENAPI_PATH)


def plan_versions(
    listed: list[Version],
    *,
    release: str | None,
    available: set[str],
) -> list[Version]:
    """The versions to build, in order; the first is the default."""
    planned: list[Version] = []
    for version in listed:
        if version.path is not None:
            if release is not None:
                preview = replace(version, display_name=f"{release} (preview)", slug=release)
                planned.insert(0, preview)
            else:
                planned.append(version)
        elif version.ref in available and (release is None or version.ref != f"v{release}"):
            planned.append(version)
    return planned


def build(
    source: Path,
    output: Path,
    *,
    release: str | None = None,
    fetch: bool = False,
    include_releases: bool = True,
    require_tag: str | None = None,
) -> list[str]:
    """Build the output tree; return the summary lines."""
    source = source.resolve()
    output = output.resolve()
    if output.exists():
        raise BuildError(f"output path already exists: {output}")
    if output == source or source in output.parents:
        raise BuildError("output path must be outside the source repository")
    if release is not None and not RELEASE_PATTERN.fullmatch(release):
        raise BuildError(f"--release {release!r} is not X.Y.Z")

    docs_yml = (source / DOCS_YML).read_text(encoding="utf-8")
    listed = parse_versions(docs_yml)
    base = site_base_path(docs_yml)

    summary: list[str] = []
    available: set[str] = set()
    for version in listed:
        if version.ref is None:
            continue
        if not include_releases:
            summary.append(f"skipped {version.ref}: --no-releases")
            continue
        if fetch and not tag_exists(source, version.ref):
            fetch_tag(source, version.ref)
        if tag_exists(source, version.ref):
            available.add(version.ref)
        else:
            summary.append(f"warning: skipped {version.ref}: tag does not exist yet")
    if require_tag is not None and require_tag not in available:
        if require_tag in {version.ref for version in listed}:
            raise BuildError(f"{require_tag} is listed in {DOCS_YML} but the tag is not available")
        raise BuildError(
            f"{require_tag} is not listed in {DOCS_YML}; "
            f"add a `ref: {require_tag}` entry on develop"
        )

    planned = plan_versions(listed, release=release, available=available)

    copy_checkout(source, output)
    rendered: list[Version] = []
    for index, version in enumerate(planned):
        if version.ref is None:
            root = output
            values = release_values(release) if release is not None else DEV_VALUES
            rendered.append(version)
        else:
            root = output / VERSIONS_DIR / version.ref
            extract_tag(source, version.ref, root)
            values = release_values(version.ref.removeprefix("v"))
            rendered.append(
                replace(version, ref=None, path=f"./versions/{version.ref}/docs/index.yml")
            )
        # The checkout and a release being published must be self-contained;
        # older immutable tags are reported but still built.
        offenders = transform_version(
            root,
            values,
            base=base,
            strict_links=version.ref in (None, require_tag),
            label=version.ref or "checkout",
        )
        if offenders:
            summary.append(
                f"warning: {version.ref} has {len(offenders)} link(s) Fern cannot scope to "
                f"the version; first: {offenders[0]}"
            )
        role = "default" if index == 0 else f"/{version.slug}"
        stamped = " / ".join(values[name] for name in (GIT_REF, IMAGE_TAG, SBSA_IMAGE_TAG))
        summary.append(f"{version.display_name} ({role}): {stamped}")

    start, end = block_span(docs_yml, "versions")
    (output / DOCS_YML).write_text(
        docs_yml[:start] + render_versions(rendered) + docs_yml[end:], encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--source", type=Path, default=REPO_ROOT, help="checkout to build (default: this repo)"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="new directory for the Fern input tree"
    )
    parser.add_argument(
        "--release", help="build the checkout as release X.Y.Z (preview), the default version"
    )
    parser.add_argument(
        "--fetch", action="store_true", help="fetch listed release tags missing locally"
    )
    parser.add_argument(
        "--no-releases", action="store_true", help="build only the checkout (fast local preview)"
    )
    parser.add_argument(
        "--require-tag", help="fail unless this listed release tag is built (release publish)"
    )
    args = parser.parse_args()

    try:
        summary = build(
            args.source,
            args.output,
            release=args.release,
            fetch=args.fetch,
            include_releases=not args.no_releases,
            require_tag=args.require_tag,
        )
    except (OSError, subprocess.CalledProcessError, tarfile.TarError, BuildError) as error:
        print(f"Fern site build failed: {error}", file=sys.stderr)
        return 1

    print(f"Built Fern input tree at {args.output.resolve()}")
    for line in summary:
        print(f"  {line}")
        if line.startswith("warning:") and os.environ.get("GITHUB_ACTIONS") == "true":
            # The plain warning above stays in the stdout that workflows tee to
            # the job summary; the annotation goes to stderr so the raw
            # ::warning:: command text is not copied there too.
            print(
                f"::warning title=Fern site build::{line.removeprefix('warning: ')}",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
