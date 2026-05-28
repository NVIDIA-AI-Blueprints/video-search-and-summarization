# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import base64
from collections.abc import AsyncGenerator
import logging

import boto3
import cv2
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger(__name__)


class S3PictureURLConfig(FunctionBaseConfig, name="s3_picture_url"):
    """Configuration for the S3 Picture URL tool."""

    minio_url: str = Field(
        "http://localhost:9000",
        description="The endpoint URL of the MinIO server",
    )
    access_key: str = Field(
        "minioadmin",
        description="The access key of the S3 bucket",
    )
    secret_key: str = Field(
        "minioadmin",
        description="The secret key of the S3 bucket",
    )
    bucket_name: str = Field(
        "my-bucket",
        description="The name of the S3 bucket to use for video storage",
    )


class S3PictureURLInput(BaseModel):
    """Input for the S3 Picture URL tool"""

    sensor_id: str = Field(
        ...,
        description="The stream ID to get video URL for",
        min_length=1,
    )


class S3PictureURLOutput(BaseModel):
    """Output for the VST Video URL tool"""

    image_url: str = Field(
        ...,
        description="Direct URL to access the image file",
    )
    base64_frame: str = Field(
        ...,
        description="Base64 encoded frame",
    )
    video_url: str = Field(
        ...,
        description="Direct URL to access the video file",
    )


@register_function(config_type=S3PictureURLConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def s3_picture_url(config: S3PictureURLConfig, _builder: Builder) -> AsyncGenerator[FunctionInfo]:
    s3_client = boto3.client(
        "s3",
        endpoint_url=config.minio_url,
        aws_access_key_id=config.access_key,
        aws_secret_access_key=config.secret_key,
        region_name="us-east-1",
        verify=True,
    )

    async def _s3_picture_url(s3_picture_url_input: S3PictureURLInput) -> S3PictureURLOutput:
        """
        S3 Picture URL tool that gets the first frame from a stored video file in the s3 bucket.

        Input:
            sensor_id: str, the sensor ID of the video to get the picture URL for


        Output:
            picture_url: str, the URL of the first frame of the video, served from the S3 bucket
        """
        try:
            logger.info(f"Getting video URL for sensor {s3_picture_url_input.sensor_id}")
            #
            # use cv2 to get the first frame of the video
            video_path = s3_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": config.bucket_name,
                    "Key": s3_picture_url_input.sensor_id + ".mp4",
                },
                ExpiresIn=3600,
            )

            cap = cv2.VideoCapture(video_path)
            _ret, frame = cap.read()
            cap.release()
            _, buffer = cv2.imencode(".jpg", frame)
            # store the frame as jpg in the S3 bucket
            file_name = s3_picture_url_input.sensor_id + ".jpg"
            # use s3 client to upload the frame to the S3 bucket
            s3_client.put_object(
                Bucket=config.bucket_name,
                Key=file_name,
                Body=buffer.tobytes(),
                ContentType="image/jpeg",
            )
            image_url = s3_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": config.bucket_name,
                    "Key": file_name,
                },
                ExpiresIn=3600,
            )

            base64_frame = base64.b64encode(buffer.tobytes()).decode("utf-8")

            return S3PictureURLOutput(
                image_url=image_url,
                base64_frame=base64_frame,
                video_url=video_path,
            )

        except Exception as e:
            logger.error(f"Error getting S3 video/picture URL: {e}")
            raise

    yield FunctionInfo.create(
        single_fn=_s3_picture_url,
        description=_s3_picture_url.__doc__,
        input_schema=S3PictureURLInput,
        single_output_schema=S3PictureURLOutput,
    )
