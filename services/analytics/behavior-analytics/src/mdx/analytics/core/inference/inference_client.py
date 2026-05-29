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

import logging
import math

import numpy as np
from tritonclient.grpc import InferenceServerClient, InferInput, InferRequestedOutput

from mdx.analytics.core.schema.proto import ext_pb2 as extSchema

logger = logging.getLogger(__name__)


class InferenceClient:
    """
    Client for Triton Inference Server over gRPC.

    :ivar str server_url: URL of the Triton server.
    :ivar InferenceServerClient client: gRPC client for the server.
    """

    def __init__(self, server_url: str) -> None:
        """
        Initialize the inference client.

        :param str server_url: URL of the Triton server.
        """
        self.server_url = server_url
        self.client = InferenceServerClient(url=server_url)

    def server_live(self) -> bool:
        """
        Check liveness of the inference server.

        This method sends a request to check if the inference server is live and responding.

        :return bool: True if the server is live, False if not live

        Examples::
            >>> client = InferenceClient("localhost:8001")
            >>> if client.server_live():
            ...     print("Server is live")
        """
        try:
            response = self.client.is_server_live()
            return response
        except Exception as e:
            logger.info(f"Server : {e}")
            return False

    def server_ready(self) -> bool:
        """
        Check readiness of the inference server.

        This method checks if the inference server is ready to accept inference requests.

        :return bool: True if the server is ready, False if not ready

        Examples::
            >>> client = InferenceClient("localhost:8001")
            >>> if client.server_ready():
            ...     print("Server is ready for inference")
        """
        try:
            response = self.client.is_server_ready()
            return response
        except Exception as e:
            logger.info(f"Server : {e}")
            return False

    def server_metadata(self) -> dict | None:
        """
        Get server metadata.

        This method retrieves metadata about the inference server, including version,
        extensions, and other server information.

        :return Dict | None: The JSON dict holding the server metadata, None if there is an error

        Examples::
            >>> client = InferenceClient("localhost:8001")
            >>> metadata = client.server_metadata()
            >>> print(f"Server version: {metadata['version']}")
        """
        try:
            response = self.client.get_server_metadata()
            return response
        except Exception as e:
            logger.info(f"Server : {e}")
            return None

    def model_ready(self, model: str, version: str = "") -> bool:
        """
        Check readiness of a model in the inference server.

        This method checks if a specific model is ready to accept inference requests.

        :param str model: The name of the model
        :param str version: The version of the model to check readiness
        :return bool: True if the model is ready, False if not ready

        Examples::
            >>> client = InferenceClient("localhost:8001")
            >>> if client.model_ready("my_model"):
            ...     print("Model is ready for inference")
        """
        try:
            response = self.client.is_model_ready(model, version)
            logger.info(f"model : {model} ready : {response}")
            return response
        except Exception as e:
            logger.info(f"Server : {e}")
            return False

    def model_metadata(self, model: str, version: str = "") -> dict | None:
        """
        Get model metadata (name, version, platform, inputs, outputs).

        This method retrieves metadata about a specific model, including its inputs,
        outputs, and configuration.

        :param str model: The name of the model
        :param str version: The version of the model to get metadata
        :return Dict | None: The JSON dict holding the model metadata, None if there is an error

        Examples::
            >>> client = InferenceClient("localhost:8001")
            >>> metadata = client.model_metadata("my_model")
            >>> print(f"Model inputs: {metadata['inputs']}")
        """
        try:
            response = self.client.get_model_metadata(model, version)
            return response
        except Exception as e:
            logger.info(f"Server : {e}")
            return None

    def model_config(self, model: str, version: str = "") -> dict | None:
        """
        Contact the inference server and get the configuration for specified model.

        This method retrieves the configuration details for a specific model,
        including its backend settings and optimization parameters.

        :param str model: The name of the model
        :param str version: The version of the model to get configuration
        :return Dict | None: The JSON dict holding the model config, None if there is an error

        Examples::
            >>> client = InferenceClient("localhost:8001")
            >>> config = client.model_config("my_model")
            >>> print(f"Model backend: {config['backend']}")
        """
        try:
            response = self.client.get_model_config(model, version)
            return response
        except Exception as e:
            logger.info(f"Server : {e}")
            return None

    def row_major(self, tensor: list[list[list[float]]], tr_length: int = 100) -> list[list[list[float]]]:
        """
        Transform an input tensor to row major format, before sending the gRPC request.

        This method converts a 3D tensor into a 2D row-major format array, which is
        required for sending data to the inference server.

        :param list[list[list[float]]] tensor: Input tensor
        :param int tr_length: Target length for interpolation
        :return list[list[list[float]]]: Transformed tensor with interpolated trajectories

        Examples::
            >>> client = InferenceClient("localhost:8001")
            >>> tensor = [[[1.0, 2.0], [3.0, 4.0]], [[5.0, 6.0], [7.0, 8.0]]]
            >>> result = client.row_major(tensor, 100)
            >>> print(f"Transformed tensor shape: {len(result)}x{len(result[0])}")
        """

        new_tensor = []
        for tr in tensor:
            xy = self.linear_interpolate(tr, tr_length)
            new_tensor.append(xy)
        return new_tensor

    def linear_interpolate(self, coordinates: list[list[float]], target_length: int = 100) -> list[list[float]]:
        """
        Generates a locations coordinate array with desired length.

        This method can take a dimension of [40,2] and translate it to [100,2], where target_length=100.
        Similarly, it can handle longer locations arrays (e.g., [120,2]) and translate them to [100,2].

        :param list[list[float]] coordinates: Input 2D Coordinates
        :param int target_length: Desired target length
        :return list[list[float]]: Interpolated coordinates of length target_length

        Examples::
            >>> client = InferenceClient("localhost:8001")
            >>> coords = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
            >>> result = client.linear_interpolate(coords, 100)
            >>> print(f"Interpolated coordinates length: {len(result)}")
        """

        coordinates_length = len(coordinates)
        xy = []
        interval = (coordinates_length - 1) * 1.0 / (target_length - 1)

        for i in range(target_length):
            index = i * interval
            lower_index = math.floor(index)
            higher_index = lower_index + 1
            fraction = higher_index - index

            x = (
                coordinates[coordinates_length - 1][0]
                if i == (target_length - 1)
                else coordinates[lower_index][0] * fraction + coordinates[higher_index][0] * (1 - fraction)
            )

            y = (
                coordinates[coordinates_length - 1][1]
                if i == (target_length - 1)
                else coordinates[lower_index][1] * fraction + coordinates[higher_index][1] * (1 - fraction)
            )

            xy.append([x, y])

        return xy

    def infer_with_protobuf(self, sensor_id: str, behaviors: list[extSchema.Behavior]) -> tuple[list[int], str]:
        """
        Calls Triton inference server and update the behaviors with additional info.

        This method sends a batch of behaviors to the inference server and updates them
        with cluster indices and model version information.

        :param str sensor_id: Sensor id
        :param list[extSchema.Behavior] behaviors: List of Behaviors
        :return tuple[list[int], str]: Tuple containing list of cluster indices and model version

        Examples::
            >>> client = InferenceClient("localhost:8001")
            >>> behaviors = [Behavior(...), Behavior(...)]
            >>> cluster_indices, model_version = client.infer_with_protobuf("sensor1", behaviors)
            >>> print(f"Cluster indices: {cluster_indices}")
            >>> print(f"Model version: {model_version}")
        """

        tr_length = 100
        feature_size = 2
        version = ""
        output_data = []
        modelVersion = ""

        model = sensor_id
        model_ready = self.model_ready(model)

        if model_ready:
            batch_size = len(behaviors)
            tensor = [[list(x.point) for x in data.locations.coordinates] for data in behaviors]

            interpolated_input_data = self.row_major(tensor, tr_length)
            input_data = np.array(interpolated_input_data, dtype=np.float64)
            input_data = input_data.reshape(batch_size, tr_length, feature_size)
            input_tensor = InferInput(name="input__0", shape=input_data.shape, datatype="FP64")
            input_tensor.set_data_from_numpy(input_data)
            output_tensor = InferRequestedOutput("output__0")
            response = self.client.infer(
                model_name=model,
                inputs=[input_tensor],
                outputs=[output_tensor],
                model_version=version,
            )

            if response:
                resp_arr = response.as_numpy('output__0')
                if resp_arr is not None and resp_arr.size > 0:
                    output_data = resp_arr.tolist()
                    logger.info(f"output_data : {output_data}")
                    output_as_protobuf = response.get_response(False)
                    modelVersion = output_as_protobuf["model_version"]

                    return (output_data, modelVersion)
        
        return [], ""

    def close(self) -> None:
        """Close the inference client connection."""
        if self.client:
            self.client.close()
