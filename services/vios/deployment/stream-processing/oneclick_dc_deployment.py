# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
VST & NVStreamer One-Click Deployment Script

Automates deployment of the stream-processing VST stack and NVStreamer
docker-compose services.

Usage:
    python3 oneclick_dc_deployment.py [ACTION] [--target TARGET] [OPTIONS]

Actions:  deploy (default) | stop | config-only
Targets:  vios (default)   | nvstreamer | all

Run with --help for full usage details.
"""

import os
import sys
import subprocess
import argparse
import re
import shlex
import time
import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Dict, Optional, Any, Set

DEFAULT_SCRIPT_DIR = Path(__file__).parent.absolute()
# Default NVStreamer videos base path lives next to the nvstreamer compose
# file: <script_dir>/docker-compose/nvstreamer/videos. The script always uses
# the absolute form here so the value written to compose.env is unambiguous
# regardless of where docker compose is invoked from. Override on the CLI with
# --nvstreamer-video-path PATH.
DEFAULT_NVSTREAMER_BASE_PATH = str(
    DEFAULT_SCRIPT_DIR / "docker-compose" / "nvstreamer" / "videos"
)
# tools/data lives at vms_shim/tools/data; this script is at
# vms_shim/deployment/stream-processing/oneclick_dc_deployment.py
DEFAULT_NVSTREAMER_TEST_DATA = str(DEFAULT_SCRIPT_DIR.parent.parent / "tools/data")


class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'


class Logger:
    """Logging utility with colored output"""

    @staticmethod
    def info(message: str):
        print(f"{Colors.BLUE}[INFO]{Colors.NC} {message}")

    @staticmethod
    def success(message: str):
        print(f"{Colors.GREEN}[SUCCESS]{Colors.NC} {message}")

    @staticmethod
    def warning(message: str):
        print(f"{Colors.YELLOW}[WARNING]{Colors.NC} {message}")

    @staticmethod
    def error(message: str):
        print(f"{Colors.RED}[ERROR]{Colors.NC} {message}")

    @staticmethod
    def destructive_prompt(
        title: str,
        path: str,
        note: Optional[str] = None,
        auto_confirm: bool = False,
    ) -> bool:
        """Show a visually distinct destructive-action banner.

        Renders a red-rule banner with the action title, target path
        (yellow), and an optional note, then either auto-confirms (when
        --clean/--fresh-start was passed) or prompts (y/N, default No --
        Enter alone or any non-affirmative input cancels).

        Returns True if confirmed, False otherwise.
        """
        rule = "-" * 72
        print()
        print(f"{Colors.RED}{rule}{Colors.NC}")
        print(f"{Colors.RED}  [DESTRUCTIVE]{Colors.NC} {Colors.YELLOW}{title}{Colors.NC}")
        print(f"{Colors.RED}{rule}{Colors.NC}")
        print(f"  Path : {Colors.YELLOW}{path}{Colors.NC}")
        if note:
            print(f"  Note : {note}")
        print(f"{Colors.RED}{rule}{Colors.NC}")
        if auto_confirm:
            print(
                f"  {Colors.GREEN}Auto-confirmed (--clean / --fresh-start) "
                f"-> proceeding{Colors.NC}"
            )
            print()
            return True
        response = input("  Delete this path? (y/N): ").strip().lower()
        print()
        return response in ('y', 'yes')


class DeploymentConfig:
    """Configuration container for deployment settings"""

    def __init__(self):
        self.script_dir = DEFAULT_SCRIPT_DIR

        self.default_nvstreamer_base_path = DEFAULT_NVSTREAMER_BASE_PATH

        self.nvstreamer_video_paths = {
            i: f"{self.default_nvstreamer_base_path}/nvstreamer-{i}"
            for i in range(1, 6)
        }

        # Runtime configuration
        self.host_ip = ""
        self.vst_config_path = ""
        self.vst_volume = ""
        self.nvstreamer_videos: Dict[int, str] = {}
        self.rtsp_ports: Dict[int, int] = {}
        self.customize_rtsp_ports = False
        # If set via --instances N (1..5), the script will rewrite
        # COMPOSE_PROFILES to "nvstreamer-1,...,nvstreamer-N" and use that as
        # the active instance list. None = honor whatever is already in
        # nvstreamer/compose.env.
        self.nvstreamer_instances: Optional[int] = None
        # True when --nvstreamer-video-path PATH was provided. Changes the
        # NVStreamer videos resolution: PATH is used directly (no
        # `nvstreamer-N` subdirs auto-created). Single-instance friendly.
        self.nvstreamer_path_explicit: bool = False

        # Deployment flags
        self.with_monitoring = False
        self.force_deployment = False
        # Auto-detect / non-interactive by default. Pass --interactive on the
        # CLI to get the old prompt-driven behavior.
        self.auto_mode = True
        self.fresh_start = False
        self.pull_always = False
        self.existing_vst_deployment = False
        self.existing_nvstreamer_deployment = False
        self.tag_overrides: Dict[str, str] = {}
        self.image_registry: str = ""
        self.nvstreamer_image: str = ""

    @property
    def vst_compose_dir(self) -> Path:
        return self.script_dir / "docker-compose"

    @property
    def nvstreamer_compose_dir(self) -> Path:
        return self.vst_compose_dir / "nvstreamer"

    @property
    def nvstreamer_data_root(self) -> Path:
        """Script-managed runtime-state directory for NVStreamer instances.

        Per-instance subdirs ``<root>/nvstreamer-{1..5}`` are bind-mounted
        into each container at /home/vst/vst_release/vst_data by the
        compose file. Kept here (under the script tree) so it stays
        decoupled from the user-supplied ``NVSTREAMER_VIDEO_*`` paths.
        """
        return self.nvstreamer_compose_dir / "vst_data"

    @property
    def main_compose_env(self) -> Path:
        return self.vst_compose_dir / "compose.env"

    @property
    def main_compose_file(self) -> Path:
        return self.vst_compose_dir / "docker-compose.yaml"

    @property
    def nvstreamer_compose_env(self) -> Path:
        return self.nvstreamer_compose_dir / "compose.env"

    @property
    def nvstreamer_compose_file(self) -> Path:
        return self.nvstreamer_compose_dir / "docker-compose.yaml"

    @property
    def default_vst_config_path(self) -> Path:
        return self.vst_compose_dir / "configs"

    @property
    def default_vst_volume(self) -> Path:
        return self.vst_compose_dir / "vst_volume"


class Validator:
    """Input validation utilities"""

    @staticmethod
    def validate_ip(ip: str) -> bool:
        if not re.match(r'^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$', ip):
            Logger.error(f"Invalid IP address format: {ip}")
            return False
        for part in ip.split('.'):
            if int(part) > 255:
                Logger.error(f"Invalid IP address format: {ip}")
                return False
        return True

    @staticmethod
    def validate_path(path: str) -> bool:
        if not os.path.isabs(path):
            Logger.error(f"Path must be absolute (start with /): {path}")
            return False
        return True

    @staticmethod
    def validate_port(port: str) -> bool:
        try:
            port_num = int(port)
            if 1 <= port_num <= 65535:
                return True
            Logger.error(f"Invalid port number: {port} (must be 1-65535)")
            return False
        except ValueError:
            Logger.error(f"Invalid port number: {port} (must be 1-65535)")
            return False


class SystemUtils:
    """System utilities for deployment operations"""

    @staticmethod
    def run_command(command: str, shell: bool = True, check: bool = True,
                    capture_output: bool = False,
                    cwd: Optional[str] = None) -> subprocess.CompletedProcess:
        try:
            if capture_output:
                result = subprocess.run(command, shell=shell, check=check,
                                        capture_output=True, text=True, cwd=cwd)
            else:
                result = subprocess.run(command, shell=shell, check=check, cwd=cwd)
            return result
        except subprocess.CalledProcessError as e:
            if check:
                Logger.error(f"Command failed: {command}")
                if capture_output:
                    Logger.error(f"Error output: {e.stderr}")
                raise
            return e

    @staticmethod
    def is_port_in_use(port: int) -> bool:
        try:
            result = SystemUtils.run_command(
                f"ss -tuln | grep ':{port} '",
                capture_output=True, check=False,
            )
            return result.returncode == 0
        except Exception:
            return False

    @staticmethod
    def auto_detect_ip() -> Optional[str]:
        methods = [
            "ip route get 8.8.8.8 2>/dev/null | grep -oP 'src \\K[0-9.]+' | head -1",
            "hostname -I 2>/dev/null | awk '{print $1}'",
            "ip addr show | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}' | cut -d'/' -f1",
        ]
        for method in methods:
            try:
                result = SystemUtils.run_command(method, capture_output=True, check=False)
                if result.returncode == 0 and result.stdout.strip():
                    detected_ip = result.stdout.strip()
                    if Validator.validate_ip(detected_ip):
                        return detected_ip
            except Exception:
                continue

        try:
            which = SystemUtils.run_command("which ifconfig", capture_output=True, check=False)
            if which.returncode == 0:
                result = SystemUtils.run_command(
                    "ifconfig | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}'",
                    capture_output=True, check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    detected_ip = result.stdout.strip()
                    if Validator.validate_ip(detected_ip):
                        return detected_ip
        except Exception:
            pass

        return None


class DirectoryManager:
    """Directory management utilities"""

    @staticmethod
    def ensure_directory(directory: str, description: str = "directory") -> bool:
        if not directory:
            Logger.error("Directory path is empty - check your configuration")
            return False
        if not os.path.isabs(directory):
            Logger.error(f"Directory path must be absolute: {directory}")
            return False
        dir_path = Path(directory)
        if dir_path.exists():
            Logger.info(f"{description.capitalize()} already exists: {directory}")
            return True
        Logger.info(f"Creating {description}: {directory}")
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            Logger.success(f"{description.capitalize()} created: {directory}")
            return True
        except Exception as e:
            Logger.error(f"Failed to create {description}: {directory} - {e}")
            return False

    @staticmethod
    def copy_test_videos(dest_dir: str, src_dir: str = DEFAULT_NVSTREAMER_TEST_DATA) -> None:
        """Optional: seed dest_dir with sample videos from src_dir.

        Seeding is best-effort:
        - If src_dir doesn't exist, log and continue.
        - If dest_dir already contains video files (e.g. user provided their
          own directory via --nvstreamer-video-path), skip seeding so we don't
          pollute the user's videos with our test samples.
        """
        video_extensions = {".mp4", ".mkv", ".avi", ".mov", ".ts"}

        dest_path = Path(dest_dir)
        if dest_path.exists() and dest_path.is_dir():
            existing = [
                p for p in dest_path.iterdir()
                if p.is_file() and p.suffix.lower() in video_extensions
            ]
            if existing:
                Logger.info(
                    f"{dest_dir} already has {len(existing)} video file(s); "
                    "skipping sample-video seeding"
                )
                return

        src_path = Path(src_dir)
        if not src_path.exists() or not src_path.is_dir():
            Logger.info(
                f"Sample-video source not found at {src_path} (optional, skipping)"
            )
            return
        copied = 0
        try:
            for src_file in src_path.iterdir():
                if src_file.is_file() and src_file.suffix.lower() in video_extensions:
                    dest_file = dest_path / src_file.name
                    if not dest_file.exists():
                        shutil.copy2(src_file, dest_file)
                        Logger.info(f"Copied {src_file.name} -> {dest_dir}")
                        copied += 1
        except OSError as e:
            # Don't let a copy failure abort the deploy - sample videos are optional.
            Logger.warning(f"Could not copy sample videos to {dest_dir}: {e}")
            return
        if copied == 0:
            Logger.info(f"No sample videos available to seed into {dest_dir}")


class DockerManager:
    """Docker and Docker Compose management"""

    @staticmethod
    def check_docker_prerequisites() -> bool:
        if os.geteuid() == 0:
            Logger.warning("Running as root. Consider using a regular user in the docker group.")
        else:
            try:
                SystemUtils.run_command("docker ps", capture_output=True)
            except subprocess.CalledProcessError:
                Logger.error(
                    "Cannot connect to the Docker daemon. "
                    "Add your user to the docker group and re-login:\n"
                    "  sudo usermod -aG docker $USER && newgrp docker"
                )
                return False

        try:
            SystemUtils.run_command("docker --version", capture_output=True)
        except subprocess.CalledProcessError:
            Logger.error("Docker is not installed. Please install Docker first.")
            return False

        try:
            SystemUtils.run_command("docker compose version", capture_output=True)
        except subprocess.CalledProcessError:
            Logger.error("Docker Compose is not installed or not working.")
            return False

        try:
            result = SystemUtils.run_command(
                "docker info 2>/dev/null | grep -i nvidia",
                capture_output=True, check=False,
            )
            if result.returncode != 0:
                Logger.warning("NVIDIA Docker runtime not detected. GPU acceleration may not work.")
                if not input("Continue anyway? (y/N): ").lower().startswith('y'):
                    return False
        except Exception:
            Logger.warning("Could not check NVIDIA Docker runtime")

        return True

    @staticmethod
    def get_running_containers(container_names: List[str]) -> List[str]:
        running = []
        for container in container_names:
            try:
                result = SystemUtils.run_command(
                    f"docker ps -q -f name='{container}'",
                    capture_output=True, check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    running.append(container)
            except Exception:
                continue
        return running


# Container groups used for detection / cleanup. Keep these centralized so
# the rest of the script can reference them by name.
VST_CONTAINERS = [
    "centralizedb",
    "vst-ingress",
    "redis-server",
    "sensor-ms",
    "streamprocessing-ms-1",
    "sdr-streamprocessing",
    "envoy-streamprocessing",
    "prometheus",
    "grafana",
]
NVSTREAMER_CONTAINERS = [f"nvstreamer-{i}" for i in range(1, 6)]


class ConfigurationManager:
    """Configuration file management"""

    def __init__(self, config: DeploymentConfig):
        self.config = config

    def get_active_nvstreamer_instances(self) -> List[int]:
        """Return the list of active NVStreamer instance numbers (1..5).

        Resolution order:
        1. CLI override: --instances N -> [1..N]
        2. nvstreamer/compose.env COMPOSE_PROFILES, parsed for nvstreamer-{n}
        3. Fallback to [1] (matches the default compose.env shipped with the
           repo).
        """
        if self.config.nvstreamer_instances is not None:
            n = self.config.nvstreamer_instances
            return list(range(1, n + 1))

        profiles = self._read_env_value(
            self.config.nvstreamer_compose_env, 'COMPOSE_PROFILES',
        )
        if not profiles:
            return [1]
        instances: List[int] = []
        for entry in profiles.split(','):
            match = re.match(r'^nvstreamer-(\d+)$', entry.strip())
            if not match:
                continue
            n = int(match.group(1))
            if 1 <= n <= 5 and n not in instances:
                instances.append(n)
        return sorted(instances) if instances else [1]

    def get_smart_defaults(self):
        Logger.info("Detecting system configuration...")
        detected_ip = SystemUtils.auto_detect_ip()
        if detected_ip:
            Logger.success(f"Auto-detected IP address: {detected_ip}")
        else:
            Logger.warning("Could not auto-detect IP address")

        current_host_ip = self._read_env_value(self.config.main_compose_env, "HOST_IP")
        self.config.host_ip = detected_ip or current_host_ip or ""

        return {
            'host_ip': self.config.host_ip,
            'config_path': str(self.config.default_vst_config_path),
            'volume_path': str(self.config.default_vst_volume),
            'nvstreamer_videos': self.config.nvstreamer_video_paths.copy(),
        }

    def get_vst_service_images(self):
        """Read currently configured VST service images from compose.env files"""
        vst_images: Dict[str, str] = {}
        vst_image_vars = {
            'VST_STREAM_PROCESSOR_IMAGE': 'Stream Processor Service',
            'VST_SENSOR_IMAGE': 'Sensor Service',
        }
        for var_name, display_name in vst_image_vars.items():
            image_name = self._read_env_value(self.config.main_compose_env, var_name)
            vst_images[display_name] = image_name or "(not configured)"

        nvstreamer_image = self._read_env_value(
            self.config.nvstreamer_compose_env, 'NVSTREAMER_IMAGE',
        )
        vst_images['NVStreamer Service'] = nvstreamer_image or "(not configured)"
        return vst_images

    def _read_env_value(self, env_file: Path, key: str) -> str:
        if not env_file.exists():
            return ""
        try:
            with open(env_file, 'r') as f:
                for line in f:
                    if not line.startswith(f"{key}="):
                        continue
                    raw = line.split('=', 1)[1].strip()
                    # Honor shell/dotenv-style quoting: characters inside
                    # matching single or double quotes are taken literally
                    # (so values that legitimately contain `#` can be quoted
                    # by the operator without being truncated below).
                    if len(raw) >= 2 and raw[0] in ('"', "'") and raw[-1] == raw[0]:
                        return raw[1:-1]
                    # Unquoted value: strip inline comments after whitespace.
                    # Tolerates both single-hash (` #change me`) and multi-
                    # hash (` ### change me`) markers — older releases of
                    # this script wrote a mix of both, and a previous regex
                    # (`###.*$`) silently left ` #change me` suffixes
                    # attached to values, which broke path-based cleanup.
                    # Operators with values containing literal ` #` should
                    # wrap the value in quotes (handled above).
                    return re.sub(r'\s+#.*$', '', raw).strip()
        except Exception:
            pass
        return ""

    @staticmethod
    def _retarget_image(current_image: str, org: str = "", tag: str = "",
                        org_is_prefix: bool = True) -> str:
        """Rebuild a Docker image reference with a new registry/org and/or tag.

        org_is_prefix=True  -> "<org>/<basename>:<tag>"  (VST service images)
        org_is_prefix=False -> "<org>:<tag>"             (NVStreamer; org is the full repo)

        An empty org or tag leaves that component unchanged. Returns "" when
        current_image is empty.
        """
        if not current_image:
            return ""
        # Split off the tag only when the last ':' is in the final path component,
        # so a registry port (e.g. host:5000/img) is not mistaken for a tag.
        last_slash = current_image.rfind('/')
        last_colon = current_image.rfind(':')
        if last_colon > last_slash:
            repo, current_tag = current_image[:last_colon], current_image[last_colon + 1:]
        else:
            repo, current_tag = current_image, ""
        new_tag = tag or current_tag
        if org:
            if org_is_prefix:
                basename = repo.rsplit('/', 1)[-1]
                repo = f"{org}/{basename}"
            else:
                repo = org
        return f"{repo}:{new_tag}" if new_tag else repo

    def update_main_compose_env(self):
        Logger.info("Updating main compose.env file...")

        updates = {
            'HOST_IP': self.config.host_ip,
            'VST_CONFIG_PATH': f"{self.config.vst_config_path} ### change me",
            'VST_VOLUME': f"{self.config.vst_volume} ### change me",
        }

        # Apply registry/org and tag overrides for stream-processing service images
        vst_image_vars = ['VST_STREAM_PROCESSOR_IMAGE', 'VST_SENSOR_IMAGE']
        for image_var in vst_image_vars:
            org = self.config.image_registry
            tag = self.config.tag_overrides.get(image_var, "")
            if not org and not tag:
                continue
            current_image = self._read_env_value(self.config.main_compose_env, image_var)
            new_image = self._retarget_image(current_image, org=org, tag=tag, org_is_prefix=True)
            if new_image:
                updates[image_var] = new_image
                Logger.info(f"Applying image override: {image_var} = {new_image}")
            else:
                Logger.warning(
                    f"Could not apply image override for {image_var} - current image not found",
                )

        self._update_env_file(self.config.main_compose_env, updates)

        if self.config.customize_rtsp_ports:
            rtsp_updates = {}
            for i, port in self.config.rtsp_ports.items():
                rtsp_updates[f'RTSP_SERVER_PORT_{i}'] = (
                    f"{port} ### change me if you want to stream more than 100 streams per pod"
                )
            self._update_env_file(self.config.main_compose_env, rtsp_updates)

        Logger.success("Main compose.env updated")

    def update_nvstreamer_compose_env(self):
        Logger.info("Updating nvstreamer compose.env file...")

        updates = {}
        for i in range(1, 6):
            if i in self.config.nvstreamer_videos:
                updates[f'NVSTREAMER_VIDEO_{i}'] = (
                    f"{self.config.nvstreamer_videos[i]} #change me"
                )

        # If --instances N was passed, rewrite COMPOSE_PROFILES so docker
        # compose runs exactly that many NVStreamer instances.
        if self.config.nvstreamer_instances is not None:
            n = self.config.nvstreamer_instances
            profiles_value = ",".join(f"nvstreamer-{i}" for i in range(1, n + 1))
            updates['COMPOSE_PROFILES'] = profiles_value
            Logger.info(f"Setting COMPOSE_PROFILES = {profiles_value}")

        nvstreamer_image = self.config.nvstreamer_image
        nvstreamer_tag = self.config.tag_overrides.get('NVSTREAMER_IMAGE', "")
        if nvstreamer_image or nvstreamer_tag:
            current_image = self._read_env_value(
                self.config.nvstreamer_compose_env, 'NVSTREAMER_IMAGE',
            )
            new_image = self._retarget_image(current_image, org=nvstreamer_image,
                                             tag=nvstreamer_tag, org_is_prefix=False)
            if new_image:
                updates['NVSTREAMER_IMAGE'] = new_image
                Logger.info(f"Applying NVStreamer image override: {new_image}")
            else:
                Logger.warning(
                    "Could not apply NVStreamer image override - current image not found",
                )

        self._update_env_file(self.config.nvstreamer_compose_env, updates)
        Logger.success("NVStreamer compose.env updated")

    def _update_env_file(self, env_file: Path, updates: Dict[str, str]):
        if not env_file.exists():
            Logger.error(f"Environment file not found: {env_file}")
            return
        try:
            with open(env_file, 'r') as f:
                lines = f.readlines()
            with open(env_file, 'w') as f:
                for line in lines:
                    updated = False
                    for key, value in updates.items():
                        if line.startswith(f"{key}="):
                            f.write(f"{key}={value}\n")
                            updated = True
                            break
                    if not updated:
                        f.write(line)
        except Exception as e:
            Logger.error(f"Failed to update {env_file}: {e}")


class InteractiveConfiguration:
    """Interactive configuration management"""

    def __init__(self, config: DeploymentConfig):
        self.config = config
        self.config_manager = ConfigurationManager(config)

    def get_user_input(self, prompt: str, default: str = "", validate_func=None) -> str:
        while True:
            if default:
                user_input = input(f"{prompt} [{default}]: ").strip()
                value = user_input if user_input else default
            else:
                value = input(f"{prompt}: ").strip()
            if not validate_func or validate_func(value):
                return value

    def _resolve_host_ip(self, smart_defaults, use_smart_defaults: bool):
        if use_smart_defaults and smart_defaults['host_ip']:
            self.config.host_ip = smart_defaults['host_ip']
            Logger.info(f"Using auto-detected IP: {self.config.host_ip}")
        else:
            self.config.host_ip = self.get_user_input(
                "Enter host IP address",
                smart_defaults['host_ip'],
                Validator.validate_ip,
            )

    def _resolve_vst_paths(self, smart_defaults, use_smart_defaults: bool):
        if use_smart_defaults:
            self.config.vst_config_path = smart_defaults['config_path']
            Logger.info(f"Using config path: {self.config.vst_config_path}")
        else:
            self.config.vst_config_path = self.get_user_input(
                "Enter VST config path (absolute)",
                smart_defaults['config_path'],
                Validator.validate_path,
            )
        DirectoryManager.ensure_directory(
            self.config.vst_config_path, "VST config directory",
        )

        if use_smart_defaults:
            self.config.vst_volume = smart_defaults['volume_path']
            Logger.info(f"Using volume path: {self.config.vst_volume}")
        else:
            self.config.vst_volume = self.get_user_input(
                "Enter VST volume path (absolute)",
                smart_defaults['volume_path'],
                Validator.validate_path,
            )
        DirectoryManager.ensure_directory(
            self.config.vst_volume, "VST volume directory",
        )

        for subdir in ['vst_data', 'vst_video', 'postgres/db']:
            DirectoryManager.ensure_directory(
                os.path.join(self.config.vst_volume, subdir),
                f"VST {subdir} directory",
            )

    def _resolve_nvstreamer_paths(self, smart_defaults, use_smart_defaults: bool):
        active = self.config_manager.get_active_nvstreamer_instances()

        print()
        print("=== NVStreamer Configuration ===")
        print(f"Auto Detected NVStreamer Base Path: {self.config.default_nvstreamer_base_path}")
        print(f"Active NVStreamer instances (per COMPOSE_PROFILES): {active}")

        # Single-instance flat path is fine; multi-instance with shared path
        # collides on vst_data. Warn and tell the user how to fix.
        if self.config.nvstreamer_path_explicit and len(active) > 1:
            Logger.warning(
                f"--nvstreamer-video-path is set, but {len(active)} NVStreamer instances "
                "are active. All instances would share the same directory "
                "(vst_data conflict)."
            )
            Logger.warning(
                "For multi-instance setups, edit "
                f"{self.config.nvstreamer_compose_env} and set NVSTREAMER_VIDEO_1, "
                "NVSTREAMER_VIDEO_2, ... to different absolute paths manually."
            )

        if not self.config.auto_mode and use_smart_defaults:
            response = input(
                "Do you want to use auto-detected NVStreamer video path? (y/N): ",
            ).strip().lower()
            if response.startswith('n'):
                use_smart_defaults = False

        if use_smart_defaults:
            Logger.info("Using default NVStreamer video paths:")
            self.config.nvstreamer_videos = {
                i: smart_defaults['nvstreamer_videos'][i] for i in active
            }
            for i in active:
                path = self.config.nvstreamer_videos[i]
                print(f"  NVStreamer-{i}: {path}")
                DirectoryManager.ensure_directory(path, f"NVStreamer-{i} directory")
                # NOTE: vst_data/ is intentionally NOT created under the
                # videos path anymore. Runtime state lives under
                # <nvstreamer_compose_dir>/vst_data/nvstreamer-{i} and is
                # created in DeploymentManager._ensure_nvstreamer_state_dirs.
                DirectoryManager.copy_test_videos(path)
            return

        print()
        response = input(
            "Do you want to enter a parent directory for all NVStreamer instances? (y/N): ",
        ).strip().lower()

        if response.startswith('y'):
            parent_dir = self.get_user_input(
                "Enter parent directory for NVStreamer instances (absolute)",
                self.config.default_nvstreamer_base_path,
                Validator.validate_path,
            )
            Logger.info(f"Using parent directory: {parent_dir}")
            for i in active:
                nvstreamer_path = os.path.join(parent_dir, f"nvstreamer-{i}")
                self.config.nvstreamer_videos[i] = nvstreamer_path
                print(f"  NVStreamer-{i}: {nvstreamer_path}")
                DirectoryManager.ensure_directory(
                    nvstreamer_path, f"NVStreamer-{i} directory",
                )
                # vst_data lives under <nvstreamer_compose_dir>/vst_data/
                # now -- see DeploymentManager._ensure_nvstreamer_state_dirs.
        else:
            Logger.info("Configuring each NVStreamer path individually:")
            for i in active:
                default_path = self.config.nvstreamer_video_paths[i]
                self.config.nvstreamer_videos[i] = self.get_user_input(
                    f"Enter NVStreamer-{i} video path (absolute)",
                    default_path,
                    Validator.validate_path,
                )
                DirectoryManager.ensure_directory(
                    self.config.nvstreamer_videos[i],
                    f"NVStreamer-{i} directory",
                )
                # vst_data lives under <nvstreamer_compose_dir>/vst_data/
                # now -- see DeploymentManager._ensure_nvstreamer_state_dirs.

    def _maybe_customize_rtsp_ports(self):
        print()
        print("=== Optional RTSP Server Port Configuration ===")
        print("Default ports support ~100 streams per pod. Change only if you need more streams.")

        if self.config.auto_mode:
            Logger.info("Auto mode - using default RTSP server ports")
            return

        response = input("Do you want to customize RTSP server ports? (y/N): ").strip().lower()
        if not response.startswith('y'):
            return

        self.config.customize_rtsp_ports = True
        for i in range(1, 6):
            default_port = str(30554 + (i - 1) * 10)
            self.config.rtsp_ports[i] = int(self.get_user_input(
                f"Enter RTSP server port {i}",
                default_port,
                Validator.validate_port,
            ))

    def _resolve_use_smart_defaults(self, smart_defaults) -> bool:
        if self.config.auto_mode:
            Logger.info("Auto mode enabled - using all detected/default values")
            if not smart_defaults['host_ip']:
                Logger.error("Auto mode requires IP address detection, but could not detect IP")
                Logger.error("Specify IP manually via --host <IP> or run with --interactive")
                sys.exit(1)
            return True
        response = input(
            "Do you want to use these auto-detected/default values? (Y/n): ",
        ).strip().lower()
        return not response.startswith('n')

    def run_full_configuration(self):
        """Configure both VST stream-processing and NVStreamer"""
        Logger.info("Starting interactive configuration...")
        smart_defaults = self.config_manager.get_smart_defaults()

        print()
        print("=== Auto-Detected Configuration ===")
        print(f"  Host IP:          {smart_defaults['host_ip'] or '(not detected)'}")
        print(f"  VST Config Path:  {smart_defaults['config_path']}")
        print(f"  VST Volume Path:  {smart_defaults['volume_path']}")
        print()

        use_smart_defaults = self._resolve_use_smart_defaults(smart_defaults)

        print()
        print("=== VST Configuration ===")
        print("Press Enter to keep default value mentioned in square brackets []")
        self._resolve_host_ip(smart_defaults, use_smart_defaults)
        self._resolve_vst_paths(smart_defaults, use_smart_defaults)
        self._resolve_nvstreamer_paths(smart_defaults, use_smart_defaults)
        self._maybe_customize_rtsp_ports()

        print()
        print("=== Auto-Detected Docker Images ===")
        for service_name, image_name in self.config_manager.get_vst_service_images().items():
            print(f"  {service_name:<25}: {image_name}")
        print()

        if not self.config.auto_mode:
            self._handle_image_tag_changes(include_nvstreamer=True)

        self._display_full_summary()
        Logger.success("Configuration completed")

    def run_vst_only_configuration(self):
        """Configure only the VST stream-processing stack"""
        Logger.info("Starting VST-only configuration...")
        smart_defaults = self.config_manager.get_smart_defaults()

        print()
        print("=== VST-Only Configuration ===")
        print(f"  Host IP:          {smart_defaults['host_ip'] or '(not detected)'}")
        print(f"  VST Config Path:  {smart_defaults['config_path']}")
        print(f"  VST Volume Path:  {smart_defaults['volume_path']}")
        print()

        use_smart_defaults = self._resolve_use_smart_defaults(smart_defaults)

        print()
        print("=== VST Configuration ===")
        print("Press Enter to keep default value mentioned in square brackets []")
        self._resolve_host_ip(smart_defaults, use_smart_defaults)
        self._resolve_vst_paths(smart_defaults, use_smart_defaults)

        print()
        print("=== Auto-Detected VST Images ===")
        vst_images = self.config_manager.get_vst_service_images()
        for service_name, image_name in vst_images.items():
            if service_name == 'NVStreamer Service':
                continue
            print(f"  {service_name:<25}: {image_name}")
        print()

        if not self.config.auto_mode:
            self._handle_image_tag_changes(include_nvstreamer=False)

        self._maybe_customize_rtsp_ports()
        self._display_vst_summary()
        Logger.success("VST configuration completed")

    def run_nvstreamer_only_configuration(self):
        """Configure only NVStreamer instances"""
        Logger.info("Starting NVStreamer-only configuration...")
        smart_defaults = self.config_manager.get_smart_defaults()

        print()
        print("=== NVStreamer-Only Configuration ===")
        print(f"  Host IP:                 {smart_defaults['host_ip'] or '(not detected)'}")
        print(f"  NVStreamer Base Path:    {self.config.default_nvstreamer_base_path}")
        print()

        use_smart_defaults = self._resolve_use_smart_defaults(smart_defaults)

        print()
        print("=== Host IP Configuration ===")
        self._resolve_host_ip(smart_defaults, use_smart_defaults)
        self._resolve_nvstreamer_paths(smart_defaults, use_smart_defaults)

        print()
        print("=== Auto-Detected NVStreamer Image ===")
        nvstreamer_image = self.config_manager._read_env_value(
            self.config.nvstreamer_compose_env, 'NVSTREAMER_IMAGE',
        )
        print(f"  NVStreamer Service       : {nvstreamer_image or '(not configured)'}")
        print()

        if not self.config.auto_mode:
            response = input(
                "Do you want to change the NVStreamer image tag? (y/N): ",
            ).strip().lower()
            if response.startswith('y'):
                self._change_nvstreamer_image_tag()

        self._display_nvstreamer_summary()
        Logger.success("NVStreamer configuration completed")

    def _handle_image_tag_changes(self, include_nvstreamer: bool):
        response = input("Do you want to change any Docker image tags? (y/N): ").strip().lower()
        if not response.startswith('y'):
            return

        print()
        print("=== Change Docker Image Tags ===")
        print("Select which images to change:")
        print("  1. Stream Processor + Sensor (apply same tag to both)")
        print("  2. Change Stream Processor + Sensor tags individually")
        if include_nvstreamer:
            print("  3. NVStreamer Image")
            print("  4. Cancel")
            choices = {'1', '2', '3', '4'}
        else:
            print("  3. Cancel")
            choices = {'1', '2', '3'}
        print()

        while True:
            choice = input(f"Enter your choice ({'/'.join(sorted(choices))}): ").strip()
            if choice == '1':
                self._change_all_service_tags_together()
                break
            if choice == '2':
                self._change_service_tags_individually()
                break
            if include_nvstreamer and choice == '3':
                self._change_nvstreamer_image_tag()
                break
            if (include_nvstreamer and choice == '4') or (not include_nvstreamer and choice == '3'):
                Logger.info("Image tag changes cancelled")
                break
            Logger.warning("Invalid choice. Please enter a valid option.")

    def _service_image_vars(self) -> List:
        return [
            ('VST_STREAM_PROCESSOR_IMAGE', 'Stream Processor'),
            ('VST_SENSOR_IMAGE', 'Sensor'),
        ]

    def _change_all_service_tags_together(self):
        service_images = self._service_image_vars()
        current_tags = []
        print()
        print("Current service images:")
        for env_var, service_name in service_images:
            current_image = self.config_manager._read_env_value(
                self.config.main_compose_env, env_var,
            )
            if current_image and ':' in current_image:
                current_tags.append(current_image.rsplit(':', 1)[1])
                print(f"  {service_name:<20}: {current_image}")

        most_common_tag = (
            max(set(current_tags), key=current_tags.count) if current_tags else "latest"
        )
        new_tag = input(f"Enter new tag for all services [{most_common_tag}]: ").strip()
        if not new_tag:
            new_tag = most_common_tag

        if new_tag == most_common_tag and len(set(current_tags)) == 1:
            Logger.info("No changes made - same tag as current")
            return

        updates = {}
        for env_var, service_name in service_images:
            current_image = self.config_manager._read_env_value(
                self.config.main_compose_env, env_var,
            )
            if not current_image:
                continue
            base_image = current_image.rsplit(':', 1)[0] if ':' in current_image else current_image
            new_image = f"{base_image}:{new_tag}"
            updates[env_var] = new_image
            Logger.info(f"Will update {service_name} to: {new_image}")

        if updates:
            self.config_manager._update_env_file(self.config.main_compose_env, updates)
            Logger.success(f"Updated {len(updates)} service image tags to: {new_tag}")

    def _change_service_tags_individually(self):
        service_images = self._service_image_vars()
        updates = {}
        print()
        print("Enter new tag for each service (press Enter to keep current tag):")
        for env_var, service_name in service_images:
            current_image = self.config_manager._read_env_value(
                self.config.main_compose_env, env_var,
            )
            if not current_image:
                continue
            if ':' in current_image:
                base_image, current_tag = current_image.rsplit(':', 1)
            else:
                base_image, current_tag = current_image, "latest"

            print(f"{service_name} Service:")
            print(f"  Current: {current_image}")
            new_tag = input(f"  New tag [{current_tag}]: ").strip()
            if new_tag and new_tag != current_tag:
                new_image = f"{base_image}:{new_tag}"
                updates[env_var] = new_image
                Logger.info(f"Will update {service_name} to: {new_image}")

        if updates:
            self.config_manager._update_env_file(self.config.main_compose_env, updates)
            Logger.success(f"Updated {len(updates)} service image tags")

    def _change_nvstreamer_image_tag(self):
        current_image = self.config_manager._read_env_value(
            self.config.nvstreamer_compose_env, 'NVSTREAMER_IMAGE',
        )
        if not current_image:
            Logger.warning("NVStreamer image not found in configuration")
            return
        if ':' in current_image:
            base_image, current_tag = current_image.rsplit(':', 1)
        else:
            base_image, current_tag = current_image, "latest"
        print(f"NVStreamer Service:")
        print(f"  Current: {current_image}")
        new_tag = input(f"  New tag [{current_tag}]: ").strip()
        if new_tag and new_tag != current_tag:
            new_image = f"{base_image}:{new_tag}"
            self.config_manager._update_env_file(
                self.config.nvstreamer_compose_env,
                {'NVSTREAMER_IMAGE': new_image},
            )
            Logger.success(f"Updated NVStreamer image to: {new_image}")
        else:
            Logger.info("NVStreamer image tag was not changed")

    def _display_full_summary(self):
        active = self.config_manager.get_active_nvstreamer_instances()
        print()
        print("=== Final Configuration Summary ===")
        print(f"Host IP: {self.config.host_ip}")
        print(f"VST Config Path: {self.config.vst_config_path}")
        print(f"VST Volume Path: {self.config.vst_volume}")
        print("NVStreamer Video Paths:")
        for i in active:
            print(f"  NVStreamer-{i}: {self.config.nvstreamer_videos.get(i)}")
        if self.config.customize_rtsp_ports:
            print("Custom RTSP Ports:")
            for i in range(1, 6):
                print(f"  RTSP Server {i}: {self.config.rtsp_ports.get(i)}")
        else:
            print("Using default RTSP ports (30554, 30564, 30574, 30584, 30594)")
        print()

    def _display_vst_summary(self):
        print()
        print("=== VST Configuration Summary ===")
        print(f"Host IP: {self.config.host_ip}")
        print(f"VST Config Path: {self.config.vst_config_path}")
        print(f"VST Volume Path: {self.config.vst_volume}")
        if self.config.customize_rtsp_ports:
            print("Custom RTSP Ports:")
            for i in range(1, 6):
                print(f"  RTSP Server {i}: {self.config.rtsp_ports.get(i)}")
        else:
            print("Using default RTSP ports (30554, 30564, 30574, 30584, 30594)")
        print()

    def _display_nvstreamer_summary(self):
        active = self.config_manager.get_active_nvstreamer_instances()
        print()
        print("=== NVStreamer Configuration Summary ===")
        print(f"Host IP: {self.config.host_ip}")
        print(f"Active NVStreamer instances: {active}")
        print("NVStreamer Video Paths:")
        for i in active:
            print(f"  NVStreamer-{i}: {self.config.nvstreamer_videos.get(i)}")
        print()


class DeploymentManager:
    """Main deployment management"""

    def __init__(self, config: DeploymentConfig):
        self.config = config
        self.config_manager = ConfigurationManager(config)

    def _get_compose_health_states(
        self, compose_base_cmd: str, cwd: str,
    ) -> Dict[str, str]:
        ps_result = SystemUtils.run_command(
            f"{compose_base_cmd} ps -q", capture_output=True, check=False, cwd=cwd,
        )
        if ps_result.returncode != 0 or not ps_result.stdout.strip():
            return {}

        container_ids = [c for c in ps_result.stdout.split() if c.strip()]
        if not container_ids:
            return {}

        insp_result = SystemUtils.run_command(
            f"docker inspect {' '.join(container_ids)}",
            capture_output=True, check=False, cwd=cwd,
        )
        if insp_result.returncode != 0 or not insp_result.stdout.strip():
            return {}

        try:
            items: Any = json.loads(insp_result.stdout)
        except json.JSONDecodeError:
            return {}
        if not isinstance(items, list):
            return {}

        health_states: Dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("Name") or "").lstrip("/").strip()
            labels = item.get("Config", {}).get("Labels", {}) if isinstance(item.get("Config"), dict) else {}
            if not isinstance(labels, dict):
                labels = {}

            service_label = str(labels.get("com.docker.compose.service") or "").strip()
            container_number = str(labels.get("com.docker.compose.container-number") or "").strip()
            if service_label and container_number:
                display = f"{service_label}-{container_number}"
            elif service_label:
                display = service_label
            else:
                display = name or str(item.get("Id") or "")

            state = item.get("State", {}) if isinstance(item.get("State"), dict) else {}
            state_status = str(state.get("Status") or "").lower()

            health_obj = state.get("Health")
            if not isinstance(health_obj, dict):
                continue

            health_status = str(health_obj.get("Status") or "").lower()
            if state_status in {"exited", "dead", "removing"}:
                health_states[display] = state_status
            else:
                health_states[display] = health_status
        return health_states

    def _wait_for_compose_services_healthy(
        self,
        compose_base_cmd: str,
        cwd: str,
        *,
        start_time: Optional[float] = None,
        timeout_s: int = 600,
        poll_interval_s: int = 2,
    ) -> Optional[float]:
        start = start_time if start_time is not None else time.monotonic()
        health_services: Optional[Set[str]] = None
        last_log_t = 0.0

        ps_result = SystemUtils.run_command(
            f"{compose_base_cmd} ps -q", capture_output=True, check=False, cwd=cwd,
        )
        if ps_result.returncode != 0 or not ps_result.stdout.strip():
            return None

        while True:
            health_states = self._get_compose_health_states(compose_base_cmd, cwd=cwd)
            if health_services is None:
                health_services = set(health_states.keys())
                if not health_services:
                    return -1.0
            else:
                health_services |= set(health_states.keys())

            pending = [s for s in sorted(health_services) if health_states.get(s) != "healthy"]
            if not pending:
                return time.monotonic() - start

            elapsed = time.monotonic() - start
            if elapsed >= timeout_s:
                Logger.warning(
                    f"Timed out waiting for Docker healthchecks ({int(elapsed)}s). "
                    f"Pending: {', '.join(pending)}"
                )
                return None

            if elapsed - last_log_t >= 10:
                Logger.info(
                    f"Waiting for Docker healthchecks... pending {len(pending)}/{len(health_services)}"
                )
                last_log_t = elapsed
            time.sleep(poll_interval_s)

    def check_prerequisites(self):
        Logger.info("Checking system prerequisites...")
        if not DockerManager.check_docker_prerequisites():
            sys.exit(1)
        if not self.config.main_compose_file.exists():
            Logger.error(f"Main docker-compose.yaml not found at: {self.config.main_compose_file}")
            sys.exit(1)
        if not self.config.nvstreamer_compose_file.exists():
            Logger.error(
                f"NVStreamer docker-compose.yaml not found at: {self.config.nvstreamer_compose_file}",
            )
            sys.exit(1)
        Logger.success("Prerequisites check completed")

    def _validate_memory_availability(self) -> bool:
        try:
            result = SystemUtils.run_command("grep MemAvailable /proc/meminfo", capture_output=True)
            available_kb = int(result.stdout.split()[1])
            available_bytes = available_kb * 1024
            required_bytes = 100 * 1024 * 1024
            if available_bytes < required_bytes:
                Logger.warning(
                    f"Available memory ({available_bytes // (1024*1024)}MB) is below minimum "
                    f"({required_bytes // (1024*1024)}MB)"
                )
                return False
            return True
        except (subprocess.CalledProcessError, ValueError, IndexError) as e:
            Logger.warning(f"Could not validate memory availability: {e}")
            return True

    @staticmethod
    def _read_sysctl(key: str) -> Optional[str]:
        """Read a sysctl value as a single trimmed string. Returns None on failure."""
        try:
            result = SystemUtils.run_command(
                f"sysctl -n {key}", capture_output=True, check=False,
            )
            if result.returncode != 0:
                return None
            return result.stdout.strip()
        except Exception:  # noqa: BLE001
            return None

    def configure_network_buffers(self):
        """Apply host-level sysctl tuning needed for high-throughput streaming.

        Sets:
          - net.core.rmem_max     = 2000000
          - net.core.wmem_max     = 2000000
          - net.ipv4.tcp_rmem     = "4096 2000000 6291456"
          - net.ipv4.tcp_wmem     = "4096 2000000 6291456"

        Values are set and left in place: containers need the tuning while
        they run, not just during the deploy. No automatic restore on
        script exit -- the user can re-tune manually (e.g. `sysctl -w
        net.core.rmem_max=212992`) if they want the pre-deploy defaults
        back.
        """
        Logger.info("Configuring network buffer sizes for high-throughput streaming...")

        # Read current values once (used both for the log below and the
        # "don't downgrade" guard further down).
        current_rmem_max = self._read_sysctl("net.core.rmem_max")
        current_wmem_max = self._read_sysctl("net.core.wmem_max")
        current_tcp_rmem = self._read_sysctl("net.ipv4.tcp_rmem")
        current_tcp_wmem = self._read_sysctl("net.ipv4.tcp_wmem")

        Logger.info(
            f"Current values:\n"
            f"  net.core.rmem_max  = {current_rmem_max}\n"
            f"  net.core.wmem_max  = {current_wmem_max}\n"
            f"  net.ipv4.tcp_rmem  = {current_tcp_rmem}\n"
            f"  net.ipv4.tcp_wmem  = {current_tcp_wmem}"
        )

        # Pick conservative target buffers if memory is constrained
        if self._validate_memory_availability():
            new_rmem_max = "2000000"
            new_wmem_max = "2000000"
        else:
            new_rmem_max = "1000000"
            new_wmem_max = "1000000"
        new_tcp_rmem = "4096 2000000 6291456"
        new_tcp_wmem = "4096 2000000 6291456"

        def _needs_bump(current: Optional[str], target: str, key: str) -> bool:
            """True iff we should apply sysctl key=target.

            Skips when current >= target so we never downgrade a host that
            an admin has already pre-tuned beyond our defaults.
            """
            if current is None:
                return True  # couldn't read current; play it safe and set
            try:
                if int(current) >= int(target):
                    Logger.info(
                        f"Skipping {key}: current ({current}) is already "
                        f">= target ({target})"
                    )
                    return False
            except ValueError:
                pass  # non-integer current; just set
            return True

        commands = []
        if _needs_bump(current_rmem_max, new_rmem_max, "net.core.rmem_max"):
            commands.append(f"sudo sysctl -w net.core.rmem_max={new_rmem_max}")
        if _needs_bump(current_wmem_max, new_wmem_max, "net.core.wmem_max"):
            commands.append(f"sudo sysctl -w net.core.wmem_max={new_wmem_max}")
        # tcp_rmem / tcp_wmem are "min default max" triplets; "don't
        # downgrade" comparison semantics are non-trivial, so always apply.
        commands.append(f"sudo sysctl -w net.ipv4.tcp_rmem='{new_tcp_rmem}'")
        commands.append(f"sudo sysctl -w net.ipv4.tcp_wmem='{new_tcp_wmem}'")

        all_ok = True
        for cmd in commands:
            try:
                SystemUtils.run_command(cmd)
            except subprocess.CalledProcessError as e:
                Logger.warning(f"sysctl command failed: {cmd} ({e})")
                all_ok = False

        if all_ok:
            Logger.success("Network buffers configured successfully")
        else:
            Logger.warning(
                "One or more sysctl settings failed. Continuing deployment, "
                "but high-throughput streaming may be degraded."
            )

    def detect_existing_deployments(self) -> bool:
        Logger.info("Detecting existing Docker Compose deployments...")
        running_vst = DockerManager.get_running_containers(VST_CONTAINERS)
        running_nvstreamer = DockerManager.get_running_containers(NVSTREAMER_CONTAINERS)

        self.config.existing_vst_deployment = bool(running_vst)
        self.config.existing_nvstreamer_deployment = bool(running_nvstreamer)

        for container in running_vst:
            Logger.info(f"Found running VST container: {container}")
        for container in running_nvstreamer:
            Logger.info(f"Found running NVStreamer container: {container}")

        if running_vst or running_nvstreamer:
            Logger.warning("Existing deployments detected!")
            return True
        Logger.success("No existing deployments found")
        return False

    def _stop_vst_compose(self):
        stop_commands = [
            "docker compose -f docker-compose.yaml --env-file ./compose.env "
            "--profile monitoring down --remove-orphans",
            "docker compose -f docker-compose.yaml --env-file ./compose.env "
            "down --remove-orphans",
        ]
        for cmd in stop_commands:
            Logger.info(f"Executing: {cmd}")
            try:
                SystemUtils.run_command(cmd, cwd=str(self.config.vst_compose_dir))
                Logger.success("VST services stopped successfully")
                break
            except subprocess.CalledProcessError:
                Logger.warning("Command failed, trying next approach...")

        for container in VST_CONTAINERS:
            try:
                result = SystemUtils.run_command(
                    f"docker ps -q -f name='{container}'",
                    capture_output=True, check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    Logger.info(f"Force stopping container: {container}")
                    SystemUtils.run_command(f"docker stop {container}", check=False)
                    SystemUtils.run_command(f"docker rm {container}", check=False)
            except Exception:
                pass

    def _stop_nvstreamer_compose(self):
        try:
            SystemUtils.run_command(
                "docker compose -f docker-compose.yaml --env-file ./compose.env "
                "down --remove-orphans",
                cwd=str(self.config.nvstreamer_compose_dir),
            )
            Logger.success("NVStreamer services stopped successfully")
        except subprocess.CalledProcessError:
            Logger.warning("Docker compose stop failed, trying manual container stop...")
            for container in NVSTREAMER_CONTAINERS:
                try:
                    result = SystemUtils.run_command(
                        f"docker ps -q -f name='{container}'",
                        capture_output=True, check=False,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        Logger.info(f"Force stopping container: {container}")
                        SystemUtils.run_command(f"docker stop {container}", check=False)
                        SystemUtils.run_command(f"docker rm {container}", check=False)
                except Exception:
                    pass

    def stop_existing_deployments(self):
        Logger.info("Stopping existing deployments...")
        if self.config.existing_vst_deployment:
            Logger.info("Stopping existing VST deployment...")
            self._stop_vst_compose()
        if self.config.existing_nvstreamer_deployment:
            Logger.info("Stopping existing NVStreamer deployment...")
            self._stop_nvstreamer_compose()

        Logger.info("Waiting for containers to fully stop...")
        time.sleep(5)
        Logger.info("Cleaning up Docker networks...")
        SystemUtils.run_command("docker network prune -f", check=False)
        Logger.success("Existing deployments stopped")

    def stop_existing_vst_only(self):
        Logger.info("Stopping existing VST deployment...")
        self._stop_vst_compose()
        Logger.info("Waiting for VST containers to fully stop...")
        time.sleep(3)
        SystemUtils.run_command("docker network prune -f", check=False)
        Logger.success("VST deployment stopped")

    def stop_existing_nvstreamer_only(self):
        Logger.info("Stopping existing NVStreamer deployment...")
        self._stop_nvstreamer_compose()
        Logger.info("Waiting for NVStreamer containers to fully stop...")
        time.sleep(3)
        Logger.success("NVStreamer deployment stopped")

    def _check_ports(self, ports: List[int], description: str):
        Logger.info(f"Checking {description} port availability...")
        conflicts = 0
        for port in ports:
            if SystemUtils.is_port_in_use(port):
                Logger.warning(f"Port {port} is already in use")
                conflicts += 1
        if conflicts > 0:
            Logger.warning(
                f"Found {conflicts} {description} port conflicts. Services may fail to start."
            )
            if not input("Continue anyway? (y/N): ").lower().startswith('y'):
                sys.exit(1)
        else:
            Logger.success(f"All required {description} ports are available")

    def check_vst_port_availability(self):
        ports = [
            5432,   # PostgreSQL
            6379,   # Redis
            3000,   # Grafana
            30000,  # Sensor HTTP
            30001,  # Stream Processor HTTP
            30554,  # Stream Processor RTSP
            30888,  # VST Ingress
        ]
        self._check_ports(ports, "VST")

    def check_nvstreamer_port_availability(self):
        # Only check ports for instances that are active in COMPOSE_PROFILES.
        active = self.config_manager.get_active_nvstreamer_instances()
        ports: List[int] = []
        for i in active:
            ports.append(31000 + (i - 1))            # HTTP port
            ports.append(31554 + (i - 1) * 10)       # RTSP port
        self._check_ports(ports, "NVStreamer")

    def check_port_availability(self):
        self.check_vst_port_availability()
        self.check_nvstreamer_port_availability()

    @staticmethod
    def _http_endpoint_responds(url: str, timeout: float = 3.0) -> bool:
        """Return True if `url` returns any HTTP response (server is up).

        4xx/5xx still mean "the HTTP server is alive and listening", which is
        sufficient for "service is reachable" in our gate.
        """
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.status is not None
        except urllib.error.HTTPError:
            return True
        except (urllib.error.URLError, ConnectionResetError, OSError):
            return False
        except Exception:  # noqa: BLE001 - any unexpected error == not ready
            return False

    def _wait_for_nvstreamer_http_ready(
        self, *, timeout_s: int = 120, poll_interval_s: int = 3,
    ) -> bool:
        """Block until each active NVStreamer instance answers on its HTTP port.

        Returns True once every active instance is reachable, or False if the
        timeout elapses with at least one instance still unreachable.
        """
        active = self.config_manager.get_active_nvstreamer_instances()
        ports = {i: 31000 + (i - 1) for i in active}
        Logger.info(
            f"Waiting for NVStreamer HTTP endpoints on ports "
            f"{sorted(ports.values())} (timeout: {timeout_s}s)..."
        )

        pending = set(ports.keys())
        start = time.monotonic()
        last_log_t = 0.0

        while pending:
            elapsed = time.monotonic() - start
            if elapsed >= timeout_s:
                Logger.warning(
                    f"Timed out after {int(elapsed)}s waiting for NVStreamer HTTP. "
                    f"Pending: {[f'NVStreamer-{i}' for i in sorted(pending)]}"
                )
                return False

            for i in list(pending):
                port = ports[i]
                url = f"http://{self.config.host_ip}:{port}"
                if self._http_endpoint_responds(url):
                    pending.discard(i)
                    Logger.success(
                        f"NVStreamer-{i} reachable on http://{self.config.host_ip}:{port}"
                    )

            if not pending:
                Logger.success(
                    f"All {len(ports)} NVStreamer instance(s) HTTP-reachable in "
                    f"{time.monotonic() - start:.1f}s"
                )
                return True

            if elapsed - last_log_t >= 10:
                Logger.info(
                    f"  ... still waiting on {len(pending)}/{len(ports)} "
                    f"NVStreamer HTTP endpoints"
                )
                last_log_t = elapsed
            time.sleep(poll_interval_s)
        return True

    def _ensure_nvstreamer_state_dirs(self) -> None:
        """Create per-instance vst_data directories under the script tree.

        The compose file bind-mounts ``${NVSTREAMER_CONFIG}/../vst_data/nvstreamer-{N}``
        into each container's /home/vst/vst_release/vst_data. Pre-creating
        these as the deploy user (rather than letting Docker auto-create
        them as root) keeps ownership predictable and makes the layout
        observable before the compose up step.

        Fails fast if any directory can't be created: continuing into
        ``docker compose up`` with missing bind-mount targets would surface
        as an opaque Docker error and could leave partial root-owned state
        behind. ``ensure_directory`` has already logged the actionable
        underlying error before we get here.
        """
        active = self.config_manager.get_active_nvstreamer_instances()
        state_root = self.config.nvstreamer_data_root
        if not DirectoryManager.ensure_directory(
            str(state_root), "NVStreamer state root",
        ):
            raise RuntimeError(
                f"Aborting NVStreamer deploy: failed to create state root {state_root}"
            )
        for i in active:
            state_dir = state_root / f"nvstreamer-{i}"
            if not DirectoryManager.ensure_directory(
                str(state_dir),
                f"NVStreamer-{i} state (vst_data) directory",
            ):
                raise RuntimeError(
                    f"Aborting NVStreamer deploy: failed to create state "
                    f"directory for instance {i} ({state_dir})"
                )

    def deploy_nvstreamer(self):
        Logger.info("Deploying NVStreamer instances...")

        nvstreamer_dir = self.config.nvstreamer_compose_dir
        compose_base_cmd = "docker compose -f docker-compose.yaml --env-file ./compose.env"

        self._ensure_nvstreamer_state_dirs()

        if self.config.pull_always:
            Logger.info("Pulling NVStreamer images...")
            SystemUtils.run_command(f"{compose_base_cmd} pull", cwd=str(nvstreamer_dir))
        else:
            Logger.info("Skipping image pull (use --pull-always to pull latest images)")

        Logger.info("Starting NVStreamer containers...")
        compose_start_t = time.monotonic()
        SystemUtils.run_command(
            f"{compose_base_cmd} up --force-recreate -d", cwd=str(nvstreamer_dir),
        )
        Logger.success("NVStreamer deployment completed")

        elapsed_s = self._wait_for_compose_services_healthy(
            compose_base_cmd, cwd=str(nvstreamer_dir), start_time=compose_start_t,
        )
        if elapsed_s is None:
            Logger.info("Timed out waiting for NVStreamer Docker healthchecks.")
        elif elapsed_s < 0:
            Logger.info("No Docker healthchecks enabled for NVStreamer services.")
        else:
            Logger.success(
                f"NVStreamer Docker healthchecks: all healthy in {elapsed_s:.1f}s",
            )

        # NVStreamer compose file currently has no Docker healthcheck, so the
        # wait above returns immediately. Block until each active instance's
        # HTTP port answers, then it's safe to start dependent services (VST).
        self._wait_for_nvstreamer_http_ready()

    def deploy_vst(self):
        Logger.info("Deploying VST stream-processing services...")

        profiles = ""
        if self.config.with_monitoring:
            profiles += " --profile monitoring"

        compose_base_cmd = (
            f"docker compose -f docker-compose.yaml --env-file ./compose.env{profiles}"
        )

        if self.config.pull_always:
            Logger.info("Pulling VST images...")
            SystemUtils.run_command(
                f"{compose_base_cmd} pull", cwd=str(self.config.vst_compose_dir),
            )
        else:
            Logger.info("Skipping image pull (use --pull-always to pull latest images)")

        Logger.info("Starting VST containers...")
        compose_start_t = time.monotonic()
        SystemUtils.run_command(
            f"{compose_base_cmd} up --force-recreate -d",
            cwd=str(self.config.vst_compose_dir),
        )
        Logger.success("VST deployment completed")

        elapsed_s = self._wait_for_compose_services_healthy(
            compose_base_cmd,
            cwd=str(self.config.vst_compose_dir),
            start_time=compose_start_t,
        )
        if elapsed_s is None:
            Logger.info("Timed out waiting for VST Docker healthchecks.")
        elif elapsed_s < 0:
            Logger.info("No Docker healthchecks enabled for VST services.")
        else:
            Logger.success(f"VST Docker healthchecks: all healthy in {elapsed_s:.1f}s")

    def _resolve_vst_volume_path(self) -> str:
        """Resolve VST_VOLUME from runtime config / compose.env / default."""
        if self.config.vst_volume:
            return self.config.vst_volume
        val = self.config_manager._read_env_value(
            self.config.main_compose_env, 'VST_VOLUME',
        )
        return val or str(self.config.default_vst_volume)

    def _remove_postgres_named_volume(self) -> None:
        """Remove the postgres named volume (pg_data) used by the
        centralizedb service.

        PGDATA was switched from a host bind mount
        (``${VST_VOLUME}/postgres/db``) to a Docker-managed named
        volume (``pg_data``) so the postgres entrypoint's chown to
        uid 70 works under rootless Docker. The host ``sudo rm -rf``
        in ``remove_vst_volume`` no longer wipes PGDATA, so we run
        ``docker compose down -v`` against the same compose file to
        let compose remove the project-prefixed named volume
        (e.g. ``docker-compose_pg_data``). Idempotent: succeeds even
        if the volume doesn't exist yet.
        """
        Logger.info("Removing postgres named volume (pg_data)...")
        cmd = (
            "docker compose -f docker-compose.yaml --env-file ./compose.env "
            "down -v --remove-orphans"
        )
        try:
            result = SystemUtils.run_command(
                cmd, cwd=str(self.config.vst_compose_dir), check=False,
            )
        except Exception as e:
            # check=False suppresses CalledProcessError, so anything that
            # surfaces here is structural (docker binary missing, cwd
            # inaccessible, etc.). Keep this best-effort — don't abort
            # the broader cleanup flow on a volume-removal hiccup.
            Logger.warning(f"Failed to invoke docker compose for postgres volume cleanup: {e}")
            return
        if result.returncode == 0:
            Logger.success("Postgres named volume removed (or was not present)")
        else:
            Logger.warning(
                f"docker compose down -v exited {result.returncode} while removing "
                "postgres named volume; volume may still exist (see stderr above)"
            )

    def remove_vst_volume(self, *, auto_confirm: bool = False):
        Logger.info("Removing VST volume directory...")
        vst_volume_path = self._resolve_vst_volume_path()
        if not vst_volume_path or not os.path.exists(vst_volume_path):
            Logger.warning(f"VST volume directory not found: {vst_volume_path}")
            # First, try the script-relative default
            # (<script_dir>/docker-compose/vst_volume) before giving up.
            # Handles the case where compose.env has been reverted
            # (e.g. via git checkout) after a deploy wrote the real path,
            # so VST_VOLUME no longer matches what's actually on disk but
            # the data still lives at the script-relative default location.
            fallback = str(self.config.default_vst_volume)
            if fallback != vst_volume_path and os.path.exists(fallback):
                Logger.info(f"Removing default VST volume path: {fallback}")
                vst_volume_path = fallback
            else:
                # Neither the configured path nor the script-relative
                # default exist on disk, but the postgres named volume
                # (pg_data) can still hold PGDATA from a previous deploy.
                # Run the named-volume cleanup so --clean is genuinely
                # clean before returning.
                self._remove_postgres_named_volume()
                return

        proceed = Logger.destructive_prompt(
            title="Delete VST volume (postgres DB, vst_data, vst_video, ...)",
            path=vst_volume_path,
            note="All VST data at this path will be permanently removed.",
            auto_confirm=self.config.fresh_start or auto_confirm,
        )
        if not proceed:
            Logger.info("VST volume directory removal cancelled by user")
            return

        try:
            SystemUtils.run_command(f"sudo rm -rf {vst_volume_path}")
            Logger.success(f"Removed: {vst_volume_path}")
        except subprocess.CalledProcessError as e:
            Logger.error(f"Failed to remove VST volume directory: {e}")
        # Host bind-mount dirs are wiped above; PGDATA now lives in a
        # Docker-managed named volume, so additionally remove that.
        self._remove_postgres_named_volume()

    def _rm_rf_path(self, path: Path, *, label: str) -> None:
        """`sudo rm -rf` a path with consistent log output.

        The path is shell-quoted to defend against shell metacharacters in
        operator-supplied paths (e.g. legacy env-file values).
        """
        quoted = shlex.quote(str(path))
        try:
            SystemUtils.run_command(f"sudo rm -rf -- {quoted}")
            Logger.success(f"Removed: {path}")
        except subprocess.CalledProcessError as e:
            Logger.error(f"Failed to remove {label} ({path}): {e}")

    def _remove_nvstreamer_state_root(self) -> None:
        """Remove the script-managed vst_data tree (always safe to wipe)."""
        state_root = self.config.nvstreamer_data_root
        if not state_root.exists():
            Logger.info(
                f"No script-managed state at {state_root}; nothing to clean"
            )
            return
        Logger.info(f"Removing script-managed state: {state_root}")
        self._rm_rf_path(state_root, label="state root")

    def _collect_legacy_state_dirs(self) -> List[Path]:
        """Return existing legacy ``<NVSTREAMER_VIDEO_N>/vst_data`` dirs."""
        legacy_seen: List[Path] = []
        for i in range(1, 6):
            val = self.config_manager._read_env_value(
                self.config.nvstreamer_compose_env, f'NVSTREAMER_VIDEO_{i}',
            )
            if not val:
                continue
            legacy = Path(val) / "vst_data"
            if legacy.exists() and legacy not in legacy_seen:
                legacy_seen.append(legacy)
        return legacy_seen

    def _remove_nvstreamer_legacy_state(self, *, auto_confirm: bool) -> None:
        """Clean up state dirs from pre-split (vst_data-under-videos) deploys.

        Removes only ``<videos>/vst_data`` per configured path; sibling
        video files are preserved.
        """
        for legacy in self._collect_legacy_state_dirs():
            videos_dir = legacy.parent
            proceed = Logger.destructive_prompt(
                title="Delete legacy NVStreamer state",
                path=str(legacy),
                note=(
                    f"Removes ONLY this 'vst_data' dir; sibling video files "
                    f"at {videos_dir} are preserved."
                ),
                auto_confirm=self.config.fresh_start or auto_confirm,
            )
            if not proceed:
                Logger.info(f"  Skipped: {legacy}")
                continue
            self._rm_rf_path(legacy, label="legacy vst_data")

    def _remove_nvstreamer_script_managed_videos(self) -> None:
        """Prompt and (if confirmed) wipe the default videos directory.

        Always prompts -- even under ``--clean`` / ``--fresh-start`` --
        because this directory may contain UI-uploaded clips.
        """
        videos_root = (self.config.nvstreamer_compose_dir / "videos").resolve()
        if not videos_root.exists():
            return
        # auto_confirm=False unconditionally: this dir may contain user
        # UI-uploaded clips, so --clean / --fresh-start does NOT skip the
        # prompt here (intentional per the docstring above).
        proceed = Logger.destructive_prompt(
            title="Delete NVStreamer videos (script-managed)",
            path=str(videos_root),
            note="May contain videos you uploaded via the NVStreamer UI.",
            auto_confirm=False,
        )
        if proceed:
            self._rm_rf_path(videos_root, label="script-managed videos")
        else:
            Logger.info("Skipped script-managed videos cleanup")

    def remove_nvstreamer_videos(self, *, auto_confirm: bool = False):
        """Clean NVStreamer host-side state on `stop --clean`.

        Three things may be removed:

        1. **Script-managed state root**
           ``<nvstreamer_compose_dir>/vst_data/`` — the bind-mount source
           for each instance's /home/vst/vst_release/vst_data. Owned by
           the script, always safe to wipe. Auto-confirmed under
           ``--clean`` / ``--fresh-start``.

        2. **Legacy state dirs**
           ``<NVSTREAMER_VIDEO_N>/vst_data`` — from older releases that
           nested runtime state inside the user's videos directory. Best-
           effort cleanup so upgrades clean up after themselves. The user's
           video files (siblings of vst_data) are never touched.

        3. **Script-managed videos tree**
           ``<nvstreamer_compose_dir>/videos/`` — the default location used
           when ``--nvstreamer-video-path`` was not specified. May contain
           clips the user uploaded via the NVStreamer UI, so it ALWAYS
           prompts, even with ``--clean``.
        """
        Logger.info("Cleaning NVStreamer state...")
        self._remove_nvstreamer_state_root()
        self._remove_nvstreamer_legacy_state(auto_confirm=auto_confirm)
        self._remove_nvstreamer_script_managed_videos()

    def cleanup_services(self):
        Logger.info("Stopping and cleaning up all services...")
        self.config.existing_vst_deployment = False
        self.config.existing_nvstreamer_deployment = False

        if self.detect_existing_deployments():
            self.stop_existing_deployments()
        else:
            Logger.info("No running deployments found, attempting cleanup anyway...")
            stop_commands = [
                "docker compose -f docker-compose.yaml --env-file ./compose.env "
                "--profile monitoring down --remove-orphans -v",
                "docker compose -f docker-compose.yaml --env-file ./compose.env "
                "down --remove-orphans -v",
            ]
            for cmd in stop_commands:
                SystemUtils.run_command(
                    cmd, cwd=str(self.config.vst_compose_dir), check=False,
                )
            SystemUtils.run_command(
                "docker compose -f docker-compose.yaml --env-file ./compose.env "
                "down --remove-orphans -v",
                cwd=str(self.config.nvstreamer_compose_dir),
                check=False,
            )

        Logger.info("Performing additional cleanup...")
        for container in VST_CONTAINERS + NVSTREAMER_CONTAINERS:
            try:
                result = SystemUtils.run_command(
                    f"docker ps -aq -f name='{container}'",
                    capture_output=True, check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    Logger.info(f"Removing container: {container}")
                    SystemUtils.run_command(f"docker stop {container}", check=False)
                    SystemUtils.run_command(f"docker rm {container}", check=False)
            except Exception:
                pass

        SystemUtils.run_command("docker network prune -f", check=False)
        Logger.success("Cleanup completed")
        print()
        print("Note: To completely reset VST data, you may also want to:")
        print("  sudo rm -rf <VST_VOLUME_PATH>/*")

    def display_full_access_urls(self):
        active = self.config_manager.get_active_nvstreamer_instances()
        print()
        Logger.success("=== Deployment Completed Successfully ===")
        print()
        print("Access URLs:")
        print("============")
        print()
        print(f"NVStreamer Instances ({len(active)} active):")
        for i in active:
            port = 31000 + i - 1
            print(f"  NVStreamer-{i}: http://{self.config.host_ip}:{port}/#/dashboard")
        print()
        print("VST Services:")
        print(f"  VST UI:        http://{self.config.host_ip}:30888/vst/#/dashboard")
        if self.config.with_monitoring:
            print(f"  Grafana:       http://{self.config.host_ip}:3000")
        print()
        print("Host Path Information:")
        print("=====================")
        print(f"  VST Config Path:  {self.config.vst_config_path}")
        print(f"  VST Volume Path:  {self.config.vst_volume}")
        print("  NVStreamer Paths:")
        for i in active:
            print(f"    NVStreamer-{i}:  {self.config.nvstreamer_videos.get(i)}")
        print()
        print("Management Commands:")
        print("===================")
        print(
            "  View logs:    docker compose -f docker-compose.yaml "
            "--env-file ./compose.env logs -f"
        )
        print(f"  Stop services: python3 {__file__} stop")
        print(f"  Restart:      python3 {__file__} deploy --target all")
        print()

    def display_vst_access_urls(self):
        print()
        Logger.success("=== VST Deployment Completed Successfully ===")
        print()
        print("Access URLs:")
        print(f"  VST UI:        http://{self.config.host_ip}:30888/vst/#/dashboard")
        if self.config.with_monitoring:
            print(f"  Grafana:       http://{self.config.host_ip}:3000")
        print()
        print("Host Path Information:")
        print(f"  VST Config Path:  {self.config.vst_config_path}")
        print(f"  VST Volume Path:  {self.config.vst_volume}")
        print()
        print("Management Commands:")
        print(
            "  View logs:    docker compose -f docker-compose.yaml "
            "--env-file ./compose.env logs -f"
        )
        print(f"  Stop services: python3 {__file__} stop --target vst")
        print(f"  Restart:      python3 {__file__} deploy --target vst")
        print()

    def display_nvstreamer_access_urls(self):
        active = self.config_manager.get_active_nvstreamer_instances()
        print()
        Logger.success("=== NVStreamer Deployment Completed Successfully ===")
        print()
        print(f"Access URLs ({len(active)} active):")
        for i in active:
            port = 31000 + i - 1
            print(f"  NVStreamer-{i}: http://{self.config.host_ip}:{port}/#/dashboard")
        print()
        print("NVStreamer Paths:")
        for i in active:
            print(f"  NVStreamer-{i}:  {self.config.nvstreamer_videos.get(i)}")
        print()
        print("Management Commands:")
        print(
            "  View logs:    docker compose -f docker-compose.yaml "
            "--env-file ./compose.env logs -f  (from nvstreamer directory)"
        )
        print(f"  Stop services: python3 {__file__} stop --target nvstreamer")
        print(f"  Restart:      python3 {__file__} deploy --target nvstreamer")
        print()


def show_help():
    help_text = """
