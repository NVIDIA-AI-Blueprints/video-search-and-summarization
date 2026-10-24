# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Staging a model version into the shared model repository.

Backend-agnostic: download, verify, publish the sidecars atomically, remove a
version directory. The single point where backends differ is the
``_load_to_ready`` hook ``stage_and_load`` calls last.

The Model Manager owns every write to the shared model repository, so it owns
the deletes too; the serving stack never removes an artifact it did not create.

``_version_dir`` here and ``unified.inventory.on_disk_versions`` both guard a
path against escaping the repo root and are NOT shared: this one requires
exactly two components below the root and raises, that one requires exactly one
and is forbidden from raising.
"""

import contextlib
import hashlib
import json
import logging
import os
import shutil
import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal
from urllib.parse import unquote, urlparse
from uuid import uuid4

import requests

from src.clients.http import request_with_retries
from src.config import settings
from src.core.errors import ModelManagerError

logger = logging.getLogger(__name__)

_CHUNK = 1024 * 1024

# Canonical filename the serving stack expects for the training/inference spec,
# staged next to the weights in the shared model repository ({model}/{version}/).
SPEC_FILENAME = "spec.yaml"

# MM-authored metadata (e.g. DEFT's decision threshold), kept separate from
# spec.yaml so the DEFT-authored spec stays byte-for-byte verbatim.
METADATA_FILENAME = "metadata.json"

# Marker a pull writes inside the version directory while staging into it. Read
# by the reclaim path through ``staging_in_progress``; see ``_claim_staging``.
STAGING_LOCK_FILENAME = ".staging.lock"


class ActivationMode(ABC):
    """Strategy for staging/loading and unloading a model version."""

    @abstractmethod
    def stage_and_load(
        self,
        name: str,
        version: str,
        artifact_uri: str,
        sha256: str,
        *,
        spec_uri: str | None = None,
        threshold: float | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """Stage a version into the repo and make it available; return repo path.

        ``spec_uri`` stages as ``spec.yaml`` so the serving stack can
        rebuild/convert; ``threshold`` persists as a ``metadata.json`` sidecar so
        it can apply the decision boundary on load.

        ``idempotency_key`` reserves the load against repeats: the caller's key
        when it supplies one, otherwise one derived from the immutable inputs.
        """
        ...

    @abstractmethod
    def unload(self, name: str, version: str) -> None:
        """Remove a version from the serving stack."""
        ...

    @abstractmethod
    def delete(self, name: str, version: str) -> None:
        """Remove a version's directory from the model repo."""
        ...


