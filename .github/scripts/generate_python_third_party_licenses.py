# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Generate the VSS Agent third-party notice from an installed final image.

Run this script with the Python interpreter from the final ``agent-runtime``
image. Using the final image, rather than ``uv.lock`` or a pre-removal build
environment, keeps the notice aligned with the distributions that NVIDIA
actually ships.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

FIRST_PARTY_DISTRIBUTIONS = {
    "nvidia-vss",
    "nvidia-vss-agents",
    "nvidia-vss-cli",
    "nvidia-vss-core",
}
LICENSE_NAME_OVERRIDES = {
    "python-bidi": "LGPL-3.0-or-later",
}
LICENSE_URL_OVERRIDES = {
    "arize-phoenix-otel": "https://github.com/Arize-ai/phoenix/blob/main/packages/phoenix-otel/LICENSE",
    "exa-py": "https://github.com/exa-labs/exa-py/blob/6749a6e0fcb92e2b0fc716aa686f9ff8257c5d62/LICENSE",
    "langchain-oci": "https://github.com/oracle/langchain-oracle/blob/main/LICENSE.txt",
    "python-bidi": "https://github.com/MeirKriheli/python-bidi/blob/v0.6.11/COPYING.LESSER",
}
NOTICE_FILE_PREFIXES = ("COPYING", "COPYRIGHT", "LICENCE", "LICENSE", "NOTICE")
HEADING_RE = re.compile(r"^## (?P<name>.+?) \((?P<version>[^()]*)\)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ExistingSection:
    license_name: str
    license_url: str
    text: str
    version: str


@dataclass(frozen=True)
class NoticeText:
    source: str
    text: str


@dataclass(frozen=True)
class Component:
    license_name: str
    license_url: str
    name: str
    notes: tuple[str, ...]
    scope: str
    texts: tuple[NoticeText, ...]
    version: str


def canonicalize(name: str) -> str:
    """Return the normalized Python distribution name."""

    return re.sub(r"[-_.]+", "-", name).lower()


def normalize_notice_text(text: str) -> str:
    """Normalize line endings and remove non-semantic trailing whitespace."""

    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def parse_existing_sections(text: str) -> dict[str, ExistingSection]:
    """Extract reusable license text from the previous generated notice."""

    matches = list(HEADING_RE.finditer(text))
    sections: dict[str, ExistingSection] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[match.start() : end]
        license_match = re.search(r"^\*\*License:\*\*\s*(.+?)\s*$", chunk, re.MULTILINE)
        url_match = re.search(r"^\*\*License URL:\*\*\s*(.+?)\s*$", chunk, re.MULTILINE)
        fence_match = re.search(
            r"````?(?:text)?\n(?P<text>.*)\n````?", chunk, re.DOTALL
        )
        if not license_match or not fence_match:
            continue
        sections[canonicalize(match.group("name"))] = ExistingSection(
            license_name=license_match.group(1).strip(),
            license_url=url_match.group(1).strip() if url_match else "",
            text=normalize_notice_text(fence_match.group("text")),
            version=match.group("version").strip(),
        )
    return sections


def is_notice_file(path: str) -> bool:
    """Return whether a wheel path is a license or attribution file."""

    basename = Path(path).name.upper()
    return any(
        basename == prefix or basename.startswith((f"{prefix}.", f"{prefix}-"))
        for prefix in NOTICE_FILE_PREFIXES
    )


def metadata_license_name(metadata: importlib.metadata.PackageMetadata) -> str:
    """Resolve a concise license label, preferring PEP 639."""

    expression = (metadata.get("License-Expression") or "").strip()
    if expression:
        return expression
    license_field = (metadata.get("License") or "").strip()
    if license_field:
        return next(
            (line.strip() for line in license_field.splitlines() if line.strip()), ""
        )
    classifiers = metadata.get_all("Classifier") or []
    license_classifiers = [
        item for item in classifiers if item.startswith("License ::")
    ]
    if license_classifiers:
        return max(license_classifiers, key=len).rsplit("::", 1)[-1].strip()
    return ""


def metadata_project_url(
    metadata: importlib.metadata.PackageMetadata, name: str, version: str
) -> str:
    """Choose the best available upstream project URL."""

    project_urls: dict[str, str] = {}
    for value in metadata.get_all("Project-URL") or []:
        label, separator, url = value.partition(",")
        if separator:
            project_urls[label.strip().lower()] = url.strip()
    for label in ("source", "repository", "source code", "homepage", "home", "code"):
        if project_urls.get(label):
            return project_urls[label]
    homepage = (metadata.get("Home-page") or "").strip()
    if homepage:
        return homepage
    return f"https://pypi.org/project/{name}/{version}/"


def read_distribution_notice_files(
    distribution: importlib.metadata.Distribution,
    canonical_name: str,
) -> list[NoticeText]:
    """Read all license and NOTICE files retained in an installed wheel."""

    notices: list[NoticeText] = []
    for file in sorted(distribution.files or [], key=str):
        file_name = str(file)
        if not is_notice_file(file_name):
            continue
        # Arize confirmed that phoenix-otel's IP_NOTICE is a stale packaging
        # leftover; packages/phoenix-otel/LICENSE is the governing Apache-2.0
        # license for this independently released sub-package.
        if (
            canonical_name == "arize-phoenix-otel"
            and Path(file_name).name == "IP_NOTICE"
        ):
            continue
        # langchain-oci's wheel contains an unrelated LangChain MIT template
        # license. Upstream's repository, package metadata, and source headers
        # consistently declare UPL-1.0.
        if canonical_name == "langchain-oci" and Path(file_name).name == "LICENSE":
            continue
        path = distribution.locate_file(file)
        if not path.is_file():
            continue
        text = normalize_notice_text(path.read_text(encoding="utf-8", errors="replace"))
        if text:
            notices.append(NoticeText(source=file_name, text=text))
    return notices


def find_notice_text(
    distribution: importlib.metadata.Distribution,
    basename: str,
) -> NoticeText:
    """Read one required notice file by basename."""

    for file in distribution.files or []:
        if Path(str(file)).name == basename:
            path = distribution.locate_file(file)
            if path.is_file():
                return NoticeText(
                    source=str(file),
                    text=normalize_notice_text(
                        path.read_text(encoding="utf-8", errors="replace")
                    ),
                )
    raise RuntimeError(
        f"{distribution.metadata.get('Name')} does not contain {basename}"
    )


def load_override(overrides_dir: Path | None, canonical_name: str) -> NoticeText | None:
    """Load an externally verified license text for a wheel that omits it."""

    if overrides_dir is None:
        return None
    path = overrides_dir / f"{canonical_name}.txt"
    if not path.is_file():
        return None
    return NoticeText(
        source=f"license-text-overrides/{path.name}",
        text=normalize_notice_text(path.read_text(encoding="utf-8")),
    )


def distribution_components(
    existing: dict[str, ExistingSection],
    overrides_dir: Path | None,
) -> list[Component]:
    """Build components from the interpreter's installed distributions."""

    distributions: dict[str, importlib.metadata.Distribution] = {}
    for distribution in importlib.metadata.distributions():
        name = (distribution.metadata.get("Name") or "").strip()
        if not name:
            continue
        canonical_name = canonicalize(name)
        if canonical_name in FIRST_PARTY_DISTRIBUTIONS:
            continue
        if canonical_name in distributions:
            raise RuntimeError(f"duplicate installed distribution: {canonical_name}")
        distributions[canonical_name] = distribution

    components: list[Component] = []
    for canonical_name, distribution in sorted(distributions.items()):
        metadata = distribution.metadata
        name = (metadata.get("Name") or canonical_name).strip()
        version = distribution.version
        previous = existing.get(canonical_name)
        notices = read_distribution_notice_files(distribution, canonical_name)
        override = load_override(overrides_dir, canonical_name)
        notes: list[str] = []

        if override is not None:
            notices.append(override)
            notes.append(
                "The installed wheel omits its governing license text; this verified upstream text is supplied."
            )
        if canonical_name == "svglib":
            python_bidi = distributions.get("python-bidi")
            if python_bidi is None:
                raise RuntimeError(
                    "svglib requires a GPL-3.0 copy, but python-bidi is not installed"
                )
            gpl_text = find_notice_text(python_bidi, "COPYING")
            notices.append(
                NoticeText(
                    source="GNU GPL v3 incorporated by LGPL-3.0 (copy from the installed python-bidi wheel)",
                    text=gpl_text.text,
                )
            )
        if canonical_name == "arize-phoenix-otel":
            notes.append(
                "The bundled IP_NOTICE is an upstream-confirmed stale leftover; "
                "the bundled Apache-2.0 LICENSE governs phoenix-otel."
            )
        if canonical_name == "langchain-oci":
            notes.append(
                "The wheel's MIT file is an unrelated LangChain template leftover; "
                "upstream metadata, source headers, and LICENSE.txt declare UPL-1.0."
            )
        if canonical_name == "python-bidi":
            notes.append(
                "Upstream source containing the Python and Rust code is available at "
                "https://files.pythonhosted.org/packages/ce/e7/f168f2c3151aa05b9f9c9b2f7767bc8e06a133ea822c231ab497d4f36833/"
                "python_bidi-0.6.11.tar.gz "
                "(SHA-256 034090c597af250d699299d7e7f1e83eb016f9e47b3b707bd89ab2bdec77bce0). "
                "OSRB must confirm separately whether an NVIDIA written source offer is required."
            )

        if not notices:
            license_field = (metadata.get("License") or "").strip()
            if len(license_field) >= 200:
                notices.append(
                    NoticeText(
                        source="Core Metadata License field",
                        text=normalize_notice_text(license_field),
                    )
                )
            elif previous and previous.text:
                notices.append(
                    NoticeText(
                        source="previously reviewed project notice; installed wheel contains no license file",
                        text=previous.text,
                    )
                )
                notes.append(
                    "The installed wheel does not bundle a license file; the existing reviewed text is retained."
                )
            else:
                raise RuntimeError(
                    f"{name}=={version} has no bundled, overridden, or reusable license text"
                )

        license_name = (
            LICENSE_NAME_OVERRIDES.get(canonical_name)
            or metadata_license_name(metadata)
            or (previous.license_name if previous else "")
        )
        if not license_name:
            raise RuntimeError(f"{name}=={version} has no resolvable license name")
        license_url = LICENSE_URL_OVERRIDES.get(canonical_name) or metadata_project_url(
            metadata, name, version
        )
        components.append(
            Component(
                license_name=license_name,
                license_url=license_url,
                name=name,
                notes=tuple(notes),
                scope="Installed in the default vss-agent image",
                texts=tuple(notices),
                version=version,
            )
        )
    return components


def optional_opencv_component(existing: dict[str, ExistingSection]) -> Component:
    """Preserve the reviewed runtime-only OpenCV notice."""

    section = existing.get("opencv-python-headless")
    if section is None or section.version != "4.13.0.92":
        raise RuntimeError(
            "existing notice must contain opencv-python-headless 4.13.0.92"
        )
    return Component(
        license_name=section.license_name,
        license_url="https://pypi.org/project/opencv-python-headless/4.13.0.92/",
        name="opencv-python-headless",
        notes=(
            (
                "Not present in the default image. The container startup helper downloads this wheel "
                "from PyPI only when INSTALL_PROPRIETARY_CODECS is enabled."
            ),
        ),
        scope="Runtime optional; downloaded on the operator system",
        texts=(
            NoticeText(
                source="previously reviewed opencv-python-headless 4.13.0.92 notice",
                text=section.text,
            ),
        ),
        version="4.13.0.92",
    )


def documented_components(
    python_components: Iterable[Component],
    existing: dict[str, ExistingSection],
) -> list[Component]:
    """Combine final-image Python distributions with runtime-only notices."""

    return [*python_components, optional_opencv_component(existing)]


def render(
    components: Iterable[Component], python_package_count: int, inventory_basis: str
) -> str:
    """Render components in the repository's Markdown notice format."""

    ordered = sorted(components, key=lambda component: canonicalize(component.name))
    lines = [
        "# Dependencies Licenses",
        "",
        "This file contains the license texts and attribution notices for third-party components used by the VSS Agent.",
        "",
        f"Inventory basis: {inventory_basis}",
        "",
        f"Installed third-party Python distributions: {python_package_count}",
        f"Total documented components: {len(ordered)}",
        "",
        "---",
        "",
    ]
    for component in ordered:
        lines.extend(
            [
                "--------------------------------------------------------------------------------",
                "",
                f"## {component.name} ({component.version})",
                "",
                f"**License:** {component.license_name}",
                "",
                f"**License URL:** {component.license_url}",
                "",
                f"**Distribution scope:** {component.scope}",
                "",
            ]
        )
        for note in component.notes:
            lines.extend([f"**Note:** {note}", ""])
        for notice in component.texts:
            lines.extend(
                [
                    f"**Notice source:** `{notice.source}`",
                    "",
                    "````text",
                    notice.text,
                    "````",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--existing-notice", type=Path, required=True)
    parser.add_argument("--expected-python-packages", type=int, required=True)
    parser.add_argument("--inventory-basis", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overrides-dir", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    existing = parse_existing_sections(args.existing_notice.read_text(encoding="utf-8"))
    python_components = distribution_components(existing, args.overrides_dir)
    if len(python_components) != args.expected_python_packages:
        raise RuntimeError(
            f"expected {args.expected_python_packages} installed third-party Python distributions, "
            f"found {len(python_components)}"
        )
    components = documented_components(python_components, existing)
    args.output.write_text(
        render(components, len(python_components), args.inventory_basis),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