VST & NVStreamer One-Click Deployment Script

USAGE:
    python3 oneclick_dc_deployment.py [ACTION] [TARGET] [OPTIONS]
    python3 oneclick_dc_deployment.py [ACTION] [--target TARGET] [OPTIONS]

    TARGET can be passed positionally (e.g. `deploy vst`) or via the
    --target flag. Both forms are equivalent.

ACTIONS (default: deploy):
    deploy          Deploy services
    stop            Stop and cleanup services
    config-only     Only update environment files

TARGETS (--target, default: vst):
    vst             VST stream-processing stack (docker-compose)
                    'vios' is also accepted instead of 'vst'
    nvstreamer      NVStreamer only (docker-compose/nvstreamer)
    all             VST + NVStreamer together

DEPLOYMENT OPTIONS:
    --with-monitoring   Deploy with monitoring services (Grafana, Prometheus)
    --force             Automatically stop existing deployments without prompting
    --interactive       Prompt for confirmation of every value
                        (default: auto-detect & use smart defaults)
    --fresh-start       Stop existing deployments and remove VST volume data
                        (only honored on `deploy`)
    --clean             When used with `stop`, also delete persistent data:
                        - stop vst --clean         -> remove VST volume directory
                        - stop nvstreamer --clean  -> remove NVStreamer videos directory
                        - stop --clean             -> remove both
    --pull-always       Pull latest Docker images before deployment
    --help              Show this help message

