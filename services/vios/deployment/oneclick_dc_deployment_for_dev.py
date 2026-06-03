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

"""
VIOS One-Click Deployment Script

Automates deployment of VIOS and NVStreamer docker-compose services.

Usage:
    python3 oneclick_dc_deployment_for_dev.py [ACTION] [--target TARGET] [OPTIONS]

Actions:  deploy (default) | stop | config-only
Targets:  vios (default)   | nvstreamer | all

Run with --help for full usage details.
"""

import os
import sys
import subprocess
import argparse
import re
import time
import json
import socket
import shutil
import atexit
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any, Set
import requests
from urllib.parse import urlparse

DEFAULT_SCRIPT_DIR = Path(__file__).parent.absolute()
DEFAULT_NVSTREAMER_BASE_PATH = "/tmp/nvstreamer_auto_deploy"
DEFAULT_NVSTREAMER_TEST_DATA = str(DEFAULT_SCRIPT_DIR.parent / "tools/data")

# Color constants for terminal output
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color

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

class DeploymentConfig:
    """Configuration container for deployment settings"""
    
    def __init__(self):
        # Script configuration - use global constants that can be overridden
        self.script_dir = DEFAULT_SCRIPT_DIR
        
        # Stream-processing is the only supported deployment.
        self.scaled_services = False
        
        self.nvstreamer_compose_env = self.script_dir / "stream-processing/docker-compose/nvstreamer/compose.env"
        self.nvstreamer_compose_file = self.script_dir / "stream-processing/docker-compose/nvstreamer/docker-compose.yaml"
        
        # Default path constants (can be overridden by user)
        self.default_nvstreamer_base_path = DEFAULT_NVSTREAMER_BASE_PATH
        
        # Individual NVStreamer video paths
        self.nvstreamer_video_paths = {
            1: f"{self.default_nvstreamer_base_path}/nvstreamer-1",
            2: f"{self.default_nvstreamer_base_path}/nvstreamer-2",
            3: f"{self.default_nvstreamer_base_path}/nvstreamer-3",
            4: f"{self.default_nvstreamer_base_path}/nvstreamer-4",
            5: f"{self.default_nvstreamer_base_path}/nvstreamer-5"
        }
        
        # Runtime configuration
        self.host_ip = ""
        self.vst_config_path = ""
        self.vst_volume = ""
        self.nvstreamer_videos = {}
        self.rtsp_ports = {}
        self.customize_rtsp_ports = False
        
        # Deployment flags
        self.with_minio = False
        self.with_monitoring = False
        self.force_deployment = False
        self.auto_mode = False
        self.fresh_start = False
        self.pull_always = False
        self.existing_vst_deployment = False
        self.existing_nvstreamer_deployment = False
        self.tag_overrides = {}
        self.image_registry = ""
        self.nvstreamer_image = ""
    
    @property
    def vst_compose_dir(self) -> Path:
        return self.script_dir / "stream-processing" / "docker-compose"
    
    @property
    def main_compose_env(self) -> Path:
        return self.vst_compose_dir / "compose.env"
    
    @property
    def main_compose_file(self) -> Path:
        return self.vst_compose_dir / "docker-compose.yaml"
    
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
        """Validate IP address format and range"""
        if not re.match(r'^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$', ip):
            Logger.error(f"Invalid IP address format: {ip}")
            return False
        
        parts = ip.split('.')
        for part in parts:
            if int(part) > 255:
                Logger.error(f"Invalid IP address format: {ip}")
                return False
        return True
    
    @staticmethod
    def validate_path(path: str) -> bool:
        """Validate that path is absolute"""
        if not os.path.isabs(path):
            Logger.error(f"Path must be absolute (start with /): {path}")
            return False
        return True
    
    @staticmethod
    def validate_port(port: str) -> bool:
        """Validate port number range"""
        try:
            port_num = int(port)
            if 1 <= port_num <= 65535:
                return True
            else:
                Logger.error(f"Invalid port number: {port} (must be 1-65535)")
                return False
        except ValueError:
            Logger.error(f"Invalid port number: {port} (must be 1-65535)")
            return False