class StagingActivation(ActivationMode):
    """Download, verify and publish a version; defer the load to a subclass.

    Everything here touches only the model repository and the artifact URI. A
    subclass supplies ``_load_to_ready`` and ``unload`` and inherits the rest.
    """

    @abstractmethod
    def _load_to_ready(self, name: str, version: str, idempotency_key: str) -> None:
        """Make a staged version servable, returning once it is.

        Implementations own the whole wait: a routing write is rejected while
        its target is not ready, so there is no "publish it now and let it warm
        up" state for a slow load to settle into.
        """
        ...

    def stage_and_load(
        self,
        name: str,
        version: str,
        artifact_uri: str,
        sha256: str,
        *,
        spec_uri: str | None = None,
        threshold: float | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """Stage a version's artifacts into the model repo, then load it to READY.

        Returns the version directory it wrote to.

        Idempotent and takes no lock. When it returns the artifact at the final
        path has digest ``sha256``, whatever the starting state and however many
        callers run at once, because: a matching digest skips the download and a
        mismatching one re-fetches; each attempt downloads to a request-private
        scratch file; and publication is verify-then-rename via the atomic
        ``os.replace``.

        The cost is that two overlapping pulls both download, and the skip
        re-hashes the artifact rather than being free.
        """
        with _staging_errors(name, version, artifact_uri):
            repo_dir = self._stage(
                name,
                version,
                artifact_uri,
                sha256,
                spec_uri=spec_uri,
                threshold=threshold,
            )
        self._load_to_ready(
            name,
            version,
            idempotency_key or _derived_idempotency_key(name, version, sha256),
        )
        # Released here, not at the end of ``_stage``: an engine build writes
        # back into the directory, so the bytes landing does not end the window.
        _release_staging(repo_dir, name, version)
        return str(repo_dir)

    def _stage(
        self,
        name: str,
        version: str,
        artifact_uri: str,
        sha256: str,
        *,
        spec_uri: str | None,
        threshold: float | None,
    ) -> Path:
        """Put the version's files on disk and return the version directory.

        Every filesystem touch of a pull lives here except the marker release.
        The one call site is wrapped in ``_staging_errors``, so a staging step
        added later is enveloped without anyone remembering to wrap it.
        """
        repo_dir = _version_dir(name, version)
        existed = repo_dir.exists()
        if existed:
            # Before the directory is READ, not just written: the digest check
            # below re-hashes a multi-GB artifact for seconds, and an unmarked
            # directory is deletable to the backend throughout.
            _claim_staging(repo_dir, name, version)

        artifact_path = repo_dir / _artifact_filename(artifact_uri)
        # Answered before anything is created, because it decides how much of the
        # locator matters: a re-drive on a matching digest needs nothing from it
        # but the extension, so rejecting one on its scheme would break "converges
        # whatever the starting state". The extension still counts --
        # ``_artifact_filename`` derives the staged name from the suffix.
        already_staged = artifact_path.exists() and _sha256_of(artifact_path) == sha256.lower()

        if not already_staged:
            # Rejected BEFORE the version directory exists: on a Triton-style
            # repo an empty version directory IS a version, so a caller's typo
            # used to leave a phantom one behind.
            #
            # Rejecting beats creating-then-cleaning-up, which needs a lock this
            # verb promises not to take -- two pulls both see it absent, both
            # create it, and the failing one deletes the directory the other is
            # staging into.
            #
            # The marker claimed above stays behind on this raise.
            _reject_unsupported_scheme(artifact_uri)

        # ``spec_uri`` is deliberately not pre-validated: failing before the
        # download throws away work a corrected-spec re-drive keeps on the digest
        # short-circuit. The cost is that a rejected spec leaves a directory with
        # weights and no spec, so the "creates nothing" guarantee above covers the
        # ARTIFACT locator only.

        if not existed:
            repo_dir.mkdir(parents=True, exist_ok=True)
            _claim_staging(repo_dir, name, version)
        return self._stage_into(
            repo_dir,
            name,
            version,
            artifact_uri,
            sha256,
            spec_uri=spec_uri,
            threshold=threshold,
            already_staged=already_staged,
        )

    def _stage_into(
        self,
        repo_dir: Path,
        name: str,
        version: str,
        artifact_uri: str,
        sha256: str,
        *,
        spec_uri: str | None,
        threshold: float | None,
        already_staged: bool = False,
    ) -> Path:
        """The staging body, with the version directory already created.

        ``already_staged`` is passed in rather than recomputed: re-hashing a
        multi-GB file to reach the same answer is not free.
        """
        artifact_path = repo_dir / _artifact_filename(artifact_uri)
        # No lock here on purpose; see the docstring above.
        if already_staged:
            logger.info("artifact already staged and verified: %s", artifact_path)
        else:
            # Absent or wrong bytes converge the same way: download to a
            # request-private sibling, publish by rename, so a digest mismatch
            # leaves nothing at the final path.
            tmp_path = _staging_tmp_path(artifact_path)
            try:
                self._download(artifact_uri, tmp_path)
                digest = _sha256_of(tmp_path)
                if digest != sha256.lower():
                    raise ModelManagerError(
                        code="checksum_mismatch",
                        message=(
                            f"sha256 mismatch for {name}@{version}: "
                            f"expected {sha256.lower()}, got {digest}"
                        ),
                        retryable=False,
                        http_status=409,
                    )
                os.replace(tmp_path, artifact_path)
            finally:
                # A no-op once the rename has happened; removes the partial
                # file when the download or the digest check raised.
                with contextlib.suppress(OSError):
                    tmp_path.unlink(missing_ok=True)

        # Staged BEFORE load so the serving stack has it for the rebuild step.
        # ALWAYS re-downloaded when set, so a changed spec is never masked by a
        # stale file; whether to set it is decided upstream.
        if spec_uri:
            spec_path = repo_dir / SPEC_FILENAME
            tmp_path = _staging_tmp_path(spec_path)
            try:
                self._download(spec_uri, tmp_path)
                os.replace(tmp_path, spec_path)
            finally:
                # A no-op once the rename has happened; removes the partial file
                # when the download raised. Suppressed so a cleanup failure can
                # never mask the download error that caused it.
                with contextlib.suppress(OSError):
                    tmp_path.unlink(missing_ok=True)
            logger.info("staged spec for %s@%s at %s", name, version, spec_path)

        # The filesystem is the source of truth for the threshold -- it is not in
        # the load config body -- so a serving restart that reloads from the repo
        # still sees it. Always rewritten when provided; an ABSENT threshold does
        # NOT clear a staged one, because the serving stack has no "no threshold"
        # state and falls back to a hardcoded default without logging it, silently
        # moving the decision boundary on live traffic.
        if threshold is not None:
            metadata_path = repo_dir / METADATA_FILENAME
            tmp_path = _staging_tmp_path(metadata_path)
            payload = {
                "schema_version": 1,
                "model_name": name,
                "version": version,
                "threshold": threshold,
            }
            try:
                tmp_path.write_text(json.dumps(payload))
                os.replace(tmp_path, metadata_path)
            finally:
                with contextlib.suppress(OSError):
                    tmp_path.unlink(missing_ok=True)
            logger.info(
                "staged metadata for %s@%s at %s (threshold=%s)",
                name,
                version,
                metadata_path,
                threshold,
            )

        return repo_dir

    def delete(self, name: str, version: str) -> None:
        """Remove ``{repo}/{name}/{version}/`` from the shared volume.

        The only component that removes a version directory, and not the
        authority on whether that is safe -- the caller establishes that from the
        serving stack's inventory first. This does the removal and nothing else.
        """
        version_dir = _version_dir(name, version)
        if not version_dir.exists():
            logger.info("version directory already absent: %s", version_dir)
            return
        try:
            shutil.rmtree(version_dir)
        except OSError as exc:
            raise ModelManagerError(
                code="internal",
                message=f"Failed to delete version directory {version_dir}: {exc}",
                retryable=True,
                http_status=500,
            ) from exc
        logger.info("deleted version directory %s", version_dir)

    def _download(self, artifact_uri: str, dest: Path) -> None:
        """Fetch ``artifact_uri`` into ``dest`` for the supported schemes."""
        scheme = urlparse(artifact_uri).scheme.lower()

        if scheme in ("http", "https"):
            # Streamed to disk (~1 MiB in memory), not buffered via
            # ``response.content``, which would hold multi-GB weights in RAM.
            # ``stream=True`` also leaves the body unread so the status code is
            # checked before any bytes are pulled. A mid-stream drop is NOT
            # resumed; presigned HTTPS URLs come through this same branch.
            response = request_with_retries(
                "GET",
                artifact_uri,
                timeout=settings.request_timeout,
                max_retries=settings.request_max_retries,
                backoff_base=settings.request_backoff_base,
                unreachable_code="registry_unreachable",
                stream=True,
            )
            # Always closed: ``stream=True`` leaves the body unread, so a raise
            # anywhere below leaks the pooled connection until the pool
            # exhausts.
            try:
                if response.status_code // 100 != 2:
                    raise ModelManagerError(
                        code="registry_unreachable",
                        message=f"Failed to download {artifact_uri} ({response.status_code})",
                        retryable=True,
                        http_status=502,
                    )
                with dest.open("wb") as fh:
                    for chunk in response.iter_content(chunk_size=_CHUNK):
                        if chunk:
                            fh.write(chunk)
            finally:
                response.close()
            return

        if scheme in ("file", ""):
            # Local filesystem path (``file://`` URI or a bare path).
            src = (
                Path(unquote(urlparse(artifact_uri).path))
                if scheme == "file"
                else Path(artifact_uri)
            )
            if not src.exists():
                raise ModelManagerError(
                    code="not_found",
                    message=f"Artifact source not found: {src}",
                    retryable=False,
                    http_status=404,
                )
            shutil.copyfile(src, dest)
            return

        # s3:// is not handled directly: DFW resolves object-store locators to
        # presigned HTTPS URLs before the pull, so they arrive through http(s).
        # TODO: resolve mlflow:// artifacts into the model repo.
        raise ModelManagerError(
            code="internal",
            message=(
                f"Unsupported artifact scheme {scheme!r} "
                "(file/http(s) implemented; s3/mlflow not supported)"
            ),
            retryable=False,
            http_status=501,
        )


#: Locators ``_download`` can actually fetch. Shared with the pre-flight check
#: so the two cannot drift into disagreeing about what is supported.
_SUPPORTED_SCHEMES = frozenset({"http", "https", "file", ""})

#: Transport failures a re-drive can never fix, because the locator is wrong.
#: NOT the whole ``RequestException`` tree: a mid-download connection reset is
#: also a transport failure, and there a retry is the only recovery.
_UNFIXABLE_LOCATOR_ERRORS = (
    requests.exceptions.InvalidURL,
    requests.exceptions.MissingSchema,
    requests.exceptions.InvalidSchema,
    requests.exceptions.URLRequired,
    requests.exceptions.TooManyRedirects,
)


def _reject_unsupported_scheme(uri: str) -> None:
    """Raise before anything is created if the locator cannot be fetched.

    Called ahead of ``mkdir`` so a request that cannot succeed leaves nothing on
    a shared mount. ``_download`` keeps its own dispatch; this is the pre-flight.
    """
    scheme = urlparse(uri).scheme.lower()
    if scheme in _SUPPORTED_SCHEMES:
        return
    raise ModelManagerError(
        code="internal",
        message=(
            f"Unsupported artifact scheme {scheme!r} "
            "(file/http(s) implemented; s3/mlflow not supported)"
        ),
        retryable=False,
        http_status=501,
    )


def _budget_exhausted(name: str, version: str) -> ModelManagerError:
    """Return the error for a load that ran out of its build budget.

    Retryable: the budget expiring says nothing about whether the build can
    succeed, and the next attempt re-attaches rather than starting over.
    """
    return ModelManagerError(
        code="readiness_timeout",
        message=(
            f"{name}@{version} had not finished building after {settings.build_poll_timeout:.0f}s"
        ),
        retryable=True,
        http_status=504,
    )


def repo_versions(name: str) -> set[str]:
    """Return the version directories present for ``name`` in the model repo.

    Wider than a backend's status view: a version staged but never loaded,
    evicted, or excluded by a version-policy pin has a directory and no row.

    Never raises; callers read the empty set as "not known to be on disk", never
    as "nothing is there". The path is checked to be one level under the repo
    root, so a name cannot walk out.
    """
    try:
        repo_root = Path(settings.model_repo_path).resolve()
        model_dir = Path(settings.model_repo_path) / name
        relative = model_dir.resolve().relative_to(repo_root)
        if len(relative.parts) != 1:
            logger.warning("refusing to list %r: it is not a direct child of the model repo", name)
            return set()
        if not model_dir.is_dir():
            return set()
        return {entry.name for entry in model_dir.iterdir() if entry.is_dir()}
    except Exception as exc:  # - see the docstring
        logger.warning("could not list version directories for %s: %s", name, exc)
        return set()


def _claim_staging(repo_dir: Path, name: str, version: str) -> None:
    """Mark ``repo_dir`` as being staged into, until the version is loadable.

    deft publishes a directory that merely exists as deletable within seconds --
    measured: ``mkdir 9`` turned ``deletable: ['2','4']`` into ``['2','4','9']``,
    and ``rm -rf 9`` turned it back. That list is the only delete authority, so
    the backend nominates an in-flight pull's own directory.

    Held until after the LOAD, not until the bytes land: an engine build writes
    back into the directory.

    Leaks toward retention: a killed pull never removes the marker, and the fixed
    filename means only the next pull of the SAME version clears it -- so two
    overlapping pulls share one, safe only because pulls are serialised today
    (one uvicorn worker, blocking handler).

    Never raises; an unwritable marker falls back to the age floor.
    """
    lock_path = repo_dir / STAGING_LOCK_FILENAME
    try:
        if lock_path.exists():
            logger.warning(
                "%s@%s carries a leaked %s from an earlier interrupted pull (%s); "
                "it has been uncollectable since then and this pull now owns it",
                name,
                version,
                STAGING_LOCK_FILENAME,
                lock_path.read_text(errors="replace").strip() or "contents unreadable",
            )
        lock_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "claimed_at": time.time(),
                    "claimed_at_iso": datetime.now(UTC).isoformat(),
                    "pid": os.getpid(),
                    "model_name": name,
                    "version": version,
                }
            )
        )
    except OSError as exc:
        logger.warning(
            "could not write %s for %s@%s (%s): this pull's directory is protected "
            "only by the age floor",
            STAGING_LOCK_FILENAME,
            name,
            version,
            exc,
        )