CONFIGURATION OVERRIDES:
    --host IP                Override host IP address
    --config-path PATH       Override VST config path
    --volume-path PATH       Override VST volume path
    --nvstreamer-video-path PATH   Use PATH as the NVStreamer videos directory
                             directly (no per-instance subdirs created).
                             Best used with single-instance deploys when the
                             directory already contains your videos.
                             For multi-instance deploys, edit
                             docker-compose/nvstreamer/compose.env to set
                             NVSTREAMER_VIDEO_1..N to different paths manually.
    --instances N            Number of NVStreamer instances to deploy (1-5).
                             Rewrites COMPOSE_PROFILES accordingly.
                             ('--instance N' is also accepted)

IMAGE TAG OVERRIDES:
    --all-tag TAG            Override tag for stream-processor + sensor images
    --streamprocessor-tag TAG  Override stream-processor image tag
    --sensor-tag TAG         Override sensor service image tag
    --nvstreamer-tag TAG     Override NVStreamer service image tag

IMAGE REGISTRY / REPOSITORY OVERRIDES:
    --image-registry REGISTRY   Override the registry/org prefix for VST service images (keeps name and tag), e.g. vios -> vios/vst-sensor:<tag>
    --nvstreamer-image REPO     Override the NVStreamer image repository (keeps tag), e.g. nvstreamer or my-registry/nvstreamer

