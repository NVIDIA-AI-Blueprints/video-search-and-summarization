# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""NGC Model Download helper."""

import os
import re
import shutil
import subprocess
from importlib.metadata import PackageNotFoundError, version
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import requests.exceptions

from common.logger import logger

SUPPORTED_HF_HUB_VERSION = "0.36.2"
HF_MODEL_SPEC = re.compile(
    r"^(?P<repo>[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"@(?P<revision>[0-9a-f]{40,64})$"
)
HF_REVISION_MARKER = ".hf-revision"


def _validate_hf_endpoint(endpoint: str | None) -> str | None:
    """Validate an optional Hub-compatible endpoint without selecting a fallback."""
    if not endpoint:
        return None
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "HF_ENDPOINT must be an HTTP(S) origin without credentials or query data"
        )
    return endpoint.rstrip("/")


def _require_supported_hf_client() -> None:
    try:
        installed = version("huggingface_hub")
    except PackageNotFoundError:
        raise RuntimeError(
            f"huggingface_hub=={SUPPORTED_HF_HUB_VERSION} is required for hf: model paths"
        ) from None
    if installed != SUPPORTED_HF_HUB_VERSION:
        raise RuntimeError(
            "Unsupported Hugging Face Hub client: "
            f"expected {SUPPORTED_HF_HUB_VERSION}, found {installed}"
        )


def download_model_hf(model_spec: str, download_path_prefix: str) -> str:
    """Download a revision-pinned Hugging Face model snapshot.

    ``model_spec`` must be ``owner/repository@<immutable commit>``. The Hub
    client's cache is temporary; the materialized local directory retains the
    historical service model layout and is reused only when its revision marker
    matches.
    """
    match = HF_MODEL_SPEC.fullmatch(model_spec)
    if not match:
        raise ValueError(
            "Hugging Face model paths must use "
            "hf:owner/repository@<40-64 lowercase hex immutable commit>"
        )
    repo_id = match.group("repo")
    revision = match.group("revision")
    model_name = repo_id.rsplit("/", 1)[-1]
    model_dir = os.path.join(download_path_prefix, model_name)
    revision_marker = os.path.join(model_dir, HF_REVISION_MARKER)

    if os.path.isdir(model_dir):
        try:
            with open(revision_marker, encoding="utf-8") as marker:
                cached_revision = marker.read().strip()
        except OSError:
            raise RuntimeError(
                f"Existing model directory {model_dir} has no verifiable immutable revision; "
                "remove it before using an hf: model path"
            ) from None
        if cached_revision != revision:
            raise RuntimeError(
                f"Existing model directory {model_dir} is revision {cached_revision}, "
                f"not requested revision {revision}"
            )
        logger.info(f"Using model cached at {model_dir}")
        return model_dir

    endpoint = _validate_hf_endpoint(os.environ.get("HF_ENDPOINT"))
    if endpoint:
        os.environ["HF_ENDPOINT"] = endpoint
    else:
        # An empty Compose expansion overrides huggingface_hub's official
        # default during import even when endpoint=None is passed below.
        os.environ.pop("HF_ENDPOINT", None)
    _require_supported_hf_client()
    # These must be set before importing huggingface_hub. Xet/CAS would bypass
    # an HF_ENDPOINT resolve cache.
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
    from huggingface_hub import constants, snapshot_download  # noqa: PLC0415

    if not constants.HF_HUB_DISABLE_XET:
        raise RuntimeError(
            "huggingface_hub was initialized before HF_HUB_DISABLE_XET=1; "
            "set it in the container environment before process startup"
        )

    logger.info(f"Downloading Hugging Face model {repo_id}@{revision} ...")
    os.makedirs(download_path_prefix, exist_ok=True)
    with (
        TemporaryDirectory(
            prefix=".hf-model-", dir=download_path_prefix
        ) as staging_dir,
        TemporaryDirectory(prefix="rtvi-hf-home-") as hf_home,
    ):
        try:
            snapshot_download(
                repo_id=repo_id,
                revision=revision,
                endpoint=endpoint,
                token=os.environ.get("HF_TOKEN") or None,
                cache_dir=hf_home,
                local_dir=staging_dir,
            )
            # local_dir writes client bookkeeping under .cache/huggingface.
            # It is not model content and the previous Git path did not expose
            # it in the materialized model directory.
            hub_metadata = os.path.join(staging_dir, ".cache", "huggingface")
            if os.path.exists(hub_metadata):
                shutil.rmtree(hub_metadata)
            try:
                os.rmdir(os.path.join(staging_dir, ".cache"))
            except OSError:
                pass
            with open(
                os.path.join(staging_dir, HF_REVISION_MARKER), "w", encoding="utf-8"
            ) as marker:
                marker.write(f"{revision}\n")
            # TemporaryDirectory is 0700, while the historical Git clone
            # materialized a traversable model root under the process umask.
            os.chmod(staging_dir, 0o755)
            os.rename(staging_dir, model_dir)
        except Exception as ex:
            raise RuntimeError(
                f"Failed to download Hugging Face model {repo_id}@{revision}: {ex}"
            ) from ex
    logger.info(f"Downloaded model to {model_dir}")
    return model_dir