def _release_staging(repo_dir: Path, name: str, version: str) -> None:
    """Clear the staging marker now that the version is published.

    Never raises: the leak is toward retention, and a pull that staged everything
    must not fail because the marker would not come off.
    """
    try:
        (repo_dir / STAGING_LOCK_FILENAME).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            "could not remove %s from %s@%s (%s): the directory will refuse reclaim "
            "until the marker is cleared",
            STAGING_LOCK_FILENAME,
            name,
            version,
            exc,
        )


def staging_in_progress(name: str, version: str) -> bool:
    """Whether a pull is currently staging into this version's directory.

    True means refuse, and any read error is True.

    Two exceptions: an absent directory is False (no bytes to lose, and True
    would make cleanup of an already-gone version raise), and a marker older than
    ``staging_lock_max_age_s`` reads as abandoned, logged at WARNING. It is not
    removed here; this is a read.

    Lets ``_version_dir``'s rejection escape, so the containment proof covers
    this read too.
    """
    directory = _version_dir(name, version)
    lock_path = directory / STAGING_LOCK_FILENAME
    try:
        # ``stat``, not ``Path.exists``: only ENOENT may answer False here, and
        # ``exists`` also answers False for ENOTDIR and ELOOP.
        marker = lock_path.stat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning(
            "could not read %s for %s@%s (%s): treating it as being staged",
            STAGING_LOCK_FILENAME,
            name,
            version,
            exc,
        )
        return True

    bound = settings.staging_lock_max_age_s
    age = _staging_claim_age_seconds(lock_path, marker.st_mtime)
    if bound <= 0 or age <= bound:
        return True
    logger.warning(
        "%s@%s carries a %s claimed %.0fs ago, past the %.0fs bound: the pull that wrote "
        "it is not running and the marker leaked. Reading it as abandoned and allowing "
        "reclaim; the marker said %s",
        name,
        version,
        STAGING_LOCK_FILENAME,
        age,
        bound,
        _marker_contents(lock_path),
    )
    return False