EXAMPLES:
    # Deploy stream-processing stack only (default target).
    # Auto-detects HOST_IP and uses default VST_CONFIG_PATH / VST_VOLUME paths.
    python3 oneclick_dc_deployment.py --force
    python3 oneclick_dc_deployment.py deploy vst --force

    # Deploy NVStreamer only (1 instance, the default)
    python3 oneclick_dc_deployment.py deploy nvstreamer --force

    # Deploy locally built images (built with IMAGE_REGISTRY=vios, NVSTREAMER_IMAGE=nvstreamer)
    python3 oneclick_dc_deployment.py --force --image-registry vios --nvstreamer-image nvstreamer

    # Deploy 5 NVStreamer instances
    python3 oneclick_dc_deployment.py deploy nvstreamer --instances 5 --force

    # Deploy stream-processing + 3 NVStreamer instances together
    python3 oneclick_dc_deployment.py deploy all --instances 3 --force

    # Equivalent --target form
    python3 oneclick_dc_deployment.py deploy --target nvstreamer --force

    # Override host IP if auto-detection picks the wrong interface
    python3 oneclick_dc_deployment.py deploy --host 192.168.1.50 --force

    # Run with prompts to confirm/change every value
    python3 oneclick_dc_deployment.py deploy --interactive

    # Stop services
    python3 oneclick_dc_deployment.py stop                  # stops everything
    python3 oneclick_dc_deployment.py stop vst              # stops VST stack only
    python3 oneclick_dc_deployment.py stop nvstreamer       # stops NVStreamer only

    # Stop + clean persistent data (--clean)
    python3 oneclick_dc_deployment.py stop --clean              # stop + wipe both
    python3 oneclick_dc_deployment.py stop vst --clean          # stop VST + wipe vst_volume/
    python3 oneclick_dc_deployment.py stop nvstreamer --clean   # stop NVS + wipe videos/

    # Update configuration only (compose.env files), without deploying
    python3 oneclick_dc_deployment.py config-only
