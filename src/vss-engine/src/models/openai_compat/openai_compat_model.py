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

import base64
import os
import sys

import numpy
import torch
from langchain_openai import AzureChatOpenAI

from via_logger import TimeMeasure, logger

OPENAI_RECONNECT_ATTEMPTS = 3


def jpeg_single_tensor_to_array_of_numpys(tensor):
    """
    Takes a PyTorch tensor of shape (1, 10, N) and returns an array of 10 numpy arrays.
    (1,10) are example lengths - could be any
    1: number of chunks
    10: n_frms in each chunk
    """
    # Unstack the tensor into 10 PyTorch tensors
    unstacked_tensors = tensor.squeeze(0).unbind(0)

    # Convert the PyTorch tensors to numpy arrays
    numpy_arrays = [t.cpu().numpy() for t in unstacked_tensors]

    return numpy_arrays


def tensor_to_base64_jpeg(tensor, idx=0):
    """
    Selects one JPEG at index=idx from a PyTorch tensor containing N X NumPy arrays
    representing N JPEG images and converts to a base64 encoded string.

    Args:
        tensor (torch.Tensor): The PyTorch tensor containing the NumPy arrays.

    Returns:
        str: The base64 encoded string representing the JPEG image at idx.
    """

    numpy_arrays = jpeg_single_tensor_to_array_of_numpys(tensor)
    # Convert the tensor to a NumPy array
    numpy_array = numpy_arrays[idx]

    # Convert the encoded image to bytes
    encoded_image_bytes = numpy_array.tobytes()

    # Encode the bytes as base64
    base64_encoded = base64.b64encode(encoded_image_bytes)

    # Decode the base64 bytes to a string
    base64_string = base64_encoded.decode("utf-8")

    return base64_string