def _staging_claim_age_seconds(lock_path: Path, mtime: float) -> float:
    """Seconds since the staging marker was claimed, on the YOUNGEST clock available.

    Two clocks set at claim time: ``claimed_at`` inside the marker and the file's
    mtime. Younger wins -- the fail-closed direction, so a back-dated
    ``claimed_at`` still expires on the filesystem's clock.
    """
    now = time.time()
    ages = [now - mtime]
    try:
        claimed = json.loads(lock_path.read_text()).get("claimed_at")
    except (OSError, ValueError, AttributeError):
        # Unreadable, not JSON, or not an object -- the mtime alone then decides.
        claimed = None
    if isinstance(claimed, int | float) and not isinstance(claimed, bool):
        ages.append(now - claimed)
    return min(ages)


def _marker_contents(lock_path: Path) -> str:
    """The marker's raw text for an operator, or a note that it could not be read."""
    try:
        return lock_path.read_text(errors="replace").strip() or "(empty)"
    except OSError as exc:
        return f"(unreadable: {exc})"


# Kept apart from the ``None`` for a directory that could not be READ: both stop
# the age comparison, but only ``None`` is a refusal.
DIRECTORY_ABSENT: Final = "absent"


def version_age_seconds(name: str, version: str) -> float | Literal["absent"] | None:
    """Seconds since the version directory was last modified, or a non-answer.

    Feeds cleanup's age floor, so anything ambiguous is None and the caller
    refuses. An absent directory is ``DIRECTORY_ABSENT``: folded into None, a
    reclaim that had already removed the directory was refused forever.

    The NEWEST mtime anywhere in the tree, not the directory's own: the
    directory's mtime does not move while a download writes into a scratch file
    that already exists, so any pull longer than the floor read as OLD.

    Defence in depth behind ``staging_in_progress`` only: a re-drive on the digest
    short-circuit writes nothing into the tree.
    """
    try:
        directory = _version_dir(name, version)
    except ModelManagerError:
        raise
    except Exception as exc:  # - an unreadable path is "unknown"
        logger.warning("could not resolve a version directory for %s@%s: %s", name, version, exc)
        return None
    try:
        newest = directory.stat().st_mtime
    except FileNotFoundError:
        # Neither "old enough" nor "unreadable".
        return DIRECTORY_ABSENT
    except OSError as exc:
        logger.info("could not stat the version directory for %s@%s: %s", name, version, exc)
        return None
    try:
        for entry in directory.rglob("*"):
            try:
                # ``lstat``, so a symlink reports its own mtime: a dangling one
                # would raise and lose the answer, and one pointing out of the
                # repo would let an unrelated file decide.
                newest = max(newest, entry.lstat().st_mtime)
            except FileNotFoundError:
                # Listed a moment ago and gone now -- a scratch file renamed into
                # place, which happens on every pull. The youngest age there is,
                # not an unknowable one.
                return 0.0
    except OSError as exc:
        # Every other error stays unknown, and None is read as a refusal.
        logger.info("could not read the mtimes under %s@%s: %s", name, version, exc)
        return None
    return max(0.0, time.time() - newest)


