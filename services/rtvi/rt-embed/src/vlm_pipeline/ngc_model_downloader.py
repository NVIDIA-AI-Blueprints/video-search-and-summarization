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
import shutil
import subprocess
from tempfile import TemporaryDirectory

import requests.exceptions

from common.logger import logger


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
                raise Exception("Could not find the model. Check if model path is correct.")
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
            if git_url.startswith("https://huggingface.co/") or git_url.startswith(
                "https://hf.co/"
            ):
                run_cmd = [
                    "hf",
                    "download",
                    git_url.replace("https://huggingface.co/", "").replace("https://hf.co/", ""),
                    "--local-dir",
                    td,
                ]
            else:
                run_cmd = ["git", "clone", git_url, td]
            subprocess.run(
                run_cmd,
                check=True,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            subprocess.run(["rm", "-rf", td + "/.git"], check=True, stdin=subprocess.DEVNULL)
        except Exception:
            raise Exception(f"Failed to download model {model_name} from {git_url}") from None
        os.makedirs(download_path_prefix, exist_ok=True)
        shutil.move(str(td), str(model_dir))
    logger.info(f"Downloaded model to {model_dir}")
    return model_dir