def download_model(ngc_model: str, download_path_prefix: str, model_type: str = ""):
    """Download a model from NGC

    Args:
        ngc_model: NGC model in the format "model:version"
        download_path_prefix: Path to download the model in.
        Another directory would be created inside this.
    Returns:
        Path to the directory where the model is downloaded.
    """
    try:
        # Parse the model name, version and NGC org
        model_name_full, version = ngc_model.split(":")
        parts = model_name_full.split("/")
        org = parts[0]
        team = parts[1] if len(parts) == 3 else "no-team"
        model_name = parts[2] if len(parts) == 3 else parts[1]
    except Exception:
        raise Exception(f"{ngc_model} does not look like an NGC model")

    # Check if the model is already downloaded.
    # Use underscores instead of dots in the version so the cache dir is a valid Python
    # identifier — HuggingFace dynamic_module_utils uses the basename as a module namespace
    # and a literal dot (e.g. "v1.0") breaks relative imports inside trust_remote_code models.
    sanitized_version = version.replace(".", "_")
    model_dir = os.path.join(
        download_path_prefix, f"{model_name_full.replace('/', '_')}_{sanitized_version}"
    )
    if os.path.exists(model_dir):
        logger.info(f"Using model cached at {model_dir}")
        return model_dir

    # Create a NGC client and authenticate with NGC
    os.environ["NGC_CLI_API_KEY"] = os.environ["NGC_API_KEY"]
    os.environ["NGC_CLI_ORG"] = org
    if team:
        os.environ["NGC_CLI_TEAM"] = team
    from ngcsdk import Client  # noqa: E402

    clt = Client()

    logger.info(f"Downloading model {ngc_model} ...")

    # Download the model to a temporary directory first and then move it to the
    # user requested path.
    with TemporaryDirectory() as td:
        try:
            clt.registry.model.download_version(ngc_model, td)
        except requests.exceptions.HTTPError as ex:
            raise Exception(
                f"Model download failed with status code {ex.status_code}."
                " Check if NGC_API_KEY and model path is correct"
            )
        except Exception as ex:
            if "not Authenticated" in ex.args[0]:
                raise Exception(
                    "Could not authenticate with NGC."
                    " Check if NGC_API_KEY and model path is correct."
                )
            if "could not be found" in ex.args[0]:
                raise Exception(
                    "Could not find the model. Check if model path is correct."
                )
            raise ex from None
        os.makedirs(download_path_prefix, exist_ok=True)
        shutil.move(os.path.join(td, f"{model_name}_v{version}"), model_dir)
    logger.info(f"Downloaded model to {model_dir}")
    return model_dir


def download_model_git(git_url: str, download_path_prefix: str):
    """Download a model from git

    Args:
        git_url: Git URL for the model
        download_path_prefix: Path to download the model in.
        Another directory would be created inside this.
    Returns:
        Path to the directory where the model is downloaded.
    """

    if git_url.startswith(("https://huggingface.co/", "https://hf.co/")):
        raise ValueError(
            "Hugging Face Git URLs bypass HF_ENDPOINT; use "
            "hf:owner/repository@<immutable commit>"
        )

    model_name = git_url.rstrip(".git").split("/")[-1]

    # Check if the model is already downloaded

    model_dir = os.path.join(download_path_prefix, f"{model_name.replace('/', '_')}")

    if os.path.exists(model_dir):
        logger.info(f"Using model cached at {model_dir}")
        return model_dir

    logger.info(f"Downloading model {model_name} ...")

    # Download the model to a temporary directory first and then move it to the
    # user requested path.
    with TemporaryDirectory() as td:
        try:
            run_cmd = ["git", "clone", git_url, td]
            subprocess.run(
                run_cmd,
                check=True,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            subprocess.run(
                ["rm", "-rf", td + "/.git"], check=True, stdin=subprocess.DEVNULL
            )
        except Exception:
            raise Exception(
                f"Failed to download model {model_name} from {git_url}"
            ) from None
        os.makedirs(download_path_prefix, exist_ok=True)
        shutil.move(str(td), str(model_dir))
    logger.info(f"Downloaded model to {model_dir}")
    return model_dir