"""
    print(help_text)


def apply_command_line_overrides(config: DeploymentConfig, args):
    if args.host:
        config.host_ip = args.host
        Logger.info(f"Using command line IP override: {args.host}")
    if args.config_path:
        config.vst_config_path = args.config_path
        Logger.info(f"Using command line config path override: {args.config_path}")
    if args.volume_path:
        config.vst_volume = args.volume_path
        Logger.info(f"Using command line volume path override: {args.volume_path}")
    if args.nvstreamer_video_path:
        # Use the user-provided path DIRECTLY as each instance's video dir
        # (no auto-created `nvstreamer-N` subdirs). This is the typical
        # single-instance case where the user already has a populated video
        # directory. For multi-instance setups, all instances would share
        # this directory and conflict on vst_data; in that case the user is
        # expected to edit nvstreamer/compose.env per-instance manually.
        config.default_nvstreamer_base_path = args.nvstreamer_video_path
        config.nvstreamer_path_explicit = True
        for i in range(1, 6):
            config.nvstreamer_video_paths[i] = args.nvstreamer_video_path
        Logger.info(
            f"Using command line NVStreamer path override (used as-is, no "
            f"per-instance subdirs): {args.nvstreamer_video_path}"
        )
    if args.instances:
        config.nvstreamer_instances = args.instances
        Logger.info(
            f"Using command line --instances override: {args.instances} "
            f"(COMPOSE_PROFILES will become nvstreamer-1..nvstreamer-{args.instances})"
        )

    config.tag_overrides = {}

    config.image_registry = args.image_registry or ""
    config.nvstreamer_image = args.nvstreamer_image or ""
    if config.image_registry:
        Logger.info(f"Using command line image registry override: {config.image_registry}")
    if config.nvstreamer_image:
        Logger.info(f"Using command line NVStreamer image override: {config.nvstreamer_image}")

    if args.all_tag:
        for img in ['VST_STREAM_PROCESSOR_IMAGE', 'VST_SENSOR_IMAGE']:
            config.tag_overrides[img] = args.all_tag
        Logger.info(f"Using command line all-tag override: {args.all_tag}")
    if args.streamprocessor_tag:
        config.tag_overrides['VST_STREAM_PROCESSOR_IMAGE'] = args.streamprocessor_tag
        Logger.info(f"Using command line stream-processor tag override: {args.streamprocessor_tag}")
    if args.sensor_tag:
        config.tag_overrides['VST_SENSOR_IMAGE'] = args.sensor_tag
        Logger.info(f"Using command line sensor tag override: {args.sensor_tag}")
    if args.nvstreamer_tag:
        config.tag_overrides['NVSTREAMER_IMAGE'] = args.nvstreamer_tag
        Logger.info(f"Using command line NVStreamer tag override: {args.nvstreamer_tag}")


def nvstreamer_deploy(deployment_manager: DeploymentManager, config: DeploymentConfig):
    Logger.info("Starting NVStreamer-only deployment...")
    deployment_manager.check_prerequisites()

    running_nvstreamer = DockerManager.get_running_containers(NVSTREAMER_CONTAINERS)
    if config.fresh_start:
        Logger.info("Fresh start mode enabled - will stop existing NVStreamer deployments")
        if running_nvstreamer:
            deployment_manager.stop_existing_nvstreamer_only()
        else:
            Logger.info("No existing NVStreamer deployments found")
    elif running_nvstreamer:
        print()
        Logger.warning("Existing NVStreamer deployments found.")
        print("This will stop all running NVStreamer containers and remove networks.")
        if config.force_deployment:
            Logger.info("Force deployment enabled. Stopping existing NVStreamer deployments...")
            deployment_manager.stop_existing_nvstreamer_only()
        else:
            response = input(
                "Stop existing NVStreamer deployments and continue? (y/N): ",
            ).strip().lower()
            if response.startswith('y'):
                deployment_manager.stop_existing_nvstreamer_only()
            else:
                Logger.info("NVStreamer deployment cancelled by user")
                print("Tip: Use --force flag to automatically stop existing deployments")
                return

    config_manager = ConfigurationManager(config)
    interactive_config = InteractiveConfiguration(config)
    interactive_config.run_nvstreamer_only_configuration()

    config_manager.update_nvstreamer_compose_env()
    deployment_manager.check_nvstreamer_port_availability()
    deployment_manager.configure_network_buffers()
    deployment_manager.deploy_nvstreamer()
    deployment_manager.display_nvstreamer_access_urls()


def vst_deploy(deployment_manager: DeploymentManager, config: DeploymentConfig):
    Logger.info("Starting VST-only deployment...")
    deployment_manager.check_prerequisites()

    running_vst = DockerManager.get_running_containers(VST_CONTAINERS)
    if config.fresh_start:
        Logger.info("Fresh start mode enabled - will stop existing VST deployments")
        if running_vst:
            deployment_manager.stop_existing_vst_only()
        else:
            Logger.info("No existing VST deployments found")
        deployment_manager.remove_vst_volume()
    elif running_vst:
        print()
        Logger.warning("Existing VST deployments found.")
        print("This will stop all running VST containers and remove networks.")
        if config.force_deployment:
            Logger.info("Force deployment enabled. Stopping existing VST deployments...")
            deployment_manager.stop_existing_vst_only()
        else:
            response = input(
                "Stop existing VST deployments and continue? (y/N): ",
            ).strip().lower()
            if response.startswith('y'):
                deployment_manager.stop_existing_vst_only()
            else:
                Logger.info("VST deployment cancelled by user")
                print("Tip: Use --force flag to automatically stop existing deployments")
                return

    config_manager = ConfigurationManager(config)
    interactive_config = InteractiveConfiguration(config)
    interactive_config.run_vst_only_configuration()

    config_manager.update_main_compose_env()
    deployment_manager.check_vst_port_availability()
    deployment_manager.configure_network_buffers()
    deployment_manager.deploy_vst()
    deployment_manager.display_vst_access_urls()


def main_deploy(deployment_manager: DeploymentManager, config: DeploymentConfig):
    Logger.info("Starting VST stream-processing and NVStreamer deployment...")
    deployment_manager.check_prerequisites()

    if config.fresh_start:
        Logger.info("Fresh start mode enabled - will stop existing deployments")
        if deployment_manager.detect_existing_deployments():
            deployment_manager.stop_existing_deployments()
        else:
            Logger.info("No existing deployments found")
        deployment_manager.remove_vst_volume()
    elif deployment_manager.detect_existing_deployments():
        print()
        Logger.warning("Existing deployments found.")
        print("This will:")
        print("  - Stop all running VST and NVStreamer containers")
        print("  - Remove containers and networks (data volumes preserved)")
        print()
        if config.force_deployment:
            Logger.info("Force deployment enabled. Stopping existing deployments...")
            deployment_manager.stop_existing_deployments()
        else:
            response = input(
                "Stop existing deployments and continue? (y/N): ",
            ).strip().lower()
            if response.startswith('y'):
                deployment_manager.stop_existing_deployments()
            else:
                Logger.info("Deployment cancelled by user")
                print("Tip: Use --force flag to automatically stop existing deployments")
                return

    deployment_manager.configure_network_buffers()

    interactive_config = InteractiveConfiguration(config)
    interactive_config.run_full_configuration()

    deployment_manager.config_manager.update_main_compose_env()
    deployment_manager.config_manager.update_nvstreamer_compose_env()
    deployment_manager.check_port_availability()

    deployment_manager.deploy_nvstreamer()
    deployment_manager.deploy_vst()
    deployment_manager.display_full_access_urls()


def main():
    parser = argparse.ArgumentParser(
        description="VST & NVStreamer One-Click Deployment Script",
        add_help=False,
    )

    parser.add_argument(
        'action', nargs='?', default='deploy',
        choices=['deploy', 'stop', 'config-only'],
        help='Action to perform (default: deploy)',
    )
    # Allow target as a second positional (e.g. `deploy vst`,
    # `deploy nvstreamer`) in addition to the --target flag below.
    parser.add_argument(
        'target_pos', nargs='?', default=None,
        # 'vios' is accepted as a backwards-compat alias for 'vst'.
        choices=['vst', 'vios', 'nvstreamer', 'all'],
        help='Deployment target as a positional (alternative to --target)',
        metavar='TARGET',
    )
    parser.add_argument(
        '--target', default='vst',
        choices=['vst', 'vios', 'nvstreamer', 'all'],
        help='Deployment target (default: vst)',
    )

    parser.add_argument('--with-monitoring', action='store_true',
                        help='Deploy with monitoring services')
    parser.add_argument('--force', action='store_true',
                        help='Automatically stop existing deployments')
    parser.add_argument('--interactive', action='store_true',
                        help='Prompt for confirmation of every value '
                             '(default: auto-detect & use smart defaults)')
    parser.add_argument('--fresh-start', action='store_true',
                        help='Clean start: stop existing, remove VST volume data')
    parser.add_argument('--clean', action='store_true',
                        help='When used with `stop`, also delete persistent data: '
                             'VST volume directory (target=vst) and/or NVStreamer '
                             'videos directory (target=nvstreamer). Bare `stop --clean` '
                             'cleans both.')
    parser.add_argument('--pull-always', action='store_true',
                        help='Pull latest Docker images before deployment')

    parser.add_argument('--host', type=str, help='Override host IP address')
    parser.add_argument('--config-path', type=str, help='Override VST config path')
    parser.add_argument('--volume-path', type=str, help='Override VST volume path')
    parser.add_argument(
        '--nvstreamer-video-path', type=str,
        help='Use this absolute path directly as the NVStreamer videos '
             'directory (no per-instance subdirs). For single-instance '
             'deploys; for multi-instance, edit nvstreamer/compose.env '
             'manually.',
    )
    parser.add_argument(
        '--instances', '--instance',
        type=int, choices=range(1, 6), metavar='N',
        help='Number of NVStreamer instances to deploy (1-5). '
             'Rewrites COMPOSE_PROFILES in nvstreamer/compose.env to '
             'nvstreamer-1,...,nvstreamer-N',
    )

    parser.add_argument('--all-tag', type=str,
                        help='Override tag for stream-processor + sensor images')
    parser.add_argument('--streamprocessor-tag', type=str,
                        help='Override stream-processor service image tag')
    parser.add_argument('--sensor-tag', type=str,
                        help='Override sensor service image tag')
    parser.add_argument('--nvstreamer-tag', type=str,
                        help='Override NVStreamer service image tag')

    # Image registry / repository overrides
    parser.add_argument('--image-registry', type=str,
                        help='Override registry/org prefix for VST service images, keeping name and tag (e.g. vios -> vios/vst-sensor:<tag>)')
    parser.add_argument('--nvstreamer-image', type=str,
                        help='Override the NVStreamer image repository, keeping the tag (e.g. nvstreamer or my-registry/nvstreamer)')

    parser.add_argument('--help', action='store_true', help='Show this help message')

    args = parser.parse_args()

    if args.help:
        show_help()
        return

    action = args.action
    # Positional target (e.g. `deploy vst`) takes precedence over --target.
    explicit_target_given = args.target_pos is not None or any(
        arg == '--target' or arg.startswith('--target=') for arg in sys.argv
    )
    target = args.target_pos if args.target_pos is not None else args.target
    # Treat 'vios' as an alias for 'vst' (kept for backwards compatibility).
    if target == 'vios':
        target = 'vst'

    config = DeploymentConfig()
    config.with_monitoring = args.with_monitoring
    config.force_deployment = args.force
    config.auto_mode = not args.interactive
    config.fresh_start = args.fresh_start
    config.pull_always = args.pull_always

    apply_command_line_overrides(config, args)

    deployment_manager = DeploymentManager(config)

    if action == 'stop':
        # Bare `stop` with no target stops everything; an explicit target
        # narrows the scope.
        effective_stop_target = target if explicit_target_given else 'all'
        if effective_stop_target == 'all':
            deployment_manager.cleanup_services()
        elif effective_stop_target == 'nvstreamer':
            deployment_manager.stop_existing_nvstreamer_only()
        else:
            deployment_manager.stop_existing_vst_only()

        # --clean: also delete the host-side data directories that bind-mount
        # into the containers. Driven by --clean (auto-confirmed since the user
        # has explicitly opted in).
        if args.clean:
            print()
            Logger.info(f"--clean specified: removing data for target={effective_stop_target}")
            if effective_stop_target in ('all', 'vst'):
                deployment_manager.remove_vst_volume(auto_confirm=True)
            if effective_stop_target in ('all', 'nvstreamer'):
                deployment_manager.remove_nvstreamer_videos(auto_confirm=True)
            Logger.success("Clean completed")
    elif action == 'config-only':
        config_manager = ConfigurationManager(config)
        interactive_config = InteractiveConfiguration(config)
        interactive_config.run_full_configuration()
        config_manager.update_main_compose_env()
        config_manager.update_nvstreamer_compose_env()
        Logger.success("Configuration files updated successfully")
        print()
        print("Updated files:")
        print(f"  {config.main_compose_env}")
        print(f"  {config.nvstreamer_compose_env}")
        print()
        print(f"Run 'python3 {__file__} deploy' to deploy with the new configuration.")
    elif action == 'deploy':
        if target == 'nvstreamer':
            nvstreamer_deploy(deployment_manager, config)
        elif target == 'all':
            main_deploy(deployment_manager, config)
        else:
            vst_deploy(deployment_manager, config)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        Logger.info("Deployment interrupted by user")
        sys.exit(1)
    except Exception as e:
        Logger.error(f"Unexpected error: {e}")
        sys.exit(1)