def version_directory_absent(name: str, version: str) -> bool:
    """True only when the version directory is OBSERVED to be missing.

    This retires a caller's nomination, so it must be a positive observation --
    an unreadable mount answers False. Otherwise a filesystem the sweep cannot see
    reports as a completed reclaim: a growing volume that looks converged.

    Neither ``repo_versions`` nor ``DIRECTORY_ABSENT`` can serve: the first
    answers the empty set for both cases, the second walks the whole tree and runs
    only under an age floor.
    """
    try:
        directory = _version_dir(name, version)
        if not Path(settings.model_repo_path).is_dir():
            # The root proves the volume is mounted at all: without it an
            # unmounted volume looks exactly like a repo swept clean, and every
            # version reports as reclaimed.
            logger.warning(
                "the model repository %s is not a directory, so nothing under it can be "
                "shown absent; treating %s@%s as unproven",
                settings.model_repo_path,
                name,
                version,
            )
            return False
        directory.stat()
    except ModelManagerError:
        # A refused path is a bad request, not an absence.
        raise
    except FileNotFoundError:
        return True
    except Exception as exc:  # - anything else is "could not find out"
        logger.info(
            "could not stat the version directory for %s@%s, so its absence is unproven: %s",
            name,
            version,
            exc,
        )
    return False


