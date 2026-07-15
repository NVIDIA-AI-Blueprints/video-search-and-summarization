#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Create an OSS source archive zip from package inventories collected from images."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"
USER_AGENT = "vss-summarization-oss-source-ci/1.0"

SKIP_EXACT = {
    "cuda-bindings",
    "cuda-python",
    "ngcsdk",
    "onnx-graphsurgeon",
    "pyds",
    "pyservicemaker",
    "python-apt",
    "tensorrt",
    "tensorrt-bindings",
    "tensorrt-dispatch",
    "tensorrt-lean",
    "tensorrt-libs",
    "tritonfrontend",
    "tritonserver",
    "vss-ctx-rag",
    "vss-ctx-rag-arango",
}

SKIP_PREFIXES = (
    "cuda-",
    "nvidia-",
    "tensorrt-",
    "cupy-cuda",
    "cudf-cu",
    "cuml-cu",
    "cuvs-cu",
    "dask-cudf-cu",
    "distributed-ucxx-cu",
    "libcudf-cu",
    "libcugraph-cu",
    "libcuml-cu",
    "libcuvs-cu",
    "libkvikio-cu",
    "libraft-cu",
    "librmm-cu",
    "libucx-cu",
    "libucxx-cu",
    "nx-cugraph-cu",
    "pylibcudf-cu",
    "pylibcugraph-cu",
    "pylibraft-cu",
    "pynvjitlink-cu",
    "raft-dask-cu",
    "rmm-cu",
    "ucx-py-cu",
    "ucxx-cu",
)

INCLUDE_EXACT = {
    "nvidia-rag",
}

GITHUB_FALLBACKS = {
    "faiss-cpu": ("facebookresearch", "faiss", "v{version}"),
    "flash-attn": ("Dao-AILab", "flash-attention", "v{version}"),
    "flatbuffers": ("google", "flatbuffers", "v{version}"),
    "milvus": ("milvus-io", "milvus", "v{version}"),
    "milvus-lite": ("milvus-io", "milvus-lite", "v{version}"),
    "nvidia-rag": ("NVIDIA-AI-Blueprints", "rag", "v{version}"),
    "onnxruntime": ("microsoft", "onnxruntime", "v{version}"),
    "phenolrs": ("arangoml", "phenolrs", "v{version}"),
    "pytorch-triton": ("triton-lang", "triton", "v{version}"),
    "ray": ("ray-project", "ray", "ray-{version}"),
    "sseclient-py": ("mpetazzoni", "sseclient", "sseclient-py-{version}"),
    "triton": ("triton-lang", "triton", "v{version}"),
    "xformers": ("facebookresearch", "xformers", "v{version}"),
}

OPENCV_PACKAGES = {
    "opencv-contrib-python",
    "opencv-contrib-python-headless",
    "opencv-python",
    "opencv-python-headless",
}


@dataclass(frozen=True)
class PackageRecord:
    name: str
    version: str
    arches: tuple[str, ...]
    images: tuple[str, ...]


@dataclass(frozen=True)
class SourceCandidate:
    url: str
    source_type: str
    original_filename: str


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def safe_file_component(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._+-]+", "_", text)


def should_skip(name: str) -> bool:
    normalized = canonical_name(name)
    if normalized in INCLUDE_EXACT:
        return False
    return normalized in SKIP_EXACT or any(normalized.startswith(p) for p in SKIP_PREFIXES)


