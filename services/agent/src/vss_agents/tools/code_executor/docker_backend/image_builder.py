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
import atexit
import io
import logging
import tarfile
from typing import Any
from typing import TypedDict

import docker

logger = logging.getLogger(__name__)


class ImageInfo(TypedDict):
    image_tag: str
    base_image: str
    system_packages: list[str]
    language_packages: list[str]


class ImageBuilder:
    # Class-level type annotations for attributes set in __new__
    client: Any
    _image_cache: dict[str, ImageInfo]
    _image_usage_count: dict[str, int]
    """
    ImageBuilder is a singleton class that builds Docker images for different languages.
    It uses the docker SDK to build the images.

    This singleton is shared across all tools and persists for the application lifetime.
    Images are only cleaned up when the process exits.
    """

    _instance: "ImageBuilder | None" = None
    _cleanup_registered: bool = False

    def __new__(cls) -> "ImageBuilder":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.client = docker.from_env()
            cls._instance._image_cache = {}
            cls._instance._image_usage_count = {}  # Track usage count for each image

            # Register cleanup on process exit
            if not cls._cleanup_registered:
                atexit.register(cls._cleanup_at_exit)
                cls._cleanup_registered = True
                logger.info("ImageBuilder singleton created and cleanup registered")
        return cls._instance

    @classmethod
    def _cleanup_at_exit(cls) -> None:
        """Cleanup handler for process exit."""
        if cls._instance is not None:
            logger.info("Running ImageBuilder cleanup at exit...")
            cls.reset_instance()

    def __del__(self) -> None:
        """Cleanup when the singleton is garbage collected."""
        # Note: This might not always be called reliably, atexit is more reliable
        try:
            self.cleanup()
        except Exception as e:
            logger.error(f"Error during ImageBuilder cleanup: {e}")

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance and cleanup resources.
        This should only be called on application shutdown."""
        if cls._instance is not None:
            try:
                cls._instance.cleanup()
            except Exception as e:
                logger.error(f"Error during cleanup: {e}")
            finally:
                cls._instance = None

    def cleanup(self) -> None:
        """Cleanup all Docker images in the cache.
        Warning: This removes ALL cached images. Only call on shutdown."""
        logger.info(f"Cleaning up {len(self._image_cache)} cached Docker images...")
        removed_count = 0
        failed_count = 0

        for _, image_info in self._image_cache.items():
            try:
                self.client.images.remove(image_info["image_tag"], force=True)
                logger.info(f"Removed Docker image: {image_info['image_tag']}")
                removed_count += 1
            except docker.errors.ImageNotFound:
                logger.debug(f"Image already removed: {image_info['image_tag']}")
            except Exception as e:
                logger.warning(f"Failed to remove Docker image {image_info['image_tag']}: {e}")
                failed_count += 1

        self._image_cache.clear()
        self._image_usage_count.clear()
        logger.info(f"Cleanup complete: {removed_count} images removed, {failed_count} failed")

    def _generate_dockerfile(
        self,
        base_image: str,
        system_packages: None | list[str] = None,
        language_packages: None | list[str] = None,
    ) -> str:
        """Generate Dockerfile content based on config"""

        # Start building Dockerfile
        dockerfile = [f"FROM {base_image}"]

        # Install system packages
        if system_packages:
            if "debian" in base_image or "ubuntu" in base_image or "python" in base_image:
                packages_str = " ".join(system_packages)
                dockerfile.extend(
                    [
                        "",
                        "# Install system dependencies",
                        "RUN apt-get update && apt-get install -y --no-install-recommends \\",
                        f"    {packages_str} \\",
                        "    && rm -rf /var/lib/apt/lists/*",
                    ]
                )
            elif "alpine" in base_image:
                packages_str = " ".join(system_packages)
                dockerfile.extend(["", "# Install system dependencies", f"RUN apk add --no-cache {packages_str}"])

        # Install language-specific packages
        if language_packages:
            if "python" in base_image:
                packages_str = " ".join(language_packages)
                dockerfile.extend(
                    ["", "# Install Python packages", "RUN pip install --no-cache-dir \\", f"    {packages_str}"]
                )
            elif "node" in base_image:
                packages_str = " ".join(language_packages)
                dockerfile.extend(
                    ["", "# Install Node.js packages globally", "RUN npm install -g \\", f"    {packages_str}"]
                )
        # Create non-root user
        user_uid = 1000
        user_name = "executor"
        working_dir = "/work"

        dockerfile.extend(
            [
                "",
                f"# Create non-root user with UID {user_uid}",
                f"RUN useradd -m -u {user_uid} -s /bin/bash {user_name}",
                "",
                "# Set working directory",
                f"WORKDIR {working_dir}",
                "",
                "# Switch to non-root user",
                f"USER {user_name}",
                "",
                "# Default command",
                'CMD ["/bin/bash"]',
            ]
        )

        return "\n".join(dockerfile)

    def _create_dockerfile_tar(self, dockerfile_content: str) -> bytes:
        """Create a tar archive containing the Dockerfile"""
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as tar:
            dockerfile_bytes = dockerfile_content.encode("utf-8")
            info = tarfile.TarInfo("Dockerfile")
            info.size = len(dockerfile_bytes)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(dockerfile_bytes))
        data.seek(0)
        return data.getvalue()

    def build_image(
        self,
        image: str,
        base_image: str,
        system_packages: None | list[str] = None,
        language_packages: None | list[str] = None,
        force_rebuild: bool = False,
    ) -> str:
        """Build Docker image for specified language"""
        image_tag = f"deep-search/{image}-executor"

        # Check if image already exists
        if not force_rebuild and image in self._image_cache:
            try:
                self.client.images.get(image_tag)
                logger.info(f"Image {image_tag} already exists. Using cached version.")
                return image_tag
            except docker.errors.ImageNotFound as err:
                raise ValueError(f"Image {image_tag} not found. Please rebuild the image.") from err

        logger.info(f"Building image for {image}...")

        # Generate Dockerfile
        dockerfile_content = self._generate_dockerfile(base_image, system_packages, language_packages)
        dockerfile_tar = self._create_dockerfile_tar(dockerfile_content)

        # Build image
        try:
            # Build with progress output
            build_logs = self.client.api.build(
                fileobj=io.BytesIO(dockerfile_tar), custom_context=True, tag=image_tag, rm=True, decode=True
            )

            # Print build progress
            for log in build_logs:
                if "stream" in log:
                    logger.info(log["stream"].strip())

            logger.info(f"Successfully built image: {image_tag}")
            self._image_cache[image] = ImageInfo(
                image_tag=image_tag,
                base_image=base_image,
                system_packages=system_packages or [],
                language_packages=language_packages or [],
            )
            return image_tag

        except docker.errors.BuildError as e:
            print(f"Failed to build image: {e}")
            raise

    def get_image_tag(self, image: str) -> str | None:
        """Get the image tag for the specified language"""
        return self._image_cache[image]["image_tag"]

    def get_all_images(self) -> dict[str, ImageInfo]:
        """Get all images' information
        Returns:
            dict[str, ImageInfo]: A dictionary of image information, the key is the image name, the value is the image information
        """
        return self._image_cache
