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
"""VIA Asset Management Module."""

import asyncio
import json
import os
import shutil
import time
import uuid
from threading import Thread
from typing import Callable

import aiofiles

from via_exception import ViaException
from via_logger import TimeMeasure, logger

AGE_OUT_THRESHOLD = 0.9  # Start aging out when usage is within this threshold of the max
AGE_OUT_RUN_INTERVAL_SEC = 300


class Asset:
    """VIA Asset. Can be a file or a live stream."""

    def __init__(
        self,
        asset_id: str,
        path: str,
        purpose: str,
        media_type: str,
        asset_dir: str,
        fileName="",
        username="",
        password="",
        description="",
        video_fps=None,
        camera_id="",
    ) -> None:
        """Asset constructor.

        Args:
            asset_id: Unique ID for the asset
            path: Path for the asset. Path to the file or RTSP URL
            purpose: Purpose of the file.
            media_type: Media Type (video/image) of the file.
            asset_dir: Directory where the asset information and other files related
                       to the asset are stored.
            fileName (optional): Name of the file. Defaults to "".
            username (optional): Username to access the live stream. Defaults to "".
            password (optional): Password to access the live stream. Defaults to "".
            description (optional): Description of the asset (live-stream only). Defaults to "".
            video_fps (optional): Cached video FPS. Defaults to None.
            camera_id (optional): Camera ID to be used for the asset. Defaults to "".
        """
        self._asset_id = asset_id
        self._filename = fileName
        self._purpose = purpose
        self._media_type = media_type
        self._path = path
        self._use_count = 0
        self._asset_dir = asset_dir
        self._description = description
        self._username = username
        self._password = password
        self._video_fps = video_fps
        self._camera_id = camera_id

    @classmethod
    def fromdir(cls, asset_dir):
        with open(os.path.join(asset_dir, "info.json")) as f:
            info = json.load(f)

            return Asset(
                asset_id=info["assetId"],
                path=info["path"],
                fileName=info["fileName"],
                purpose=info["purpose"],
                media_type=info.get("media_type", "video"),
                username=info["username"],
                password=info["password"],
                description=info["description"],
                asset_dir=asset_dir,
                video_fps=info.get("video_fps", None),
                camera_id=info.get("camera_id", ""),
            )

    @property
    def asset_id(self):
        """Unique ID of the asset"""
        return self._asset_id

    @property
    def filename(self):
        """Name of the file"""
        return self._filename

    @property
    def purpose(self):
        """Purpose of the file"""
        return self._purpose

    @property
    def media_type(self):
        """Media type of the file"""
        return self._media_type

    @property
    def path(self):
        """Path to the file / live stream URL"""
        return self._path

    @property
    def description(self):
        """Description of the asset (live-stream only)"""
        return self._description

    @property
    def username(self):
        """Username to access the live stream"""
        return self._username

    @property
    def password(self):
        """Password to access the live stream"""
        return self._password

    @property
    def asset_dir(self):
        """Storage directory for the asset"""
        return self._asset_dir

    @property
    def camera_id(self):
        """Camera ID to be used for the asset"""
        return self._camera_id

    def lock(self):
        """Lock the asset. Asset cannot be deleted if in use."""
        self._use_count += 1

    def unlock(self):
        """Unock the asset"""
        self._use_count -= 1

    @property
    def use_count(self):
        """Reference count for the file"""
        return self._use_count

    @property
    def is_live(self):
        """Boolean indicating if the asset is a live stream."""
        return self.path.startswith("rtsp://")

    @property
    def video_fps(self):
        """Cached video FPS."""
        return self._video_fps

    def update_video_fps(self, fps: float):
        """Update the cached video FPS and save to info.json.

        Args:
            fps: Video frames per second
        """
        self._video_fps = fps

        # Update the info.json file
        info_path = os.path.join(self._asset_dir, "info.json")
        if os.path.exists(info_path):
            with open(info_path, "r") as f:
                info = json.load(f)

            info["video_fps"] = fps

            with open(info_path, "w") as f:
                json.dump(info, f)


