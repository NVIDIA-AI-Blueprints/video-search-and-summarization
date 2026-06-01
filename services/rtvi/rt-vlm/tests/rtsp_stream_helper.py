# SPDX-FileCopyrightText: Copyright (c) 2024-2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
RTSP Stream Helper using cvlc (VLC command-line)

Provides utilities to create and manage RTSP streams from video files
for testing purposes. Uses cvlc to stream video files as RTSP sources.
"""

import os
import subprocess
import time
import uuid
from typing import Optional


class RTSPStreamManager:
    """Manages RTSP streams created using cvlc"""

    def __init__(self, base_port: int = 8554, base_ip: str = "127.0.0.1"):
        """
        Initialize RTSP stream manager.

        Args:
            base_port: Base port for RTSP streams (will increment for each stream)
            base_ip: IP address to bind RTSP streams to
        """
        self.base_port = base_port
        self.base_ip = base_ip
        self.active_streams = {}  # stream_id -> process info
        self._current_port = base_port

    def _check_cvlc_available(self) -> tuple[bool, str]:
        """
        Check if cvlc or vlc-wrapper is available on the system.

        Returns:
            Tuple of (is_available, command_path)
        """
        # Check if running as root
        is_root = os.geteuid() == 0

        # If root, ONLY try vlc-wrapper (cvlc won't work as root)
        if is_root:
            vlc_wrapper_paths = ["/usr/bin/vlc-wrapper", "/usr/local/bin/vlc-wrapper"]
            for vlc_wrapper_path in vlc_wrapper_paths:
                if os.path.exists(vlc_wrapper_path) and os.access(vlc_wrapper_path, os.X_OK):
                    return True, vlc_wrapper_path
            # Also try which as fallback
            try:
                result = subprocess.run(
                    ["which", "vlc-wrapper"], capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    wrapper_path = result.stdout.strip()
                    if wrapper_path:
                        return True, wrapper_path
            except Exception:
                pass
            # If root and vlc-wrapper not found, return False (don't try cvlc)
            return False, ""

        # Try regular cvlc (only if not root)
        try:
            result = subprocess.run(["which", "cvlc"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                cvlc_path = result.stdout.strip()
                if cvlc_path:
                    return True, cvlc_path
        except Exception:
            pass

        return False, ""

    def start_stream(
        self,
        video_file: str,
        stream_id: Optional[str] = None,
        port: Optional[int] = None,
        loop: bool = True,
        network_caching: int = 1500,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> dict:
        """
        Start an RTSP stream from a video file using cvlc.

        Args:
            video_file: Path to video file to stream
            stream_id: Unique identifier for the stream (auto-generated if None)
            port: RTSP port (auto-assigned if None)
            loop: Whether to loop the video
            network_caching: Network caching in milliseconds
            username: Optional username for RTSP authentication
            password: Optional password for RTSP authentication

        Returns:
            dict with stream_id, rtsp_url, port, and process info

        Raises:
            FileNotFoundError: If video file doesn't exist
            RuntimeError: If cvlc is not available or stream fails to start
        """
        if not os.path.exists(video_file):
            raise FileNotFoundError(f"Video file not found: {video_file}")

        cvlc_available, cvlc_cmd = self._check_cvlc_available()
        if not cvlc_available:
            is_root = os.geteuid() == 0
            if is_root:
                raise RuntimeError(
                    "Cannot run VLC as root. Options:\n"
                    "1. Run tests as non-root user\n"
                    "2. Install and configure vlc-wrapper:\n"
                    "   apt-get install -y vlc-plugin-base\n"
                    "   chmod u+s /usr/bin/vlc-wrapper  # Make it Set-UID root\n"
                    "3. Skip RTSP stream tests when running as root"
                )
            else:
                raise RuntimeError(
                    "cvlc (VLC command-line) is not available. Install with: apt-get install -y vlc"
                )

        if stream_id is None:
            stream_id = str(uuid.uuid4())

        if port is None:
            port = self._current_port
            self._current_port += 1

        # Build RTSP URL
        if username and password:
            rtsp_url = f"rtsp://{username}:{password}@{self.base_ip}:{port}/file-stream"
        else:
            rtsp_url = f"rtsp://{self.base_ip}:{port}/file-stream"

        # Build cvlc command (use vlc-wrapper if root, otherwise cvlc)
        cmd = [
            cvlc_cmd,
            "--intf",
            "dummy",  # No interface
            "--quiet",  # Suppress output
        ]

        if loop:
            cmd.append("--loop")

        # RTSP output configuration
        rtsp_output = (
            f":sout=#gather:rtp{{sdp=rtsp://:{port}/file-stream}} "
            f":network-caching={network_caching} "
            ":sout-all "
            ":sout-keep"
        )

        cmd.extend([video_file, rtsp_output])

        # Start cvlc process
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid,  # Create new process group
            )

            # Wait a bit for stream to start
            time.sleep(2)

            # Check if process is still running
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise RuntimeError(
                    f"cvlc process exited immediately. "
                    f"stdout: {stdout.decode()}, stderr: {stderr.decode()}"
                )

            stream_info = {
                "stream_id": stream_id,
                "rtsp_url": rtsp_url,
                "port": port,
                "process": process,
                "video_file": video_file,
                "cmd": " ".join(cmd),
            }

            self.active_streams[stream_id] = stream_info

            return stream_info

        except Exception as e:
            raise RuntimeError(f"Failed to start RTSP stream: {e}") from e

    def stop_stream(self, stream_id: str, timeout: int = 5) -> bool:
        """
        Stop an RTSP stream.

        Args:
            stream_id: Stream identifier
            timeout: Timeout in seconds for graceful shutdown

        Returns:
            True if stream was stopped successfully, False otherwise
        """
        if stream_id not in self.active_streams:
            return False

        stream_info = self.active_streams[stream_id]
        process = stream_info["process"]

        try:
            # Try graceful termination first
            os.killpg(os.getpgid(process.pid), 15)  # SIGTERM

            # Wait for process to terminate
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Force kill if graceful termination failed
                os.killpg(os.getpgid(process.pid), 9)  # SIGKILL
                process.wait()

            del self.active_streams[stream_id]
            return True

        except ProcessLookupError:
            # Process already terminated
            if stream_id in self.active_streams:
                del self.active_streams[stream_id]
            return True
        except Exception as e:
            print(f"Error stopping stream {stream_id}: {e}")
            return False

    def stop_all_streams(self, timeout: int = 5):
        """Stop all active streams"""
        stream_ids = list(self.active_streams.keys())
        for stream_id in stream_ids:
            self.stop_stream(stream_id, timeout)

    def get_stream_info(self, stream_id: str) -> Optional[dict]:
        """Get information about an active stream"""
        return self.active_streams.get(stream_id)

    def list_active_streams(self) -> list:
        """List all active stream IDs"""
        return list(self.active_streams.keys())

    def cleanup(self):
        """Cleanup all streams and reset state"""
        self.stop_all_streams()
        self._current_port = self.base_port


# Global stream manager instance
_global_manager: Optional[RTSPStreamManager] = None


def get_stream_manager() -> RTSPStreamManager:
    """Get or create global stream manager instance"""
    global _global_manager
    if _global_manager is None:
        _global_manager = RTSPStreamManager()
    return _global_manager


def start_rtsp_stream(
    video_file: str,
    stream_id: Optional[str] = None,
    port: Optional[int] = None,
    loop: bool = True,
) -> dict:
    """
    Convenience function to start an RTSP stream.

    Args:
        video_file: Path to video file
        stream_id: Optional stream ID
        port: Optional port (auto-assigned if None)
        loop: Whether to loop the video

    Returns:
        Stream info dictionary with rtsp_url, stream_id, etc.
    """
    manager = get_stream_manager()
    return manager.start_stream(video_file, stream_id, port, loop)


def stop_rtsp_stream(stream_id: str) -> bool:
    """
    Convenience function to stop an RTSP stream.

    Args:
        stream_id: Stream identifier

    Returns:
        True if stopped successfully
    """
    manager = get_stream_manager()
    return manager.stop_stream(stream_id)


def cleanup_all_streams():
    """Cleanup all active streams"""
    manager = get_stream_manager()
    manager.cleanup()