class CompOpenAIModel:
    def configure_azure_openai(
        self, key=None, azureEndpointConfigured=False, nvSecretConfigured=False
    ):

        # Configure endpoint
        self._endpoint = ""
        if azureEndpointConfigured:
            # The environment variable is set to a valid string
            self._endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
            logger.info(f"Azure OpenAI Endpoint: {self._endpoint}")

            # Run your code here if the environment variable is set
            # ...
        elif nvSecretConfigured:
            from models.openai_compat.internal.util import endpoint

            self._endpoint = endpoint
        else:
            # The environment variable is not set or is an empty string
            logger.info("Azure OpenAI Endpoint environment variable is not set or is empty.")
        os.environ["AZURE_OPENAI_ENDPOINT"] = self._endpoint

        # configure key
        if nvSecretConfigured:
            if key is None:
                from models.openai_compat.internal.util import get_nv_oauth_token

                self._key = get_nv_oauth_token(120)
                os.environ["AZURE_OPENAI_API_KEY"] = self._key
            else:
                self._key = key
                os.environ["AZURE_OPENAI_API_KEY"] = self._key

        if self._model_name:
            self._model = AzureChatOpenAI(model=self._model_name, deployment_name=self._model_name)

    # Configure common environments between Azure Open AI and Open AI APIs
    def configure_openai_common(self):
        if "OPENAI_API_VERSION" in os.environ and os.environ["OPENAI_API_VERSION"]:
            self._openai_api_version = os.environ["OPENAI_API_VERSION"]
        else:
            logger.warning(
                "OPENAI_API_VERSION is not configured;"
                " May be required for certain model deployments;"
            )
        if "AZURE_OPENAI_API_VERSION" in os.environ and os.environ["AZURE_OPENAI_API_VERSION"]:
            self._azure_openai_api_version = os.environ["AZURE_OPENAI_API_VERSION"]
        else:
            logger.info(
                "AZURE_OPENAI_API_VERSION is not configured;"
                " May be required for certain model deployments;"
            )
        # Model config:
        if (
            "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME" in os.environ
            and os.environ["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"]
        ):
            self._model_name = os.environ["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"]
        else:
            logger.error("VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME is not configured")

    def configure_openai(self):
        from openai import OpenAI

        if "OPENAI_API_KEY" in os.environ and os.environ["OPENAI_API_KEY"]:
            logger.info("OPENAI_API_KEY configured")
        else:
            logger.error("OPENAI_API_KEY not configured")
        if self._model_name:
            if "VIA_VLM_ENDPOINT" in os.environ and os.environ["VIA_VLM_ENDPOINT"]:
                self._endpoint = base_url = os.environ["VIA_VLM_ENDPOINT"]
                logger.info(f"VIA_VLM_ENDPOINT is configured to {base_url}")
                if "VIA_VLM_API_KEY" in os.environ and os.environ["VIA_VLM_API_KEY"]:
                    logger.info("VIA_VLM_API_KEY is configured")
                    self._key = os.environ["VIA_VLM_API_KEY"]
                    self._client = OpenAI(
                        base_url=base_url, api_key=self._key, max_retries=OPENAI_RECONNECT_ATTEMPTS
                    )
                else:
                    logger.info("VIA_VLM_API_KEY is not configured; will try use OPENAI_API_KEY")
                    self._client = OpenAI(base_url=base_url, max_retries=OPENAI_RECONNECT_ATTEMPTS)
            else:
                logger.info("VIA_VLM_ENDPOINT is not configured; using OpenAI() default")
                self._client = OpenAI(max_retries=OPENAI_RECONNECT_ATTEMPTS)
                self._endpoint = "https://api.openai.com/v1/"  # default
                if not os.environ.get("OPENAI_API_KEY", ""):
                    raise Exception("OPENAI_API_KEY not configured")

    def init_gpt_4(self, key=None):
        self._key = key

        # Model config:
        if (
            "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME" in os.environ
            and os.environ["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"]
        ):
            self._model_name = os.environ["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"]

        self.configure_openai_common()
        self._azureEndpointConfigured = (
            "AZURE_OPENAI_ENDPOINT" in os.environ and os.environ["AZURE_OPENAI_ENDPOINT"]
        )

        try:
            from models.openai_compat.internal.util import is_nv_secret_configured

            self._nvSecretConfigured = is_nv_secret_configured()
        except ModuleNotFoundError:
            self._nvSecretConfigured = False

        if self._azureEndpointConfigured or self._nvSecretConfigured:
            self.configure_azure_openai(
                key,
                azureEndpointConfigured=self._azureEndpointConfigured,
                nvSecretConfigured=self._nvSecretConfigured,
            )
        else:
            self.configure_openai()

    def __init__(self, test_api_call=False) -> None:
        self._model_name = None
        self._model = None
        self._client = None
        self._endpoint = ""
        self.init_gpt_4()
        # Overwrite environment with final selected endpoint
        logger.info(f"endpoint is {self._endpoint}")
        os.environ["VIA_VLM_ENDPOINT"] = self._endpoint
        if test_api_call:
            self.generate("", [[]], [[]], None, None)

    @property
    def model_name(self):
        return self._model_name

    @property
    def model_config(self):
        return None

    def get_conv(self):
        return self._conv.copy()

    @staticmethod
    def get_model_info():
        api_type = "openai"
        if (
            "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME" in os.environ
            and os.environ["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"]
        ):
            id = os.environ["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"]
        else:
            id = "ModelNotLoaded"
            logger.error("VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME is not configured")
        if "VIA_VLM_ENDPOINT" in os.environ and os.environ["VIA_VLM_ENDPOINT"]:
            owned_by = os.environ["VIA_VLM_ENDPOINT"]
            owned_by = "".join(
                char.replace(".", "-").replace("/", "-")
                for char in owned_by
                if char.isalnum() or char in "./"
            )
        else:
            owned_by = "ModelNotLoaded"
            logger.info("VIA_VLM_ENDPOINT is not configured")
        return id, api_type, owned_by

    def generate(
        self, prompt, video_embeds, video_frames_times, generation_config=None, chunk=None
    ):
        responses = []

        if not generation_config:
            generation_config = {}

        if "temperature" not in generation_config:
            generation_config["temperature"] = 0.2

        if "max_new_tokens" not in generation_config:
            generation_config["max_new_tokens"] = 1024

        if "top_p" not in generation_config:
            generation_config["top_p"] = 1

        if "seed" in generation_config:
            seed = generation_config["seed"]
            generation_config.pop("seed")
        else:
            seed = 1
        numpy.random.seed(seed)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)

        if chunk:
            if len(video_frames_times) != len(chunk):
                logger.error("chunk size not matching in openai-compat generate")

        for tidx, video_frames_times_ in enumerate(video_frames_times):
            num_of_embeds_in_one_chunk = int(len(video_frames_times_))
            image_list = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": (
                            "data:image/jpeg;base64," + tensor_to_base64_jpeg(video_embeds[tidx], j)
                        ),
                        "detail": "auto",
                    },
                }
                for j in range(num_of_embeds_in_one_chunk)
            ]
            string_of_times = ""
            string_timestamp = ""
            time_format_str = ""

            for j in range(num_of_embeds_in_one_chunk):
                if chunk:
                    if tidx <= len(chunk):
                        string_timestamp = chunk[tidx].get_timestamp(video_frames_times_[j])
                        if not time_format_str:
                            if chunk[tidx].file.startswith("rtsp://"):
                                time_format_str = " at timestamps in RFC3339 format"
                            else:
                                time_format_str = " at timestamps in seconds"
                    else:
                        logger.error("Chunk ID going out of chunk size")
                        string_timestamp = str(video_frames_times_[j])
                        time_format_str = " at timestamps in seconds"

                else:
                    string_timestamp = str(video_frames_times_[j])
                    time_format_str = " at timestamps in seconds"

                string_of_times += "<" + string_timestamp + "> "

            PROMPT = (
                "These are images sampled from a video "
                + time_format_str
                + " : "
                + string_of_times
                + "."
                + prompt
                + "Make sure the answer contain correct timestamps."
            )

            logger.debug(f"PROMPT is  {PROMPT}")
            messages = [
                {
                    "role": "user",
                    "content": [
                        *image_list,
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ]
            if self._nvSecretConfigured:
                from models.openai_compat.internal.util import get_nv_oauth_token

                new_key = get_nv_oauth_token(120)
                if self._key != new_key:
                    logger.info("NV key changed:")
                    # re-initialize azure:
                    self.init_gpt_4(new_key)
                else:
                    logger.info("No change in NV key")

            with TimeMeasure("OpenAI model inference"):
                logger.debug("Invoke call")
                try:
                    if self._model:
                        response_obj = self._model.invoke(
                            messages,
                            max_tokens=generation_config["max_new_tokens"],
                            temperature=generation_config["temperature"],
                            seed=seed,
                            top_p=generation_config["top_p"],
                        )
                        content = response_obj.content
                    elif self._client:
                        resp = self._client.chat.completions.create(
                            model=self._model_name,
                            messages=messages,
                            max_tokens=generation_config["max_new_tokens"],
                            temperature=generation_config["temperature"],
                            seed=seed,
                            top_p=generation_config["top_p"],
                        )
                        content = ""
                        for choice in resp.choices:
                            content += str(choice.message.content)
                    logger.debug("Invoke call done")
                    logger.debug(f"content is {str(content)}")
                    response = content
                except Exception as ex:
                    import traceback

                    exc_type, exc_value, exc_traceback = sys.exc_info()
                    error_string = "".join(
                        traceback.format_exception(exc_type, exc_value, exc_traceback)
                    )
                    logger.info(error_string)
                    response = error_string
                    raise ex from None

                responses.append(response)
        return responses, [
            {"input_tokens": 0, "output_tokens": 0},
        ] * len(responses)


if __name__ == "__main__":
    # To test and debug, please use harness:
    # PYTHONPATH=src pytest tests/model/gpt4/test_gpt4v_jpeg_tensor_gen.py -s
    # Please add new test case for each bug
    pass