def _version_dir(name: str, version: str) -> Path:
    """Return ``{repo}/{name}/{version}/``, proven to be inside the repo root.

    The last line of defence for the ``mkdir`` that stages a version and the
    ``rmtree`` that retires one. Both take their path from request body fields,
    and path joining is silently destructive: ``..`` climbs out, an absolute
    segment discards the root, and an EMPTY segment collapses the join by a level
    so a per-version delete lands on the whole model's directory.

    The schema rejects those already; duplicated here because the proof belongs
    next to the irreversible call, where it survives a caller building a request
    object directly and a later relaxation of the schema. Resolving both sides
    also catches a symlinked version directory pointing out of the repository.

    "Exactly two components below the root", not a prefix test: a prefix test
    accepts both a deeper path and the root itself.
    """
    repo_root = Path(settings.model_repo_path).resolve()
    version_dir = Path(settings.model_repo_path) / name / version
    try:
        relative = version_dir.resolve().relative_to(repo_root)
    except ValueError:
        relative = None
    if relative is None or len(relative.parts) != 2:
        raise ModelManagerError(
            code="bad_request",
            message=(
                f"Refusing to touch {name!r}@{version!r}: it does not resolve to a single "
                f"version directory inside the model repository at {repo_root}"
            ),
            retryable=False,
            http_status=400,
        )
    return version_dir


