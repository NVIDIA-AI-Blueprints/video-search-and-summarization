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
"""Unit tests for s3_picture_url module."""

from pydantic import ValidationError
import pytest

from vss_agents.tools.s3_picture_url import S3PictureURLConfig
from vss_agents.tools.s3_picture_url import S3PictureURLInput
from vss_agents.tools.s3_picture_url import S3PictureURLOutput


class TestS3PictureURLConfig:
    """Test S3PictureURLConfig model."""

    def test_defaults(self):
        config = S3PictureURLConfig()
        assert config.minio_url == "http://localhost:9000"
        assert config.access_key == "minioadmin"
        assert config.secret_key == "minioadmin"
        assert config.bucket_name == "my-bucket"

    def test_custom_values(self):
        config = S3PictureURLConfig(
            minio_url="http://minio-server:9000",
            access_key="custom-access",
            secret_key="custom-secret",
            bucket_name="custom-bucket",
        )
        assert config.minio_url == "http://minio-server:9000"
        assert config.access_key == "custom-access"
        assert config.secret_key == "custom-secret"
        assert config.bucket_name == "custom-bucket"


class TestS3PictureURLInput:
    """Test S3PictureURLInput model."""

    def test_valid_sensor_id(self):
        input_data = S3PictureURLInput(sensor_id="sensor-001")
        assert input_data.sensor_id == "sensor-001"

    def test_various_sensor_ids(self):
        sensor_ids = ["sensor-001", "camera_123", "stream-abc", "x"]
        for sid in sensor_ids:
            input_data = S3PictureURLInput(sensor_id=sid)
            assert input_data.sensor_id == sid

    def test_empty_sensor_id_fails(self):
        with pytest.raises(ValidationError):
            S3PictureURLInput(sensor_id="")

    def test_missing_sensor_id_fails(self):
        with pytest.raises(ValidationError):
            S3PictureURLInput()


class TestS3PictureURLOutput:
    """Test S3PictureURLOutput model."""

    def test_output_creation(self):
        output = S3PictureURLOutput(
            image_url="http://minio:9000/bucket/image.png",
            base64_frame="base64encodeddata==",
            video_url="http://minio:9000/bucket/video.mp4",
        )
        assert output.image_url == "http://minio:9000/bucket/image.png"
        assert output.base64_frame == "base64encodeddata=="
        assert output.video_url == "http://minio:9000/bucket/video.mp4"

    def test_output_serialization(self):
        output = S3PictureURLOutput(
            image_url="http://example.com/image.png",
            base64_frame="SGVsbG8gV29ybGQ=",
            video_url="http://example.com/video.mp4",
        )
        data = output.model_dump()
        assert "image_url" in data
        assert "base64_frame" in data
        assert "video_url" in data

    def test_output_various_urls(self):
        urls = [
            ("http://localhost:9000/bucket/img.png", "data", "http://localhost:9000/bucket/vid.mp4"),
            ("https://s3.amazonaws.com/bucket/img.jpg", "base64", "https://s3.amazonaws.com/bucket/vid.mkv"),
            ("http://minio/assets/snapshot.png", "frame", "http://minio/assets/recording.mp4"),
        ]
        for img_url, b64, vid_url in urls:
            output = S3PictureURLOutput(
                image_url=img_url,
                base64_frame=b64,
                video_url=vid_url,
            )
            assert output.image_url == img_url
            assert output.video_url == vid_url
