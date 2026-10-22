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

import sys
from time import sleep

import requests

from src.backends.nmp.dms_client import DMSClient
from src.config import settings
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.nemo.llm_as_judge")


class LLMAsJudge:
    def __init__(self):
        self.config = settings.system_config.llm_judge_config
        self.config_type = self.config.deployment_type

    def spin_up_llm_judge(self) -> bool:
        """Spin up the local LLM Judge if it is not already deployed."""
        dms_client = DMSClient(nim=self.config)

        if not dms_client.is_deployed():
            logger.info(f"Deploying LLM Judge {self.config.model_name}")

            try:
                dms_client.deploy_model()
            except Exception as e:
                logger.error(f"Error deploying LLM Judge {self.config.model_name}: {e}")
                raise e
        else:
            logger.info(f"LLM Judge {self.config.model_name} is already deployed")

        return True

    def validate_llm_judge_availability(self) -> bool:
        """Ensure the configured LLM judge endpoint is reachable.

        If the judge is configured as a *remote* service we make a minimal
        inference request ("hi") to the chat-completion endpoint to verify it
        is operational.  Any failure will raise an exception so that the
        hosting process (e.g. a Celery worker) fails fast instead of running
        without a functional judge.
        """

        # No check needed for local (NIM) judge
        # it will be spun-up inside NMP
        if not self.config.is_remote:
            return self.spin_up_llm_judge()

        url = self.config.url
        model_name = self.config.model_name

        if not url or not model_name:
            raise RuntimeError("Remote LLM judge configuration is missing 'url' or 'model_name'.")

        headers = {"Content-Type": "application/json"}
        # Optional API key support - many internal endpoints don't need it, but
        # when provided we include it.
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": "hi"}],
        }

        try:
            resp = requests.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                return False

            # Basic structural validation - we expect a JSON with a 'choices' field
            data = resp.json()
            if "choices" not in data:
                return False

            logger.info("Remote LLM judge is reachable and responded successfully.")

            return True

        except Exception:
            logger.exception("Unable to reach remote LLM judge - aborting worker startup.")
            # Re-raise to fail fast (Celery worker will exit)
            return False


def validate_llm_judge():
    """
    Validate NGC API key and LLM judge availability.
    Exits the application if validation fails after 3 retry attempts.
    """
    # bring up local LLM judge or check if remote LLM judge is available
    # if remote is not reachable, exit with error after 3 retries
    llm_as_judge = LLMAsJudge()
    max_retries = 3
    retry_delay = 10  # seconds

    for attempt in range(1, max_retries + 1):
        logger.info(
            f"Attempting to validate LLM judge availability (attempt {attempt}/{max_retries})"
        )

        try:
            llm_judge_available = llm_as_judge.validate_llm_judge_availability()

            if llm_judge_available:
                logger.info("LLM judge is available!")
                return

            logger.warning(f"LLM judge validation failed on attempt {attempt}/{max_retries}")

        except Exception as e:
            logger.warning(f"LLM judge validation error on attempt {attempt}/{max_retries}: {e}")

        # If this is not the last attempt, wait before retrying
        if attempt < max_retries:
            logger.info(f"Retrying in {retry_delay} seconds...")
            sleep(retry_delay)
            # Increase delay for subsequent retries (exponential backoff)
            retry_delay *= 2

    # If we get here, all retries failed
    logger.error("""
        **************************************************
        *                                                *
        *  Remote Evaluator LLM judge is not available!  *
        *  Did you set the correct API key?              *
        *  `NVIDIA_API_KEY` needs to be set.             *
        *                                                *
        *  Exiting                                       *
        *                                                *
        **************************************************
        """)
    sys.exit(1)