def _derived_idempotency_key(name: str, version: str, sha256: str) -> str:
    """Return the fallback idempotency key for a load.

    A caller key wins: DFW sends ``{promotion_id}:pull``, stable across its own
    retries, which is what re-attaches a build after a restart. This covers
    callers that send none.

    The preimage is the weights digest ONLY, not the spec or threshold, because a
    version's contents are meant to be immutable (DFW mints a fresh version per
    candidate). So a caller that mutates a staged version in place and omits its
    own key re-attaches to the previous build. Do not "fix" that by hashing the
    version directory: the serving build writes model.onnx/model.plan back into
    it, so the key would change after every build and rebuild forever.
    """
    digest = hashlib.sha256(f"{name}:{version}:{sha256.lower()}".encode()).hexdigest()
    return f"sha256:{digest}"


def deterministic_build_failure(detail: str) -> str | None:
    """Return the operator remedy for a build failure retrying cannot fix, else None.

    A conversion that cannot write into the version directory fails with a
    ``PermissionError``, and retrying cannot fix a mount.

    TWO processes write under that repository as different users: MM stages as
    UID 10001 (``appuser``, see the Dockerfile) and the serving process builds as
    UID 1000, so the remedy names both.

    Shared with the unified adapter, which reports every build outcome through one
    blanket ``500 MODEL_OPERATION_FAILED`` and needs exactly this classification.

    Matched on the full ``permission denied`` phrase, not the bare word
    ``permission``, so a message mentioning permissions in passing keeps its
    retries. NOT narrowed to a filename: an errno-13 on any file in the directory
    has the same cause and the same fix.
    """
    if "permission denied" in (detail or "").lower():
        return (
            f"The version directory under {settings.model_repo_path} must be writable by both "
            "the Model Manager (UID 10001), which stages into it, and the serving process "
            "(UID 1000), which builds into it — set securityContext.fsGroup, or an equivalent "
            "shared group on a bind mount, on the shared model repository"
        )
    return None