class AssetManager:
    """VIA Asset Manager. Responsible for managing the assets - files & live streams
    added to the backend server."""

    def __init__(
        self,
        asset_dir: str,
        max_storage_usage_gb=None,
        asset_removal_callback: Callable[[Asset], bool] = None,
    ) -> None:
        """Default constructor

        Args:
            asset_dir: Path to the directory to store assets in
        """
        self._asset_dir = asset_dir
        self._max_storage_usage_gb = max_storage_usage_gb
        self._asset_removal_callback = asset_removal_callback

        try:
            os.makedirs(self._asset_dir, exist_ok=True)
        except Exception:
            raise ViaException(f"Could not create assets directory '{asset_dir}'")

        # Get existing assets and populate the asset map.
        asset_ids = self._get_existing_asset_ids()
        self._asset_map: dict[str, Asset] = {
            asset_id: Asset.fromdir(os.path.join(asset_dir, asset_id)) for asset_id in asset_ids
        }

        self._aged_out_assets = []
        if self._max_storage_usage_gb:
            self._age_out_thread = Thread(target=self._age_out_thread_func, daemon=True)
            self._age_out_thread.start()

    async def save_file(self, file, file_name, purpose: str, media_type: str, camera_id: str):
        """Save the uploaded as a file.

        Args:
            file: File object to save file.
            file_name: Name of the file.
            purpose: Purpose of the file.
            media_type: Media type (video/image) of the file.
            camera_id: Camera ID to be used for the file.
        Returns:
            A unique id for the asset.
        """
        # Generate a unique id for the asset.
        asset_id = str(uuid.uuid4())
        while asset_id in self._asset_map:
            asset_id = str(uuid.uuid4())

        asset_dir = os.path.join(self._asset_dir, asset_id)
        try:
            os.makedirs(asset_dir)
        except Exception:
            raise ViaException("Could not create directory for asset")

        current_storage_size = await self._get_storage_usage()
        current_file_size = 0

        # Write the uploaded file to assets directory
        async with aiofiles.open(os.path.join(asset_dir, file_name), "wb") as f:
            while chunk := await file.read(1024 * 1024 * 10):
                current_file_size += len(chunk)

                # Check if writing the current chunk will cross threshold
                if self._max_storage_usage_gb and (
                    current_storage_size + current_file_size / (1024.0**3)
                    > AGE_OUT_THRESHOLD * self._max_storage_usage_gb
                ):
                    # Try to clean assets
                    await self._age_out_assets()
                    current_storage_size = await self._get_storage_usage()
                    current_file_size = 0

                # Check if writing the current chunk will cross max size
                if self._max_storage_usage_gb and (
                    current_storage_size + current_file_size / (1024.0**3)
                    > self._max_storage_usage_gb
                ):
                    #
                    f.close()
                    try:
                        shutil.rmtree(asset_dir)
                    except Exception:
                        pass
                    raise ViaException(
                        "Asset storage full. Could not remove existing older assets"
                        " because they are in use",
                        "ServerBusy",
                        503,
                    )
                await f.write(chunk)

        # Save asset info as json
        with open(os.path.join(asset_dir, "info.json"), "w") as f:
            json.dump(
                {
                    "assetId": asset_id,
                    "path": os.path.join(asset_dir, file_name),
                    "fileName": file_name,
                    "purpose": purpose,
                    "media_type": media_type,
                    "username": "",
                    "password": "",
                    "description": "",
                    "video_fps": None,
                    "camera_id": camera_id,
                },
                f,
            )

        # add an entry in the asset map
        self._asset_map[asset_id] = Asset.fromdir(asset_dir)

        logger.info(f"[AssetManager] Saved file - asset-id: {asset_id} name: {file_name}")

        await self._age_out_assets()

        return asset_id

    def add_file(self, file_path, purpose, media_type, camera_id="", reuse_asset=False):
        """Add a file already on the file system as a path.

        Args:
            file_path: Path of the file to add.
            purpose: Purpose of the file.
            media_type: Media type (video/image) of the file.
            camera_id: Camera ID to be used for the file.
            reuse_asset: Whether to reuse an existing asset.
        Returns:
            A unique id for the asset.
        """
        if not os.path.isfile(file_path):
            raise ViaException(f"{file_path} is not a valid file", "InvalidParameters", 400)

        if reuse_asset:
            asset = self._get_asset_id_for_file(file_path)
            if asset:
                logger.info(f"Reusing asset id {asset.asset_id} for {file_path}")
                return asset.asset_id

        # Generate a unique id for the asset.
        asset_id = str(uuid.uuid4())
        while asset_id in self._asset_map:
            asset_id = str(uuid.uuid4())
        asset_dir = os.path.join(self._asset_dir, asset_id)

        try:
            os.makedirs(asset_dir)
        except Exception:
            raise ViaException("Could not create directory for asset")

        # Save asset info as json
        with open(os.path.join(asset_dir, "info.json"), "w") as f:
            json.dump(
                {
                    "assetId": asset_id,
                    "path": file_path,
                    "fileName": os.path.basename(file_path),
                    "purpose": purpose,
                    "media_type": media_type,
                    "username": "",
                    "password": "",
                    "description": "",
                    "video_fps": None,
                    "camera_id": camera_id,
                },
                f,
            )

        # add an entry in the asset map
        self._asset_map[asset_id] = Asset.fromdir(asset_dir)
        logger.info(
            f"[AssetManager] Added file from path - asset-id: {asset_id} original path: {file_path}"
        )
        return asset_id

    def add_live_stream(self, url: str, description="", username="", password="", camera_id=""):
        """Add a live stream.

        Args:
            url: RTSP url of the stream
            description (optional): Description of the live stream. Defaults to "".
            username (optional): Username to access the stream. Defaults to "".
            password (optional): Password to access the stream. Defaults to "".
            camera_id (optional): Camera ID to be used for the live stream. Defaults to "".
        Returns:
            A unique id for the asset.
        """
        # Generate a unique id for the asset.
        asset_id = str(uuid.uuid4())
        while asset_id in self._asset_map:
            asset_id = str(uuid.uuid4())
        asset_dir = os.path.join(self._asset_dir, asset_id)

        try:
            os.makedirs(asset_dir)
        except Exception:
            raise ViaException("Could not create directory for asset")

        # Save asset info as json
        with open(os.path.join(asset_dir, "info.json"), "w") as f:
            json.dump(
                {
                    "assetId": asset_id,
                    "fileName": url,
                    "path": url,
                    "purpose": "",
                    "media_type": "",
                    "username": username,
                    "password": password,
                    "description": description,
                    "video_fps": None,
                    "camera_id": camera_id,
                },
                f,
            )

        # add an entry in the asset map
        self._asset_map[asset_id] = Asset.fromdir(asset_dir)
        logger.info(f"[AssetManager] Added live stream - asset-id: {asset_id} URL: {url}")
        return asset_id

    def cleanup_asset(self, asset_id: str):
        """Remove the asset and associated storage directory

        Raises an exception if the asset is in use.

        Args:
            asset_id: ID of the asset to remove
        """
        if asset_id in self._aged_out_assets:
            raise ViaException(f"{asset_id} already deleted", "BadParameter", 400)

        if asset_id not in self._asset_map:
            raise ViaException(f"No such resource {asset_id}", "BadParameter", 400)

        # Do not allow asset to be removed if it is in use.
        if self._asset_map[asset_id].use_count > 0:
            raise ViaException(f"Resource {asset_id} is currently being used", "ResourceInUse", 409)

        asset_dir = os.path.join(self._asset_dir, asset_id)
        try:
            shutil.rmtree(asset_dir)
        except Exception:
            pass
        self._asset_map.pop(asset_id)
        logger.info(f"Removed asset {asset_id} and cleaned up associated resources")

    def _get_existing_asset_ids(self):
        entries = os.listdir(self._asset_dir)
        return [
            entry
            for entry in entries
            if os.path.isdir(os.path.join(self._asset_dir, entry))
            and os.path.isfile(os.path.join(self._asset_dir, entry, "info.json"))
        ]

    def _get_asset_id_for_file(self, filepath: str) -> Asset:
        """
        Returns the Asset object that matches the given filename.

        Args:
            filename (str): The filename to search for in the asset map.

        Returns:
            Asset: The Asset object that matches the filename, or None if not found.
        """
        for asset in self._asset_map.values():
            if asset.path == filepath:
                return asset
        return None

    def list_assets(self):
        """Get a list of all assets"""
        return list(self._asset_map.values())

    def get_asset(self, asset_id: str):
        """Get asset information.

        Args:
            asset_id: Unique id of the asset.

        Returns:
            Information of the asset.
        """
        if asset_id in self._aged_out_assets:
            raise ViaException(
                f"{asset_id} already deleted because of age out policy", "BadParameter", 400
            )

        if asset_id not in self._asset_map:
            raise ViaException(f"No such resource {asset_id}", "BadParameter", 400)
        return self._asset_map[asset_id]

    async def _get_storage_usage(self):
        """Get the current storage usage of the assets directory in GB."""
        proc = await asyncio.subprocess.create_subprocess_exec(
            "du", "-s", "-b", self._asset_dir, stdout=asyncio.subprocess.PIPE
        )
        await proc.wait()
        output = await proc.stdout.read()
        return int(output.split()[0].decode("utf-8")) / (1024.0**3)

    async def _is_storage_above_threshold(self):
        return (
            bool(self._max_storage_usage_gb)
            and (await self._get_storage_usage()) > self._max_storage_usage_gb * AGE_OUT_THRESHOLD
        )

    async def _age_out_assets(self):
        """Age out old assets to free up storage space."""
        if not self._max_storage_usage_gb:
            return

        logger.debug(
            "Asset storage current size: %.2f GB, Threshold: %.2f GB, Max size: %.2f GB",
            await self._get_storage_usage(),
            self._max_storage_usage_gb * AGE_OUT_THRESHOLD,
            self._max_storage_usage_gb,
        )

        if not (await self._is_storage_above_threshold()):
            return

        logger.info(
            "Asset storage size above threshold. Current size: %.2f GB,"
            " Threshold: %.2f GB, Max size: %.2f GB",
            await self._get_storage_usage(),
            self._max_storage_usage_gb * AGE_OUT_THRESHOLD,
            self._max_storage_usage_gb,
        )

        # Get a list of all assets in the assets directory
        asset_ids = self._get_existing_asset_ids()

        # Sort the asset directories by their last modification time
        mtimes = await asyncio.gather(
            *[
                aiofiles.os.path.getmtime(os.path.join(self._asset_dir, asset_id))
                for asset_id in asset_ids
            ]
        )
        asset_ids = [d for _, d in sorted(zip(mtimes, asset_ids))]

        loop = asyncio.get_event_loop()
        # Age out the oldest asset directories until the storage usage is below the threshold
        while await self._is_storage_above_threshold() and asset_ids:
            oldest_asset_dir = asset_ids.pop(0)
            oldest_asset = self.get_asset(oldest_asset_dir)

            if oldest_asset.use_count:
                continue

            # Remove the oldest asset directory
            size_before_removal = await self._get_storage_usage()
            try:
                if self._asset_removal_callback and not (
                    await loop.run_in_executor(None, self._asset_removal_callback, oldest_asset)
                ):
                    continue
                await loop.run_in_executor(None, self.cleanup_asset, oldest_asset_dir)
                self._aged_out_assets.append(oldest_asset_dir)
            except Exception:
                continue
            logger.info(
                "Removed asset %s due to age out policy. Asset storage size before removal"
                " = %.2f GB. After removal = %.2f GB. Max asset storage size = %.2f GB",
                oldest_asset_dir,
                size_before_removal,
                await self._get_storage_usage(),
                self._max_storage_usage_gb,
            )

        if await self._is_storage_above_threshold():
            logger.warning(
                "Asset storage close to limit. Current size = %.2f GB. Max size = %.2f GB",
                await self._get_storage_usage(),
                self._max_storage_usage_gb,
            )

    def _age_out_thread_func(self):
        logger.info(
            "Started asset storage size monitoring. Current size = %.2f GB. Max size = %.2f GB",
            asyncio.run(self._get_storage_usage()),
            self._max_storage_usage_gb,
        )
        while True:
            with TimeMeasure("Age out assets"):
                asyncio.run(self._age_out_assets())
            time.sleep(AGE_OUT_RUN_INTERVAL_SEC)
