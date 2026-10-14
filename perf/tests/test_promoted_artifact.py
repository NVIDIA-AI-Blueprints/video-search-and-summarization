# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Hermetic resource acquisition, coordinate provenance, and archive safety checks."""

import importlib.util
import io
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path
from unittest import mock

import pytest

spec = importlib.util.spec_from_file_location(
    "promoted_artifact", Path(__file__).parents[1] / "scripts/promoted_artifact.py"
)
artifact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifact)
REF = "nv-metropolis-dev/vss-developer/vss-blueprints-compose-base:nightly-20261010"


def production_tree(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    lines = [
        'VSS_CONTAINER_REGISTRY="${VSS_CONTAINER_REGISTRY:-nvcr.io/nvstaging/vss-core}"',
        'VSS_CONTAINER_TAG="${VSS_CONTAINER_TAG:-published-image-version}"',
    ]
    for prefix in artifact.REQUIRED_IMAGES:
        name = prefix.lower().replace("_", "-")
        lines.extend(
            [
                f'{prefix}_IMAGE="${{{prefix}_IMAGE:-${{VSS_CONTAINER_REGISTRY}}/{name}}}"',
                f'{prefix}_TAG="${{{prefix}_TAG:-${{VSS_CONTAINER_TAG}}}}"',
            ]
        )
    (root / "containers.env").write_text("\n".join(lines) + "\n")
    for relative in artifact.REQUIRED_DEFINITIONS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("services: {}\n")
    for prefix, (relative, service) in artifact.IMAGE_DEFINITIONS.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"services:\n  {service}:\n    image: ${{{prefix}_IMAGE}}:${{{prefix}_TAG}}\n"
        )
    return root


@pytest.mark.parametrize("archive_type", ["tar", "zip", "files"])
def test_exact_resource_acquired_and_published_pins_used(
    tmp_path, archive_type, monkeypatch
):
    published = production_tree(tmp_path / "published" / "deploy" / "docker")
    monkeypatch.setenv("VSS_CONTAINER_TAG", "unrelated-process-tag")
    monkeypatch.setenv("VSS_RT_CV_IMAGE", "unrelated-process-image")

    def downloader(reference, destination):
        assert reference == REF
        if archive_type == "tar":
            with tarfile.open(destination / "profile.tar.gz", "w:gz") as bundle:
                bundle.add(
                    published.parents[1], arcname="video-search-and-summarization"
                )
        elif archive_type == "zip":
            with zipfile.ZipFile(destination / "profile.zip", "w") as bundle:
                for path in published.rglob("*"):
                    if path.is_file():
                        bundle.write(
                            path, "deploy/docker/" + str(path.relative_to(published))
                        )
        else:
            shutil.copytree(published, destination / "downloaded-profile")

    target = tmp_path / "verified"
    report = artifact.acquire(REF, target, downloader)
    assert report["verified"] is True
    assert report["artifact_ref"] == REF
    assert report["resource_version"] == "nightly-20261010"
    assert Path(report["compose_root"]).is_relative_to(target)
    assert (Path(report["compose_root"]) / "containers.env").is_file()
    assert report["environment"]["VSS_RT_CV_TAG"] == "published-image-version"
    assert (
        report["environment"]["VSS_RT_CV_IMAGE"]
        == "nvcr.io/nvstaging/vss-core/vss-rt-cv"
    )
    assert report["container_pins"]["VSS_RT_CV"].endswith(":published-image-version")
    assert len(report["compose_tree_sha256"]) == 64


@pytest.mark.parametrize(
    "name",
    ["../outside", "/tmp/outside", "dir/../../outside", "C:/outside", "dir\\outside"],
)
def test_tar_path_traversal_rejected(tmp_path, name):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as bundle:
        member = tarfile.TarInfo(name)
        member.size = 1
        bundle.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(artifact.ArtifactError, match="Unsafe path"):
        artifact.unpack_archive(archive, tmp_path / "extracted")


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_tar_links_and_special_files_rejected(tmp_path, kind):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as bundle:
        member = tarfile.TarInfo("link")
        member.type = kind
        member.linkname = "../outside"
        bundle.addfile(member)
    with pytest.raises(artifact.ArtifactError, match="links and special"):
        artifact.unpack_archive(archive, tmp_path / "extracted")