class SystemUtils:
    """System utilities for deployment operations"""
    
    @staticmethod
    def run_command(command: str, shell: bool = True, check: bool = True, 
                   capture_output: bool = False, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
        """Execute shell command with error handling"""
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
        """Check if a port is already in use"""
        try:
            result = SystemUtils.run_command(f"ss -tuln | grep ':{port} '", 
                                           capture_output=True, check=False)
            return result.returncode == 0
        except:
            return False
    
    @staticmethod
    def auto_detect_ip() -> Optional[str]:
        """Auto-detect IP address using multiple methods"""
        methods = [
            # Method 1: Try to get IP from default route
            "ip route get 8.8.8.8 2>/dev/null | grep -oP 'src \\K[0-9.]+' | head -1",
            # Method 2: hostname -I
            "hostname -I 2>/dev/null | awk '{print $1}'",
            # Method 3: ip addr show
            "ip addr show | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}' | cut -d'/' -f1"
        ]
        
        for method in methods:
            try:
                result = SystemUtils.run_command(method, capture_output=True, check=False)
                if result.returncode == 0 and result.stdout.strip():
                    detected_ip = result.stdout.strip()
                    if Validator.validate_ip(detected_ip):
                        return detected_ip
            except:
                continue
        
        # Method 4: Try ifconfig if available
        try:
            result = SystemUtils.run_command("which ifconfig", capture_output=True, check=False)
            if result.returncode == 0:
                result = SystemUtils.run_command(
                    "ifconfig | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}'",
                    capture_output=True, check=False
                )
                if result.returncode == 0 and result.stdout.strip():
                    detected_ip = result.stdout.strip()
                    if Validator.validate_ip(detected_ip):
                        return detected_ip
        except:
            pass
        
        return None

class DirectoryManager:
    """Directory management utilities"""
    
    @staticmethod
    def ensure_directory(directory: str, description: str = "directory") -> bool:
        """Create directory if it doesn't exist"""
        if not directory:
            Logger.error("Directory path is empty - check your configuration")
            return False
        
        if not os.path.isabs(directory):
            Logger.error(f"Directory path must be absolute: {directory}")
            return False
        
        dir_path = Path(directory)
        if not dir_path.exists():
            Logger.info(f"Creating {description}: {directory}")
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
                Logger.success(f"{description.capitalize()} created: {directory}")
                return True
            except Exception as e:
                Logger.error(f"Failed to create {description}: {directory} - {e}")
                return False
        else:
            Logger.info(f"{description.capitalize()} already exists: {directory}")
            return True

    @staticmethod
    def copy_test_videos(dest_dir: str, src_dir: str = DEFAULT_NVSTREAMER_TEST_DATA) -> None:
        """Copy video files from src_dir into dest_dir (skips files already present)"""
        src_path = Path(src_dir)
        if not src_path.exists() or not src_path.is_dir():
            Logger.warning(f"Test data source directory does not exist or is not a directory: {src_path}")
            return
        dest_path = Path(dest_dir)
        video_extensions = {".mp4", ".mkv", ".avi", ".mov", ".ts"}
        copied = 0
        for src_file in src_path.iterdir():
            if src_file.is_file() and src_file.suffix.lower() in video_extensions:
                dest_file = dest_path / src_file.name
                if not dest_file.exists():
                    shutil.copy2(src_file, dest_file)
                    Logger.info(f"Copied {src_file.name} -> {dest_dir}")
                    copied += 1
        if copied == 0:
            Logger.info(f"Test videos already present in {dest_dir}")

class DockerManager:
    """Docker and Docker Compose management"""
    
    @staticmethod
    def check_docker_prerequisites() -> bool:
        """Check if Docker and Docker Compose are available"""
        # Check if the current user can talk to the Docker daemon
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
        
        # Check Docker installation
        try:
            SystemUtils.run_command("docker --version", capture_output=True)
        except subprocess.CalledProcessError:
            Logger.error("Docker is not installed. Please install Docker first.")
            return False
        
        # Check Docker Compose installation
        try:
            SystemUtils.run_command("docker compose version", capture_output=True)
        except subprocess.CalledProcessError:
            Logger.error("Docker Compose is not installed or not working. Please install Docker Compose.")
            return False
        
        # Check NVIDIA Docker runtime
        try:
            result = SystemUtils.run_command("docker info 2>/dev/null | grep -i nvidia", 
                                           capture_output=True, check=False)
            if result.returncode != 0:
                Logger.warning("NVIDIA Docker runtime not detected. GPU acceleration may not work.")
                if not input("Continue anyway? (y/N): ").lower().startswith('y'):
                    return False
        except:
            Logger.warning("Could not check NVIDIA Docker runtime")
        
        return True
    
    @staticmethod
    def get_running_containers(container_names: List[str]) -> List[str]:
        """Get list of running containers from given names"""
        running = []
        for container in container_names:
            try:
                result = SystemUtils.run_command(f"docker ps -q -f name='{container}'", 
                                               capture_output=True, check=False)
                if result.returncode == 0 and result.stdout.strip():
                    running.append(container)
            except:
                continue
        return running

class ConfigurationManager:
    """Configuration file management"""
    
    def __init__(self, config: DeploymentConfig):
        self.config = config
    
    
    def get_smart_defaults(self):
        """Get smart defaults for configuration"""
        Logger.info("Detecting system configuration...")
        
        # Auto-detect IP address
        detected_ip = SystemUtils.auto_detect_ip()
        if detected_ip:
            Logger.success(f"Auto-detected IP address: {detected_ip}")
        else:
            Logger.warning("Could not auto-detect IP address")
        
        # Get current values from compose.env files
        current_host_ip = self._read_env_value(self.config.main_compose_env, "HOST_IP")
        current_config_path = self._read_env_value(self.config.main_compose_env, "VST_CONFIG_PATH")
        current_volume_path = self._read_env_value(self.config.main_compose_env, "VST_VOLUME")
        
        # Determine best defaults
        self.config.host_ip = detected_ip or current_host_ip or ""
        
        return {
            'host_ip': self.config.host_ip,
            'config_path': self.config.default_vst_config_path,
            'volume_path': self.config.default_vst_volume,
            'nvstreamer_videos': self.config.nvstreamer_video_paths.copy()
        }
    
    def get_vst_service_images(self):
        """Get VST service Docker images from compose.env"""
        vst_images = {}
        
        # Define the VST service image variables and their display names
        vst_image_vars = {
            'VST_STREAM_PROCESSOR_IMAGE': 'Stream Processor Service',
            'VST_SENSOR_IMAGE': 'Sensor Service',
            'VST_RTSPSERVER_IMAGE': 'RTSP Server Service',
            'VST_RECORDER_IMAGE': 'Recorder Service',
            'VST_STORAGE_IMAGE': 'Storage Service',
            'VST_REPLAYSTREAM_IMAGE': 'Replay Stream Service',
            'VST_LIVESTREAM_IMAGE': 'Live Stream Service',
            'VST_MCP_IMAGE': 'MCP Service'
        }
        
        for var_name, display_name in vst_image_vars.items():
            image_name = self._read_env_value(self.config.main_compose_env, var_name)
            if image_name:
                vst_images[display_name] = image_name
            else:
                vst_images[display_name] = "(not configured)"
        
        # Add NVStreamer image from nvstreamer compose.env
        nvstreamer_image = self._read_env_value(self.config.nvstreamer_compose_env, 'NVSTREAMER_IMAGE')
        if nvstreamer_image:
            vst_images['NVStreamer Service'] = nvstreamer_image
        else:
            vst_images['NVStreamer Service'] = "(not configured)"
        
        return vst_images
    
    def _read_env_value(self, env_file: Path, key: str) -> str:
        """Read value from environment file"""
        if not env_file.exists():
            return ""
        
        try:
            with open(env_file, 'r') as f:
                for line in f:
                    if line.startswith(f"{key}="):
                        value = line.split('=', 1)[1].strip()
                        # Remove comments
                        value = re.sub(r'###.*$', '', value).strip()
                        return value
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
        """Update main compose.env file"""
        Logger.info("Updating main compose.env file...")
        
        updates = {
            'HOST_IP': self.config.host_ip,
            'VST_CONFIG_PATH': f"{self.config.vst_config_path} ### change me",
            'VST_VOLUME': f"{self.config.vst_volume} ### change me"
        }
        
        # Apply registry/org and tag overrides for VST services
        vst_image_vars = ['VST_STREAM_PROCESSOR_IMAGE', 'VST_SENSOR_IMAGE', 'VST_RTSPSERVER_IMAGE',
                          'VST_RECORDER_IMAGE', 'VST_STORAGE_IMAGE', 'VST_REPLAYSTREAM_IMAGE',
                          'VST_LIVESTREAM_IMAGE', 'VST_MCP_IMAGE']
        
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
                Logger.warning(f"Could not apply image override for {image_var} - current image not found")
        
        self._update_env_file(self.config.main_compose_env, updates)
        
        # Update RTSP server ports if customized
        if self.config.customize_rtsp_ports:
            rtsp_updates = {}
            for i in range(1, 6):
                if i in self.config.rtsp_ports:
                    rtsp_updates[f'RTSP_SERVER_PORT_{i}'] = f"{self.config.rtsp_ports[i]} ### change me if you want to stream more than 100 streams per pod"
            self._update_env_file(self.config.main_compose_env, rtsp_updates)
        
        Logger.success("Main compose.env updated")
    
    def update_nvstreamer_compose_env(self):
        """Update nvstreamer compose.env file"""
        Logger.info("Updating nvstreamer compose.env file...")
        
        updates = {}
        for i in range(1, 6):
            if i in self.config.nvstreamer_videos:
                updates[f'NVSTREAMER_VIDEO_{i}'] = f"{self.config.nvstreamer_videos[i]} #change me"
        
        # Apply NVStreamer registry/org and tag overrides if provided
        nvstreamer_image = self.config.nvstreamer_image
        nvstreamer_tag = self.config.tag_overrides.get('NVSTREAMER_IMAGE', "")
        if nvstreamer_image or nvstreamer_tag:
            current_image = self._read_env_value(self.config.nvstreamer_compose_env, 'NVSTREAMER_IMAGE')
            new_image = self._retarget_image(current_image, org=nvstreamer_image,
                                             tag=nvstreamer_tag, org_is_prefix=False)
            if new_image:
                updates['NVSTREAMER_IMAGE'] = new_image
                Logger.info(f"Applying NVStreamer image override: {new_image}")
            else:
                Logger.warning("Could not apply NVStreamer image override - current image not found")
        
        self._update_env_file(self.config.nvstreamer_compose_env, updates)
        Logger.success("NVStreamer compose.env updated")
    
    def _update_env_file(self, env_file: Path, updates: Dict[str, str]):
        """Update environment file with key-value pairs"""
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
        """Get user input with validation"""
        while True:
            if default:
                user_input = input(f"{prompt} [{default}]: ").strip()
                value = user_input if user_input else default
            else:
                value = input(f"{prompt}: ").strip()
            
            if not validate_func or validate_func(value):
                return value
    
    def run_interactive_configuration(self):
        """Run interactive configuration process"""
        Logger.info("Starting interactive configuration...")
        
        # Get smart defaults
        smart_defaults = self.config_manager.get_smart_defaults()
        
        print()
        print("=== Auto-Detected Configuration ===")
        print("The following values have been auto-detected or use smart defaults:")
        print(f"  Host IP: {smart_defaults['host_ip'] or '(not detected)'}")
        print(f"  VST Config Path: {smart_defaults['config_path']}")
        print(f"  VST Volume Path: {smart_defaults['volume_path']}")
        print()
        
        use_smart_defaults = True
        if self.config.auto_mode:
            Logger.info("Auto mode enabled - using all detected/default values")
            
            if not smart_defaults['host_ip']:
                Logger.error("Auto mode requires IP address detection, but could not detect IP")
                Logger.error("Please run without --auto flag or specify IP manually")
                sys.exit(1)
        else:
            response = input("Do you want to use these auto-detected/default values? (Y/n): ").strip().lower()
            if response.startswith('n'):
                use_smart_defaults = False
        
        print()
        print("=== VST Configuration ===")

        print("Press Enter to keep Default value mentioned in Square Brackets []")
        
        # Host IP
        if use_smart_defaults and smart_defaults['host_ip']:
            self.config.host_ip = smart_defaults['host_ip']
            Logger.info(f"Using auto-detected IP: {self.config.host_ip}")
        else:
            self.config.host_ip = self.get_user_input(
                "Enter host IP address", 
                smart_defaults['host_ip'], 
                Validator.validate_ip
            )
        
        # VST Config Path
        if use_smart_defaults:
            self.config.vst_config_path = smart_defaults['config_path']
            Logger.info(f"Using config path: {self.config.vst_config_path}")
        else:
            self.config.vst_config_path = self.get_user_input(
                "Enter VST config path (absolute)", 
                smart_defaults['config_path'], 
                Validator.validate_path
            )
        
        Logger.info(f"Setting up VST configuration directory: {self.config.vst_config_path}")
        DirectoryManager.ensure_directory(self.config.vst_config_path, "VST config directory")
        
        # VST Volume Path
        if use_smart_defaults:
            self.config.vst_volume = smart_defaults['volume_path']
            Logger.info(f"Using volume path: {self.config.vst_volume}")
        else:
            self.config.vst_volume = self.get_user_input(
                "Enter VST volume path (absolute)", 
                smart_defaults['volume_path'], 
                Validator.validate_path
            )
        
        DirectoryManager.ensure_directory(self.config.vst_volume, "VST volume directory")
        Logger.info(f"Setting up VST volume directory: {self.config.vst_volume}")
        
        # Create VST subdirectories
        subdirs = ['vst_data', 'vst_video', 'postgres/db', 'minio/data']
        for subdir in subdirs:
            DirectoryManager.ensure_directory(
                os.path.join(self.config.vst_volume, subdir), 
                f"VST {subdir} directory"
            )
        
        print()
        print("=== NVStreamer Configuration ===")
        print(f"Auto Detected NVStreamer Base Path: {self.config.default_nvstreamer_base_path}")
        if self.config.auto_mode:
            use_smart_defaults = True
        else:
            response = input("Do you want to use auto-detected NVStreamer video path? (y/N): ").strip().lower()
            if response.startswith('n'):
                use_smart_defaults = False
        
        # NVStreamer video paths
        if use_smart_defaults:
            Logger.info("Using default NVStreamer video paths:")
            self.config.nvstreamer_videos = smart_defaults['nvstreamer_videos'].copy()
            
            for i in range(1, 6):
                path = self.config.nvstreamer_videos[i]
                print(f"  NVStreamer-{i}: {path}")
                DirectoryManager.ensure_directory(path, f"NVStreamer-{i} directory")
                DirectoryManager.ensure_directory(
                    os.path.join(path, "vst_data"),
                    f"NVStreamer-{i} vst_data directory"
                )
                DirectoryManager.copy_test_videos(path)
        else:
            # Ask user if they want to use parent directory or configure individually
            print()
            response = input("Do you want to enter a parent directory for all NVStreamer instances? (y/N): ").strip().lower()
            
            if response.startswith('y'):
                # Get parent directory and create nvstreamer-1 through nvstreamer-5
                parent_dir = self.get_user_input(
                    "Enter parent directory for NVStreamer instances (absolute)", 
                    self.config.default_nvstreamer_base_path, 
                    Validator.validate_path
                )
                
                Logger.info(f"Using parent directory: {parent_dir}")
                Logger.info("Will create subdirectories: nvstreamer-1, nvstreamer-2, nvstreamer-3, nvstreamer-4, nvstreamer-5")
                
                for i in range(1, 6):
                    nvstreamer_path = os.path.join(parent_dir, f"nvstreamer-{i}")
                    self.config.nvstreamer_videos[i] = nvstreamer_path
                    print(f"  NVStreamer-{i}: {nvstreamer_path}")
                    
                    DirectoryManager.ensure_directory(
                        nvstreamer_path, 
                        f"NVStreamer-{i} directory"
                    )
                    DirectoryManager.ensure_directory(
                        os.path.join(nvstreamer_path, "vst_data"), 
                        f"NVStreamer-{i} vst_data directory"
                    )
            else:
                # Configure each NVStreamer path individually
                Logger.info("Configuring each NVStreamer path individually:")
                for i in range(1, 6):
                    default_path = self.config.nvstreamer_video_paths[i]
                    self.config.nvstreamer_videos[i] = self.get_user_input(
                        f"Enter NVStreamer-{i} video path (absolute)", 
                        default_path, 
                        Validator.validate_path
                    )
                    DirectoryManager.ensure_directory(
                        self.config.nvstreamer_videos[i], 
                        f"NVStreamer-{i} directory"
                    )
                    DirectoryManager.ensure_directory(
                        os.path.join(self.config.nvstreamer_videos[i], "vst_data"), 
                        f"NVStreamer-{i} vst_data directory"
                    )
        
        print()
        print("=== Optional RTSP Server Port Configuration ===")
        print("Default ports support ~100 streams per pod. Change only if you need more streams.")
        
        if self.config.auto_mode:
            Logger.info("Auto mode - using default RTSP server ports")
        else:
            response = input("Do you want to customize RTSP server ports? (y/N): ").strip().lower()
            if response.startswith('y'):
                self.config.customize_rtsp_ports = True
                for i in range(1, 6):
                    default_port = str(30554 + (i-1)*10)
                    self.config.rtsp_ports[i] = int(self.get_user_input(
                        f"Enter RTSP server port {i}", 
                        default_port, 
                        Validator.validate_port
                    ))

        
        print()
        print("=== Auto-Detected Docker Images ===")
        vst_images = self.config_manager.get_vst_service_images()
        
        for service_name, image_name in vst_images.items():
            print(f"  {service_name:<25}: {image_name}")
        print()
        
        # Ask if user wants to change image tags
        if not self.config.auto_mode:
            self._handle_image_tag_changes()
        
        # Display final configuration summary
        self._display_configuration_summary()
        Logger.success("Configuration completed")
    
    def run_nvstreamer_only_configuration(self):
        """Run NVStreamer-only configuration process"""
        Logger.info("Starting NVStreamer-only configuration...")
        
        # Get smart defaults
        smart_defaults = self.config_manager.get_smart_defaults()
        
        print()
        print("=== NVStreamer-Only Configuration ===")
        print("The following values have been auto-detected or use smart defaults:")
        print(f"  Host IP: {smart_defaults['host_ip'] or '(not detected)'}")
        print(f"  NVStreamer Base Path: {self.config.default_nvstreamer_base_path}")
        print()
        
        use_smart_defaults = True
        if self.config.auto_mode:
            Logger.info("Auto mode enabled - using all detected/default values")
            
            if not smart_defaults['host_ip']:
                Logger.error("Auto mode requires IP address detection, but could not detect IP")
                Logger.error("Please run without --auto flag or specify IP manually")
                sys.exit(1)
        else:
            response = input("Do you want to use these auto-detected/default values? (Y/n): ").strip().lower()
            if response.startswith('n'):
                use_smart_defaults = False
        
        print()
        print("=== Host IP Configuration ===")
        print("Press Enter to keep Default value mentioned in Square Brackets []")
        
        # Host IP
        if use_smart_defaults and smart_defaults['host_ip']:
            self.config.host_ip = smart_defaults['host_ip']
            Logger.info(f"Using auto-detected IP: {self.config.host_ip}")
        else:
            self.config.host_ip = self.get_user_input(
                "Enter host IP address", 
                smart_defaults['host_ip'], 
                Validator.validate_ip
            )
        
        # Configure NVStreamer paths
        print()
        print("=== NVStreamer Configuration ===")
        print(f"Auto Detected NVStreamer Base Path: {self.config.default_nvstreamer_base_path}")
        
        # NVStreamer video paths
        if use_smart_defaults:
            Logger.info("Using default NVStreamer video paths:")
            self.config.nvstreamer_videos = smart_defaults['nvstreamer_videos'].copy()
            
            for i in range(1, 6):
                path = self.config.nvstreamer_videos[i]
                print(f"  NVStreamer-{i}: {path}")
                DirectoryManager.ensure_directory(path, f"NVStreamer-{i} directory")
                DirectoryManager.ensure_directory(
                    os.path.join(path, "vst_data"),
                    f"NVStreamer-{i} vst_data directory"
                )
                DirectoryManager.copy_test_videos(path)
        else:
            # Ask user if they want to use parent directory or configure individually
            print()
            response = input("Do you want to enter a parent directory for all NVStreamer instances? (y/N): ").strip().lower()
            
            if response.startswith('y'):
                # Get parent directory and create nvstreamer-1 through nvstreamer-5
                parent_dir = self.get_user_input(
                    "Enter parent directory for NVStreamer instances (absolute)", 
                    self.config.default_nvstreamer_base_path, 
                    Validator.validate_path
                )
                
                Logger.info(f"Using parent directory: {parent_dir}")
                Logger.info("Will create subdirectories: nvstreamer-1, nvstreamer-2, nvstreamer-3, nvstreamer-4, nvstreamer-5")
                
                for i in range(1, 6):
                    nvstreamer_path = os.path.join(parent_dir, f"nvstreamer-{i}")
                    self.config.nvstreamer_videos[i] = nvstreamer_path
                    print(f"  NVStreamer-{i}: {nvstreamer_path}")
                    
                    DirectoryManager.ensure_directory(
                        nvstreamer_path, 
                        f"NVStreamer-{i} directory"
                    )
                    DirectoryManager.ensure_directory(
                        os.path.join(nvstreamer_path, "vst_data"), 
                        f"NVStreamer-{i} vst_data directory"
                    )
            else:
                # Configure each NVStreamer path individually
                Logger.info("Configuring each NVStreamer path individually:")
                for i in range(1, 6):
                    default_path = self.config.nvstreamer_video_paths[i]
                    self.config.nvstreamer_videos[i] = self.get_user_input(
                        f"Enter NVStreamer-{i} video path (absolute)", 
                        default_path, 
                        Validator.validate_path
                    )
                    DirectoryManager.ensure_directory(
                        self.config.nvstreamer_videos[i], 
                        f"NVStreamer-{i} directory"
                    )
                    DirectoryManager.ensure_directory(
                        os.path.join(self.config.nvstreamer_videos[i], "vst_data"), 
                        f"NVStreamer-{i} vst_data directory"
                    )
        
        print()
        print("=== Auto-Detected NVStreamer Image ===")
        nvstreamer_image = self.config_manager._read_env_value(self.config.nvstreamer_compose_env, 'NVSTREAMER_IMAGE')
        if nvstreamer_image:
            print(f"  NVStreamer Service       : {nvstreamer_image}")
        else:
            print(f"  NVStreamer Service       : (not configured)")
        print()
        
        # Ask if user wants to change NVStreamer image tag
        if not self.config.auto_mode:
            response = input("Do you want to change the NVStreamer image tag? (y/N): ").strip().lower()
            if response.startswith('y'):
                self._change_nvstreamer_image_tag()
        
        # Display final configuration summary
        self._display_nvstreamer_configuration_summary()
        Logger.success("NVStreamer configuration completed")
    
    def _display_nvstreamer_configuration_summary(self):
        """Display NVStreamer configuration summary"""
        print()
        print("=== NVStreamer Configuration Summary ===")
        print(f"Host IP: {self.config.host_ip}")
        print("NVStreamer Video Paths:")
        for i in range(1, 6):
            print(f"  NVStreamer-{i}: {self.config.nvstreamer_videos[i]}")
        print()
    
    def run_vst_only_configuration(self):
        """Run VST-only configuration process"""
        Logger.info("Starting VST-only configuration...")
        
        # Get smart defaults
        smart_defaults = self.config_manager.get_smart_defaults()
        
        print()
        print("=== VST-Only Configuration ===")
        print("The following values have been auto-detected or use smart defaults:")
        print(f"  Host IP: {smart_defaults['host_ip'] or '(not detected)'}")
        print(f"  VST Config Path: {smart_defaults['config_path']}")
        print(f"  VST Volume Path: {smart_defaults['volume_path']}")
        print()
        
        use_smart_defaults = True
        if self.config.auto_mode:
            Logger.info("Auto mode enabled - using all detected/default values")
            
            if not smart_defaults['host_ip']:
                Logger.error("Auto mode requires IP address detection, but could not detect IP")
                Logger.error("Please run without --auto flag or specify IP manually")
                sys.exit(1)
        else:
            response = input("Do you want to use these auto-detected/default values? (Y/n): ").strip().lower()
            if response.startswith('n'):
                use_smart_defaults = False
        
        print()
        print("=== VST Configuration ===")
        print("Press Enter to keep Default value mentioned in Square Brackets []")
        
        # Host IP
        if use_smart_defaults and smart_defaults['host_ip']:
            self.config.host_ip = smart_defaults['host_ip']
            Logger.info(f"Using auto-detected IP: {self.config.host_ip}")
        else:
            self.config.host_ip = self.get_user_input(
                "Enter host IP address", 
                smart_defaults['host_ip'], 
                Validator.validate_ip
            )
        
        # VST Config Path
        if use_smart_defaults:
            self.config.vst_config_path = smart_defaults['config_path']
            Logger.info(f"Using config path: {self.config.vst_config_path}")
        else:
            self.config.vst_config_path = self.get_user_input(
                "Enter VST config path (absolute)", 
                smart_defaults['config_path'], 
                Validator.validate_path
            )
        
        Logger.info(f"Setting up VST configuration directory: {self.config.vst_config_path}")
        DirectoryManager.ensure_directory(self.config.vst_config_path, "VST config directory")
        
        # VST Volume Path
        if use_smart_defaults:
            self.config.vst_volume = smart_defaults['volume_path']
            Logger.info(f"Using volume path: {self.config.vst_volume}")
        else:
            self.config.vst_volume = self.get_user_input(
                "Enter VST volume path (absolute)", 
                smart_defaults['volume_path'], 
                Validator.validate_path
            )
        
        DirectoryManager.ensure_directory(self.config.vst_volume, "VST volume directory")
        Logger.info(f"Setting up VST volume directory: {self.config.vst_volume}")
        
        # Create VST subdirectories
        subdirs = ['vst_data', 'vst_video', 'postgres/db', 'minio/data']
        for subdir in subdirs:
            DirectoryManager.ensure_directory(
                os.path.join(self.config.vst_volume, subdir), 
                f"VST {subdir} directory"
            )
        
        print()
        print("=== Auto-Detected VST Images ===")
        vst_images = self.config_manager.get_vst_service_images()
        
        # Filter out NVStreamer image for VST-only display
        vst_only_images = {k: v for k, v in vst_images.items() if k != 'NVStreamer Service'}
        
        for service_name, image_name in vst_only_images.items():
            print(f"  {service_name:<25}: {image_name}")
        print()
        
        # Ask if user wants to change VST image tags
        if not self.config.auto_mode:
            response = input("Do you want to change any VST image tags? (y/N): ").strip().lower()
            if response.startswith('y'):
                self._handle_vst_image_tag_changes()
        
        print()
        print("=== Optional RTSP Server Port Configuration ===")
        print("Default ports support ~100 streams per pod. Change only if you need more streams.")
        
        if self.config.auto_mode:
            Logger.info("Auto mode - using default RTSP server ports")
        else:
            response = input("Do you want to customize RTSP server ports? (y/N): ").strip().lower()
            if response.startswith('y'):
                self.config.customize_rtsp_ports = True
                for i in range(1, 6):
                    default_port = str(30554 + (i-1)*10)
                    self.config.rtsp_ports[i] = int(self.get_user_input(
                        f"Enter RTSP server port {i}", 
                        default_port, 
                        Validator.validate_port
                    ))
        
        # Display final configuration summary
        self._display_vst_configuration_summary()
        Logger.success("VST configuration completed")
    
    def _display_vst_configuration_summary(self):
        """Display VST configuration summary"""
        print()
        print("=== VST Configuration Summary ===")
        print(f"Host IP: {self.config.host_ip}")
        print(f"VST Config Path: {self.config.vst_config_path}")
        print(f"VST Volume Path: {self.config.vst_volume}")
        
        if self.config.customize_rtsp_ports:
            print("Custom RTSP Ports:")
            for i in range(1, 6):
                print(f"  RTSP Server {i}: {self.config.rtsp_ports[i]}")
        else:
            print("Using default RTSP ports (30554, 30564, 30574, 30584, 30594)")
        print()
    
    def _handle_vst_image_tag_changes(self):
        """Handle VST image tag changes only"""
        print()
        print("=== Change VST Image Tags ===")
        print("Select which VST images to change:")
        print("  1. Service Images (Sensor, RTSP Server, Recorder, Live Stream, Storage, Replay Stream)")
        print("  2. MCP Image")
        print("  3. Cancel")
        print()
        
        while True:
            choice = input("Enter your choice (1/2/3): ").strip()
            
            if choice == '1':
                self._change_service_image_tags()
                break
            elif choice == '2':
                self._change_mcp_image_tag()
                break
            elif choice == '3':
                Logger.info("VST image tag changes cancelled")
                break
            else:
                Logger.warning("Invalid choice. Please enter 1, 2, or 3.")
    
    def _handle_image_tag_changes(self):
        """Handle user input for changing Docker image tags"""
        response = input("Do you want to change any Docker image tags? (y/N): ").strip().lower()
        if not response.startswith('y'):
            return
        
        print()
        print("=== Change Docker Image Tags ===")
        print("Select which images to change:")
        print("  1. Service Images (Sensor, RTSP Server, Recorder, Live Stream, Storage, Replay Stream)")
        print("  2. MCP Image")
        print("  3. NVStreamer Image")
        print("  4. Cancel")
        print()
        
        while True:
            choice = input("Enter your choice (1/2/3/4): ").strip()
            
            if choice == '1':
                self._change_service_image_tags()
                break
            elif choice == '2':
                self._change_mcp_image_tag()
                break
            elif choice == '3':
                self._change_nvstreamer_image_tag()
                break
            elif choice == '4':
                Logger.info("Image tag changes cancelled")
                break
            else:
                Logger.warning("Invalid choice. Please enter 1, 2, 3, or 4.")
    
    def _change_service_image_tags(self):
        """Change image tags for VST service images"""
        print()
        print("=== Change Service Image Tags ===")
        
        # Define service images in order
        service_images = [
            ('VST_SENSOR_IMAGE', 'Sensor'),
            ('VST_RTSPSERVER_IMAGE', 'RTSP Server'),
            ('VST_RECORDER_IMAGE', 'Recorder'),
            ('VST_LIVESTREAM_IMAGE', 'Live Stream'),
            ('VST_STORAGE_IMAGE', 'Storage'),
            ('VST_REPLAYSTREAM_IMAGE', 'Replay Stream')
        ]
        
        # Show current tags
        print("Current service images:")
        current_tags = []
        for env_var, service_name in service_images:
            current_image = self.config_manager._read_env_value(self.config.main_compose_env, env_var)
            if current_image and ':' in current_image:
                current_tag = current_image.rsplit(':', 1)[1]
                current_tags.append(current_tag)
                print(f"  {service_name:<20}: {current_image}")
        
        print()
        print("How do you want to change the tags?")
        print("  1. Change all service tags together (same tag for all)")
        print("  2. Change each service tag individually")
        print("  3. Cancel")
        print()
        
        while True:
            choice = input("Enter your choice (1/2/3): ").strip()
            
            if choice == '1':
                self._change_all_service_tags_together(service_images, current_tags)
                break
            elif choice == '2':
                self._change_service_tags_individually(service_images)
                break
            elif choice == '3':
                Logger.info("Service image tag changes cancelled")
                break
            else:
                Logger.warning("Invalid choice. Please enter 1, 2, or 3.")
    
    def _change_all_service_tags_together(self, service_images, current_tags):
        """Change all service tags to the same new tag"""
        print()
        print("=== Change All Service Tags Together ===")
        print("This will apply the same tag to ALL service images")
        print()
        
        # Get the most common current tag as default
        if current_tags:
            most_common_tag = max(set(current_tags), key=current_tags.count)
        else:
            most_common_tag = "latest"
        
        new_tag = input(f"Enter new tag for all services [{most_common_tag}]: ").strip()
        
        if not new_tag:
            new_tag = most_common_tag
        
        if new_tag == most_common_tag and len(set(current_tags)) == 1:
            Logger.info("No changes made - same tag as current")
            return
        
        # Apply new tag to all services
        updates = {}
        for env_var, service_name in service_images:
            current_image = self.config_manager._read_env_value(self.config.main_compose_env, env_var)
            if current_image:
                # Extract base image (everything before the last colon)
                if ':' in current_image:
                    base_image = current_image.rsplit(':', 1)[0]
                else:
                    base_image = current_image
                
                new_image = f"{base_image}:{new_tag}"
                updates[env_var] = new_image
                Logger.info(f"Will update {service_name} to: {new_image}")
        
        if updates:
            self.config_manager._update_env_file(self.config.main_compose_env, updates)
            Logger.success(f"Updated {len(updates)} service image tags to: {new_tag}")
        else:
            Logger.info("No service image tags were changed")
    
    def _change_service_tags_individually(self, service_images):
        """Change service tags individually for each service"""
        print()
        print("=== Change Service Tags Individually ===")
        print("Enter new tag for each service (press Enter to keep current tag):")
        print()
        
        updates = {}
        for env_var, service_name in service_images:
            current_image = self.config_manager._read_env_value(self.config.main_compose_env, env_var)
            if current_image:
                # Extract current tag (everything after the last colon)
                if ':' in current_image:
                    base_image, current_tag = current_image.rsplit(':', 1)
                else:
                    base_image = current_image
                    current_tag = "latest"
                
                print(f"{service_name} Service:")
                print(f"  Current: {current_image}")
                new_tag = input(f"  New tag [{current_tag}]: ").strip()
                
                if new_tag and new_tag != current_tag:
                    new_image = f"{base_image}:{new_tag}"
                    updates[env_var] = new_image
                    Logger.info(f"Will update {service_name} to: {new_image}")
                print()
        
        if updates:
            self.config_manager._update_env_file(self.config.main_compose_env, updates)
            Logger.success(f"Updated {len(updates)} service image tags")
        else:
            Logger.info("No service image tags were changed")
    
    def _change_mcp_image_tag(self):
        """Change image tag for MCP service"""
        print()
        print("=== Change MCP Image Tag ===")
        
        current_image = self.config_manager._read_env_value(self.config.main_compose_env, 'VST_MCP_IMAGE')
        if not current_image:
            Logger.warning("MCP image not found in configuration")
            return
        
        # Extract current tag
        if ':' in current_image:
            base_image, current_tag = current_image.rsplit(':', 1)
        else:
            base_image = current_image
            current_tag = "latest"
        
        print(f"MCP Service:")
        print(f"  Current: {current_image}")
        new_tag = input(f"  New tag [{current_tag}]: ").strip()
        
        if new_tag and new_tag != current_tag:
            new_image = f"{base_image}:{new_tag}"
            updates = {'VST_MCP_IMAGE': new_image}
            self.config_manager._update_env_file(self.config.main_compose_env, updates)
            Logger.success(f"Updated MCP image to: {new_image}")
        else:
            Logger.info("MCP image tag was not changed")
        print()
    
    def _change_nvstreamer_image_tag(self):
        """Change image tag for NVStreamer service"""
        print()
        print("=== Change NVStreamer Image Tag ===")
        
        current_image = self.config_manager._read_env_value(self.config.nvstreamer_compose_env, 'NVSTREAMER_IMAGE')
        if not current_image:
            Logger.warning("NVStreamer image not found in configuration")
            return
        
        # Extract current tag
        if ':' in current_image:
            base_image, current_tag = current_image.rsplit(':', 1)
        else:
            base_image = current_image
            current_tag = "latest"
        
        print(f"NVStreamer Service:")
        print(f"  Current: {current_image}")
        new_tag = input(f"  New tag [{current_tag}]: ").strip()
        
        if new_tag and new_tag != current_tag:
            new_image = f"{base_image}:{new_tag}"
            updates = {'NVSTREAMER_IMAGE': new_image}
            self.config_manager._update_env_file(self.config.nvstreamer_compose_env, updates)
            Logger.success(f"Updated NVStreamer image to: {new_image}")
        else:
            Logger.info("NVStreamer image tag was not changed")
        print()
    
    def _display_configuration_summary(self):
        """Display final configuration summary"""
        print()
        print("=== Final Configuration Summary ===")
        print(f"Host IP: {self.config.host_ip}")
        print(f"VST Config Path: {self.config.vst_config_path}")
        print(f"VST Volume Path: {self.config.vst_volume}")
        print("NVStreamer Video Paths:")
        for i in range(1, 6):
            print(f"  NVStreamer-{i}: {self.config.nvstreamer_videos[i]}")
        
        if self.config.customize_rtsp_ports:
            print("Custom RTSP Ports:")
            for i in range(1, 6):
                print(f"  RTSP Server {i}: {self.config.rtsp_ports[i]}")
        else:
            print("Using default RTSP ports (30554, 30564, 30574, 30584, 30594)")
        print()

class DeploymentManager:
    """Main deployment management"""
    
    def __init__(self, config: DeploymentConfig):
        self.config = config
        self.config_manager = ConfigurationManager(config)
        self.original_rmem_max = None
        self.original_wmem_max = None

    def _get_compose_health_states(self, compose_base_cmd: str, cwd: str) -> Dict[str, str]:
        """Return health states for compose containers that have healthchecks enabled.

        Uses `docker compose ps -q` + `docker inspect` for reliability across compose versions.
        """
        ps_result = SystemUtils.run_command(
            f"{compose_base_cmd} ps -q",
            capture_output=True,
            check=False,
            cwd=cwd,
        )
        if ps_result.returncode != 0 or not ps_result.stdout.strip():
            return {}

        container_ids = [c for c in ps_result.stdout.split() if c.strip()]
        if not container_ids:
            return {}

        docker_cmd = "docker"

        insp_result = SystemUtils.run_command(
            f"{docker_cmd} inspect {' '.join(container_ids)}",
            capture_output=True,
            check=False,
            cwd=cwd,
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

            # Prefer compose labels when available (still unique across scaled services in this stack)
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
                continue  # no healthcheck configured for this container

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
        """Wait until all healthcheck-enabled compose services report healthy; returns elapsed seconds."""
        start = start_time if start_time is not None else time.monotonic()
        health_services: Optional[Set[str]] = None
        last_log_t = 0.0

        ps_result = SystemUtils.run_command(
            f"{compose_base_cmd} ps -q",
            capture_output=True,
            check=False,
            cwd=cwd,
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
                Logger.info(f"Waiting for Docker healthchecks... pending {len(pending)}/{len(health_services)}")
                last_log_t = elapsed

            time.sleep(poll_interval_s)
    
    def check_prerequisites(self):
        """Check system prerequisites"""
        Logger.info("Checking system prerequisites...")
        
        if not DockerManager.check_docker_prerequisites():
            sys.exit(1)
        
        # Check if compose files exist
        if not self.config.main_compose_file.exists():
            Logger.error(f"Main docker-compose.yaml not found at: {self.config.main_compose_file}")
            sys.exit(1)
        
        if not self.config.nvstreamer_compose_file.exists():
            Logger.error(f"NVStreamer docker-compose.yaml not found at: {self.config.nvstreamer_compose_file}")
            sys.exit(1)
        
        Logger.success("Prerequisites check completed")
    
    def _validate_memory_availability(self) -> bool:
        """Validate if system has enough memory for buffer optimization"""
        try:
            # Get available memory in KB
            result = SystemUtils.run_command("grep MemAvailable /proc/meminfo", capture_output=True)
            available_kb = int(result.stdout.split()[1])
            
            # Convert to bytes and check if we have at least 100MB available
            available_bytes = available_kb * 1024
            required_bytes = 100 * 1024 * 1024  # 100MB minimum
            
            if available_bytes < required_bytes:
                Logger.warning(f"Available memory ({available_bytes // (1024*1024)}MB) is below recommended minimum ({required_bytes // (1024*1024)}MB)")
                return False
            
            return True
        except (subprocess.CalledProcessError, ValueError, IndexError) as e:
            Logger.warning(f"Could not validate memory availability: {e}")
            return True  # Default to allowing if we can't check
    
    def restore_network_buffers(self):
        """Restore original network buffer values"""
        if self.original_rmem_max and self.original_wmem_max:
            try:
                SystemUtils.run_command(f"sudo sysctl -w net.core.rmem_max={self.original_rmem_max}")
                SystemUtils.run_command(f"sudo sysctl -w net.core.wmem_max={self.original_wmem_max}")
                Logger.info("Network buffers restored to original values")
            except subprocess.CalledProcessError as e:
                Logger.warning(f"Failed to restore original network buffer values: {e}")
    
    def configure_network_buffers(self):
        """Configure network buffers for high-throughput streaming with validation and rollback"""
        Logger.info("Configuring network buffer sizes for high-throughput streaming...")
        
        # Store original values for rollback
        try:
            # Get current values
            result = SystemUtils.run_command("sysctl net.core.rmem_max", capture_output=True)
            self.original_rmem_max = result.stdout.strip().split('=')[1].strip()
            
            result = SystemUtils.run_command("sysctl net.core.wmem_max", capture_output=True)
            self.original_wmem_max = result.stdout.strip().split('=')[1].strip()
            
            Logger.info(f"Current buffer sizes - rmem_max: {self.original_rmem_max}, wmem_max: {self.original_wmem_max}")
            
            # Validate system memory before applying changes
            if not self._validate_memory_availability():
                Logger.warning("Insufficient memory for optimal buffer sizes, using conservative values")
                # Use smaller buffer sizes for systems with limited memory
                new_rmem_max = "1000000"  # 1MB instead of 2MB
                new_wmem_max = "1000000"
            else:
                new_rmem_max = "2000000"  # 2MB
                new_wmem_max = "2000000"
            
            # Check if values are already optimal
            if (int(self.original_rmem_max) >= int(new_rmem_max) and 
                int(self.original_wmem_max) >= int(new_wmem_max)):
                Logger.info("Network buffers are already optimally configured")
                return
            
            # Apply new values
            Logger.info(f"Setting network buffers - rmem_max: {new_rmem_max}, wmem_max: {new_wmem_max}")
            SystemUtils.run_command(f"sudo sysctl -w net.core.rmem_max={new_rmem_max}")
            SystemUtils.run_command(f"sudo sysctl -w net.core.wmem_max={new_wmem_max}")
            Logger.success("Network buffers configured successfully")
            
            # Register cleanup on exit
            atexit.register(self.restore_network_buffers)
            Logger.info("Registered network buffer cleanup for script exit")
            
        except (subprocess.CalledProcessError, ValueError, IndexError) as e:
            Logger.error(f"Failed to configure network buffers: {e}")
            Logger.warning("Continuing deployment without network buffer optimization")
    
    def detect_existing_deployments(self) -> bool:
        """Detect existing Docker Compose deployments"""
        Logger.info("Detecting existing Docker Compose deployments...")
        
        vst_containers = [
            "centralizedb", "vst-ingress", "redis-server", "sensor-ms", 
            "storage-ms", "vst-mcp", "prometheus", "grafana", "minio-server"
        ]
        
        nvstreamer_containers = [
            "nvstreamer-1", "nvstreamer-2", "nvstreamer-3", "nvstreamer-4", "nvstreamer-5"
        ]
        
        running_vst = DockerManager.get_running_containers(vst_containers)
        running_nvstreamer = DockerManager.get_running_containers(nvstreamer_containers)
        
        self.config.existing_vst_deployment = bool(running_vst)
        self.config.existing_nvstreamer_deployment = bool(running_nvstreamer)
        
        for container in running_vst:
            Logger.info(f"Found running VST container: {container}")
        
        for container in running_nvstreamer:
            Logger.info(f"Found running NVStreamer container: {container}")
        
        if running_vst or running_nvstreamer:
            Logger.warning("Existing deployments detected!")
            return True
        else:
            Logger.success("No existing deployments found")
            return False
    
    def stop_existing_deployments(self):
        """Stop existing deployments"""
        Logger.info("Stopping existing deployments...")
        
        if self.config.existing_vst_deployment:
            Logger.info("Stopping existing VST deployment...")
            
            # Try to stop with all possible profiles
            stop_commands = [
                "docker compose -f docker-compose.yaml --env-file ./compose.env --profile monitoring --profile minio down --remove-orphans",
                "docker compose -f docker-compose.yaml --env-file ./compose.env --profile monitoring down --remove-orphans",
                "docker compose -f docker-compose.yaml --env-file ./compose.env --profile minio down --remove-orphans",
                "docker compose -f docker-compose.yaml --env-file ./compose.env down --remove-orphans"
            ]
            
            for cmd in stop_commands:
                Logger.info(f"Executing: {cmd}")
                try:
                    SystemUtils.run_command(cmd, cwd=str(self.config.vst_compose_dir))
                    Logger.success("VST services stopped successfully")
                    break
                except subprocess.CalledProcessError:
                    Logger.warning("Command failed, trying next approach...")
            
            # Force stop any remaining VST containers
            vst_containers = [
                "centralizedb", "vst-ingress", "redis-server", "sensor-ms",
                "storage-ms", "vst-mcp", "prometheus", "grafana", "minio-server"
            ]
            
            for container in vst_containers:
                try:
                    result = SystemUtils.run_command(f"docker ps -q -f name='{container}'", 
                                                   capture_output=True, check=False)
                    if result.returncode == 0 and result.stdout.strip():
                        Logger.info(f"Force stopping container: {container}")
                        SystemUtils.run_command(f"docker stop {container}", check=False)
                        SystemUtils.run_command(f"docker rm {container}", check=False)
                except:
                    pass
        
        if self.config.existing_nvstreamer_deployment:
            Logger.info("Stopping existing NVStreamer deployment...")
            
            try:
                SystemUtils.run_command(
                    "docker compose -f docker-compose.yaml --env-file ./compose.env down --remove-orphans",
                    cwd=str(self.config.script_dir / "scaling" / "docker-compose" / "nvstreamer")
                )
                Logger.success("NVStreamer services stopped successfully")
            except subprocess.CalledProcessError:
                Logger.warning("Docker compose stop failed, trying manual container stop...")
                
                # Force stop any remaining NVStreamer containers
                nvstreamer_containers = [
                    "nvstreamer-1", "nvstreamer-2", "nvstreamer-3", "nvstreamer-4", "nvstreamer-5"
                ]
                
                for container in nvstreamer_containers:
                    try:
                        result = SystemUtils.run_command(f"docker ps -q -f name='{container}'", 
                                                       capture_output=True, check=False)
                        if result.returncode == 0 and result.stdout.strip():
                            Logger.info(f"Force stopping container: {container}")
                            SystemUtils.run_command(f"docker stop {container}", check=False)
                            SystemUtils.run_command(f"docker rm {container}", check=False)
                    except:
                        pass
        
        # Wait for containers to fully stop
        Logger.info("Waiting for containers to fully stop...")
        time.sleep(5)
        
        # Clean up any orphaned networks
        Logger.info("Cleaning up Docker networks...")
        SystemUtils.run_command("docker network prune -f", check=False)
        
        Logger.success("Existing deployments stopped")
    
    def check_port_availability(self):
        """Check port availability"""
        ports = [
            5432,   # PostgreSQL
            6379,   # Redis
            9000,   # MinIO API
            9001,   # MinIO Console
            3000,   # Grafana
            30000,  # Sensor HTTP
            30888,  # VST Ingress
            31000, 31001, 31002, 31003, 31004,  # NVStreamer HTTP
            31554, 31564, 31574, 31584, 31594   # NVStreamer RTSP
        ]
        
        Logger.info("Checking port availability...")
        conflicts = 0
        
        for port in ports:
            if SystemUtils.is_port_in_use(port):
                Logger.warning(f"Port {port} is already in use")
                conflicts += 1
        
        if conflicts > 0:
            Logger.warning(f"Found {conflicts} port conflicts. Services may fail to start.")
            if not input("Continue anyway? (y/N): ").lower().startswith('y'):
                sys.exit(1)
        else:
            Logger.success("All required ports are available")
    
    def deploy_nvstreamer(self):
        """Deploy NVStreamer instances"""
        Logger.info("Deploying NVStreamer instances...")
        Logger.info(f"{self.config.script_dir}")
        
        nvstreamer_dir = self.config.script_dir / "scaling" / "docker-compose" / "nvstreamer"
        
        # Pull images only if --pull-always is specified
        if self.config.pull_always:
            Logger.info("Pulling NVStreamer images...")
            SystemUtils.run_command(
                "docker compose -f docker-compose.yaml --env-file ./compose.env pull",
                cwd=str(nvstreamer_dir)
            )
        else:
            Logger.info("Skipping image pull (use --pull-always to pull latest images)")
        
        # Deploy NVStreamer
        Logger.info("Starting NVStreamer containers...")
        compose_base_cmd = "docker compose -f docker-compose.yaml --env-file ./compose.env"
        compose_start_t = time.monotonic()
        SystemUtils.run_command(
            f"{compose_base_cmd} up --force-recreate -d",
            cwd=str(nvstreamer_dir)
        )
        
        Logger.success("NVStreamer deployment completed")
        elapsed_s = self._wait_for_compose_services_healthy(
            compose_base_cmd,
            cwd=str(nvstreamer_dir),
            start_time=compose_start_t,
        )
        if elapsed_s is None:
            Logger.info("Timed out waiting for NVStreamer Docker healthchecks.")
        elif elapsed_s < 0:
            Logger.info("No Docker healthchecks enabled for NVStreamer services; skipping health timing.")
        else:
            Logger.success(f"NVStreamer Docker healthchecks: all healthy in {elapsed_s:.1f}s (from compose start)")
        
        # Wait for services to be ready
        Logger.info("Waiting for NVStreamer services to be ready...")
        time.sleep(10)
        
        # Check service health
        healthy_services = 0
        for i in range(1, 6):
            port = 31000 + i - 1
        
            try:
                response = requests.get(f"http://{self.config.host_ip}:{port}", timeout=5)
                Logger.info(f"NVStreamer-{i} response: {response.status_code}")
                if response.status_code in [200, 404, 302]:
                    healthy_services += 1
                    Logger.success(f"NVStreamer-{i} is responding on port {port}")
                else:
                    Logger.warning(f"NVStreamer-{i} may not be ready on port {port}")
                    continue # Don't retry for bad status codes, move to next service
            except:
                Logger.warning(f"NVStreamer-{i} may not be ready on port {port}")
        
        Logger.info(f"NVStreamer health check: {healthy_services}/5 services responding")
    
    def deploy_vst(self):
        """Deploy VST services"""
        Logger.info("Deploying VST services...")
        
        # Build profiles string for both pull and up commands
        profiles = ""
        if self.config.with_monitoring:
            profiles += " --profile monitoring"
        if self.config.with_minio:
            profiles += " --profile minio"
        
        # Pull images only if --pull-always is specified
        if self.config.pull_always:
            Logger.info("Pulling VST images...")
            SystemUtils.run_command(
                f"docker compose -f docker-compose.yaml --env-file ./compose.env{profiles} pull",
                cwd=str(self.config.vst_compose_dir)
            )
        else:
            Logger.info("Skipping image pull (use --pull-always to pull latest images)")
        
        # Deploy VST
        Logger.info("Starting VST containers...")
        compose_base_cmd = f"docker compose -f docker-compose.yaml --env-file ./compose.env{profiles}"
        compose_start_t = time.monotonic()
        SystemUtils.run_command(
            f"{compose_base_cmd} up --force-recreate -d",
            cwd=str(self.config.vst_compose_dir)
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
            Logger.info("No Docker healthchecks enabled for VST services; skipping health timing.")
        else:
            Logger.success(f"VST Docker healthchecks: all healthy in {elapsed_s:.1f}s (from compose start)")
        
        # Wait for services to be ready
        Logger.info("Waiting for VST services to be ready...")
        time.sleep(10)
        
        # Check VST service health
        try:
            response = requests.get(f"http://{self.config.host_ip}:30888/vst/", timeout=5)
            if response.status_code in [200, 404, 302]:
                Logger.success("VST service is responding")
            else:
                Logger.warning("VST service may not be ready yet. Please check logs if issues persist.")
        except:
            Logger.warning("VST service may not be ready yet. Please check logs if issues persist.")
        
        # Perform health checks on microservices
        self.health_check_services()
    
    def health_check_services(self):
        """Health check function for microservices with retry logic"""
        Logger.info("Performing health checks on microservices...")
        
        ms_names = {"Sensor Service": "sensor", "Livestream Service": "live", "Replay Stream Service": "replay", "Storage Service": "storage", "RTSP Server Service": "rtsp", "Recorder Service": "recorder"}
        healthy_services = 0
        total_services = len(ms_names)
        max_retries = 12  # 12 retries with 5 second intervals = 60 seconds total
        retry_interval = 5
        
        print()
        print("=== Microservice Health Check ===")
        print("Waiting for services to come online (up to 60 seconds per service)...")
        print("Progress: . = retrying, ✓ = healthy (HTTP 200 or 404), ✗ = failed")
        print()
        
        for display_name, ms_name in ms_names.items():
            api_url = f"http://{self.config.host_ip}:30888/api/v1/{ms_name}/version"
            print(f"{display_name:25} ", end="", flush=True)
            
            retry_count = 0
            service_healthy = False
            
            # Retry logic
            while retry_count < max_retries and not service_healthy:
                try:
                    response = requests.get(api_url, timeout=5)
                    if response.status_code == 200:
                        print(f"{Colors.GREEN}✓ OK{Colors.NC}")
                        service_healthy = True
                        healthy_services += 1
                    elif response.status_code == 404:
                        # 404 means service is running but /version endpoint doesn't exist
                        print(f"{Colors.GREEN}✓ OK{Colors.NC}")
                        service_healthy = True
                        healthy_services += 1
                    else:
                        retry_count += 1
                        if retry_count < max_retries:
                            print(".", end="", flush=True)
                            time.sleep(retry_interval)
                except:
                    retry_count += 1
                    if retry_count < max_retries:
                        print(".", end="", flush=True)
                        time.sleep(retry_interval)
            
            # If we exhausted retries and service is still not healthy
            if not service_healthy:
                print(f"{Colors.RED}✗ FAILED{Colors.NC} (after {max_retries} retries)")
        
        print()
        print(f"Health Check Summary: {healthy_services}/{total_services} services healthy")
        
        if healthy_services == total_services:
            Logger.success("All microservices are healthy ✓")
        elif healthy_services > 0:
            Logger.warning("Some microservices may not be fully ready yet")
        else:
            Logger.error("No microservices are responding - check deployment logs")
        
        print()
    
    def display_access_urls(self):
        """Display access URLs"""
        print()
        Logger.success("=== Deployment Completed Successfully ===")
        print()
        print("Access URLs:")
        print("============")
        print()
        print("NVStreamer Instances:")
        for i in range(1, 6):
            port = 31000 + i - 1
            print(f"  NVStreamer-{i}: http://{self.config.host_ip}:{port}/#/dashboard")
        print()
        print("VST Services:")
        print(f"  VST UI:        http://{self.config.host_ip}:30888/vst/#/dashboard")
        
        if self.config.with_monitoring:
            print(f"  Grafana:       http://{self.config.host_ip}:3000")
        
        if self.config.with_minio:
            print(f"  MinIO Console: http://{self.config.host_ip}:9001")
            print(f"  MinIO API:     http://{self.config.host_ip}:9000")
            print("                 Credentials: admin/nvidia123!")
        
        print()
        print("Host Path Information:")
        print("=====================")
        print(f"  VST Config Path:  {self.config.vst_config_path}")
        print(f"  VST Volume Path:  {self.config.vst_volume}")
        print("  NVStreamer Paths:")
        for i in range(1, 6):
            print(f"    NVStreamer-{i}:  {self.config.nvstreamer_videos[i]}")
        print()
        print("Next Steps:")
        print("===========")
        print("1. Upload video files to NVStreamer instances via their web interfaces")
        print("2. Use 'Scan Sensors' in VST UI to detect newly uploaded streams")
        print("3. Recording initialization for 500 streams takes 30-40 minutes")
        print()
        print("Management Commands:")
        print("===================")
        print("  View logs:    docker compose -f docker-compose.yaml --env-file ./compose.env logs -f")
        print(f"  Stop services: python3 {__file__} stop")
        print(f"  Restart:      python3 {__file__} deploy --target all")
        print()
    
    def cleanup_services(self):
        """Stop and cleanup all services"""
        Logger.info("Stopping and cleaning up all services...")
        
        # Initialize flags
        self.config.existing_vst_deployment = False
        self.config.existing_nvstreamer_deployment = False
        
        # Detect existing deployments
        if self.detect_existing_deployments():
            print()
            Logger.info("Found existing deployments. Proceeding with cleanup...")
            self.stop_existing_deployments()
        else:
            Logger.info("No running deployments found, but will attempt cleanup anyway...")
            
            # Try to stop services even if not detected
            Logger.info("Attempting to stop VST services...")
            
            stop_commands = [
                "docker compose -f docker-compose.yaml --env-file ./compose.env --profile monitoring --profile minio down --remove-orphans -v",
                "docker compose -f docker-compose.yaml --env-file ./compose.env --profile monitoring down --remove-orphans -v",
                "docker compose -f docker-compose.yaml --env-file ./compose.env --profile minio down --remove-orphans -v",
                "docker compose -f docker-compose.yaml --env-file ./compose.env down --remove-orphans -v"
            ]
            
            for compose_dir in [
                self.config.script_dir / "stream-processing" / "docker-compose",
                self.config.script_dir / "scaling" / "docker-compose",
            ]:
                for cmd in stop_commands:
                    Logger.info(f"Trying ({compose_dir.parent.name}): {cmd}")
                    SystemUtils.run_command(cmd, cwd=str(compose_dir), check=False)
            
            # Stop NVStreamer services
            Logger.info("Attempting to stop NVStreamer services...")
            SystemUtils.run_command(
                "docker compose -f docker-compose.yaml --env-file ./compose.env down --remove-orphans -v",
                cwd=str(self.config.script_dir / "scaling" / "docker-compose" / "nvstreamer"),
                check=False
            )
        
        # Additional cleanup
        Logger.info("Performing additional cleanup...")
        
        # Remove any remaining containers
        all_containers = [
            "centralizedb", "vst-ingress", "redis-server", "sensor-ms", "storage-ms", "vst-mcp",
            "prometheus", "grafana", "minio-server",
            "nvstreamer-1", "nvstreamer-2", "nvstreamer-3", "nvstreamer-4", "nvstreamer-5"
        ]
        
        for container in all_containers:
            try:
                result = SystemUtils.run_command(f"docker ps -aq -f name='{container}'", 
                                               capture_output=True, check=False)
                if result.returncode == 0 and result.stdout.strip():
                    Logger.info(f"Removing container: {container}")
                    SystemUtils.run_command(f"docker stop {container}", check=False)
                    SystemUtils.run_command(f"docker rm {container}", check=False)
            except:
                pass
        
        # Clean up networks
        SystemUtils.run_command("docker network prune -f", check=False)
        
        Logger.success("Cleanup completed")
        
        print()
        print("Note: To completely reset VST data, you may also want to:")
        print("  sudo rm -rf <VST_VOLUME_PATH>/*")
        print("  (This will delete all VST data, logs, and recordings)")
    
    def remove_vst_volume(self):
        """Remove VST volume directory for fresh start"""
        Logger.info("Removing VST volume directory for fresh start...")
        
        # Get the VST volume path - use configured path if available, otherwise use default
        vst_volume_path = self.config.vst_volume if self.config.vst_volume else str(self.config.default_vst_volume)
        
        if not vst_volume_path or not os.path.exists(vst_volume_path):
            Logger.warning(f"VST volume directory not found or not configured: {vst_volume_path}")
            return
        
        Logger.warning(f"This will permanently delete all VST data in: {vst_volume_path}")
        Logger.warning("This includes all recordings, configurations, and database data!")
        
        if self.config.fresh_start:
            Logger.info("Fresh start mode - automatically removing VST volume directory...")
            proceed = True
        else:
            response = input("Are you sure you want to delete all VST data? (yes/no): ").strip().lower()
            proceed = response == 'yes'
        
        if proceed:
            
            try:
                SystemUtils.run_command(f"sudo rm -rf {vst_volume_path}")
                Logger.success("VST volume directory removed successfully with sudo")
            except subprocess.CalledProcessError as e:
                Logger.error(f"Failed to remove VST volume directory: {e}")
        else:
            Logger.info("VST volume directory removal cancelled by user")
    
    def stop_existing_nvstreamer_only(self):
        """Stop only NVStreamer deployments"""
        Logger.info("Stopping existing NVStreamer deployment...")
        
        try:
            SystemUtils.run_command(
                "docker compose -f docker-compose.yaml --env-file ./compose.env down --remove-orphans",
                cwd=str(self.config.script_dir / "scaling" / "docker-compose" / "nvstreamer")
            )
            Logger.success("NVStreamer services stopped successfully")
        except subprocess.CalledProcessError:
            Logger.warning("Docker compose stop failed, trying manual container stop...")
            
            # Force stop any remaining NVStreamer containers
            nvstreamer_containers = [
                "nvstreamer-1", "nvstreamer-2", "nvstreamer-3", "nvstreamer-4", "nvstreamer-5"
            ]
            
            for container in nvstreamer_containers:
                try:
                    result = SystemUtils.run_command(f"docker ps -q -f name='{container}'", 
                                                   capture_output=True, check=False)
                    if result.returncode == 0 and result.stdout.strip():
                        Logger.info(f"Force stopping container: {container}")
                        SystemUtils.run_command(f"docker stop {container}", check=False)
                        SystemUtils.run_command(f"docker rm {container}", check=False)
                except:
                    pass
        
        # Wait for containers to fully stop
        Logger.info("Waiting for NVStreamer containers to fully stop...")
        time.sleep(3)
        
        Logger.success("NVStreamer deployment stopped")
    
    def stop_existing_vst_only(self):
        """Stop only VST deployments"""
        Logger.info("Stopping existing VST deployment...")
        
        # Try to stop with all possible profiles
        stop_commands = [
            "docker compose -f docker-compose.yaml --env-file ./compose.env --profile monitoring --profile minio down --remove-orphans",
            "docker compose -f docker-compose.yaml --env-file ./compose.env --profile monitoring down --remove-orphans",
            "docker compose -f docker-compose.yaml --env-file ./compose.env --profile minio down --remove-orphans",
            "docker compose -f docker-compose.yaml --env-file ./compose.env down --remove-orphans"
        ]
        
        for compose_dir in [
            self.config.script_dir / "stream-processing" / "docker-compose",
            self.config.script_dir / "scaling" / "docker-compose",
        ]:
            for cmd in stop_commands:
                Logger.info(f"Executing ({compose_dir.parent.name}): {cmd}")
                try:
                    SystemUtils.run_command(cmd, cwd=str(compose_dir))
                    Logger.success(f"VST services stopped successfully ({compose_dir.parent.name})")
                    break
                except subprocess.CalledProcessError:
                    Logger.warning("Command failed, trying next approach...")
        
        # Force stop any remaining VST containers
        vst_containers = [
            "centralizedb", "vst-ingress", "redis-server", "sensor-ms",
            "storage-ms", "vst-mcp", "prometheus", "grafana", "minio-server"
        ]
        
        for container in vst_containers:
            try:
                result = SystemUtils.run_command(f"docker ps -q -f name='{container}'", 
                                               capture_output=True, check=False)
                if result.returncode == 0 and result.stdout.strip():
                    Logger.info(f"Force stopping container: {container}")
                    SystemUtils.run_command(f"docker stop {container}", check=False)
                    SystemUtils.run_command(f"docker rm {container}", check=False)
            except:
                pass
        
        # Wait for containers to fully stop
        Logger.info("Waiting for VST containers to fully stop...")
        time.sleep(3)
        
        # Clean up networks
        SystemUtils.run_command("docker network prune -f", check=False)
        
        Logger.success("VST deployment stopped")
    
    def check_nvstreamer_port_availability(self):
        """Check port availability for NVStreamer only"""
        ports = [
            31000, 31001, 31002, 31003, 31004,  # NVStreamer HTTP
            31554, 31564, 31574, 31584, 31594   # NVStreamer RTSP
        ]
        
        Logger.info("Checking NVStreamer port availability...")
        conflicts = 0
        
        for port in ports:
            if SystemUtils.is_port_in_use(port):
                Logger.warning(f"Port {port} is already in use")
                conflicts += 1
        
        if conflicts > 0:
            Logger.warning(f"Found {conflicts} NVStreamer port conflicts. Services may fail to start.")
            if not input("Continue anyway? (y/N): ").lower().startswith('y'):
                sys.exit(1)
        else:
            Logger.success("All required NVStreamer ports are available")
    
    def check_vst_port_availability(self):
        """Check port availability for VST only"""
        ports = [
            5432,   # PostgreSQL
            6379,   # Redis
            9000,   # MinIO API
            9001,   # MinIO Console
            3000,   # Grafana
            30000,  # Sensor HTTP
            30888,  # VST Ingress
        ]
        
        Logger.info("Checking VST port availability...")
        conflicts = 0
        
        for port in ports:
            if SystemUtils.is_port_in_use(port):
                Logger.warning(f"Port {port} is already in use")
                conflicts += 1
        
        if conflicts > 0:
            Logger.warning(f"Found {conflicts} VST port conflicts. Services may fail to start.")
            if not input("Continue anyway? (y/N): ").lower().startswith('y'):
                sys.exit(1)
        else:
            Logger.success("All required VST ports are available")
    
    def display_vst_access_urls(self):
        """Display VST access URLs only"""
        print()
        Logger.success("=== VST Deployment Completed Successfully ===")
        print()
        print("Access URLs:")
        print("============")
        print()
        print("VST Services:")
        print(f"  VST UI:        http://{self.config.host_ip}:30888/vst/#/dashboard")
        
        if self.config.with_monitoring:
            print(f"  Grafana:       http://{self.config.host_ip}:3000")
        
        if self.config.with_minio:
            print(f"  MinIO Console: http://{self.config.host_ip}:9001")
            print(f"  MinIO API:     http://{self.config.host_ip}:9000")
            print("                 Credentials: admin/nvidia123!")
        
        print()
        print("Host Path Information:")
        print("=====================")
        print(f"  VST Config Path:  {self.config.vst_config_path}")
        print(f"  VST Volume Path:  {self.config.vst_volume}")
        print()
        print("Next Steps:")
        print("===========")
        print("1. Configure VST services via the web interface")
        print("2. Set up sensors and data sources")
        print("3. Configure analytics and monitoring")
        print()
        print("Management Commands:")
        print("===================")
        print("  View logs:    docker compose -f docker-compose.yaml --env-file ./compose.env logs -f")
        print(f"  Stop services: python3 {__file__} stop --target vios")
        print(f"  Restart:      python3 {__file__} deploy --target {'scaled' if self.config.scaled_services else 'vios'}")
        print()
    
    def display_nvstreamer_access_urls(self):
        """Display NVStreamer access URLs only"""
        print()
        Logger.success("=== NVStreamer Deployment Completed Successfully ===")
        print()
        print("Access URLs:")
        print("============")
        print()
        print("NVStreamer Instances:")
        for i in range(1, 6):
            port = 31000 + i - 1
            print(f"  NVStreamer-{i}: http://{self.config.host_ip}:{port}/#/dashboard")
        print()
        print("Host Path Information:")
        print("=====================")
        print("  NVStreamer Paths:")
        for i in range(1, 6):
            print(f"    NVStreamer-{i}:  {self.config.nvstreamer_videos[i]}")
        print()
        print("Next Steps:")
        print("===========")
        print("1. Upload video files to NVStreamer instances via their web interfaces")
        print("2. Configure streams using the NVStreamer web dashboard")
        print()
        print("Management Commands:")
        print("===================")
        print("  View logs:    docker compose -f docker-compose.yaml --env-file ./compose.env logs -f")
        print("                (from nvstreamer directory)")
        print(f"  Stop services: python3 {__file__} stop --target nvstreamer")
        print(f"  Restart:      python3 {__file__} deploy --target nvstreamer")
        print()
    

def show_help():
    """Display help information"""
    help_text = """
VIOS One-Click Deployment Script

USAGE:
    python3 oneclick_dc_deployment_for_dev.py [ACTION] [--target TARGET] [OPTIONS]

ACTIONS (default: deploy):
    deploy          Deploy services
    stop            Stop and cleanup services
    config-only     Only update environment files

TARGETS (--target, default: vios):
    vios            Stream-processor stack  (stream-processing/docker-compose)
    nvstreamer      NVStreamer only
    all             VIOS + NVStreamer together

DEPLOYMENT OPTIONS:
    --with-minio        Deploy with MinIO object storage
    --with-monitoring   Deploy with monitoring services (Grafana, Prometheus)
    --force             Automatically stop existing deployments without prompting
    --auto              Use auto-detected values without user confirmation
    --fresh-start       Stop existing deployments and remove VST volume data for clean start
    --pull-always       Pull latest Docker images before deployment (default: use local images)
    --path PATH         Copy scaling folder from specified path
    --help              Show this help message

CONFIGURATION OVERRIDES:
    --port IP               Override host IP address
    --config-path PATH      Override VST config path
    --volume-path PATH      Override VST volume path
    --nvstreamer-path PATH  Override NVStreamer base path

IMAGE TAG OVERRIDES:
    --all-tag TAG           Override tag for all VST service images including stream-processor (except MCP and NVStreamer)
    --sensor-tag TAG        Override sensor service image tag
    --rtsp-tag TAG          Override RTSP server service image tag
    --recorder-tag TAG      Override recorder service image tag
    --livestream-tag TAG    Override livestream service image tag
    --storage-tag TAG       Override storage service image tag
    --replay-tag TAG        Override replay stream service image tag
    --mcp-tag TAG           Override MCP service image tag
    --nvstreamer-tag TAG    Override NVStreamer service image tag

IMAGE REGISTRY / REPOSITORY OVERRIDES:
    --image-registry REGISTRY   Override the registry/org prefix for all VST service images including MCP (keeps name and tag), e.g. vios -> vios/vst-sensor:<tag>
    --nvstreamer-image REPO     Override the NVStreamer image repository (keeps tag), e.g. nvstreamer or my-registry/nvstreamer

EXAMPLES:
    # Deploy VIOS stream-processor (default target)
    python3 oneclick_dc_deployment_for_dev.py --auto --force
    python3 oneclick_dc_deployment_for_dev.py deploy --target vios --auto --force

    # Deploy NVStreamer only
    python3 oneclick_dc_deployment_for_dev.py deploy --target nvstreamer --auto --force

    # Deploy VIOS + NVStreamer together
    python3 oneclick_dc_deployment_for_dev.py deploy --target all --auto --force

    # Deploy with extras
    python3 oneclick_dc_deployment_for_dev.py --auto --force --with-minio --with-monitoring
    python3 oneclick_dc_deployment_for_dev.py --auto --force --pull-always

    # Deploy locally built images (built with IMAGE_REGISTRY=vios, NVSTREAMER_IMAGE=nvstreamer)
    python3 oneclick_dc_deployment_for_dev.py --auto --force --image-registry vios --nvstreamer-image nvstreamer

    # Fresh start (removes existing data)
    python3 oneclick_dc_deployment_for_dev.py --fresh-start --auto --force

    # Stop services
    python3 oneclick_dc_deployment_for_dev.py stop                      # Stop everything
    python3 oneclick_dc_deployment_for_dev.py stop --target vios        # Stop VIOS only
    python3 oneclick_dc_deployment_for_dev.py stop --target nvstreamer  # Stop NVStreamer only

    # Update configuration only
    python3 oneclick_dc_deployment_for_dev.py config-only

DESCRIPTION:
    This script automates the deployment of VIOS and NVStreamer services.
    It will:
    1. Check system prerequisites
    2. Detect and optionally stop existing deployments
    3. Prompt for configuration values (or auto-detect with --auto)
    4. Update compose.env files
    5. Deploy the selected target services
    6. Perform health checks on microservices
    7. Display access URLs

    By default, the script uses existing local Docker images.
    Use --pull-always to pull the latest images from the registry.

PREREQUISITES:
    - Python 3.6+
    - Docker and Docker Compose installed
    - NVIDIA Docker runtime (for GPU support)
    - Sudo privileges
    - Network bandwidth: minimum 2.5 Gb/s for 500 streams
    - Python packages: requests

ACCESS URLS (after deployment):
    - NVStreamer instances: http://<HOST_IP>:31000-31004/#/dashboard
    - VIOS UI: http://<HOST_IP>:30888/vios/#/dashboard
    - Grafana: http://<HOST_IP>:3000 (if --with-monitoring used)
    - MinIO Console: http://<HOST_IP>:9001 (if --with-minio used)
"""
    print(help_text)

def apply_command_line_overrides(config: DeploymentConfig, args):
    """Apply command line argument overrides to configuration"""
    
    # Configuration path overrides
    if args.port:
        config.host_ip = args.port
        Logger.info(f"Using command line IP override: {args.port}")
    
    if args.config_path:
        config.vst_config_path = args.config_path
        Logger.info(f"Using command line config path override: {args.config_path}")
    
    if args.volume_path:
        config.vst_volume = args.volume_path
        Logger.info(f"Using command line volume path override: {args.volume_path}")
    
    if args.nvstreamer_path:
        config.default_nvstreamer_base_path = args.nvstreamer_path
        # Update nvstreamer video paths
        for i in range(1, 6):
            config.nvstreamer_video_paths[i] = f"{args.nvstreamer_path}/nvstreamer-{i}"
        Logger.info(f"Using command line NVStreamer path override: {args.nvstreamer_path}")
    
    # Image tag overrides - store in config for later application
    config.tag_overrides = {}

    # Image registry / repository overrides - applied to the base image, tag preserved
    config.image_registry = args.image_registry or ""
    config.nvstreamer_image = args.nvstreamer_image or ""
    if config.image_registry:
        Logger.info(f"Using command line image registry override: {config.image_registry}")
    if config.nvstreamer_image:
        Logger.info(f"Using command line NVStreamer image override: {config.nvstreamer_image}")
    
    if args.all_tag:
        # Apply to all VST service images except MCP and NVStreamer.
        # MCP is intentionally excluded: it is never built locally (pre-built image always used).
        # NVStreamer is intentionally excluded: it has its own --nvstreamer-tag flag.
        # NOTE: update_main_compose_env() includes VST_MCP_IMAGE in vst_image_vars so that
        # per-service --mcp-tag overrides still work; this list intentionally omits it from --all-tag.
        service_images = ['VST_STREAM_PROCESSOR_IMAGE', 'VST_SENSOR_IMAGE', 'VST_RTSPSERVER_IMAGE',
                         'VST_RECORDER_IMAGE', 'VST_LIVESTREAM_IMAGE', 'VST_STORAGE_IMAGE',
                         'VST_REPLAYSTREAM_IMAGE']
        for img in service_images:
            config.tag_overrides[img] = args.all_tag
        Logger.info(f"Using command line all-tag override: {args.all_tag}")
    
    # Individual tag overrides (these take precedence over --all-tag)
    if args.sensor_tag:
        config.tag_overrides['VST_SENSOR_IMAGE'] = args.sensor_tag
        Logger.info(f"Using command line sensor tag override: {args.sensor_tag}")
    
    if args.rtsp_tag:
        config.tag_overrides['VST_RTSPSERVER_IMAGE'] = args.rtsp_tag
        Logger.info(f"Using command line RTSP tag override: {args.rtsp_tag}")
    
    if args.recorder_tag:
        config.tag_overrides['VST_RECORDER_IMAGE'] = args.recorder_tag
        Logger.info(f"Using command line recorder tag override: {args.recorder_tag}")
    
    if args.livestream_tag:
        config.tag_overrides['VST_LIVESTREAM_IMAGE'] = args.livestream_tag
        Logger.info(f"Using command line livestream tag override: {args.livestream_tag}")
    
    if args.storage_tag:
        config.tag_overrides['VST_STORAGE_IMAGE'] = args.storage_tag
        Logger.info(f"Using command line storage tag override: {args.storage_tag}")
    
    if args.replay_tag:
        config.tag_overrides['VST_REPLAYSTREAM_IMAGE'] = args.replay_tag
        Logger.info(f"Using command line replay tag override: {args.replay_tag}")
    
    if args.mcp_tag:
        config.tag_overrides['VST_MCP_IMAGE'] = args.mcp_tag
        Logger.info(f"Using command line MCP tag override: {args.mcp_tag}")
    
    if args.nvstreamer_tag:
        config.tag_overrides['NVSTREAMER_IMAGE'] = args.nvstreamer_tag
        Logger.info(f"Using command line NVStreamer tag override: {args.nvstreamer_tag}")

def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="VIOS One-Click Deployment Script",
        add_help=False,
    )

    # Primary interface: action + target
    parser.add_argument(
        'action', nargs='?', default='deploy',
        choices=['deploy', 'stop', 'config-only'],
        help='Action to perform (default: deploy)',
    )
    parser.add_argument(
        '--target', default='vios',
        choices=['vios', 'scaled', 'nvstreamer', 'all'],  # 'scaled' kept as a back-compat alias for 'vios'
        help='Deployment target (default: vios)',
    )

    # Deployment options
    parser.add_argument('--with-minio', action='store_true', help='Deploy with MinIO object storage')
    parser.add_argument('--with-monitoring', action='store_true', help='Deploy with monitoring services')
    parser.add_argument('--force', action='store_true', help='Automatically stop existing deployments')
    parser.add_argument('--auto', action='store_true', help='Use auto-detected values without confirmation')
    parser.add_argument('--fresh-start', action='store_true', help='Clean start: stop existing, remove VST volume data')
    parser.add_argument('--pull-always', action='store_true', help='Pull latest Docker images before deployment')
    parser.add_argument('--path', type=str, help='Override script directory path')

    # Configuration overrides
    parser.add_argument('--port', type=str, help='Override host IP address')
    parser.add_argument('--config-path', type=str, help='Override VST config path')
    parser.add_argument('--volume-path', type=str, help='Override VST volume path')
    parser.add_argument('--nvstreamer-path', type=str, help='Override NVStreamer base path')

    # Image tag overrides
    parser.add_argument('--all-tag', type=str, help='Override tag for all VST service images including stream-processor (except MCP and NVStreamer)')
    parser.add_argument('--sensor-tag', type=str, help='Override sensor service image tag')
    parser.add_argument('--rtsp-tag', type=str, help='Override RTSP server service image tag')
    parser.add_argument('--recorder-tag', type=str, help='Override recorder service image tag')
    parser.add_argument('--livestream-tag', type=str, help='Override livestream service image tag')
    parser.add_argument('--storage-tag', type=str, help='Override storage service image tag')
    parser.add_argument('--replay-tag', type=str, help='Override replay stream service image tag')
    parser.add_argument('--mcp-tag', type=str, help='Override MCP service image tag')
    parser.add_argument('--nvstreamer-tag', type=str, help='Override NVStreamer service image tag')

    # Image registry / repository overrides
    parser.add_argument('--image-registry', type=str, help='Override registry/org prefix for all VST service images, keeping name and tag (e.g. vios -> vios/vst-sensor:<tag>)')
    parser.add_argument('--nvstreamer-image', type=str, help='Override the NVStreamer image repository, keeping the tag (e.g. nvstreamer or my-registry/nvstreamer)')

    parser.add_argument('--help', action='store_true', help='Show this help message')

    args = parser.parse_args()

    if args.help:
        show_help()
        return

    action = args.action
    target = args.target

    # Copy deployment folder if --path is provided
    if args.path:
        source_path = Path(args.path).absolute()
        if not source_path.exists():
            Logger.error(f"Specified path does not exist: {source_path}")
            sys.exit(1)

        # Check if the provided path is directly a scaling folder
        if source_path.name == "scaling" and source_path.is_dir():
            source_scaling_folder = source_path
            Logger.info(f"Using provided scaling folder directly: {source_scaling_folder}")
        else:
            source_scaling_folder = source_path / "scaling"
            if not source_scaling_folder.exists():
                Logger.error(f"Scaling folder not found in specified path: {source_scaling_folder}")
                sys.exit(1)

        current_dir = Path(__file__).parent.absolute()
        target_scaling_folder = current_dir / "scaling"

        Logger.info(f"Copying scaling folder from {source_scaling_folder} to {target_scaling_folder}")

        if target_scaling_folder.exists():
            Logger.info("Removing existing scaling folder")
            shutil.rmtree(target_scaling_folder)

        try:
            shutil.copytree(source_scaling_folder, target_scaling_folder)
            Logger.success("Successfully copied scaling folder to current directory")
        except Exception as e:
            Logger.error(f"Failed to copy scaling folder: {e}")
            sys.exit(1)

    # Initialize configuration
    config = DeploymentConfig()
    config.scaled_services = (target == 'scaled')
    config.with_minio = args.with_minio
    config.with_monitoring = args.with_monitoring
    config.force_deployment = args.force
    config.auto_mode = args.auto
    config.fresh_start = args.fresh_start
    config.pull_always = args.pull_always

    # Apply command line overrides
    apply_command_line_overrides(config, args)

    # Initialize deployment manager
    deployment_manager = DeploymentManager(config)

    # Dispatch by action + target
    if action == 'stop':
        # Default bare `stop` to all services; --target vios/nvstreamer narrows the scope
        effective_stop_target = target if any(arg == '--target' or arg.startswith('--target=') for arg in sys.argv) else 'all'
        if effective_stop_target == 'all':
            deployment_manager.cleanup_services()
        elif effective_stop_target == 'nvstreamer':
            deployment_manager.stop_existing_nvstreamer_only()
        else:
            deployment_manager.stop_existing_vst_only()
    elif action == 'config-only':
        config_manager = ConfigurationManager(config)
        interactive_config = InteractiveConfiguration(config)
        interactive_config.run_interactive_configuration()
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
        elif target == 'scaled':
            vst_deploy(deployment_manager, config)
        else:
            vst_deploy(deployment_manager, config)

def nvstreamer_deploy(deployment_manager: DeploymentManager, config: DeploymentConfig):
    """NVStreamer-only deployment function"""
    Logger.info("Starting NVStreamer-only deployment...")
    
    deployment_manager.check_prerequisites()
    
    # Handle fresh start mode for NVStreamer
    if config.fresh_start:
        Logger.info("Fresh start mode enabled - will detect, stop NVStreamer deployments")
        
        # Only detect NVStreamer deployments
        nvstreamer_containers = [
            "nvstreamer-1", "nvstreamer-2", "nvstreamer-3", "nvstreamer-4", "nvstreamer-5"
        ]
        running_nvstreamer = DockerManager.get_running_containers(nvstreamer_containers)
        
        if running_nvstreamer:
            Logger.info("Found existing NVStreamer deployments. Stopping them for fresh start...")
            deployment_manager.stop_existing_nvstreamer_only()
        else:
            Logger.info("No existing NVStreamer deployments found")
    else:
        # Detect and handle existing NVStreamer deployments (normal mode)
        nvstreamer_containers = [
            "nvstreamer-1", "nvstreamer-2", "nvstreamer-3", "nvstreamer-4", "nvstreamer-5"
        ]
        running_nvstreamer = DockerManager.get_running_containers(nvstreamer_containers)
        
        if running_nvstreamer:
            print()
            Logger.warning("Existing NVStreamer deployments found. They need to be stopped before deploying new ones.")
            print("This will:")
            print("  - Stop all running NVStreamer containers")
            print("  - Remove containers and networks")
            print()
            
            if config.force_deployment:
                Logger.info("Force deployment enabled. Automatically stopping existing NVStreamer deployments...")
                deployment_manager.stop_existing_nvstreamer_only()
            else:
                response = input("Do you want to stop existing NVStreamer deployments and continue? (y/N): ").strip().lower()
                if response.startswith('y'):
                    deployment_manager.stop_existing_nvstreamer_only()
                else:
                    Logger.info("NVStreamer deployment cancelled by user")
                    print("Tip: Use --force flag to automatically stop existing deployments")
                    return
    
    # Configure only NVStreamer paths
    config_manager = ConfigurationManager(config)
    interactive_config = InteractiveConfiguration(config)
    
    # Run simplified NVStreamer configuration
    interactive_config.run_nvstreamer_only_configuration()
    
    config_manager.update_nvstreamer_compose_env()
    deployment_manager.check_nvstreamer_port_availability()
    
    # Use existing deploy_nvstreamer method
    deployment_manager.deploy_nvstreamer()
    
    deployment_manager.display_nvstreamer_access_urls()

def vst_deploy(deployment_manager: DeploymentManager, config: DeploymentConfig):
    """VST-only deployment function"""
    Logger.info("Starting VST-only deployment...")
    
    deployment_manager.check_prerequisites()
    
    # Handle fresh start mode for VST
    if config.fresh_start:
        Logger.info("Fresh start mode enabled - will detect, stop VST deployments, and remove VST volume")
        
        # Only detect VST deployments
        vst_containers = [
            "centralizedb", "vst-ingress", "redis-server", "sensor-ms", 
            "storage-ms", "vst-mcp", "prometheus", "grafana", "minio-server"
        ]
        running_vst = DockerManager.get_running_containers(vst_containers)
        
        if running_vst:
            Logger.info("Found existing VST deployments. Stopping them for fresh start...")
            deployment_manager.stop_existing_vst_only()
        else:
            Logger.info("No existing VST deployments found")
        
        # Remove VST volume directory
        deployment_manager.remove_vst_volume()
    else:
        # Detect and handle existing VST deployments (normal mode)
        vst_containers = [
            "centralizedb", "vst-ingress", "redis-server", "sensor-ms", 
            "storage-ms", "vst-mcp", "prometheus", "grafana", "minio-server"
        ]
        running_vst = DockerManager.get_running_containers(vst_containers)
        
        if running_vst:
            print()
            Logger.warning("Existing VST deployments found. They need to be stopped before deploying new ones.")
            print("This will:")
            print("  - Stop all running VST containers")
            print("  - Remove containers and networks")
            print()
            
            if config.force_deployment:
                Logger.info("Force deployment enabled. Automatically stopping existing VST deployments...")
                deployment_manager.stop_existing_vst_only()
            else:
                response = input("Do you want to stop existing VST deployments and continue? (y/N): ").strip().lower()
                if response.startswith('y'):
                    deployment_manager.stop_existing_vst_only()
                else:
                    Logger.info("VST deployment cancelled by user")
                    print("Tip: Use --force flag to automatically stop existing deployments")
                    return
    
    # Configure only VST paths
    config_manager = ConfigurationManager(config)
    interactive_config = InteractiveConfiguration(config)
    
    # Run simplified VST configuration
    interactive_config.run_vst_only_configuration()
    
    config_manager.update_main_compose_env()
    deployment_manager.check_vst_port_availability()
    deployment_manager.configure_network_buffers()
    
    # Use existing deploy_vst method
    deployment_manager.deploy_vst()
    
    deployment_manager.display_vst_access_urls()

def main_deploy(deployment_manager: DeploymentManager, config: DeploymentConfig):
    """Main deployment function"""
    Logger.info("Starting VST and NVStreamer deployment...")
    
    deployment_manager.check_prerequisites()
    
    # Handle fresh start mode
    if config.fresh_start:
        Logger.info("Fresh start mode enabled - will detect, stop deployments, and remove VST volume")
        
        # Always detect existing deployments for fresh start
        if deployment_manager.detect_existing_deployments():
            Logger.info("Found existing deployments. Stopping them for fresh start...")
            deployment_manager.stop_existing_deployments()
        else:
            Logger.info("No existing deployments found")
        
        # Remove VST volume directory
        deployment_manager.remove_vst_volume()
        
    else:
        # Detect and handle existing deployments (normal mode)
        if deployment_manager.detect_existing_deployments():
            print()
            Logger.warning("Existing deployments found. They need to be stopped before deploying new ones.")
            print("This will:")
            print("  - Stop all running VST and NVStreamer containers")
            print("  - Remove containers and networks (data volumes will be preserved)")
            print("  - Clean up any orphaned resources")
            print()
            
            if config.force_deployment:
                Logger.info("Force deployment enabled. Automatically stopping existing deployments...")
                deployment_manager.stop_existing_deployments()
            else:
                response = input("Do you want to stop existing deployments and continue? (y/N): ").strip().lower()
                if response.startswith('y'):
                    deployment_manager.stop_existing_deployments()
                else:
                    Logger.info("Deployment cancelled by user")
                    print("Tip: Use --force flag to automatically stop existing deployments")
                    return
    
    deployment_manager.configure_network_buffers()
    
    interactive_config = InteractiveConfiguration(config)
    interactive_config.run_interactive_configuration()
    
    deployment_manager.config_manager.update_main_compose_env()
    deployment_manager.config_manager.update_nvstreamer_compose_env()
    deployment_manager.check_port_availability()
    
    deployment_manager.deploy_nvstreamer()
    deployment_manager.deploy_vst()
    
    deployment_manager.display_access_urls()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        Logger.info("Deployment interrupted by user")
        sys.exit(1)
    except Exception as e:
        Logger.error(f"Unexpected error: {e}")
        sys.exit(1)