def request_url(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def load_json(url: str) -> dict:
    with urllib.request.urlopen(request_url(url), timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def download_bytes(url: str) -> bytes:
    with urllib.request.urlopen(request_url(url), timeout=300) as response:
        return response.read()


def parse_package_lists(paths: list[Path]) -> list[PackageRecord]:
    merged: dict[tuple[str, str], dict[str, set[str] | str]] = {}
    for path in paths:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"arch", "image", "name", "version"}
            if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                raise ValueError(f"{path} must contain columns: arch,image,name,version")
            for row in reader:
                name = (row.get("name") or "").strip()
                version = (row.get("version") or "").strip()
                if not name or not version:
                    continue
                key = (canonical_name(name), version)
                entry = merged.setdefault(
                    key,
                    {"name": name, "version": version, "arches": set(), "images": set()},
                )
                entry["arches"].add((row.get("arch") or "unknown").strip())
                entry["images"].add((row.get("image") or "unknown").strip())

    records = []
    for entry in merged.values():
        records.append(
            PackageRecord(
                name=str(entry["name"]),
                version=str(entry["version"]),
                arches=tuple(sorted(entry["arches"])),
                images=tuple(sorted(entry["images"])),
            )
        )
    return sorted(records, key=lambda item: (canonical_name(item.name), item.version))


def pypi_sdist_candidate(package: PackageRecord) -> SourceCandidate | None:
    url = PYPI_JSON.format(
        name=urllib.parse.quote(package.name),
        version=urllib.parse.quote(package.version),
    )
    try:
        metadata = load_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise

    sdists = [item for item in metadata.get("urls", []) if item.get("packagetype") == "sdist"]
    if not sdists:
        return None

    sdists.sort(key=lambda item: (not item.get("filename", "").endswith(".tar.gz"), item.get("filename", "")))
    selected = sdists[0]
    return SourceCandidate(
        url=selected["url"],
        source_type="pypi_sdist",
        original_filename=selected.get("filename") or Path(urllib.parse.urlparse(selected["url"]).path).name,
    )


def github_fallback_candidate(package: PackageRecord) -> SourceCandidate | None:
    normalized = canonical_name(package.name)
    if normalized in OPENCV_PACKAGES:
        build_tag = package.version.rsplit(".", 1)[-1]
        return SourceCandidate(
            url=f"https://github.com/opencv/opencv-python/archive/refs/tags/{build_tag}.tar.gz",
            source_type="github_tag_archive",
            original_filename=f"opencv-python-{build_tag}.tar.gz",
        )

    fallback = GITHUB_FALLBACKS.get(normalized)
    if not fallback:
        return None
    owner, repo, tag_template = fallback
    tag = tag_template.format(version=package.version)
    url = f"https://github.com/{owner}/{repo}/archive/refs/tags/{tag}.tar.gz"
    return SourceCandidate(
        url=url,
        source_type="github_tag_archive",
        original_filename=f"{repo}-{tag}.tar.gz",
    )


def resolve_candidate(package: PackageRecord) -> SourceCandidate | None:
    if canonical_name(package.name) in OPENCV_PACKAGES:
        return github_fallback_candidate(package)
    candidate = pypi_sdist_candidate(package)
    if candidate:
        return candidate
    return github_fallback_candidate(package)


def write_tar_gz_from_zip(zip_path: Path, tar_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="sdist-zip-") as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(zip_path) as source_zip:
            source_zip.extractall(tmp_path)
        with tarfile.open(tar_path, "w:gz") as tar:
            for child in sorted(tmp_path.iterdir()):
                tar.add(child, arcname=child.name)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def store_source_archive(candidate: SourceCandidate, destination: Path) -> tuple[str, str]:
    payload = download_bytes(candidate.url)
    destination.parent.mkdir(parents=True, exist_ok=True)

    lower_name = candidate.original_filename.lower()
    if lower_name.endswith((".tar.gz", ".tgz")):
        destination.write_bytes(payload)
    elif lower_name.endswith(".zip"):
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        try:
            write_tar_gz_from_zip(tmp_path, destination)
        finally:
            tmp_path.unlink(missing_ok=True)
    else:
        destination.write_bytes(payload)

    return sha256_file(destination), str(destination)


def create_zip(archive_dir: Path, zip_path: Path) -> int:
    source_archives = sorted(archive_dir.glob("*.src.tar.gz"))
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as output_zip:
        for archive in source_archives:
            output_zip.write(archive, arcname=archive.name)
    return len(source_archives)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_lists", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--zip-file", type=Path, required=True)
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Write missing sources to the missing log but still create the zip.",
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    archive_dir = output_dir / "archives"
    download_log = output_dir / "download-log.csv"
    missing_log = output_dir / "missing-sources.csv"
    skipped_log = output_dir / "skipped-packages.csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    if archive_dir.exists():
        shutil.rmtree(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)

    records = parse_package_lists(args.package_lists)
    missing: list[dict[str, str]] = []

    with download_log.open("w", newline="") as downloads, skipped_log.open("w", newline="") as skipped:
        download_writer = csv.DictWriter(
            downloads,
            fieldnames=[
                "package",
                "version",
                "arches",
                "images",
                "source_type",
                "source_url",
                "archive_path",
                "sha256",
            ],
        )
        skipped_writer = csv.DictWriter(
            skipped,
            fieldnames=["package", "version", "arches", "images", "reason"],
        )
        download_writer.writeheader()
        skipped_writer.writeheader()

        for package in records:
            arches = ";".join(package.arches)
            images = ";".join(package.images)
            if should_skip(package.name):
                skipped_writer.writerow(
                    {
                        "package": package.name,
                        "version": package.version,
                        "arches": arches,
                        "images": images,
                        "reason": "nvidia_or_proprietary_package",
                    }
                )
                continue

            try:
                candidate = resolve_candidate(package)
                if not candidate:
                    missing.append(
                        {
                            "package": package.name,
                            "version": package.version,
                            "arches": arches,
                            "images": images,
                            "reason": "no_pypi_sdist_or_known_fallback",
                        }
                    )
                    continue

                archive_name = (
                    f"{safe_file_component(canonical_name(package.name))}-"
                    f"{safe_file_component(package.version)}.src.tar.gz"
                )
                archive_path = archive_dir / archive_name
                sha256, stored_path = store_source_archive(candidate, archive_path)
                download_writer.writerow(
                    {
                        "package": package.name,
                        "version": package.version,
                        "arches": arches,
                        "images": images,
                        "source_type": candidate.source_type,
                        "source_url": candidate.url,
                        "archive_path": stored_path,
                        "sha256": sha256,
                    }
                )
                print(f"downloaded {package.name}=={package.version} from {candidate.url}")
            except (urllib.error.URLError, zipfile.BadZipFile, tarfile.TarError) as exc:
                missing.append(
                    {
                        "package": package.name,
                        "version": package.version,
                        "arches": arches,
                        "images": images,
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                )

    with missing_log.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["package", "version", "arches", "images", "reason"],
        )
        writer.writeheader()
        writer.writerows(missing)

    if missing and not args.allow_missing:
        print(f"Failed to resolve {len(missing)} source package(s). See {missing_log}.", file=sys.stderr)
        return 1

    archive_count = create_zip(archive_dir, args.zip_file)
    if archive_count == 0:
        print("No source archives were created; refusing to emit an empty zip.", file=sys.stderr)
        return 1

    for log_file in (download_log, missing_log, skipped_log):
        destination = args.zip_file.parent / log_file.name
        if log_file.resolve() != destination.resolve():
            shutil.copy2(log_file, destination)
    print(f"Created {args.zip_file} with {archive_count} source archive(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
