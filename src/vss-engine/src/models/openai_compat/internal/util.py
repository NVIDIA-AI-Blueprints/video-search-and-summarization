######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

import json
import os
import time
from pathlib import Path

import requests

from via_logger import logger


def get_oauth_token(p_token_url, p_client_id, p_client_secret, p_scope, refresh_before_seconds=10):
    file_name = "py_llm_oauth_token.json"
    token = None
    try:
        # base_path = Path(__file__).parent
        base_path = "/tmp/via/"
        if not os.path.exists(base_path):
            os.makedirs(base_path)
        file_path = Path.joinpath(Path(base_path), file_name)
        # logger.info(f"file_path {file_path}")
        # print(f"file_path {file_path}")
    except Exception as e:
        logger.info(f"Error occurred while setting file path: {e}")
        return None

    try:
        # Check if the token is cached
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                token = json.load(f)
        if token and time.time() + refresh_before_seconds < token["expires_by"]:
            logger.info(
                f"token is current:  {time.time() + refresh_before_seconds} {token['expires_by']}"
                f" {time.time() + refresh_before_seconds - token['expires_by']}"
            )
            # token is current
            pass
        else:
            # Get a new token from the OAuth server
            response = requests.post(
                p_token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": p_client_id,
                    "client_secret": p_client_secret,
                    "scope": p_scope,
                },
            )
            response.raise_for_status()
            token = response.json()
            token["expires_by"] = time.time() + token["expires_in"]
            with open(file_path, "w") as f:
                json.dump(token, f)
            logger.info("got new auth token")
    except Exception as e:
        logger.info(f"Error occurred while getting OAuth token: {e}")
        raise Exception("Failed to authenticate with LLM Gateway. Check LLM Gateway credentails")
        return None

    authToken = token["access_token"]
    return authToken


def get_oauth_token_simple(p_token_url, p_client_id, p_client_secret, p_scope):
    # Get a new token from the OAuth server
    response = requests.post(
        p_token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": p_client_id,
            "client_secret": p_client_secret,
            "scope": p_scope,
        },
    )
    response.raise_for_status()
    token = response.json()

    authToken = token["access_token"]
    return authToken


def get_nv_oauth_token(refresh_before_seconds=10):
    logger.debug("Setting key for NVIDIA LLM Gateway")
    # Define your credentials and URL
    client_id = os.environ.get("NV_LLMG_CLIENT_ID")

    client_secret = os.environ.get("NV_LLMG_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise Exception("NVIDIA LLM Gateway credentials not set")
    # Please use this URL for retrieving token
    # https://prod.api.nvidia.com/oauth/api/v1/ssa/default/token
    token_url = "https://prod.api.nvidia.com/oauth/api/v1/ssa/default/token"
    # Please use this Scope for Azure OpenAI: azureopenai-readwrite
    scope = "azureopenai-readwrite"

    try:
        token = get_oauth_token(
            token_url,
            client_id,
            client_secret,
            scope,
            refresh_before_seconds=refresh_before_seconds,
        )
        return token
    except Exception as e:
        logger.error(f"Error occurred while calling OpenAI: {e}")
        raise Exception("Failed to authenticate with LLM gateway. Check credentials")
    return None


def is_nv_secret_configured():
    return "NV_LLMG_CLIENT_SECRET" in os.environ and os.environ["NV_LLMG_CLIENT_SECRET"]


endpoint = "https://prod.api.nvidia.com/llm/v1/azure/"