def test_zip_symlink_rejected(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        member = zipfile.ZipInfo("link")
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(member, "../outside")
    with pytest.raises(artifact.ArtifactError, match="links and special"):
        artifact.unpack_archive(archive, tmp_path / "extracted")


def test_bounded_extraction(tmp_path, monkeypatch):
    archive = tmp_path / "large.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("large", b"12345")
    monkeypatch.setattr(artifact, "MAX_BYTES", 4)
    with pytest.raises(artifact.ArtifactError, match="extraction limits"):
        artifact.unpack_archive(archive, tmp_path / "extracted")


def test_missing_production_recipe_fails(tmp_path):
    root = production_tree(tmp_path / "production")
    (root / artifact.REQUIRED_DEFINITIONS[0]).unlink()
    with pytest.raises(artifact.ArtifactError, match="missing services/infra"):
        artifact.inspect_payload(tmp_path)


def test_ambiguous_production_trees_fail(tmp_path):
    production_tree(tmp_path / "one")
    production_tree(tmp_path / "two")
    with pytest.raises(artifact.ArtifactError, match="exactly one"):
        artifact.inspect_payload(tmp_path)


@pytest.mark.parametrize(
    "tag", ["latest", "develop-latest", "develop", "main", "nightly"]
)
def test_moving_image_tags_rejected(tmp_path, tag):
    root = production_tree(tmp_path / "production")
    env = root / "containers.env"
    env.write_text(env.read_text().replace("published-image-version", tag))
    with pytest.raises(artifact.ArtifactError, match="tag is moving"):
        artifact.inspect_payload(tmp_path)


def test_shell_expansions_are_never_executed(tmp_path):
    root = production_tree(tmp_path / "production")
    env = root / "containers.env"
    sentinel = tmp_path / "executed"
    env.write_text(env.read_text() + f'UNUSED="$(touch {sentinel})"\n')
    with pytest.raises(artifact.ArtifactError, match="Executable expansion"):
        artifact.inspect_payload(tmp_path)
    assert not sentinel.exists()


def test_download_failure_has_no_success_report_or_payload(tmp_path):
    def downloader(reference, destination):
        raise artifact.ArtifactError("denied")

    with pytest.raises(artifact.ArtifactError, match="denied"):
        artifact.acquire(REF, tmp_path / "verified", downloader)
    assert not (tmp_path / "verified").exists()
    assert not list(tmp_path.glob(".promoted-compose-*"))


def test_ngc_command_uses_exact_version_and_env_only_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("NGC_API_KEY", "test-key-not-printed")
    monkeypatch.delenv("NGC_CLI_API_KEY", raising=False)
    with (
        mock.patch.object(artifact.shutil, "which", return_value="/bin/ngc"),
        mock.patch.object(artifact.subprocess, "run") as run,
    ):

        def execute(command, **kwargs):
            assert command == [
                "ngc",
                "registry",
                "resource",
                "download-version",
                "--dest",
                str(tmp_path),
                REF,
            ]
            assert kwargs["env"]["NGC_CLI_API_KEY"] == "test-key-not-printed"
            assert "test-key-not-printed" not in " ".join(command)
            assert kwargs["stdout"] == artifact.subprocess.DEVNULL
            assert kwargs["stderr"] == artifact.subprocess.DEVNULL
            (tmp_path / "file").write_text("downloaded")
            return mock.Mock(returncode=0)

        run.side_effect = execute
        artifact.download_resource(REF, tmp_path)


@pytest.mark.parametrize(
    "reference",
    [
        "https://example.com/file",
        "nvcr.io/org/team/image:tag",
        "org/team/vss-blueprints-compose-base:../version",
        "org/team/other:nightly-20261010",
    ],
)
def test_invalid_or_other_artifact_references_rejected(reference):
    with pytest.raises(artifact.ArtifactError):
        artifact.parse_reference(reference)


def test_tree_digest_detects_runtime_definition_change(tmp_path):
    root = production_tree(tmp_path / "production")
    original = artifact.inspect_payload(tmp_path)["compose_tree_sha256"]
    (root / artifact.REQUIRED_DEFINITIONS[0]).write_text("services: {changed: {}}\n")
    assert artifact.inspect_payload(tmp_path)["compose_tree_sha256"] != original


def test_existing_destination_is_never_overwritten(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    with pytest.raises(artifact.ArtifactError, match="already exists"):
        artifact.acquire(REF, target, mock.Mock())


def test_tar_root_directory_and_executable_modes_preserved(tmp_path):
    archive = tmp_path / "production.tar"
    with tarfile.open(archive, "w") as bundle:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        bundle.addfile(root)
        script = tarfile.TarInfo("./run.sh")
        script.size = 4
        script.mode = 0o4755
        bundle.addfile(script, io.BytesIO(b"echo"))
    artifact.unpack_archive(archive, tmp_path / "extracted")
    assert (tmp_path / "extracted/run.sh").stat().st_mode & 0o7777 == 0o755


def test_unsupported_shell_operator_is_not_misinterpreted():
    with pytest.raises(artifact.ArtifactError, match="Unsupported expansion"):
        artifact.resolve_defaults("${VALUE:+other}", {"VALUE": "present"})


def test_direct_payload_size_bound(tmp_path, monkeypatch):
    production_tree(tmp_path / "production")
    monkeypatch.setattr(artifact, "MAX_BYTES", 1)
    with pytest.raises(artifact.ArtifactError, match="verification limits"):
        artifact.inspect_payload(tmp_path)


def test_ngc_nonzero_exit_and_empty_download_never_verify(tmp_path):
    with (
        mock.patch.object(artifact.shutil, "which", return_value="/bin/ngc"),
        mock.patch.object(
            artifact.subprocess, "run", return_value=mock.Mock(returncode=1)
        ),
        pytest.raises(artifact.ArtifactError, match="exit 1"),
    ):
        artifact.download_resource(REF, tmp_path)
    with (
        mock.patch.object(artifact.shutil, "which", return_value="/bin/ngc"),
        mock.patch.object(
            artifact.subprocess, "run", return_value=mock.Mock(returncode=0)
        ),
        pytest.raises(artifact.ArtifactError, match="produced no files"),
    ):
        artifact.download_resource(REF, tmp_path)


def test_cli_failure_does_not_write_success_json(tmp_path, capsys):
    output = tmp_path / "report.json"
    with mock.patch.object(
        artifact, "acquire", side_effect=artifact.ArtifactError("resource inaccessible")
    ):
        status = artifact.main(
            [
                "--artifact-ref",
                REF,
                "--destination",
                str(tmp_path / "resource"),
                "--output-json",
                str(output),
            ]
        )
    assert status == 1
    assert not output.exists()
    assert "resource inaccessible" in capsys.readouterr().err


def test_published_profile_pins_override_moving_defaults_in_declared_order(tmp_path):
    root = production_tree(tmp_path / "production")
    env = root / "containers.env"
    env.write_text(env.read_text().replace("published-image-version", "develop-latest"))
    profile = root / "developer-profiles/dev-profile-base"
    profile.mkdir(parents=True)
    for filename, tag in (
        (".env", "profile-default"),
        ("overrides.env", "profile-override"),
        ("generated.env", "published-generated"),
    ):
        (profile / filename).write_text(
            "\n".join(f"{prefix}_TAG={tag}" for prefix in artifact.REQUIRED_IMAGES)
            + '\nNVIDIA_API_KEY="${RUNTIME_SECRET:?provided at deployment}"\n'
        )
    report = artifact.inspect_payload(tmp_path)
    assert all(
        tag.endswith(":published-generated")
        for tag in report["container_pins"].values()
    )
    assert report["coordinate_sources"] == [
        "containers.env",
        "developer-profiles/dev-profile-base/.env",
        "developer-profiles/dev-profile-base/overrides.env",
        "developer-profiles/dev-profile-base/generated.env",
    ]
    assert "NVIDIA_API_KEY" not in report["environment"]


def test_literal_promoted_recipe_pin_wins_over_parameterized_env_defaults(tmp_path):
    root = production_tree(tmp_path / "production")
    path, service = artifact.IMAGE_DEFINITIONS["VSS_VIDEO_ANALYTICS_API"]
    (root / path).write_text(
        f"services:\n  {service}:\n    image: nvcr.io/nvstaging/vss-core/vss-video-analytics-api:literal-promoted-pin\n"
    )
    report = artifact.inspect_payload(tmp_path)
    assert (
        report["environment"]["VSS_VIDEO_ANALYTICS_API_TAG"] == "literal-promoted-pin"
    )
    assert (
        report["container_pins"]["VSS_VIDEO_ANALYTICS_API"]
        == "nvcr.io/nvstaging/vss-core/vss-video-analytics-api:literal-promoted-pin"
    )


def test_production_repository_definitions_with_published_generated_pins(tmp_path):
    # Use actual production recipes and containers.env, not handcrafted image
    # expressions, so changes to their service names/coordinate syntax surface.
    source = Path(__file__).parents[2] / "deploy/docker"
    root = tmp_path / "published-profile"
    root.mkdir()
    shutil.copy2(source / "containers.env", root / "containers.env")
    for relative in set(artifact.REQUIRED_DEFINITIONS) | {
        pair[0] for pair in artifact.IMAGE_DEFINITIONS.values()
    }:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, path)
    profile = root / "developer-profiles/dev-profile-base"
    profile.mkdir(parents=True)
    for filename in (".env", "overrides.env"):
        shutil.copy2(
            source / "developer-profiles/dev-profile-base" / filename,
            profile / filename,
        )
    # Fixture pins stand in for published generated.env. No resource version
    # or caller environment is used to infer these values.
    (profile / "generated.env").write_text(
        "\n".join(
            f"{prefix}_TAG=archived-generated-image-pin"
            for prefix in artifact.REQUIRED_IMAGES
        )
        + "\n"
    )
    report = artifact.inspect_payload(tmp_path)
    assert all(
        pin.endswith(":archived-generated-image-pin")
        for pin in report["container_pins"].values()
    )


def test_missing_service_image_definition_fails_closed(tmp_path):
    root = production_tree(tmp_path / "production")
    (root / artifact.IMAGE_DEFINITIONS["VSS_RT_CV"][0]).write_text("services: {}\n")
    with pytest.raises(
        artifact.ArtifactError, match="lacks the perception image definition"
    ):
        artifact.inspect_payload(tmp_path)


def test_literal_digest_pin_is_preserved_without_colon_tag_overrides(tmp_path):
    root = production_tree(tmp_path / "production")
    path, service = artifact.IMAGE_DEFINITIONS["VSS_VIDEO_ANALYTICS_API"]
    reference = "nvcr.io/nvstaging/vss-core/vss-video-analytics-api@sha256:" + "a" * 64
    (root / path).write_text(f"services:\n  {service}:\n    image: {reference}\n")
    report = artifact.inspect_payload(tmp_path)
    assert report["container_pins"]["VSS_VIDEO_ANALYTICS_API"] == reference
    assert "VSS_VIDEO_ANALYTICS_API_IMAGE" not in report["environment"]


def test_published_shared_channel_derives_pins_in_real_production_recipes(tmp_path):
    source = Path(__file__).parents[2] / "deploy/docker"
    root = tmp_path / "production"
    root.mkdir()
    shutil.copy2(source / "containers.env", root / "containers.env")
    for relative in set(artifact.REQUIRED_DEFINITIONS) | {
        pair[0] for pair in artifact.IMAGE_DEFINITIONS.values()
    }:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, path)
    profile = root / "developer-profiles/dev-profile-base"
    profile.mkdir(parents=True)
    (profile / "generated.env").write_text(
        "VSS_CONTAINER_REGISTRY=nvcr.io/nvstaging/vss-core\n"
        "VSS_CONTAINER_TAG=explicit-published-channel\n"
    )
    report = artifact.inspect_payload(tmp_path)
    assert all(
        pin.startswith("nvcr.io/nvstaging/vss-core/")
        for pin in report["container_pins"].values()
    )
    assert all(
        pin.endswith(":explicit-published-channel")
        for pin in report["container_pins"].values()
    )
    assert all(
        tag != "develop-latest"
        for key, tag in report["environment"].items()
        if key.endswith("_TAG")
    )