@contextlib.contextmanager
def _staging_errors(name: str, version: str, source: str | None = None):
    """Turn an OS-level staging failure into the frozen error envelope.

    Without this an ``OSError`` escapes the handler, which catches only
    ``ModelManagerError``, and the caller gets a 500 enveloped by the last-resort
    handler -- losing both the operator remedy and the non-retryable verdict.

    Wrapped once around the whole of ``_stage``, not per operation: per block it
    covered the download and missed the ``mkdir``, which is the first write of the
    verb and so the one that always fires on an unwritable mount.

    ``requests``' exceptions inherit from ``OSError``, so a malformed artifact URL
    arrives here too; those are classified separately below rather than called
    retryable. ``ModelManagerError`` is re-raised untouched so the digest verdict
    keeps its code, and a ``TypeError`` from a bug in here is left to the
    application handler rather than dressed up as IO.

    The traceback is LOGGED before the envelope is raised: converting the
    exception is also what would otherwise make the server side silent.
    """
    try:
        yield
    except ModelManagerError:
        raise
    except OSError as exc:
        detail = f"{exc.__class__.__name__}: {exc}"
        remedy = deterministic_build_failure(detail)
        # ONLY the parse-level failures, never the whole ``RequestException``
        # tree: a connection reset partway through a multi-GB download is the one
        # class where a re-drive is both safe and the ONLY recovery, since
        # ``_download`` does not resume a broken stream and
        # ``request_with_retries`` covers request INITIATION only.
        caller_fault = isinstance(exc, _UNFIXABLE_LOCATOR_ERRORS)
        # %r, not %s: a newline in the caller-supplied source would otherwise
        # forge whole log records.
        logger.exception(
            "staging %s@%s failed under %s from %r: %s",
            name,
            version,
            settings.model_repo_path,
            source,
            detail,
        )
        raise ModelManagerError(
            code="internal",
            message=(
                f"Failed to stage {name}@{version} into {settings.model_repo_path}"
                # str(exc) carries the path for OS-level errors but not for a
                # transport one. Labelled "artifact", not "the failing URL": a
                # spec or sidecar step can fail too, and this names the pull.
                + (f" (artifact {source})" if source else "")
                + f": {detail}"
                + (f". {remedy}" if remedy else "")
            ),
            # Neither a permission denial nor a malformed URL survives a retry.
            retryable=remedy is None and not caller_fault,
            http_status=500,
        ) from exc


def _staging_tmp_path(final_path: Path) -> Path:
    """Return a request-private sibling temp path for publishing ``final_path``.

    Unique per call because a fixed ``*.tmp`` is shared state: two overlapping
    pulls for the same ``{name, version}`` leave the loser's ``os.replace``
    raising a bare ``FileNotFoundError``, which escapes the envelope as an
    un-enveloped 500. They cannot overlap today (one uvicorn worker, blocking
    handler), so this is hardening for the day that changes.

    NOT ``tempfile.mkstemp``: that creates the file 0600 and ``os.replace``
    preserves the temp's mode, so the sidecar would be unreadable by the serving
    stack, which runs as a different uid -- and an unreadable ``metadata.json`` is
    a silent fallback to a default threshold, i.e. a changed decision boundary on
    live traffic. An explicit ``chmod`` is wrong the other way: it would drop
    group-write under umask 002 and add world-read under umask 077.

    Dot-prefixed and uuid-suffixed so an orphaned temp never reads as a real
    artifact, and a sibling of ``final_path`` so the rename stays atomic.

    Do NOT pair this with a sweep of stale ``.*.tmp``: with per-request names that
    would delete a concurrent request's in-flight temp.
    """
    return final_path.with_name(f".{final_path.name}.{uuid4().hex}.tmp")


def _artifact_filename(artifact_uri: str) -> str:
    """Return the canonical weights filename the inference resolves on load.

    The deployed inference loads by ``{model_name, version}`` and reads a
    canonical ``model.<ext>``, so the weights are staged under that name, not the
    source basename. The extension comes from the URI suffix
    (``.pth``/``.onnx``/``.plan``); anything else defaults to ``model.pth``.
    """
    path = urlparse(artifact_uri).path if "://" in artifact_uri else artifact_uri
    suffix = os.path.splitext(path.rstrip("/"))[1].lower()
    if suffix in (".pth", ".onnx", ".plan"):
        return f"model{suffix}"
    return "model.pth"


def _sha256_of(path: Path) -> str:
    """Return the lowercase hex sha256 digest of a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()
